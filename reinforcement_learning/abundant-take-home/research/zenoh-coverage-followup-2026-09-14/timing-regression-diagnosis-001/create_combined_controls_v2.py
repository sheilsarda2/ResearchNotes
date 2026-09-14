"""Combine two independently validated normal controls without rewriting failed controllers."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
HERE=Path(__file__).resolve().parent
BASE=HERE.parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(BASE))
import validate_controls_immutable as original
import run_oracle_confirmation as confirmation


def read(path):return json.loads(path.read_text())
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def ref(path):return {'path':str(path.relative_to(ROOT)),'sha256':digest(path)}
def bound(reference):
    path=ROOT/reference['path'];assert digest(path)==reference['sha256'];return read(path)
def run(path):
    value=read(path);identity_path=ROOT/value['run_identity'];identity=read(identity_path)
    assert digest(identity_path)==value['run_identity_sha256']
    assert value['finished_at'] and value['inputs_unchanged'] and value['tooling_unchanged'] and value['model_calls']==0
    assert identity['inputs']==original.frozen_inputs() and identity['task_checksum']==read(BASE/'task-manifest.json')['harbor_task_checksum']
    return value,identity_path,identity

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--decision',type=Path,required=True);parser.add_argument('--write',action='store_true');args=parser.parse_args()
    reviewed=confirmation.gate(args.decision.resolve())
    paths={'original_controls':BASE/'harbor-controls-final/summary.json',
        'oracle_confirmation':BASE/'harbor-oracle-confirmation-001/summary.json',
        'independent_oracle_validation':BASE/'oracle-independent-validation-001/summary.json',
        'independent_nop_validation':BASE/'nop-independent-validation-001/summary.json'}
    runs={kind:run(path) for kind,path in paths.items()}
    old,_,old_id=runs['original_controls'];new,_,_=runs['oracle_confirmation']
    assert old['passed'] is new['passed'] is False
    assert old['controls']['oracle']['passed'] is old['controls']['nop']['passed'] is new['controls']['oracle']['passed'] is False
    chosen={}
    for agent,kind,source_kind,expected_kind,original_field in [
        ('oracle','independent_oracle_validation','oracle_confirmation','independent_normal_oracle_confirmation_validation','original_control_record'),
        ('nop','independent_nop_validation','original_controls','independent_original_nop_validation','original_nop_record')]:
        cert,_,identity=runs[kind];source,source_identity_path,_=runs[source_kind]
        assert cert['passed'] and cert['kind']==expected_kind and cert['new_control_runs']==0
        assert cert['source_run']=={'summary':ref(paths[source_kind]),'run_identity':ref(source_identity_path)}
        assert cert[original_field]==source['controls'][agent]
        assert cert['original_run_file_sha256_before']==cert['original_run_file_sha256_after']==original.hashes(paths[source_kind].parent)
        record=cert['controls'][agent]
        raw=ROOT/record['result'];assert digest(raw)==record['result_sha256']
        verified=original.validate_result(raw,agent,identity['task_checksum'],identity['tooling_sha256'])
        assert all(record[k]==v for k,v in verified.items())
        assert original.validate_admission(ROOT/record['admission_lifecycle']['path'],raw.parents[3]/'control.json')==record['admission_lifecycle']
        terminal=bound(cert['terminal_absence']);sample=bound(terminal['shared_observation'])
        assert sample==terminal['terminal_sample'] and sample['terminal_sample'] and sample['own_participants']=={}
        assert terminal['original_runner_absent'] and terminal['container_absent'] and terminal['claim_absent'] and terminal['matching_containers']==[]
        chosen[agent]=record
    image=bound(chosen['oracle']['verifier_image_proof'])
    assert image['passed'] and image['oracle_result_sha256']==chosen['oracle']['result_sha256'] and image['oracle_result_path']==chosen['oracle']['result']
    failures={}
    for agent in ('oracle','nop'):
        raw_paths=list((paths['original_controls'].parent/'jobs'/old['controls'][agent]['job']).glob('*/result.json'));assert len(raw_paths)==1
        raw=raw_paths[0];score=raw.parent/'verifier/score.json'
        assert read(raw)['verifier_result']['rewards']['reward']==read(score)['reward']==0 and read(raw)['exception_info'] is None
        failures[agent]={'result':ref(raw),'score':ref(score),'reward':0,'exception':None,'observation_errors':old['controls'][agent]['observation_errors'],
            'reason':('Original timing regression failed twice; one unchanged-task normal oracle confirmation is independently validated after bounded comparison.' if agent=='oracle' else 'Existing normal nop independently revalidated from unchanged raw/source/admission evidence and fresh locked terminal absence; no additional nop execution.')}
    prior_path=BASE/'continuation-001/summary.json';prior=read(prior_path)
    assert prior['finished_at'] and prior['stages']=={} and prior['passed'] is False
    assert not (BASE/'reviews-final-001').exists()
    output=BASE/'controls-confirmed-001';assert not output.exists()
    source_runs=[{'kind':kind,'summary':ref(path),'run_identity':ref(runs[kind][1])} for kind,path in paths.items()]
    tooling={}
    for _,_,identity in runs.values():tooling.update(identity['tooling_sha256'])
    tooling[str(Path(__file__).relative_to(ROOT))]=digest(Path(__file__))
    identity={'schema_version':1,'kind':'additive_control_selection_with_independent_validation','created_at':original.now(),
        'model_calls':0,'task':old_id['task'],'task_checksum':old_id['task_checksum'],'inputs':old_id['inputs'],
        'source_runs':source_runs,'original_budgets':old_id['original_budgets'],'tooling_sha256':tooling}
    summary={'schema_version':1,'kind':'additive_combined_controls','passed':True,'model_calls':0,'inputs_unchanged':True,'tooling_unchanged':True,
        'finished_at':original.now(),'controls':chosen,'source_runs':source_runs,'superseded_oracle':failures['oracle'],'superseded_nop':failures['nop'],
        'confirmation_observer_failure':{'summary':ref(paths['oracle_confirmation']),'independent_validation':ref(paths['independent_oracle_validation']),
            'reason':'Actual normal oracle result is independently validated; checksum-only image-tag callback failure remains recorded in its original failed controller summary.'},
        'prior_continuation':{'summary':ref(prior_path),'finished_at':prior['finished_at'],'stages':{},'passed':False},
        'timing_comparison':{'result':reviewed['comparison_result'],'inputs':reviewed['comparison_inputs'],'decision':reviewed['decision']}}
    if not args.write:
        print(json.dumps({'check_only':True,'passed':True,'source_runs':source_runs,'selected_rewards':{k:v['reward'] for k,v in chosen.items()}}));return
    output.mkdir();original.dump(output/'run-identity.json',identity)
    summary.update(run_identity=str((output/'run-identity.json').relative_to(ROOT)),run_identity_sha256=digest(output/'run-identity.json'))
    original.dump(output/'summary.json',summary);print(json.dumps(ref(output/'summary.json')))

if __name__=='__main__':main()

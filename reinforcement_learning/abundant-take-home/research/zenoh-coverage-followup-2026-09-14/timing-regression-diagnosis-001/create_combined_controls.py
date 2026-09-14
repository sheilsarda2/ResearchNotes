"""Additive control certificate; preserve original failures and independent revalidation."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0,str(BASE))
import validate_controls_immutable as original
import run_oracle_confirmation as confirmation


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(path):
    return {'path':str(path.relative_to(ROOT)), 'sha256':digest(path)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--decision',type=Path,required=True)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    reviewed=confirmation.gate(args.decision.resolve())
    old_path=BASE/'harbor-controls-final/summary.json'
    new_path=BASE/'harbor-oracle-confirmation-001/summary.json'
    old,new=read(old_path),read(new_path)
    assert old['finished_at'] and not old['passed'] and new['finished_at'] and new['passed']
    assert all(r['inputs_unchanged'] and r['tooling_unchanged'] and r['model_calls']==0 for r in (old,new))
    nop_path=BASE/'nop-independent-validation-001/summary.json'
    nop=read(nop_path)
    assert nop['kind']=='independent_original_nop_validation' and nop['passed'] is True
    assert nop['execution_kind']=='independent_revalidation_of_existing_normal_nop' and nop['new_control_runs']==nop['model_calls']==0
    assert nop['source_run']['summary']==ref(old_path) and nop['original_nop_record']==old['controls']['nop']
    assert nop['finished_at'] and nop['inputs_unchanged'] and nop['tooling_unchanged']
    terminal_path=ROOT/nop['terminal_absence']['path'];terminal=read(terminal_path)
    assert digest(terminal_path)==nop['terminal_absence']['sha256']
    assert terminal['kind']=='fresh_locked_terminal_absence' and terminal['original_runner_absent'] and terminal['container_absent'] and terminal['claim_absent']
    assert terminal['matching_containers']==[] and terminal['observed_runner_start_ticks']!=terminal['historical_runner']['start_ticks']
    observation_path=ROOT/terminal['shared_observation']['path']
    assert digest(observation_path)==terminal['shared_observation']['sha256']
    assert [json.loads(line) for line in observation_path.read_text().splitlines()]==[terminal['terminal_sample']]
    assert terminal['terminal_sample']['terminal_sample'] and terminal['terminal_sample']['own_participants']=={}
    assert nop['original_run_file_sha256_before']==nop['original_run_file_sha256_after']==original.hashes(old_path.parent)
    nop_identity_path=ROOT/nop['run_identity'];nop_id=read(nop_identity_path)
    assert digest(nop_identity_path)==nop['run_identity_sha256']
    chosen={'oracle':new['controls']['oracle'],'nop':nop['controls']['nop']}
    old_identity_path=ROOT/old['run_identity'];new_identity_path=ROOT/new['run_identity']
    assert digest(old_identity_path)==old['run_identity_sha256'] and digest(new_identity_path)==new['run_identity_sha256']
    old_id,new_id=read(old_identity_path),read(new_identity_path)
    assert old_id['inputs']==new_id['inputs']==nop_id['inputs']==original.frozen_inputs()
    assert old_id['task_checksum']==new_id['task_checksum']==nop_id['task_checksum']==read(BASE/'task-manifest.json')['harbor_task_checksum']
    for agent,record in chosen.items():
        identity=nop_id if agent=='nop' else new_id
        result_path=ROOT/record['result']
        assert digest(result_path)==record['result_sha256']
        verified=original.validate_result(result_path,agent,identity['task_checksum'],identity['tooling_sha256'])
        assert all(record[key]==value for key,value in verified.items())
        assert original.validate_admission(ROOT/record['admission_lifecycle']['path'],result_path.parents[3]/'control.json')==record['admission_lifecycle']
    original_oracle=list((old_path.parent/'jobs').glob('*oracle*/*/result.json'))
    assert len(original_oracle)==1
    failed_result=original_oracle[0]
    failed_score=failed_result.parent/'verifier/score.json'
    assert read(failed_result)['verifier_result']['rewards']['reward']==0 and read(failed_result)['exception_info'] is None
    assert read(failed_score)['reward']==0
    prior_path=BASE/'continuation-001/summary.json';prior=read(prior_path)
    assert prior['finished_at'] and prior['stages']=={} and not prior['passed']
    assert not (BASE/'reviews-final-001').exists()
    image_ref=chosen['oracle']['verifier_image_proof'];image_path=ROOT/image_ref['path'];image=read(image_path)
    assert digest(image_path)==image_ref['sha256'] and image['oracle_result_sha256']==chosen['oracle']['result_sha256']
    output=BASE/'controls-confirmed-001'
    assert not output.exists(), 'Never overwrite combined evidence'
    source_runs=[{'kind':'original_controls','summary':ref(old_path),'run_identity':ref(old_identity_path)},
                 {'kind':'oracle_confirmation','summary':ref(new_path),'run_identity':ref(new_identity_path)},
                 {'kind':'independent_nop_validation','summary':ref(nop_path),'run_identity':ref(nop_identity_path)}]
    identity={'schema_version':1,'kind':'additive_control_selection_with_independent_original_nop_validation',
              'created_at':datetime.now(timezone.utc).isoformat(),'model_calls':0,
              'task':old_id['task'],'task_checksum':old_id['task_checksum'],'inputs':old_id['inputs'],
              'source_runs':source_runs,'original_budgets':old_id['original_budgets'],
              'tooling_sha256':{**old_id['tooling_sha256'],**new_id['tooling_sha256'],**nop_id['tooling_sha256'],str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))}}
    summary={'schema_version':1,'kind':'additive_combined_controls','passed':True,'model_calls':0,
             'inputs_unchanged':True,'tooling_unchanged':True,'finished_at':datetime.now(timezone.utc).isoformat(),
             'controls':chosen,'source_runs':source_runs,
             'superseded_oracle':{'result':ref(failed_result),'score':ref(failed_score),'reward':0,'exception':None,
                'observation_errors':old['controls']['oracle']['observation_errors'],
                'reason':'Original timing regression failed twice; unchanged-task full confirmation selected after bounded comparison; raw outcome preserved'},
             'superseded_nop':{'result':ref(ROOT/chosen['nop']['result']),'score':ref((ROOT/chosen['nop']['result']).parent/'verifier/score.json'),'reward':0,'exception':None,
                'observation_errors':old['controls']['nop']['observation_errors'],
                'reason':'Existing normal nop independently revalidated from unchanged raw/source/admission evidence plus fresh locked terminal absence; no additional nop execution'},
             'prior_continuation':{'summary':ref(prior_path),'finished_at':prior['finished_at'],'stages':{},'passed':False},
             'timing_comparison':{'result':reviewed['comparison_result'],'inputs':reviewed['comparison_inputs'],'decision':reviewed['decision']}}
    if not args.write:
        print(json.dumps({'check_only':True,'passed':True,'selected_rewards':{k:v['reward'] for k,v in chosen.items()},'source_runs':source_runs},indent=2))
        return
    output.mkdir()
    original.dump(output/'run-identity.json',identity)
    summary.update(run_identity=str((output/'run-identity.json').relative_to(ROOT)),run_identity_sha256=digest(output/'run-identity.json'))
    original.dump(output/'summary.json',summary)
    print(json.dumps({'path':str((output/'summary.json').relative_to(ROOT)),'sha256':digest(output/'summary.json')}))


if __name__=='__main__':main()

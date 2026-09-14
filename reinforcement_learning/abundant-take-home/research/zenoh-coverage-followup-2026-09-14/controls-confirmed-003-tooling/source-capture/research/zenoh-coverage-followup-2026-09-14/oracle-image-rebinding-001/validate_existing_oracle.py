"""Certify a completed normal oracle with a preserved image-tag callback failure."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE/'timing-regression-diagnosis-001'))
import validate_controls_immutable as original
import read_only_observer as observer


def ref(path): return {'path':str(path.relative_to(ROOT)), 'sha256':original.digest(path)}
def bound(value):
    path=ROOT/value['path']
    assert original.digest(path)==value['sha256']
    return original.read(path)
def process_identity(pid):
    try: return (Path('/proc')/str(pid)/'stat').read_text().rsplit(') ',1)[1].split()[19]
    except FileNotFoundError: return None


def validate_callback_failure(summary):
    assert summary['kind']=='single_full_oracle_confirmation' and summary['finished_at'] and summary['passed'] is False
    assert summary['inputs_unchanged'] and summary['tooling_unchanged'] and summary['decision_unchanged']
    row=summary['controls']['oracle']
    assert row['passed'] is False and row['exit_code']==0
    assert row['error_type']=='AssertionError' and row['error']==''
    observation=row['observation']
    assert observation['exit_code']==0 and observation['terminal_sample_valid'] is True and observation['terminal_sample_at']
    errors=observation['errors']
    assert errors and all(e['error_type']=='AssertionError' and e['error']=='Private tag already identifies a different image' and e['terminal'] is False for e in errors)
    return row,errors


def main():
    old=BASE/'harbor-oracle-confirmation-001'
    output=BASE/'oracle-independent-validation-001'
    assert not output.exists(), 'Never overwrite an independent certificate'
    owner_path=HERE/'controller-owner.json';owner=original.read(owner_path)
    owner_observed=process_identity(owner['pid'])
    assert owner['kind']=='observed_existing_oracle_confirmation_controller' and owner_observed!=owner['start_ticks'], 'Existing confirmation controller must finish first'
    owner_absence={'controller':ref(owner_path),'observed_start_ticks':owner_observed,'absent':True,'at':original.now()}
    before=original.hashes(old)
    summary_path=old/'summary.json';summary=original.read(summary_path)
    row,errors=validate_callback_failure(summary)
    identity_path=ROOT/summary['run_identity'];identity=original.read(identity_path)
    assert original.digest(identity_path)==summary['run_identity_sha256']==owner['run_identity_sha256']
    assert identity['inputs']==original.frozen_inputs()
    tools=identity['tooling_sha256']
    for path,value in original.tooling_hashes().items(): assert tools[path]==value
    for path,value in tools.items():
        source=original.CAPTURE/'files'/path if path.startswith('scripts/') else ROOT/path
        assert original.digest(source)==value
    paths=list((old/'jobs'/row['job']).glob('*/result.json'));assert len(paths)==1
    result_path=paths[0];raw=original.read(result_path)
    verified=original.validate_result(result_path,'oracle',identity['task_checksum'],tools)
    admission_path=old/'oracle-admission-observations.jsonl'
    admission=original.validate_admission(admission_path,old/'control.json')
    samples=[json.loads(line) for line in admission_path.read_text().splitlines()]
    owners={(key,p['pid'],p['identity']) for s in samples for key,p in s['own_participants'].items() if p['trials']}
    assert len(owners)==1
    key,pid,ticks=next(iter(owners))
    assert key==str(pid)+':'+ticks and all(s['runner_pid']==pid for s in samples)
    assert all(set(p['trials'])=={raw['trial_name']} for s in samples for p in s['own_participants'].values() if p['trials'])
    assert samples[-1]['terminal_sample'] is True and samples[-1]['at']==row['observation']['terminal_sample_at']
    assert original.epoch(samples[-1]['at'])>original.epoch(raw['finished_at'])
    actual_ticks=process_identity(pid);assert actual_ticks!=ticks
    capture_path=HERE/'capture.json';capture=original.read(capture_path)
    assert capture['kind']=='actual_normal_oracle_confirmation_image_capture' and capture['passed'] and capture['prior_tag_unchanged']
    assert capture['task_checksum']==identity['task_checksum'] and capture['run_identity']==ref(identity_path)
    assert capture['oracle_trial']==str(result_path.parent.relative_to(ROOT))
    assert bound(capture['actual_test_files'])=={k:v for k,v in identity['inputs']['task_file_sha256'].items() if k.startswith('tests/')}
    inspection=bound(capture['filtered_inspection'])
    assert inspection['Image']==capture['verifier_image_id']
    assert inspection['Config.Labels']['com.docker.compose.project']==raw['trial_name'].lower()+'__verifier__trial'
    host=inspection['HostConfig']
    assert host=={'NanoCpus':4000000000,'Memory':8589934592,'MemorySwap':17179869184,'NetworkMode':'none','StorageOpt':None}
    for tag,image_id in [(capture['private_tag'],capture['verifier_image_id']),(capture['prior_private_tag'],capture['prior_image_id'])]:
        assert subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True,timeout=20).strip()==image_id
    assert capture['prior_image_id']!=capture['verifier_image_id']
    layers=bound(capture['image_layers']);assert layers['equal_rootfs_layers'] and layers['rootfs_layers']['original']==layers['rootfs_layers']['confirmation']
    output.mkdir()
    terminal=observer.observe_once(output/'terminal-observation.jsonl',old/'control.json',pid,
        shared_control=Path(original.admission_control()['shared_pool']),gap_path=output/'recovered-read-gaps.jsonl',terminal=True)
    assert terminal['terminal_sample'] and terminal['own_participants']=={} and terminal['total_claims']<=terminal['shared_max_active']==14
    command=['docker','ps','--all','--no-trunc','--format','{{json .}}']
    inventory=subprocess.check_output(command,text=True,timeout=20)
    name=raw['trial_name'].lower()
    matches=[c for c in map(json.loads,inventory.splitlines()) if name in (c.get('Names','')+' '+c.get('Labels','')).lower()]
    assert matches==[]
    terminal_proof={'kind':'fresh_locked_terminal_absence','at':original.now(),'shared_observation':ref(output/'terminal-observation.jsonl'),
        'terminal_sample':terminal,'historical_runner':{'pid':pid,'start_ticks':ticks,'participant_key':key},
        'observed_runner_start_ticks':actual_ticks,'original_runner_absent':True,'container_inventory_command':command,
        'container_inventory_sha256':hashlib.sha256(inventory.encode()).hexdigest(),'trial_name_match':name,
        'matching_containers':matches,'container_absent':True,'claim_absent':True}
    original.dump(output/'terminal-absence.json',terminal_proof)
    image_proof={'kind':'normal_harbor_oracle_verifier_image','verifier_image_id':capture['verifier_image_id'],
        'private_tag':capture['private_tag'],'oracle_trial_path':str(result_path.parent.relative_to(ROOT)),
        'final_task_checksum':identity['task_checksum'],'observed_host_config':host,
        'raw_docker_inspection_path':capture['filtered_inspection']['path'],'raw_docker_inspection_sha256':capture['filtered_inspection']['sha256'],
        'helper_sha256':original.digest(Path(__file__)),'model_calls':0,'oracle_result_path':str(result_path.relative_to(ROOT)),
        'oracle_result_sha256':original.digest(result_path),'passed':True,'image_capture':ref(capture_path),
        'validation_kind':'independent_revalidation_of_existing_normal_oracle_confirmation'}
    original.dump(output/'verifier-image-proof.json',image_proof)
    selected={k:row[k] for k in ('job','command','exit_code','wall_seconds')};selected.update(verified)
    selected.update(admission_lifecycle=admission,validation_kind='independent_revalidation_of_existing_normal_oracle_confirmation',
        terminal_absence=ref(output/'terminal-absence.json'),original_observation_errors=errors,
        verifier_image_proof={**ref(output/'verifier-image-proof.json'),'verifier_image_id':capture['verifier_image_id'],'private_tag':capture['private_tag']})
    source_run={'summary':ref(summary_path),'run_identity':ref(identity_path)}
    sources=dict(tools)
    for p in (Path(__file__).resolve(),HERE/'capture_image.py',BASE/'timing-regression-diagnosis-001/read_only_observer.py'):
        sources[str(p.relative_to(ROOT))]=original.digest(p)
    new_identity={'schema_version':1,'kind':'independent_normal_oracle_confirmation_revalidation','created_at':original.now(),
        'task':identity['task'],'task_checksum':identity['task_checksum'],'harbor_version':identity['harbor_version'],
        'inputs':identity['inputs'],'original_budgets':identity['original_budgets'],'tooling_sha256':sources,
        'source_run':source_run,'model_calls':0,'new_control_runs':0,'original_controller':ref(owner_path)}
    assert original.hashes(old)==before
    original.dump(output/'run-identity.json',new_identity)
    certificate={'schema_version':1,'kind':'independent_normal_oracle_confirmation_validation',
        'execution_kind':'independent_revalidation_of_existing_normal_oracle_confirmation','passed':True,
        'finished_at':original.now(),'new_control_runs':0,'model_calls':0,'inputs_unchanged':True,'tooling_unchanged':True,
        'run_identity':str((output/'run-identity.json').relative_to(ROOT)),'run_identity_sha256':original.digest(output/'run-identity.json'),
        'source_run':source_run,'original_control_record':row,'original_observation_errors':errors,'original_controller_absence':owner_absence,
        'original_run_file_sha256_before':before,'original_run_file_sha256_after':original.hashes(old),
        'terminal_absence':ref(output/'terminal-absence.json'),'image_capture':ref(capture_path),'controls':{'oracle':selected},
        'interpretation':'Actual normal oracle passes all eleven unchanged verifier groups. The confirmation controller remains failed solely because its checksum-only private image tag identified the earlier image metadata ID. Raw outcome, callback errors and old tag are preserved. Separate actual-container/test-map/image/limit evidence plus independent raw/source/admission and fresh terminal absence validate this same execution; no additional oracle was run.'}
    original.dump(output/'summary.json',certificate)
    print(json.dumps({'certificate':ref(output/'summary.json'),'passed':True,'new_control_runs':0,'admission_samples':admission['sample_count']}))

if __name__=='__main__':main()

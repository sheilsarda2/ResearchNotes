import json, hashlib, subprocess
from pathlib import Path
from datetime import datetime, timezone
B=Path('research/zenoh-coverage-followup-2026-09-14')
E=Path('research/takehome-presentation-2026-09-14/evidence')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text())
def ref(p): return {'path':str(p),'sha256':sha(p)}
def manifest(p): return {x.relative_to(p).as_posix():sha(x) for x in p.rglob('*') if x.is_file()}
inputs=read(B/'paired-regrades/run-002/inputs.json')
task=Path(inputs['task']['path'])
assert manifest(task)==inputs['task']['task_file_sha256']
assert len(inputs['task']['task_file_sha256'])==24 and len(inputs['test_file_sha256'])==15
cases=[]
for prefix,name,reward,artifacts in [('eighth','08-gSYDMEG',1,258),('ninth','09-YtCswNv',0,233)]:
 rp=B/f'paired-{prefix}-case-review-001/review.json'; review=read(rp)
 case=B/'paired-regrades/run-002'/name; d=read(case/'result.json')
 assert review['passed'] and len(review['artifact_file_sha256'])==artifacts
 assert manifest(case)==review['artifact_file_sha256']
 for r in review['references'].values(): assert sha(r['path'])==r['sha256']
 assert d['status']=='complete' and d['validation_passed'] and d['reward']==reward and d['driver_exit_code']==0
 assert d['full_regrade'] and d['model_calls']==0 and not d['counted_sweep_trial']
 before=d['source_file_sha256_before']; assert before==d['source_file_sha256_after'] and len(before)==204
 assert manifest(case/'source-capture')==before
 orig=read(d['original_input_record_path'])
 for path,h in before.items():
  r=orig['source_files']['artifacts/submission/'+path]
  assert r['kind']=='file' and r['sha256']==h
  p=Path(orig['trial'])/'artifacts/submission'/path
  assert sha(p)==h and p.stat().st_size==r['size']
 assert d['test_file_sha256_before']==d['test_file_sha256_after']==inputs['test_file_sha256']
 raw=read(case/'artifacts/case/raw-result.json')
 assert not raw['timed_out'] and raw['verifier_exit_code']==0 and raw['model_calls']==0
 assert raw['workload_timeout_seconds']==3600 and raw['verifier_finished_at_epoch']<=raw['deadline_epoch']
 assert set(d['groups'])==set(d['required_groups']) and len(d['groups'])==11
 if reward: assert all(g['pass'] for g in d['groups'].values())
 else:
  assert d['groups']['anti_cheat']['pass'] and d['groups']['build_a']['exit_code']==101
  assert all(g['reason']=='not run: earlier group failed' for k,g in d['groups'].items() if k not in ('anti_cheat','build_a'))
 assert d['cleanup']=={'claim_absent':True,'container_absent':True,'errors':[],'stopped_before_collection':True}
 inspection=read(case/'artifacts/03-inspect-limits.log')
 assert inspection=={'image':inputs['verifier_image_id'],'memory':8589934592,'swap':17179869184,'cpus':4000000000,'network':'none','storage_opt':None,'running':True}
 cases.append({'case':name,'review':ref(rp),'result':ref(case/'result.json'),'reward':reward,'artifact_files_verified':artifacts,'source_files_verified':204,'test_files_verified':15,'all_11_score_groups_present':True,'all_11_groups_pass':bool(reward),'verifier_elapsed_seconds':raw['verifier_finished_at_epoch']-raw['verifier_started_at_epoch'],'before_deadline':True,'cleanup':d['cleanup'],'observed_limits':inspection,'container_name':d['container_name']})
summary_path=B/'paired-regrades/run-002/summary.json'; summary=read(summary_path)
assert summary['all_regrades_complete'] and summary['validation_passed'] and summary['model_calls']==summary['counted_sweep_trials']==0
assert len(summary['regrades'])==9
assert sorted(x['reward'] for x in summary['regrades'])==[0]+[1]*8
for pair in summary['regrades']:
 assert sha(pair['result_path'])==pair['result_sha256']
 assert manifest(Path(pair['artifact_root']))==pair['artifact_file_sha256']
cont_path=B/'continuation-confirmed-005/summary.json'; cont=read(cont_path);assert cont['passed'] and cont['model_calls']==cont['counted_sweep_trials']==0
code='''import json,fcntl,subprocess,hashlib
from pathlib import Path
from datetime import datetime,timezone
root=Path('/workspaces/sheil_research/reinforcement_learning/abundant-take-home')
names=['zenoh-paired-e34cbeb2516845b8','zenoh-paired-207d1144db924d42']
containers=set(subprocess.check_output(['docker','ps','-a','--format','{{.Names}}'],text=True).splitlines())
with (root/'jobs/candidate-campaigns-shared.control.lock').open('rb') as f:
 fcntl.flock(f,fcntl.LOCK_SH)
 raw=(root/'jobs/candidate-campaigns-shared.control.state.json').read_bytes(); state=json.loads(raw)
 fcntl.flock(f,fcntl.LOCK_UN)
claims={k:v['trials'] for k,v in state.get('participants',{}).items() if any(n in v.get('trials',{}) for n in names)}
processes={}
for pid,identity in [(56052,'6477434'),(56213,'6478346')]:
 try:
  stat=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split();present=stat[0]!='Z' and stat[19]==identity
 except OSError:present=False
 processes[str(pid)]={'expected_identity':identity,'same_live_identity_present':present}
participant=state.get('participants',{}).get('56213:6478346')
print(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'containers_remaining':sorted(containers.intersection(names)),'claims_remaining':claims,'owners':processes,'shared_state_sha256':hashlib.sha256(raw).hexdigest(),'participant_registration_present':participant is not None,'participant_trial_count':len(participant.get('trials',{})) if participant else 0}))
'''
p=subprocess.run(['docker','exec','-i','keen_black','/home/vscode/.local/share/uv/tools/harbor/bin/python','-B','-c',code],capture_output=True,text=True)
if p.returncode:
 print(p.stderr[-1500:]); raise SystemExit(p.returncode)
fresh=json.loads(p.stdout)
assert fresh['containers_remaining']==[] and fresh['claims_remaining']=={}
assert not any(p['same_live_identity_present'] for p in fresh['owners'].values())
assert fresh['participant_trial_count']==0
repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip())
prefix=Path.cwd().relative_to(repo).as_posix()
files=subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','--name-only','c1ae968','--',prefix],text=True).splitlines()
assert len(files)==36
assert all((repo/p).read_bytes()==subprocess.check_output(['git','-C',str(repo),'show','c1ae968:'+p]) for p in files)
receipt={'schema_version':1,'kind':'final_two_zenoh_root_review','at':datetime.now(timezone.utc).isoformat(),'passed':True,'cases':cases,'all_nine_summary':ref(summary_path),'all_nine_summary_artifact_files_verified':sum(len(x['artifact_file_sha256']) for x in summary['regrades']),'all_nine_rewards':[x['reward'] for x in summary['regrades']],'continuation':ref(cont_path),'fresh_terminal_absence':fresh,'task_files_verified':24,'original_materials':{'commit':'c1ae968','checked':36,'unchanged':True},'model_calls':0,'new_verifier_runs':0,'counted_sweep_trials':0,'limitations':['The ninth complete invocation failed build_a and did not execute the nine subsequent runtime groups.','An empty stale participant registration is not an active claim; no registry mutation was performed.','StorageOpt null does not establish an enforced Docker storage quota.'],'audit_source':ref('research/takehome-presentation-2026-09-14/packaging/check-final-zenoh-root.py')}
out=E/'zenoh-final-two-root-review-20260914T1338.json'; assert not out.exists();out.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({'path':str(out),'sha256':sha(out),'passed':True,'all_nine_rewards':receipt['all_nine_rewards'],'all_nine_artifacts_verified':receipt['all_nine_summary_artifact_files_verified'],'fresh':fresh}))

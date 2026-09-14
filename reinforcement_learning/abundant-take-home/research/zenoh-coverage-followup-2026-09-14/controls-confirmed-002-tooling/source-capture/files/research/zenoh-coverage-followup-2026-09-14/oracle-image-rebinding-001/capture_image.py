"""Capture and retain the actual confirmation image without repointing an older tag."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return json.loads(path.read_text())
def ref(path): return {'path': str(path.relative_to(ROOT)), 'sha256': sha(path)}
def dump(path, value):
    with path.open('x') as f: f.write(json.dumps(value, indent=2)+'\n')
def call(args): return subprocess.check_output(args, text=True, timeout=30)

def main():
    output = HERE / 'capture.json'
    assert not output.exists()
    manifest = read(BASE/'task-manifest.json')
    control = BASE/'harbor-oracle-confirmation-001'
    identity = read(control/'run-identity.json')
    assert identity['task_checksum'] == manifest['harbor_task_checksum']
    assert identity['inputs']['task_file_sha256'] == manifest['task_file_sha256']
    paths = list((control/'jobs').glob('*/*/config.json'))
    assert len(paths)==1
    trial = paths[0].parent
    assert read(paths[0])['agent']['name']=='oracle'
    project = trial.name.lower()+'__verifier__trial'
    ids=call(['docker','ps','-q','--filter','label=com.docker.compose.project='+project]).split()
    assert len(ids)==1
    raw=json.loads(call(['docker','inspect',ids[0]]))[0]
    assert raw['State']['Running'] and raw['Config']['Labels']['com.docker.compose.project']==project
    host={k:raw['HostConfig'].get(k) for k in ('NanoCpus','Memory','MemorySwap','NetworkMode','StorageOpt')}
    assert host=={'NanoCpus':4000000000,'Memory':8589934592,'MemorySwap':17179869184,'NetworkMode':'none','StorageOpt':None}
    filtered={'Id':raw['Id'],'Image':raw['Image'],'Name':raw['Name'],'Config.Labels':raw['Config']['Labels'],'HostConfig':host,'observed_at':datetime.now(timezone.utc).isoformat()}
    dump(HERE/'oracle-verifier-docker-inspection.json',filtered)
    code="from pathlib import Path; import hashlib,json; r=Path('/tests'); ps=sorted(r.rglob('*')); assert not any(p.is_symlink() for p in ps); print(json.dumps({'tests/'+str(p.relative_to(r)):hashlib.sha256(p.read_bytes()).hexdigest() for p in ps if p.is_file()},sort_keys=True))"
    files=json.loads(call(['docker','exec',ids[0],'python3','-B','-c',code]))
    expected={k:v for k,v in manifest['task_file_sha256'].items() if k.startswith('tests/')}
    assert files==expected, 'Actual normal verifier tests differ from final task'
    dump(HERE/'actual-test-file-sha256.json',files)
    old_tag='zenoh-coverage-followup-final-verifier:'+manifest['harbor_task_checksum'][:12]
    old_id=call(['docker','image','inspect','--format','{{.Id}}',old_tag]).strip()
    tag='zenoh-coverage-followup-confirmation-verifier:'+manifest['harbor_task_checksum'][:12]+'-'+trial.name.rsplit('__',1)[-1].lower()
    prior=subprocess.run(['docker','image','inspect','--format','{{.Id}}',tag],capture_output=True,text=True,timeout=30)
    if prior.returncode==0: assert prior.stdout.strip()==raw['Image']
    else: subprocess.run(['docker','image','tag',raw['Image'],tag],check=True,timeout=30)
    assert call(['docker','image','inspect','--format','{{.Id}}',old_tag]).strip()==old_id
    layers={name:json.loads(call(['docker','image','inspect','--format','{{json .RootFS.Layers}}',value])) for name,value in [('original',old_id),('confirmation',raw['Image'])]}
    dump(HERE/'image-layer-comparison.json',{'images':{'original':old_id,'confirmation':raw['Image']},'rootfs_layers':layers,'equal_rootfs_layers':layers['original']==layers['confirmation']})
    dump(output,{'kind':'actual_normal_oracle_confirmation_image_capture','passed':True,'model_calls':0,'new_control_runs':0,'captured_at':datetime.now(timezone.utc).isoformat(),
        'task_checksum':manifest['harbor_task_checksum'],'task_manifest':ref(BASE/'task-manifest.json'),'run_identity':ref(control/'run-identity.json'),
        'oracle_trial':str(trial.relative_to(ROOT)),'verifier_image_id':raw['Image'],'private_tag':tag,'prior_private_tag':old_tag,'prior_image_id':old_id,'prior_tag_unchanged':True,
        'filtered_inspection':ref(HERE/'oracle-verifier-docker-inspection.json'),'actual_test_files':ref(HERE/'actual-test-file-sha256.json'),
        'image_layers':ref(HERE/'image-layer-comparison.json'),'helper':ref(Path(__file__).resolve()),
        'meaning':'Image capture only; actual control reward is pending. The checksum-only historical private tag remains unchanged. This new tag identifies the actual normal confirmation verifier.'})
    print(json.dumps(ref(output)))

if __name__=='__main__':main()

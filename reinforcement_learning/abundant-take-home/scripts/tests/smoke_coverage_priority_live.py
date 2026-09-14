"""No-model live activation with an ongoing task, retries and a full slot cap."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))
from benchmark_interleaving import cell_key
from benchmark_shared_admission import process_identity

WORKER = r'''
import asyncio,json,os,sys,time
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,sys.argv[1]+'/scripts')
from harbor.job import Job
from benchmark_shared_admission import SharedAdmission
root=Path(sys.argv[2]); ticks=0; peak=0
class Admission:
    def __init__(self):
        self.shared=SharedAdmission(root/'shared.json',root/'local.json')
        self.active={'ongoing':time.time()}
        with self.shared.locked() as (_,s):
            s['participants'][self.shared.key]['trials']['ongoing']={'started_at':time.time(),'startup_reserve_mb':0}
    async def acquire(self,name,task_name=None):
        while self.shared.try_acquire(name,dict(total_mb=32768,available_mb=30000,memory_pressure_pct=0),{}) is not None:
            await asyncio.sleep(.02)
        self.active[name]=time.time()
admission=Admission()
job=Job.__new__(Job);job.config=NS(job_name='job')
job._remaining_trial_configs=[NS(trial_name=name,task=NS(path=Path(task)),agent=NS(model_name='m',kwargs={'reasoning_effort':'high'}))
    for name,task in [('done-next','done'),('retry-next','retry'),('retry-second','retry'),('fresh-next','fresh'),('ongoing','active')]]
job._trial_queue=NS(_semaphore=asyncio.Semaphore(1))
def event(value):
    with (root/'events.jsonl').open('a') as out:out.write(json.dumps(value)+'\n')
async def pending(c):
    async with job._trial_queue._semaphore:
        await admission.acquire(c.trial_name)
        event(dict(kind='started',name=c.trial_name,ticks=ticks,active=len(admission.active)))
        await asyncio.sleep(.15)
        admission.shared.release(c.trial_name);admission.active.pop(c.trial_name)
        # Emulate a delayed supervisor classification after the slot is released.
        await asyncio.sleep(.2)
        with admission.shared.locked() as (_,s):
            p=s['interleaving'];key=json.dumps([c.task.path.name,'m','high'],separators=(',',':'))
            p['first_sweep']['settled_receipts'][c.trial_name]={}
            p['first_sweep']['completed'][key]+=1
        event(dict(kind='classified',name=c.trial_name,ticks=ticks))
async def ongoing():
    global ticks,peak
    while True:
        ticks+=1;peak=max(peak,len(admission.active))
        p=root/'heartbeat.tmp';p.write_text(json.dumps(dict(pid=os.getpid(),ticks=ticks,peak=peak,active=sorted(admission.active))))
        p.replace(root/'heartbeat.json');await asyncio.sleep(.03)
async def main():
    await asyncio.gather(ongoing(),*(pending(c) for c in job._remaining_trial_configs if c.trial_name!='ongoing'))
asyncio.run(main())
'''


def main():
    output=Path(sys.argv[1]).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);control=dict(max_active=2,min_total_mb=0,reserve_mb=0,start_interval_sec=0,paused=True)
        (root/'shared.json').write_text(json.dumps(control))
        keys={t:cell_key(t,'m','high') for t in ['done','retry','fresh','active']}
        receipts={n:dict(cell=keys[t],job='job') for n,t in [('done-old','done'),('retry-old1','retry'),('retry-old2','retry'),('ongoing','active')]}
        for name in receipts:
            path=root/'job'/name/'config.json';path.parent.mkdir(parents=True);path.write_text('{}')
        policy=dict(version=1,campaigns=['job'],cells={k:dict(job='job',target=20) for k in keys.values()},
            counts=dict(zip(keys.values(),[1,2,0,1])),receipts=receipts)
        (root/'shared.state.json').write_text(json.dumps(dict(participants={},interleaving=policy)))
        (root/'worker.py').write_text(WORKER)
        with (output/'worker.log').open('w') as log:
            worker=subprocess.Popen([sys.executable,str(root/'worker.py'),str(ROOT),str(root)],stdout=log,stderr=log)
            try:
                deadline=time.monotonic()+20
                while not (root/'heartbeat.json').exists() and time.monotonic()<deadline:time.sleep(.05)
                before=json.loads((root/'heartbeat.json').read_text())
                command=[sys.executable,str(ROOT/'scripts/hotpatch-benchmark-interleaving.py'),
                    '--pid',str(worker.pid),'--identity',process_identity(worker.pid),'--job','job',
                    '--shared',str(root/'shared.json'),'--output',str(output/'activation'),'--apply']
                result=subprocess.run(command,capture_output=True,text=True,timeout=50)
                assert result.returncode==0,result.stdout+result.stderr
                first_activation=json.loads(result.stdout)
                command[command.index('--output')+1]=str(output/'priority-activation')
                result=subprocess.run([*command,'--priority-only'],capture_output=True,text=True,timeout=50)
                assert result.returncode==0,result.stdout+result.stderr
                with (root/'shared.lock').open('a') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX)
                    s=json.loads((root/'shared.state.json').read_text())
                    s['interleaving']['first_sweep']=dict(version=1,enabled=True,
                        scope={k:'job' for k in keys.values()},completed=dict(zip(keys.values(),[1,0,0,0])),
                        settled_receipts={n:{} for n in ['done-old','retry-old1','retry-old2']})
                    tmp=root/'state.tmp';tmp.write_text(json.dumps(s));tmp.replace(root/'shared.state.json')
                    control['paused']=False;(root/'shared.json').write_text(json.dumps(control))
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    path=root/'events.jsonl';events=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
                    if any(e['name']=='done-next' and e['kind']=='started' for e in events):break
                    time.sleep(.05)
                after=json.loads((root/'heartbeat.json').read_text())
                starts=[e['name'] for e in events if e['kind']=='started']
                checks=dict(same_worker_pid=before['pid']==after['pid'],
                    ongoing_task_continued=after['ticks']>before['ticks'] and 'ongoing' in after['active'],
                    retry_and_fresh_before_repeat=set(starts[:2])=={'retry-next','fresh-next'},
                    no_duplicate_pending_result='retry-second' not in starts[:2],
                    slot_cap_preserved=after['peak']<=2,
                    worker_alive=worker.poll() is None,
                    normal_repeats_resume='done-next' in starts)
                sources=['scripts/benchmark_coverage_priority.py','scripts/benchmark_interleaving.py',
                    'scripts/benchmark_shared_admission.py','scripts/hotpatch-benchmark-interleaving.py']
                proof=dict(passed=all(checks.values()),model_calls=0,assertions=checks,before=before,after=after,
                    events=events,activation=json.loads(result.stdout),initial_activation=first_activation,
                    source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sources})
                (output/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
                print(json.dumps(dict(passed=proof['passed'],assertions=checks,starts=starts)))
                assert proof['passed']
            finally:
                worker.terminate();worker.wait(timeout=10)


if __name__=='__main__':main()

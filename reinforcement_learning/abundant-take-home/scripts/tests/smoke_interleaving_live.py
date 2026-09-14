"""No-model proof: patch a live worker without cancelling its ongoing task."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_interleaving import cell_key
from benchmark_shared_admission import process_identity

WORKER = r'''
import asyncio, json, os, sys
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, sys.argv[1] + '/scripts')
from harbor.job import Job
from benchmark_shared_admission import SharedAdmission
root = Path(sys.argv[2])
class Admission:
    def __init__(self):
        self.shared = SharedAdmission(root/'shared.json', root/'local.json')
        self.active = {'ongoing-fake-agent': 0}
    async def acquire(self, name, task_name=None):
        while self.shared.try_acquire(name, dict(total_mb=32768,available_mb=30000,memory_pressure_pct=0), {}) is not None:
            await asyncio.sleep(.05)
        self.active[name] = 0
admission = Admission()
job = Job.__new__(Job)
job.config = NS(job_name='job')
job._remaining_trial_configs = [NS(trial_name=k+'-next', task=NS(path=Path(k)),
    agent=NS(model_name='m',kwargs={'reasoning_effort':'high'})) for k in ['ahead','behind','third']]
job._trial_queue = NS(_semaphore=asyncio.Semaphore(1))
async def pending(config):
    async with job._trial_queue._semaphore:
        await admission.acquire(config.trial_name)
async def ongoing():
    counter = 0
    while True:
        counter += 1
        p = root/'heartbeat.tmp'
        import benchmark_interleaving as rounds
        p.write_text(json.dumps(dict(pid=os.getpid(), ticks=counter,active=sorted(admission.active),
            registered=len(rounds.TRIALS), module_id=id(rounds),
            method=admission.shared.try_acquire.__code__.co_filename)))
        p.replace(root/'heartbeat.json')
        await asyncio.sleep(.05)
async def main():
    await asyncio.gather(ongoing(), *(pending(c) for c in job._remaining_trial_configs))
asyncio.run(main())
'''


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        control = dict(max_active=12, min_total_mb=0, reserve_mb=0, start_interval_sec=0, paused=True)
        (root/'shared.json').write_text(json.dumps(control))
        cells = {cell_key(k,'m','high'):dict(job='job',target=20) for k in ['ahead','behind','third']}
        policy = dict(campaigns=['job'], cells=cells, counts=dict(zip(cells,[2,0,0])), receipts={})
        (root/'shared.state.json').write_text(json.dumps(dict(participants={},interleaving=policy)))
        (root/'worker.py').write_text(WORKER)
        with (root/'worker.log').open('w') as log:
            process = subprocess.Popen([sys.executable,str(root/'worker.py'),str(ROOT),str(root)],stdout=log,stderr=log)
            try:
                deadline = time.monotonic()+20
                while not (root/'heartbeat.json').exists() and time.monotonic()<deadline:
                    time.sleep(.1)
                before = json.loads((root/'heartbeat.json').read_text())
                command = [sys.executable,str(ROOT/'scripts/hotpatch-benchmark-interleaving.py'),
                    '--pid',str(process.pid),'--identity',process_identity(process.pid),'--job','job',
                    '--shared',str(root/'shared.json'),'--output',str(output),'--apply']
                result = subprocess.run(command,capture_output=True,text=True,timeout=50)
                assert result.returncode == 0, result.stdout + result.stderr
                control['paused'] = False
                (root/'shared.json').write_text(json.dumps(control))
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    after=json.loads((root/'heartbeat.json').read_text())
                    if 'third-next' in after['active'] and 'behind-next' in after['active']:
                        break
                    time.sleep(.1)
                assertions = dict(same_pid=after['pid']==before['pid'],
                    ongoing_task_kept_running=after['ticks']>before['ticks'] and 'ongoing-fake-agent' in after['active'],
                    unseen_cells_admitted=all(k in after['active'] for k in ['behind-next','third-next']),
                    ahead_cell_waits='ahead-next' not in after['active'],
                    worker_alive=process.poll() is None)
                proof=dict(passed=all(assertions.values()), model_calls=0, assertions=assertions,
                    final_state=json.loads((root/'shared.state.json').read_text()),
                    before=before,after=after,activation=json.loads(result.stdout),
                    source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [ROOT/'scripts/benchmark_interleaving.py',ROOT/'scripts/benchmark_shared_admission.py',ROOT/'scripts/hotpatch-benchmark-interleaving.py']})
                (output/'proof.json').write_text(json.dumps(proof,indent=2)+'\n')
                print(json.dumps(dict(passed=proof['passed'],assertions=assertions)))
                assert proof['passed']
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()

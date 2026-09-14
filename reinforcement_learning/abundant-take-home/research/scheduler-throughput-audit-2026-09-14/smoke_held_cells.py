"""No-model live-update proof, using only disposable scheduler state/workers."""
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
from benchmark_shared_admission import SharedAdmission, process_identity

WORKER = r'''
import asyncio, importlib.util, json, os, sys
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, sys.argv[1] + '/scripts')
from harbor.job import Job
from benchmark_shared_admission import SharedAdmission
root = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('benchmark_interleaving', sys.argv[3])
rounds = importlib.util.module_from_spec(spec)
sys.modules['benchmark_interleaving'] = rounds
spec.loader.exec_module(rounds)
class Admission:
    def __init__(self):
        self.shared = SharedAdmission(root/'shared.json', root/'local.json')
        self.active = {'ongoing-fake-model-task': 0}
    async def acquire(self, name, task_name=None):
        while self.shared.try_acquire(name, dict(total_mb=32768,available_mb=30000,memory_pressure_pct=0), {}) is not None:
            await asyncio.sleep(.05)
        self.active[name] = 0
    async def release(self, name):
        self.shared.release(name)
        self.active.pop(name)
admission = Admission()
job = Job.__new__(Job)
job.config = NS(job_name='job')
job._remaining_trial_configs = [NS(trial_name=k+'-'+suffix, task=NS(path=Path(k)),
    agent=NS(model_name='m',kwargs={'reasoning_effort':'high'}))
    for k in ['held','ready','peer'] for suffix in ['first','second']]
job._trial_queue = NS(_semaphore=asyncio.Semaphore(1))
rounds.install(Admission, admission.shared)
rounds.register(job, admission.shared)
async def pending(config):
    async with job._trial_queue._semaphore:
        await admission.acquire(config.trial_name)
async def ongoing_model_task():
    counter, applied = 0, None
    while True:
        counter += 1
        command_path = root/'command.json'
        if command_path.exists():
            command = json.loads(command_path.read_text())
            if command['id'] != applied:
                for name in command['release']:
                    await admission.release(name)
                applied = command['id']
        p = root/'heartbeat.tmp'
        p.write_text(json.dumps(dict(pid=os.getpid(), ticks=counter, active=sorted(admission.active),
            registered=len(rounds.TRIALS), module_id=id(rounds), command_applied=applied)))
        p.replace(root/'heartbeat.json')
        await asyncio.sleep(.05)
async def main():
    await asyncio.gather(ongoing_model_task(), *(pending(c) for c in job._remaining_trial_configs))
asyncio.run(main())
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample(root, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads((root / 'heartbeat.json').read_text())
            if predicate(value):
                return value
        except FileNotFoundError:
            pass
        time.sleep(.05)
    raise AssertionError('Disposable worker did not reach the expected state')


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'proof.json').exists(), 'Use a new proof directory'
    # Bind the old code used for the live migration to this change's before hash.
    baseline = output / 'baseline-interleaving.py'
    baseline.write_bytes(subprocess.check_output([
        'git', 'show', 'HEAD:reinforcement_learning/abundant-take-home/scripts/benchmark_interleaving.py'], cwd=ROOT))
    before_source = json.loads((Path(__file__).parent / 'held-cells/source-before.json').read_text())
    assert digest(baseline) == before_source['scripts/benchmark_interleaving.py']
    source_paths = ['scripts/benchmark_interleaving.py', 'scripts/benchmark_shared_admission.py',
                    'scripts/hotpatch-benchmark-interleaving.py', 'scripts/harbor-resource-runner.py',
                    'scripts/watch-candidate-campaign.py', str(Path(__file__).relative_to(ROOT))]
    source_before = {p: digest(ROOT / p) for p in source_paths}
    keys = {name: cell_key(name, 'm', 'high') for name in ['held', 'ready', 'peer']}
    with tempfile.TemporaryDirectory(prefix='held-cell-smoke-') as temp:
        root = Path(temp)
        control = dict(max_active=2, min_total_mb=0, reserve_mb=0, start_interval_sec=0, paused=True)
        (root / 'shared.json').write_text(json.dumps(control))
        cells = {key: dict(task=name, model='m', effort='high', job='job', target=20)
                 for name, key in keys.items()}
        receipts = {name + '-historical': dict(cell=keys[name], job='job', iteration=1,
                    started_at=1, source='existing_start') for name in ['ready', 'peer']}
        policy = dict(version=1, campaigns=['job'], cells=cells,
                      counts=dict(zip(keys.values(), [0, 1, 1])), receipts=receipts,
                      held_cells={keys['held']: dict(job='job', reason='fixture verifier hold')},
                      custom_history={'retain': ['exact', 117, 2340]})
        (root / 'shared.state.json').write_text(json.dumps(dict(participants={}, interleaving=policy)))
        for name in receipts:
            directory = root / 'job' / name
            directory.mkdir(parents=True)
            (directory / 'config.json').write_text('{}')
        (root / 'worker.py').write_text(WORKER)
        observer = SharedAdmission(root / 'shared.json', root / 'observer.json')
        with (output / 'worker.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-B', str(root / 'worker.py'),
                str(ROOT), str(root), str(baseline)], stdout=log, stderr=log)
            try:
                before = sample(root, lambda value: value['ticks'] >= 3)
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/hotpatch-benchmark-interleaving.py'),
                    '--pid', str(process.pid), '--identity', process_identity(process.pid), '--job', 'job',
                    '--shared', str(root / 'shared.json'), '--output', str(output / 'activation'), '--apply'],
                    capture_output=True, text=True, timeout=50)
                (output / 'activation-output.txt').write_text(result.stdout + result.stderr)
                assert result.returncode == 0, 'Disposable activation failed; see activation-output.txt'
                activation = json.loads(result.stdout)
                with observer.locked() as (_, state):
                    pre_admission = json.loads(json.dumps(state['interleaving']))
                    control['paused'] = False
                    (root / 'shared.json').write_text(json.dumps(control))
                advanced = sample(root, lambda value: all(name in value['active'] for name in ['ready-first', 'peer-first']))
                with observer.locked() as (_, state):
                    ready_policy = json.loads(json.dumps(state['interleaving']))
                    state['interleaving']['held_cells'] = {key: dict(job='job', reason='all-held fixture') for key in cells}
                command_temp = root / 'command.tmp'
                command_temp.write_text(json.dumps(dict(id='release-ready', release=['ready-first', 'peer-first'])))
                command_temp.replace(root / 'command.json')
                all_held = sample(root, lambda value: value['command_applied'] == 'release-ready')
                # Wait for several priority and resource polls, proving no new dispatch.
                later_held = sample(root, lambda value: value['ticks'] >= all_held['ticks'] + 25)
                with observer.locked() as (_, state):
                    held_policy = json.loads(json.dumps(state['interleaving']))
                    state['interleaving']['held_cells'] = {}
                caught_up = sample(root, lambda value: all(name in value['active'] for name in ['held-first', 'held-second']))
                final_state = json.loads((root / 'shared.state.json').read_text())
                final_policy = final_state['interleaving']
                assertions = dict(
                    old_worker_loaded=before['registered'] == 6,
                    same_pid=before['pid'] == caught_up['pid'] == process.pid,
                    same_module_namespace=before['module_id'] == caught_up['module_id'],
                    ongoing_model_task_kept_running=caught_up['ticks'] > before['ticks'] and
                        all('ongoing-fake-model-task' in s['active'] for s in [before, advanced, later_held, caught_up]),
                    activation_preserves_active_set=activation['active_before'] == activation['active_after'],
                    activation_preserves_policy=pre_admission == policy,
                    ready_cells_advance_past_held_zero=ready_policy['counts'] == {keys['held']: 0, keys['ready']: 2, keys['peer']: 2},
                    held_cell_never_admitted='held-first' not in advanced['active'] and 'held-second' not in advanced['active'],
                    all_held_stays_idle=later_held['active'] == ['ongoing-fake-model-task'] and
                        held_policy['counts'] == ready_policy['counts'] and held_policy['receipts'] == ready_policy['receipts'],
                    unheld_cell_catches_up=final_policy['counts'] == dict.fromkeys(cells, 2) and
                        'ready-second' not in caught_up['active'] and 'peer-second' not in caught_up['active'],
                    cells_and_targets_preserved=all(p['cells'] == cells for p in [pre_admission, ready_policy, held_policy, final_policy]),
                    historical_receipts_preserved=all(p['receipts'].get(k) == v for p in
                        [pre_admission, ready_policy, held_policy, final_policy] for k, v in receipts.items()),
                    unrelated_history_preserved=final_policy['custom_history'] == policy['custom_history'],
                    exact_admission_receipts=set(final_policy['receipts']) == set(receipts) |
                        {'ready-first', 'peer-first', 'held-first', 'held-second'},
                    worker_alive=process.poll() is None,
                    sources_unchanged=source_before == {p: digest(ROOT / p) for p in source_paths})
                proof = dict(passed=all(assertions.values()), model_calls=0, assertions=assertions,
                             source_sha256=source_before, baseline_sha256=digest(baseline), activation=activation,
                             before=before, advanced=advanced, all_held=later_held, caught_up=caught_up,
                             policy_before=policy, ready_policy=ready_policy, held_policy=held_policy,
                             final_state=final_state)
                (output / 'proof.json').write_text(json.dumps(proof, indent=2) + '\n')
                print(json.dumps(dict(passed=proof['passed'], model_calls=0, assertions=assertions)))
                assert proof['passed']
            finally:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()

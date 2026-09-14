"""Temporarily reserve one admission slot for the queued Burn controls.

Existing trials continue. The shared cap and task budgets never change.
Original local controls are restored when nop is admitted, the controller
finishes/exits, or the reservation expires. Restoration never overwrites a
concurrent control edit. Run inside the Harbor devcontainer with Python -B.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
JOBS = ROOT / 'jobs'
SHARED = JOBS / 'candidate-campaigns-shared.control.json'
JOURNAL = BASE / 'validation-priority'
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_shared_admission import process_identity


def read(path):
    return json.loads(path.read_text())


def sha(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, data):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def dump(path, value):
    atomic(path, (json.dumps(value, indent=2) + '\n').encode())


@contextmanager
def locked():
    with SHARED.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def plan():
    user = read(JOBS / 'benchmark-user-plan.json')
    main = user['active_full_campaign']
    others = [name for name in user['active_revision_campaigns'] if 'burn-reader-v3' not in name]
    assert len(others) == 2
    latest = read(BASE / 'latest-controls.json')
    output = ROOT / latest['output']
    assert output.name == 'harbor-controls-immutable-001'
    identity = process_identity(latest['pid'])
    assert identity == '3038984' and latest['pid'] == 24716
    assert not read(output / 'summary.json').get('finished_at')
    assert read(output / 'control.json')['max_active'] == 1
    assert read(SHARED)['max_active'] == 12
    shared = read(SHARED.with_suffix('.state.json'))
    claims = {}
    for participant in shared['participants'].values():
        key = str(Path(participant['control']).resolve())
        claims[key] = claims.get(key, 0) + len(participant['trials'])
    assert claims.get(str(output / 'control.json'), 0) == 0
    main_claims = claims.get(str(JOBS / (main + '.control.json')), 0)
    assert sum(claims.values()) == 12 and main_claims >= 2
    assert sum(claims.get(str(JOBS / (name + '.control.json')), 0)
               for name in [main, *others]) == 12
    changes = []
    for name in [main, *others]:
        path = JOBS / (name + '.control.json')
        original = path.read_bytes()
        value = json.loads(original)
        assert value['paused'] is False and value['max_active'] == 12
        assert Path(value['shared_pool']).resolve() == SHARED
        if name == main:
            value['max_active'] = main_claims - 1
        else:
            value.update(paused=True, pause_reason='Reserve next available slot for Burn v4 oracle/nop validation')
        applied = (json.dumps(value, indent=2) + '\n').encode()
        changes.append({'path': str(path.relative_to(ROOT)),
                        'original': original.decode(), 'original_sha256': sha(original),
                        'applied': applied.decode(), 'applied_sha256': sha(applied)})
    return {'at': datetime.now(timezone.utc).isoformat(),
            'expires_at_epoch': time.time() + 5400,
            'controller': {'pid': latest['pid'], 'identity': identity},
            'validation_output': str(output.relative_to(ROOT)),
            'validation_control_sha256': sha((output / 'control.json').read_bytes()),
            'shared_control_sha256': sha(SHARED.read_bytes()),
            'script_sha256': sha(Path(__file__).read_bytes()),
            'changes': changes, 'initial_claims': claims,
            'shared_cap': 12, 'task_budget_changes': 0,
            'trial_signals': 0, 'model_calls': 0}


def restore(intent, reason):
    rows = []
    with locked():
        for change in intent['changes']:
            path = ROOT / change['path']
            current = sha(path.read_bytes())
            if current == change['applied_sha256']:
                atomic(path, change['original'].encode())
                status = 'restored'
            elif current == change['original_sha256']:
                status = 'already_original'
            else:
                status = 'concurrent_edit_preserved'
            rows.append({'path': change['path'], 'status': status,
                         'current_sha256': sha(path.read_bytes())})
    result = {'at': datetime.now(timezone.utc).isoformat(), 'reason': reason,
              'passed': all(r['status'] != 'concurrent_edit_preserved' for r in rows),
              'controls': rows, 'trial_signals': 0}
    dump(JOURNAL / 'restoration.json', result)
    print(json.dumps(result), flush=True)
    return result['passed']


def watch(intent):
    output = ROOT / intent['validation_output']
    dump(JOURNAL / 'watcher.json', {'pid': os.getpid(), 'identity': process_identity(os.getpid())})
    while True:
        reason = None
        if time.time() >= intent['expires_at_epoch']:
            reason = 'reservation_expired'
        elif process_identity(intent['controller']['pid']) != intent['controller']['identity']:
            reason = 'controller_exited'
        try:
            summary = read(output / 'summary.json')
            if summary.get('finished_at'):
                reason = 'controls_finished'
            state = read(SHARED.with_suffix('.state.json'))
            for participant in state['participants'].values():
                if (Path(participant['control']).resolve() != output / 'control.json'
                        or not participant['trials']
                        or process_identity(participant['pid']) != participant['identity']):
                    continue
                args = (Path('/proc') / str(participant['pid']) / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
                if '--job-name' in args:
                    name = args[args.index('--job-name') + 1]
                    if name.startswith('rs-burn-store-pytorch-reader-v4-nop-'):
                        reason = 'nop_admitted'
            if sha((output / 'control.json').read_bytes()) != intent['validation_control_sha256']:
                reason = 'validation_control_changed'
            if sha(SHARED.read_bytes()) != intent['shared_control_sha256']:
                reason = 'shared_control_changed'
        except (OSError, ValueError, KeyError, IndexError):
            # A transient observation failure cannot justify restarting work.
            # Controller identity and the absolute expiry remain independent.
            pass
        if reason:
            return restore(intent, reason)
        time.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'apply', 'restore'])
    args = parser.parse_args()
    if args.action == 'restore':
        raise SystemExit(0 if restore(read(JOURNAL / 'intent.json'), 'explicit_recovery') else 1)
    if args.action == 'check':
        with locked():
            intent = plan()
        print(json.dumps({k: v for k, v in intent.items() if k != 'changes'}, indent=2))
        raise SystemExit(0)
    assert not JOURNAL.exists(), 'Inspect an existing reservation rather than applying twice'
    with locked():
        intent = plan()
        JOURNAL.mkdir()
        dump(JOURNAL / 'intent.json', intent)
        try:
            for change in intent['changes']:
                path = ROOT / change['path']
                assert sha(path.read_bytes()) == change['original_sha256']
                atomic(path, change['applied'].encode())
        except BaseException:
            for change in intent['changes']:
                path = ROOT / change['path']
                if sha(path.read_bytes()) == change['applied_sha256']:
                    atomic(path, change['original'].encode())
            raise
        dump(JOURNAL / 'applied.json', {'at': datetime.now(timezone.utc).isoformat(),
              'changes': [{k: row[k] for k in ('path', 'original_sha256', 'applied_sha256')}
                          for row in intent['changes']], 'shared_cap': 12, 'trial_signals': 0})
    print('Reserved the next free slot; current trials continue; automatic restoration active.', flush=True)
    raise SystemExit(0 if watch(intent) else 1)

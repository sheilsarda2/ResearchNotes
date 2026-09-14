"""One bounded local-admission pause for the already-running final reviewer.

Check-only is the default. No shared state, limits, plans, tasks or trial
processes are modified. Run inside the Harbor host with Python -B.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = HERE.parent
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
REVIEW = BASE / 'reviews-final-002'
EXECUTION = HERE / 'execution-001'
REVIEWER = {'pid': 83205, 'identity': '5750249'}
JOB = 'rs-zenoh-timestamp-instrumentation-v4-validation-quality-20260914T080340Z'
CAMPAIGNS = (
    'candidates-all14-efforts-20-20260913T183301Z',
    'candidates-zenoh-v3-efforts-20-20260913T221317Z',
    'candidates-diskcache-v2-efforts-20-20260913T234023Z',
    'candidates-burn-reader-v4-efforts-20-20260914T011100Z',
)
HOLD_SECONDS = 110  # Leave ten seconds for restoration before the 120s bound.
OWNER = 'One-use reviewer admission reservation001 for PID83205/start5750249'
SOURCES = ('scripts/harbor-resource-runner.py', 'scripts/benchmark_shared_admission.py',
           'scripts/watch-candidate-campaign.py', 'scripts/run-candidate-screen.py',
           'scripts/run-sonnet-confirmation.py')


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    if path.is_symlink():
        raise ValueError('Symlink refused: ' + str(path))
    return json.loads(path.read_text())


def identity(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def atomic(path, value, expected=None):
    """Merge callers provide a fresh document; reject a detected concurrent write.

    Existing campaign writers do not all share a control-file lock. The final
    digest comparison minimizes, but cannot eliminate, their replace race.
    """
    data = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.reservation.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if expected is not None and sha(path.read_bytes()) != expected:
            raise RuntimeError('Concurrent control write detected')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def shared_lock(exclusive=False, timeout=1.0):
    # Read the existing lock; never instantiate SharedAdmission or rewrite state.
    with SHARED.with_suffix('.lock').open('r') as stream:
        until = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise TimeoutError('Shared observation lock unavailable')
                time.sleep(.02)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def health_reason(summary, image=None):
    health = summary.get('health', {})
    if summary.get('status') == 'needs_review' or health.get('unclassified_results', 0):
        return 'Unclassified result or evidence mismatch requires review'
    total = health.get('recent_results', 0)
    infra = health.get('recent_infrastructure', 0)
    if infra >= 5 and infra >= total / 2:
        return 'Sustained infrastructure failures require review'
    if image and image.get('failures'):
        return 'Task image build failed; review prewarm logs'
    return None


def campaign_health(control_path, control):
    summary_path = control_path.with_name(control_path.name.removesuffix('.control.json') + '.summary.json')
    summary = read(summary_path)
    stamp = datetime.fromisoformat(summary['updated_at'].replace('Z', '+00:00')).timestamp()
    if time.time() - stamp > 90:
        return 'Reservation ended; campaign heartbeat needs review'
    image = read(Path(control['image_readiness'])) if control.get('image_readiness') else None
    return health_reason(summary, image)


def merged_restore(current, original, safety_reason=None):
    """Only undo our two fields; preserve unrelated values and foreign holds."""
    if current.get('pause_reason') != OWNER:
        return dict(current), 'foreign_fields_preserved'
    result = dict(current)
    if current.get('paused') is not True:
        if 'pause_reason' in original:
            result['pause_reason'] = original['pause_reason']
        else:
            result.pop('pause_reason', None)
        return result, 'owner_reason_removed_external_pause_value_preserved'
    if safety_reason:
        result.update(paused=True, pause_reason=safety_reason)
        return result, 'reservation_removed_health_pause_preserved'
    for key in ('paused', 'pause_reason'):
        if key in original:
            result[key] = original[key]
        else:
            result.pop(key, None)
    return result, 'owned_fields_restored'


def snapshot():
    with shared_lock():
        shared = read(SHARED)
        state = read(SHARED.with_suffix('.state.json'))
        participants = []
        for key, row in state['participants'].items():
            participants.append({'key': key, 'pid': row['pid'], 'identity': row['identity'],
                                 'live_identity': identity(row['pid']), 'control': row['control'],
                                 'trials': sorted(row['trials'])})
        return {'at': now(), 'shared_control_sha256': sha(SHARED.read_bytes()),
                'shared_max_active': shared['max_active'], 'shared_paused': shared.get('paused', False),
                'participants': participants, 'claims': sum(len(x['trials']) for x in participants)}


def target_row(observation):
    rows = [p for p in observation['participants'] if p['key'] == '83205:5750249']
    if len(rows) != 1:
        return None
    row = rows[0]
    if (row['pid'], row['identity'], row['live_identity'], row['control']) != (
            83205, '5750249', '5750249', str(REVIEW / 'control.json')):
        return None
    return row


def prepare():
    if EXECUTION.exists():
        raise ValueError('Single-use execution journal already exists')
    observation = snapshot()
    target = target_row(observation)
    assert target and not target['trials'], 'Exact reviewer is not alive and waiting'
    assert observation['shared_max_active'] == 14 and not observation['shared_paused']
    command = Path('/proc/83205/cmdline').read_bytes().split(b'\0')
    idx = command.index(b'--job-name')
    assert command[idx + 1].decode() == JOB
    summary = read(REVIEW / 'summary.json')
    assert not summary.get('finished_at') and len(summary['reviews']) == 1
    assert next(iter(summary['reviews'].values()))['pid'] == REVIEWER['pid']
    review_control = read(REVIEW / 'control.json')
    assert review_control['max_active'] == 1 and not review_control.get('paused')
    assert Path(review_control['shared_pool']) == SHARED
    changes = []
    allowed = {str(REVIEW / 'control.json')}
    for name in CAMPAIGNS:
        path = ROOT / 'jobs' / (name + '.control.json')
        raw = path.read_bytes()
        control = json.loads(raw)
        assert control.get('paused') is False and control['max_active'] == 12
        assert 'pause_reason' not in control, 'Pre-existing pause reason requires separate review'
        assert Path(control['shared_pool']) == SHARED
        assert not campaign_health(path, control), 'Campaign has a separate safety hold'
        own = [p for p in observation['participants'] if p['control'] == str(path)]
        assert len(own) == 1 and own[0]['identity'] == own[0]['live_identity']
        watchdog = read(path.with_name(name + '.watchdog.json'))
        assert not watchdog['alerts'] and not watchdog['actions']
        watchdog_at = datetime.fromisoformat(watchdog['updated_at'].replace('Z', '+00:00')).timestamp()
        assert 0 <= time.time() - watchdog_at < 90, 'Stale watchdog observation'
        assert identity(watchdog['watchdog_pid']) is not None
        assert all(identity(x['pid']) == x['identity'] for x in watchdog['supervisors'])
        changes.append({'path': str(path.relative_to(ROOT)), 'original': control,
                        'original_sha256': sha(raw), 'runner': own[0],
                        'watchdog_pid': watchdog['watchdog_pid'],
                        'watchdog_identity': identity(watchdog['watchdog_pid'])})
        allowed.add(str(path))
    assert {p['control'] for p in observation['participants']} == allowed, 'Unexpected competing participant'
    return {'schema_version': 1, 'kind': 'single_use_reviewer_admission_plan', 'prepared_at': now(),
            'reviewer': REVIEWER, 'job': JOB, 'hold_seconds': HOLD_SECONDS, 'maximum_hold_seconds': 120,
            'review_control_sha256': sha((REVIEW / 'control.json').read_bytes()),
            'shared_control_sha256': observation['shared_control_sha256'], 'initial_observation': observation,
            'changes': changes, 'source_sha256': {p: sha((ROOT / p).read_bytes()) for p in SOURCES},
            'script_sha256': sha(Path(__file__).read_bytes()), 'trial_signals': 0,
            'model_calls': 0, 'limits_changes': 0, 'shared_state_changes': 0}


def stop_reason(intent, observation, clock=None):
    if (time.monotonic() if clock is None else clock) >= intent['deadline_monotonic']:
        return 'fixed_deadline'
    target = target_row(observation)
    if target is None:
        return 'reviewer_exited_or_identity_changed'
    if target['trials']:
        return 'exact_reviewer_admitted' if len(target['trials']) == 1 else 'unexpected_reviewer_claim_count'
    if observation['shared_control_sha256'] != intent['shared_control_sha256']:
        return 'shared_control_changed'
    expected = {p['key'] for p in intent['initial_observation']['participants']}
    if {p['key'] for p in observation['participants']} != expected:
        return 'participant_inventory_changed'
    return None


@contextmanager
def reservation_lock():
    with (EXECUTION / 'restore.lock').open('a') as lock:
        until = time.monotonic() + .5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise TimeoutError('Reservation lock unavailable')
                time.sleep(.02)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def restore_attempt(intent, reason):
    # Serialize the complete apply/restore operations, in restore→shared order.
    with reservation_lock():
        receipt = EXECUTION / 'restoration.json'
        if receipt.exists():
            return read(receipt)
        rows = []
        with shared_lock(exclusive=True):
            for item in intent['changes']:
                path = ROOT / item['path']
                try:
                    before = path.read_bytes()
                    current = json.loads(before)
                    try:
                        safety = campaign_health(path, current)
                    except (OSError, ValueError, KeyError):
                        safety = 'Reservation ended; campaign health observation requires review'
                    merged, status = merged_restore(current, item['original'], safety)
                    if merged != current:
                        atomic(path, merged, expected=sha(before))
                    after = path.read_bytes()
                    rows.append({'path': item['path'], 'status': status, 'before_sha256': sha(before),
                                 'after_sha256': sha(after), 'before': current, 'after': json.loads(after)})
                except BaseException as exc:
                    rows.append({'path': item['path'], 'status': 'restoration_error',
                                 'error_type': type(exc).__name__, 'error': str(exc)})
            absence = []
            for item in intent['changes']:
                try:
                    absence.append(read(ROOT / item['path']).get('pause_reason') != OWNER)
                except (OSError, ValueError):
                    absence.append(False)
        result = {'at': now(), 'reason': reason, 'rows': rows,
                  'passed': all(absence) and all(x['status'] != 'restoration_error' for x in rows),
                  'reservation_owner_marker_absent': all(absence),
                  'trial_signals': 0, 'shared_state_changes': 0, 'limits_changes': 0}
        atomic(EXECUTION / ('restoration-attempt-' + str(os.getpid()) + '-' + str(time.monotonic_ns()) + '.json'), result)
        if result['passed']:
            atomic(receipt, result)
        return result


def restore(intent, reason):
    until = intent['deadline_monotonic'] + 8
    while True:
        try:
            result = restore_attempt(intent, reason)
        except BaseException as exc:
            result = {'at': now(), 'reason': reason, 'passed': False, 'error_type': type(exc).__name__,
                      'error': str(exc), 'reservation_owner_marker_absent': False}
            atomic(EXECUTION / ('restoration-attempt-' + str(os.getpid()) + '-' + str(time.monotonic_ns()) + '.json'), result)
        if result['passed']:
            return result
        if time.monotonic() >= until:
            # This is not a successful/terminal restoration receipt. A later
            # explicit --restore remains possible; never hide remaining holds.
            atomic(EXECUTION / ('cleanup-incomplete-' + str(os.getpid()) + '.json'), result)
            return result
        time.sleep(.1)


def watch(intent, guardian=False):
    if guardian:
        atomic(EXECUTION / 'guardian-ready.json', {'pid': os.getpid(), 'identity': identity(os.getpid()), 'at': now()})
    while not (EXECUTION / 'restoration.json').exists():
        observation = None
        if identity(intent['owner']['pid']) != intent['owner']['identity']:
            reason = 'reservation_owner_exited'
        elif time.monotonic() >= intent['deadline_monotonic']:
            reason = 'fixed_deadline'
        else:
            try:
                observation = snapshot()
                reason = stop_reason(intent, observation)
                if sha((REVIEW / 'control.json').read_bytes()) != intent['review_control_sha256']:
                    reason = 'review_control_changed'
                if read(REVIEW / 'summary.json').get('finished_at'):
                    reason = 'review_finished'
                for item in intent['changes']:
                    current = read(ROOT / item['path'])
                    if (EXECUTION / 'applied.json').exists() and (
                            current.get('paused') is not True or current.get('pause_reason') != OWNER):
                        reason = 'reservation_fields_changed'
            except BaseException as exc:
                reason = 'observation_error_' + type(exc).__name__
        if reason:
            atomic(EXECUTION / ('trigger-' + str(os.getpid()) + '.json'),
                   {'at': now(), 'reason': reason, 'observation': observation,
                    'reviewer_identity': identity(REVIEWER['pid'])})
            restore(intent, reason)
            return
        time.sleep(.25)


def apply():
    intent = prepare()
    EXECUTION.mkdir()  # Single-use, including aborted applications.
    intent.update(owner={'pid': os.getpid(), 'identity': identity(os.getpid())},
                  deadline_monotonic=time.monotonic() + HOLD_SECONDS, started_at=now())
    atomic(EXECUTION / 'intent.json', intent)
    guardian = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--guardian'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Reservation owner interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    reason = 'application_or_owner_error'
    try:
        until = time.monotonic() + 3
        while not (EXECUTION / 'guardian-ready.json').exists():
            assert guardian.poll() is None and time.monotonic() < until, 'Fallback not armed'
            time.sleep(.02)
        ready = read(EXECUTION / 'guardian-ready.json')
        assert ready['pid'] == guardian.pid and identity(guardian.pid) == ready['identity']
        with reservation_lock(), shared_lock(exclusive=True):
            assert not (EXECUTION / 'restoration.json').exists()
            assert time.monotonic() < intent['deadline_monotonic'] - 5
            assert identity(REVIEWER['pid']) == REVIEWER['identity']
            for item in intent['changes']:
                path = ROOT / item['path']
                assert sha(path.read_bytes()) == item['original_sha256']
            for item in intent['changes']:
                value = dict(item['original'], paused=True, pause_reason=OWNER)
                atomic(ROOT / item['path'], value, expected=item['original_sha256'])
            atomic(EXECUTION / 'applied.json', {'at': now(), 'guardian': ready,
                                              'controls': [x['path'] for x in intent['changes']]})
        watch(intent)
        reason = 'owner_watch_returned'
    finally:
        result = restore(intent, reason)
        try:
            guardian.wait(timeout=2)
        except subprocess.TimeoutExpired:
            # Leave the independently bounded fallback alive; never signal it
            # while it could be restoring controls.
            pass
        print(json.dumps(result), flush=True)
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--check-only', action='store_true')
    choice.add_argument('--apply', action='store_true')
    choice.add_argument('--guardian', action='store_true', help=argparse.SUPPRESS)
    choice.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.guardian:
        watch(read(EXECUTION / 'intent.json'), guardian=True)
    elif args.restore:
        print(json.dumps(restore(read(EXECUTION / 'intent.json'), 'explicit_recovery')))
    elif args.apply:
        raise SystemExit(apply())
    else:
        print(json.dumps(prepare(), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()

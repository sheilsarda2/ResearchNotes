"""One bounded local-admission pause for the already-running fifth diagnostic.

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
CASE = BASE / 'harness/diagnostics-final-002/retain-undecodable-uhlc-as-custom'
EXECUTION = HERE / 'execution-001'
TARGET = {'pid': 17396, 'identity': '6095623'}
TARGET_KEY = '17396:6095623'
TRIAL = 'zenoh-focused-e38f0163d3404946'
INPUT_SHA = '574e156e761d89e50156ef3b2eaa9f1a8fce067aa3a39f28dace1ec22131e2aa'
CONTROL_SHA = 'bf9336ba16808c0097f9f6022bf9e4a7b196e915f089ed2eb9cbc5750d00d92e'
PARENTS = ((61545, '6030050'), (60710, '6029740'))
PASSIVE = {'key': '76965:5940603', 'pid': 76965, 'identity': '5940603',
           'control': str(ROOT / 'research/provider-streaming-validation-2026-09-14/luigi-canary-preparation-001/control.json'),
           'trials': ['luigi-generation-target__RigDBsV']}
CAMPAIGNS = (
    'candidates-all14-efforts-20-20260913T183301Z',
    'candidates-zenoh-v3-efforts-20-20260913T221317Z',
    'candidates-diskcache-v2-efforts-20-20260913T234023Z',
    'candidates-burn-reader-v4-efforts-20-20260914T011100Z',
)
HOLD_SECONDS = 110  # Leave ten seconds for restoration before the 120s bound.
OWNER = 'One-use diagnostic admission reservation002 for PID17396/start6095623'
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


def snapshot_unlocked():
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


def snapshot():
    with shared_lock():
        return snapshot_unlocked()


def target_row(observation):
    rows = [p for p in observation['participants'] if p['key'] == TARGET_KEY]
    if len(rows) != 1:
        return None
    row = rows[0]
    if (row['pid'], row['identity'], row['live_identity'], row['control']) != (
            TARGET['pid'], TARGET['identity'], TARGET['identity'], str(CASE / 'control.json')):
        return None
    return row


def target_state(observation):
    record = read(CASE / 'result.json')
    assert record['name'] == TRIAL and record['admission_key'] == TARGET_KEY, 'Wrong diagnostic reservation'
    assert record['kind'] == 'focused_no_model_diagnostic' and record['model_calls'] == 0
    assert record['inputs_sha256'] == INPUT_SHA == sha((CASE / 'inputs.json').read_bytes())
    if record.get('finished_at'):
        return 'terminal'
    target = target_row(observation)
    if target is None:
        return 'exited_or_identity_changed'
    if target['trials']:
        assert target['trials'] == [TRIAL], 'Unexpected diagnostic reservation claim'
        return 'already_admitted'
    if any(x['event'] == 'claimed' for x in record['lifecycle']):
        return 'already_admitted'
    assert record['status'] == 'waiting', 'Diagnostic is not waiting'
    return 'waiting'


def validate_process():
    assert identity(TARGET['pid']) == TARGET['identity'], 'Target process identity changed'
    args = Path('/proc', str(TARGET['pid']), 'cmdline').read_bytes().split(b'\0')
    assert str(BASE / 'harness/focused_validation_v3.py').encode() in args
    index = args.index(b'--output')
    assert args[index + 1].decode() == str(CASE), 'Wrong diagnostic output'
    pid = TARGET['pid']
    for expected_pid, expected_identity in PARENTS:
        stat = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        assert int(stat[1]) == expected_pid and identity(expected_pid) == expected_identity
        pid = expected_pid


def validate_passive(observation):
    rows = [p for p in observation['participants'] if p['control'] == PASSIVE['control']]
    # A terminal canary may already have unregistered; never reserve for it.
    if not rows:
        return []
    assert len(rows) == 1
    row = rows[0]
    assert all(row[k] == v for k, v in PASSIVE.items()), 'Unexpected passive canary participant'
    assert row['live_identity'] == PASSIVE['identity']
    return rows


def prepare():
    if EXECUTION.exists():
        raise ValueError('Single-use execution journal already exists')
    observation = snapshot()
    state = target_state(observation)
    if state != 'waiting':
        return {'schema_version': 1, 'kind': 'single_use_diagnostic_admission_noop',
                'prepared_at': now(), 'action': 'noop', 'reason': state,
                'target': TARGET, 'trial': TRIAL, 'initial_observation': observation,
                'changes': [], 'model_calls': 0, 'trial_signals': 0}
    assert observation['shared_max_active'] == 14 and not observation['shared_paused']
    validate_process()
    target_control = read(CASE / 'control.json')
    assert sha((CASE / 'control.json').read_bytes()) == CONTROL_SHA
    assert target_control['max_active'] == 1 and not target_control.get('paused')
    assert Path(target_control['shared_pool']) == SHARED
    passive = validate_passive(observation)
    changes = []
    allowed = {str(CASE / 'control.json')} | {p['control'] for p in passive}
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
    return {'schema_version': 1, 'kind': 'single_use_diagnostic_admission_plan', 'action': 'reserve', 'prepared_at': now(),
            'target': TARGET, 'trial': TRIAL, 'target_inputs_sha256': INPUT_SHA, 'hold_seconds': HOLD_SECONDS, 'maximum_hold_seconds': 120,
            'target_control_sha256': sha((CASE / 'control.json').read_bytes()),
            'shared_control_sha256': observation['shared_control_sha256'], 'initial_observation': observation,
            'changes': changes, 'source_sha256': {p: sha((ROOT / p).read_bytes()) for p in SOURCES},
            'script_sha256': sha(Path(__file__).read_bytes()), 'trial_signals': 0,
            'model_calls': 0, 'limits_changes': 0, 'shared_state_changes': 0}


def stop_reason(intent, observation, clock=None):
    if (time.monotonic() if clock is None else clock) >= intent['deadline_monotonic']:
        return 'fixed_deadline'
    target = target_row(observation)
    if target is None:
        return 'diagnostic_exited_or_identity_changed'
    if target['trials']:
        return 'exact_diagnostic_admitted' if target['trials'] == [TRIAL] else 'unexpected_diagnostic_claim'
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
                if sha((CASE / 'control.json').read_bytes()) != intent['target_control_sha256']:
                    reason = 'diagnostic_control_changed'
                if read(CASE / 'result.json').get('finished_at'):
                    reason = 'diagnostic_finished'
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
                    'target_identity': identity(TARGET['pid'])})
            restore(intent, reason)
            return
        time.sleep(.25)


def validate_application_target(intent):
    # Caller holds the existing exclusive shared lock, so admission cannot race
    # the last check and our four local control updates.
    validate_process()
    observation = snapshot_unlocked()
    assert target_state(observation) == 'waiting', 'Target no longer waiting; no controls applied'
    assert stop_reason(intent, observation) is None, 'Admission state changed before application'
    assert sha((CASE / 'control.json').read_bytes()) == intent['target_control_sha256']


def apply():
    intent = prepare()
    if intent['action'] == 'noop':
        print(json.dumps(intent), flush=True)
        return 0
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
            validate_application_target(intent)
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

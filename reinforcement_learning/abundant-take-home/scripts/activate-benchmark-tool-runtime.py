#!/usr/bin/env python3
"""Roll existing queues onto a reviewed tool runtime, without cancelling trials.

Inspection is the default. Repeat --campaign NAME=EXPECTED_PID in rollout order.
--apply owns a temporary admission pause for one campaign at a time, waits for
three independent empty-work signals twice, and interrupts only that empty
runner. Existing supervisors resume the original jobs. A new runner and a new,
hash-matching agent preflight sidecar are required before the next campaign.
Restart the same command/output directory to recover an interrupted activation.
This script never launches a job, changes its config, or calls a model directly.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    '_tool_activation_process_helpers', ROOT / 'scripts/activate-benchmark-deadline.py')
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
process = _helpers.process
same_process = _helpers.same_process
safe_identity = _helpers.safe_identity
job_containers = _helpers.job_containers

RUNTIME_SOURCES = {
    'runtime_wrapper_sha256': 'scripts/benchmark_mini_tool_runtime.py',
    'bootstrap_sha256': 'scripts/benchmark_mini_tool_bootstrap.py',
    'helper_sha256': 'scripts/benchmark_mini_tool_cleanup.py',
}
LAUNCHERS = {'scripts/harbor-resource-runner.py', 'scripts/harbor-benchmark-runner.py'}
BOUND_SOURCES = set(RUNTIME_SOURCES.values()) | LAUNCHERS | {
    'scripts/activate-benchmark-tool-runtime.py', 'scripts/activate-benchmark-deadline.py',
    'scripts/benchmark_agent_runtime.py', 'scripts/benchmark_deadline.py',
    'scripts/benchmark_process_guard.py', 'scripts/benchmark_trial_containment.py',
    'scripts/benchmark_shared_admission.py', 'scripts/benchmark_job_scheduling.py',
    'scripts/benchmark_task_selection.py', 'scripts/benchmark_networks.py',
    'scripts/benchmark_recovery.py', 'scripts/run-candidate-screen.py',
}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temporary.open('w') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def contained(path, parent=ROOT):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(parent.resolve()) or path.is_symlink():
        raise RuntimeError('Activation input must be a regular file inside its expected workspace')
    return resolved


def validate_proof(path):
    proof = read(path)
    assertions = proof.get('assertions')
    if (proof.get('passed') is not True or type(proof.get('model_calls')) is not int
            or proof['model_calls'] != 0 or not isinstance(assertions, dict)
            or not assertions or not all(value is True for value in assertions.values())):
        raise RuntimeError('A passing no-model proof with complete assertions is required')
    hashes = proof.get('source_sha256', {})
    if not set(RUNTIME_SOURCES.values()).issubset(hashes):
        raise RuntimeError('Proof must bind wrapper, bootstrap, and cleanup sources')
    for name, expected in hashes.items():
        if digest(contained(name, ROOT)) != expected:
            raise RuntimeError('Proof source hash mismatch')
    for name in LAUNCHERS:
        tree = ast.parse((ROOT / name).read_text())
        if not any(isinstance(node, ast.ImportFrom)
                   and node.module == 'benchmark_mini_tool_runtime'
                   and any(alias.name == 'install' and alias.asname == 'install_agent_runtime'
                           for alias in node.names) for node in tree.body):
            raise RuntimeError('Both runner entrypoints must select the reviewed tool runtime')
    return {name: digest(contained(name, ROOT)) for name in BOUND_SOURCES | set(hashes)}


def flag(args, name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def runner_identity(pid, job_names):
    value = process(pid)
    if value is None:
        return None
    scripts = {(ROOT / arg).resolve() for arg in value['args'][1:3] if arg.endswith('.py')}
    if value['cwd'] != ROOT or ROOT / 'scripts/harbor-resource-runner.py' not in scripts:
        raise RuntimeError('Expected PID is not a benchmark resource runner in this workspace')
    name = flag(value['args'], '--job-name') or Path(flag(value['args'], '--job-path') or '').name
    if name not in job_names:
        raise RuntimeError('Expected runner belongs to a different campaign job')
    return {**safe_identity(value), 'job': name}


def runners(job_names):
    found = []
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            value = runner_identity(int(directory.name), job_names)
        except (RuntimeError, OSError, UnicodeError):
            continue
        if value is not None:
            found.append(value)
    return found


def snapshot_campaign(name, pid, activation_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]+', name):
        raise RuntimeError('Invalid campaign name')
    control_path = contained(ROOT / 'jobs' / (name + '.control.json'), ROOT / 'jobs')
    before = read(control_path)
    if before.get('paused') or before.get('cancelled'):
        raise RuntimeError('Campaign already paused or cancelled; prior intent must be reviewed')
    shared_path = contained(before['shared_pool'], ROOT / 'jobs')
    plan_path = contained(ROOT / 'jobs' / (name + '.plan.json'), ROOT / 'jobs')
    plan = read(plan_path)
    hashes = {str(plan_path.relative_to(ROOT)): digest(plan_path),
              str(shared_path.relative_to(ROOT)): digest(shared_path)}
    names = [job['name'] for job in plan['jobs']]
    runner = runner_identity(pid, names)
    if runner is None or runners(names) != [runner]:
        raise RuntimeError('Exactly the named runner must be live in this campaign')
    for job in plan['jobs']:
        config = contained(job['config'], ROOT / 'jobs')
        if digest(config) != job['sha256']:
            raise RuntimeError('Campaign config differs from its locked plan')
        hashes[str(config.relative_to(ROOT))] = job['sha256']
        for filename in ('config.json', 'lock.json'):
            path = ROOT / 'jobs' / job['name'] / filename
            if path.exists():
                hashes[str(contained(path, ROOT / 'jobs').relative_to(ROOT))] = digest(path)
            elif job['name'] == runner['job']:
                raise RuntimeError('Live job lacks its raw config or lock')
    return {'campaign': name, 'runner': runner, 'job_names': names,
            'control': str(control_path.relative_to(ROOT)),
            'shared_control': str(shared_path.relative_to(ROOT)),
            'before': before,
            'after': {**before, 'paused': True,
                      'pause_reason': 'Rolling tool-runtime activation ' + activation_id},
            'workload_sha256': hashes, 'phase': 'pending'}


def check_hashes(state):
    hashes = {**state['source_sha256'], state['proof']: state['proof_sha256']}
    for entry in state['campaigns']:
        hashes.update(entry['workload_sha256'])
    for name, expected in hashes.items():
        if digest(contained(name, ROOT)) != expected:
            raise RuntimeError('Approved source, proof, plan, shared control, config, or job lock changed')


def require_control(entry, expected):
    if read(ROOT / entry['control']) != entry[expected]:
        raise RuntimeError('Campaign control changed; refusing to overwrite another decision')


def set_pause(entry, paused):
    """Recover either side of our durable write intent; never merge foreign edits."""
    old, new = ('before', 'after') if paused else ('after', 'before')
    current = read(ROOT / entry['control'])
    if current == entry[new]:
        return
    require_control(entry, old)
    dump(ROOT / entry['control'], entry[new])


def shared_claims(entry, alive):
    path = ROOT / entry['shared_control']
    state = read(path.with_suffix('.state.json'))
    participants = state.get('participants')
    if not isinstance(participants, dict):
        raise RuntimeError('Shared admission participants are unavailable')
    expected = entry['runner']
    matching = [value for value in participants.values()
                if value.get('pid') == expected['pid'] and value.get('identity') == expected['start_ticks']]
    if alive and (len(matching) != 1 or matching[0].get('control') != str(ROOT / entry['control'])):
        raise RuntimeError('Runner identity is not bound to its shared admission participant')
    owned = [value for value in participants.values()
             if value.get('control') == str(ROOT / entry['control'])]
    if any(not isinstance(value.get('trials'), dict) for value in owned):
        raise RuntimeError('Shared claim evidence is malformed')
    return sum(len(value['trials']) for value in owned)


def admission_state(entry):
    require_control(entry, 'after')
    expected = entry['runner']
    alive = same_process(expected) is not None
    resources = read((ROOT / entry['control']).with_suffix('.resources.json'))
    active, names = resources.get('active_trials'), resources.get('trial_names')
    if type(active) is not int or active < 0 or not isinstance(names, list) or len(names) != active:
        raise RuntimeError('Local admission count is unavailable or inconsistent')
    updated = datetime.fromisoformat(resources['updated_at'].replace('Z', '+00:00'))
    if updated.tzinfo is None:
        raise RuntimeError('Local admission timestamp must include its timezone')
    fresh = 0 <= time.time() - updated.timestamp() < 90
    claims = shared_claims(entry, alive)
    return {'active_trials': active, 'shared_claims': claims, 'old_runner_alive': alive,
            'resource_status_fresh': fresh,
            'ready': active == 0 and claims == 0 and (fresh or not alive)}


def drain_state(entry):
    report = admission_state(entry)
    containers = sum(value['state']['Running'] for job in entry['job_names']
                     for value in job_containers(ROOT / 'jobs' / job))
    unexpected = [value for value in runners(entry['job_names']) if value != entry['runner']]
    if unexpected:
        raise RuntimeError('An unexpected runner appeared while campaign admissions were paused')
    return {**report, 'running_trial_containers': containers,
            'ready': report['ready'] and containers == 0}


def stop_empty(entry, check):
    """No escalation: interrupt only the identity-bound, rechecked empty queue."""
    if same_process(entry['runner']) is None:
        return
    check()
    if not drain_state(entry)['ready']:
        raise RuntimeError('Work reappeared before stopping the empty runner')
    descriptor = os.pidfd_open(entry['runner']['pid'])
    try:
        with (ROOT / entry['shared_control']).with_suffix('.lock').open('a') as lock:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Shared admission lock is busy; no runner was signalled')
                    time.sleep(0.1)
            check()
            # Docker was checked immediately above, with admissions paused. Do
            # not hold the shared lock across Docker I/O: healthy peers need it
            # to release their completed trials. Recheck local/shared ownership
            # under the lock so an admission race still prevents this signal.
            if not admission_state(entry)['ready']:
                raise RuntimeError('Work reappeared before stopping the empty runner')
            if same_process(entry['runner']) is not None:
                signal.pidfd_send_signal(descriptor, signal.SIGINT)
        deadline = time.monotonic() + 20
        while same_process(entry['runner']) is not None and time.monotonic() < deadline:
            time.sleep(0.25)
        if same_process(entry['runner']) is not None:
            raise RuntimeError('Empty runner did not exit after SIGINT; no escalation was attempted')
    finally:
        os.close(descriptor)


def sidecars(entry):
    return {str(path.relative_to(ROOT)): path
            for path in (ROOT / 'jobs' / entry['runner']['job']).glob(
                '*/agent/benchmark-agent-tool-runtime.json')}


def runtime_evidence(entry, state):
    expected = {key: state['source_sha256'][name] for key, name in RUNTIME_SOURCES.items()}
    for name, path in sorted(sidecars(entry).items()):
        if name in entry['old_sidecars'] or path.stat().st_mtime < entry['resume_started_at']:
            continue
        try:
            value = read(path)
        except json.JSONDecodeError:
            continue  # The runtime sidecar is written once, immediately before launch.
        if (any(value.get(key) != sha for key, sha in expected.items())
                or value.get('scope') != 'agent_process_only'
                or value.get('preflight_passed') is not True or value.get('installed') is not True
                or value.get('python') != '3.12.11' or value.get('package_version') != '2.4.6'):
            raise RuntimeError('New agent tool-runtime preflight is missing or does not match reviewed sources')
        return {'path': name, 'sha256': digest(path), **expected}
    return None


def advance(entry, state, save):
    """Advance one operation; persistence precedes every control or signal."""
    check_hashes(state)
    phase = entry['phase']
    if phase == 'activated':
        return
    if phase == 'pending':
        require_control(entry, 'before')
        if runner_identity(entry['runner']['pid'], entry['job_names']) != entry['runner']:
            raise RuntimeError('Expected runner changed before its activation turn')
        entry.update(phase='pausing', started_at=now())
        save()
        # Hold admissions in this same call, after persisting the write intent.
        # A recovered pausing intent follows the identical idempotent path.
        phase = 'pausing'
    if phase == 'pausing':
        set_pause(entry, True)
        entry.update(phase='draining', consecutive_empty=0)
        save()
    elif phase == 'draining':
        report = drain_state(entry)
        entry['drain'] = report
        entry['consecutive_empty'] = entry.get('consecutive_empty', 0) + 1 if report['ready'] else 0
        if entry['consecutive_empty'] >= 2:
            entry.update(phase='stopping', old_sidecars=sorted(sidecars(entry)))
        save()
    elif phase == 'stopping':
        require_control(entry, 'after')
        if entry.get('consecutive_empty', 0) < 2 or not drain_state(entry)['ready']:
            raise RuntimeError('Two confirmed empty observations are required before stopping')
        stop_empty(entry, lambda: (check_hashes(state), require_control(entry, 'after')))
        if not drain_state(entry)['ready']:
            raise RuntimeError('Drain evidence changed after the old runner exited')
        entry.update(phase='restoring', stopped_at=now(), resume_started_at=time.time())
        save()
    elif phase == 'restoring':
        if same_process(entry['runner']) is not None:
            raise RuntimeError('Old runner remains alive; refusing to restore admissions')
        set_pause(entry, False)
        entry.update(phase='resuming', restored_at=now())
        save()
    elif phase in {'resuming', 'waiting_for_evidence'}:
        require_control(entry, 'before')
        values = runners(entry['job_names'])
        if len(values) > 1 or any(value['job'] != entry['runner']['job'] for value in values):
            raise RuntimeError('Resume must retain the original single job identity')
        if values:
            new = values[0]
            if new == entry['runner'] or int(new['start_ticks']) <= int(entry['runner']['start_ticks']):
                raise RuntimeError('Replacement runner identity was not renewed')
            if entry.get('new_runner', new) != new:
                raise RuntimeError('Replacement runner changed before runtime confirmation')
            entry.update(new_runner=new, phase='waiting_for_evidence')
            evidence = runtime_evidence(entry, state)
            if evidence:
                entry.update(evidence=evidence, phase='activated', activated_at=now())
        save()
    else:
        raise RuntimeError('Unknown activation phase')


def heartbeat(path, stopped):
    while not stopped.is_set():
        dump(path, {'updated_at': now(), 'watcher': safe_identity(process(os.getpid()))})
        stopped.wait(10)


def hold_failed_resume(state):
    """Withhold further starts after failed runtime confirmation, if still ours."""
    entry = next((value for value in state['campaigns'] if value['phase'] != 'activated'), None)
    if entry and entry['phase'] in {'restoring', 'resuming', 'waiting_for_evidence'}:
        if read(ROOT / entry['control']) == entry['before']:
            set_pause(entry, True)
            entry.update(phase='blocked_after_resume', resume_hold_applied=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', action='append', required=True, metavar='NAME=EXPECTED_PID')
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--poll-sec', type=int, default=10)
    parser.add_argument('--max-wait-sec', type=int, default=21600)
    args = parser.parse_args()
    if not 1 <= args.poll_sec <= 20 or args.max_wait_sec < 1:
        parser.error('Use a 1–20 second poll and a positive bounded wait')
    pairs = [value.rsplit('=', 1) for value in args.campaign]
    if any(len(pair) != 2 or not pair[1].isdigit() for pair in pairs) or len({p[0] for p in pairs}) != len(pairs):
        parser.error('Each distinct campaign must name its expected runner PID')
    requested = [{'campaign': name, 'pid': int(pid)} for name, pid in pairs]
    proof = contained(args.proof, ROOT)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'jobs'):
        parser.error('--output must be a dedicated directory under jobs/')
    state_path = output / 'activation.json'
    global_lock = None
    if args.apply:
        global_lock = (ROOT / 'jobs/.tool-runtime-activation.lock').open('a')
        fcntl.flock(global_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        state = read(state_path)
        if state['requested'] != requested or state['proof'] != str(proof.relative_to(ROOT)):
            raise RuntimeError('Existing activation belongs to different explicit inputs')
        if not args.apply:
            check_hashes(state)
        for entry in state['campaigns']:
            if entry['phase'] == 'draining':
                entry['consecutive_empty'] = 0
    else:
        hashes = validate_proof(proof)
        state = {'schema_version': 1, 'created_at': now(), 'requested': requested,
                 'proof': str(proof.relative_to(ROOT)), 'proof_sha256': digest(proof),
                 'source_sha256': hashes,
                 'campaigns': [snapshot_campaign(name, int(pid), output.name) for name, pid in pairs],
                 'status': 'inspected'}
    if not args.apply:
        print(json.dumps(state, indent=2))
        return 0

    def save():
        state.update(updated_at=now(), watcher=safe_identity(process(os.getpid())))
        dump(state_path, state)

    stopped = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(output / 'heartbeat.json', stopped), daemon=True)
    try:
        check_hashes(state)
        state.update(status='running')
        state.pop('error', None)
        save()
        thread.start()
        deadline = time.monotonic() + args.max_wait_sec
        while time.monotonic() < deadline:
            entry = next((value for value in state['campaigns'] if value['phase'] != 'activated'), None)
            if entry is None:
                state['status'] = 'activated'
                save()
                return 0
            advance(entry, state, save)
            time.sleep(args.poll_sec)
        raise RuntimeError('Activation wait expired; inspect phase and owned pause before resuming')
    except Exception as error:
        state['status'] = 'blocked'
        state['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        try:
            hold_failed_resume(state)
        except Exception as hold_error:
            state['resume_hold_error'] = type(hold_error).__name__
        save()
        return 1
    finally:
        stopped.set()
        if thread.is_alive():
            thread.join(timeout=1)
        if global_lock is not None:
            global_lock.close()


if __name__ == '__main__':
    raise SystemExit(main())

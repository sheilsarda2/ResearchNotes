#!/usr/bin/env python3
"""Drain an explicitly identified old runner before enabling guarded launches.

Run inside the benchmark devcontainer with Harbor's Python. Inspection is the
default; --apply additionally restarts the named supervisors, waits for existing
trials, stops only the drained old runner, and restores our temporary pause.
Any failed precondition leaves admission paused. No model calls are made here.
"""
from __future__ import annotations

import argparse
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

from benchmark_recovery import job_containers

ROOT = Path(__file__).resolve().parents[1]
MODES = {'timeout', 'normal-background', 'buffered-timeout', 'startup-timeout', 'cleanup-failure'}
SOURCES = {'scripts/benchmark_deadline.py', 'scripts/benchmark_process_guard.py',
           'scripts/benchmark_evidence.py', 'scripts/tests/test_benchmark_deadline.py'}
LAUNCHERS = {'scripts/harbor-resource-runner.py', 'scripts/harbor-benchmark-runner.py',
             'scripts/run-candidate-screen.py', 'scripts/run-sonnet-confirmation.py',
             'scripts/benchmark_recovery.py', 'scripts/benchmark_networks.py',
             'scripts/benchmark_shared_admission.py', 'scripts/candidate-bench.py'}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def validate_suite(path, expected_digest=None):
    actual_digest = digest(path)
    if expected_digest is not None and actual_digest != expected_digest:
        raise RuntimeError('Approved validation summary changed')
    data = read(path)
    if data.get('overall_passed') is not True or data.get('guarded') is not True or data.get('model_calls') != 0:
        raise RuntimeError('A passing guarded no-model validation suite is required')
    cases = data.get('cases', [])
    if len(cases) != len(MODES) or {case.get('mode') for case in cases} != MODES:
        raise RuntimeError('Validation suite does not contain the five required cases')
    for case in cases:
        if case.get('passed') is not True or not case.get('assertions') or not all(
                value is True for value in case['assertions'].values()):
            raise RuntimeError('Validation case lacks complete passing assertions')
    sources = data.get('source_sha256', {})
    if not SOURCES.issubset(sources):
        raise RuntimeError('Validation suite is not bound to all guard source files')
    for name, expected in sources.items():
        source = (ROOT / name).resolve()
        if not source.is_relative_to(ROOT) or source.is_symlink() or digest(source) != expected:
            raise RuntimeError('Validated guard sources changed')
    return actual_digest


def controls_unchanged(activation):
    for entry in activation['controls'].values():
        path = (ROOT / entry['path']).resolve()
        if not path.is_relative_to(ROOT / 'jobs') or not entry.get('applied'):
            raise RuntimeError('Invalid activation control record')
        if entry['after'].get('paused') is not True or read(path) != entry['after']:
            raise RuntimeError('Admission control changed; automatic activation withheld')


def workload_snapshot(activation):
    """Bind existing plans/configs/launchers; supervisors also verify task hashes."""
    snapshot = {name: digest(ROOT / name) for name in LAUNCHERS}
    for campaign in activation['controls']:
        plan_path = ROOT / 'jobs' / (campaign + '.plan.json')
        plan = read(plan_path)
        snapshot[str(plan_path.relative_to(ROOT))] = digest(plan_path)
        for job in plan['jobs']:
            config = (ROOT / job['config']).resolve()
            if not config.is_relative_to(ROOT / 'jobs') or digest(config) != job['sha256']:
                raise RuntimeError('Workload config differs from its locked campaign plan')
            snapshot[str(config.relative_to(ROOT))] = job['sha256']
    return snapshot


def process(pid):
    """Keep arguments private; returned safe metadata excludes argv and env."""
    directory = Path('/proc') / str(pid)
    try:
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        args = (directory / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        executable = str((directory / 'exe').resolve(strict=True))
        cwd = (directory / 'cwd').resolve(strict=True)
    except (FileNotFoundError, ProcessLookupError):
        return None
    return {'pid': pid, 'start_ticks': fields[19], 'args': args,
            'executable': executable, 'cwd': cwd}


def safe_identity(value):
    return {key: value[key] for key in ('pid', 'start_ticks')}


def same_process(expected):
    current = process(expected['pid'])
    if current is not None and current['start_ticks'] != expected['start_ticks']:
        raise RuntimeError('Process identity changed; refusing to signal a reused PID')
    return current


def flag(args, name, default=None):
    if name not in args:
        return default
    position = args.index(name)
    if position + 1 == len(args):
        raise RuntimeError('Incomplete process arguments')
    return args[position + 1]


def identify(pid, kind, campaigns):
    value = process(pid)
    if value is None:
        raise RuntimeError(f'Expected {kind} process is no longer running')
    expected = ROOT / 'scripts' / ('harbor-resource-runner.py' if kind == 'runner' else 'run-candidate-screen.py')
    if value['cwd'] != ROOT or str(expected) not in value['args'][:3]:
        raise RuntimeError('Named process is not the expected benchmark script')
    interpreter = Path(value['args'][0])
    if not interpreter.is_absolute() or str(interpreter.resolve()) != value['executable']:
        raise RuntimeError('Named process lacks an unambiguous Python interpreter')
    if kind == 'runner':
        campaign = flag(value['args'], '--job-name')
        if campaign is None:
            campaign = Path(flag(value['args'], '--job-path', '')).name
    else:
        campaign = '{}-efforts-{}-{}'.format(flag(value['args'], '--campaign', 'candidates-top5'),
                                           flag(value['args'], '--attempts', '3'),
                                           flag(value['args'], '--run-id'))
    if campaign not in campaigns:
        raise RuntimeError('Named process belongs to another campaign')
    value.update(campaign=campaign, kind=kind)
    return value


def stop_named(expected, first_signal, check):
    if same_process(expected) is None:
        return
    check()
    # pidfd makes signaling immune to PID reuse between inspection and kill.
    descriptor = os.pidfd_open(expected['pid'])
    try:
        if same_process(expected) is None:
            return
        signal.pidfd_send_signal(descriptor, first_signal)
        end = time.monotonic() + 20
        while same_process(expected) is not None and time.monotonic() < end:
            time.sleep(0.25)
        if same_process(expected) is not None and first_signal != signal.SIGTERM:
            check()
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            end = time.monotonic() + 10
            while same_process(expected) is not None and time.monotonic() < end:
                time.sleep(0.25)
        if same_process(expected) is not None:
            raise RuntimeError('Named process did not exit; automatic activation withheld')
    finally:
        os.close(descriptor)


def restart_supervisor(expected, check):
    stop_named(expected, signal.SIGTERM, check)
    check()
    # The supervisor loads its own .env. Never read or export another process's
    # environment, and never serialize its command line into status artifacts.
    command = list(expected['args'])
    # Preserve the virtualenv symlink from argv[0]; /proc/exe resolves to the
    # base interpreter and would lose Harbor's installed Python environment.
    prefix = ROOT / 'jobs' / expected['campaign']
    started = time.time()
    with prefix.with_suffix('.supervisor.log').open('ab') as output:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if child.poll() is not None:
            raise RuntimeError('Replacement supervisor exited; admission remains paused')
        check()
        pidfile, summary = prefix.with_suffix('.pid'), prefix.with_suffix('.summary.json')
        if (pidfile.exists() and pidfile.read_text().strip() == str(child.pid)
                and summary.exists() and summary.stat().st_mtime >= started):
            return {'campaign': expected['campaign'], 'old_pid': expected['pid'],
                    'new_pid': child.pid, 'ready_at': now()}
        time.sleep(1)
    raise RuntimeError('Replacement supervisor readiness was not confirmed')


def drain_state(runner, activation):
    entry = activation['controls'][runner['campaign']]
    resource_path = (ROOT / entry['path']).with_suffix('.resources.json')
    directory = ROOT / 'jobs' / runner['campaign']
    running_containers = sum(container['state']['Running'] for container in job_containers(directory))
    current = same_process(runner)
    if not resource_path.exists():
        # A large queue can still be initializing before its first admission.
        # With our pause held, no trial directory or owned container means no
        # trial has entered execution. Never infer this from missing stats alone.
        empty = ((directory / 'config.json').is_file() and
                 not any(path.is_dir() for path in directory.iterdir()) and running_containers == 0)
        if not empty:
            raise RuntimeError('Runner admission count is unavailable')
        return {'active_trials': 0, 'running_trial_containers': 0,
                'resource_status_fresh': True, 'old_runner_alive': current is not None,
                'basis': 'pre_admission_empty', 'ready': True}
    resources = read(resource_path)
    active = resources.get('active_trials')
    if not isinstance(active, int) or active < 0:
        raise RuntimeError('Runner admission count is unavailable')
    updated = datetime.fromisoformat(resources['updated_at'].replace('Z', '+00:00')).timestamp()
    # A final zero report may stop refreshing after the runner exits; while it
    # is alive require the paused admission loop to keep reporting fresh state.
    fresh = time.time() - updated < 90 or current is None
    return {'active_trials': active, 'running_trial_containers': running_containers,
            'resource_status_fresh': fresh, 'old_runner_alive': current is not None,
            'basis': 'admission_status_and_daemon', 'ready': active == 0 and running_containers == 0 and fresh}


def restore_controls(activation):
    controls_unchanged(activation)
    for entry in activation['controls'].values():
        path = ROOT / entry['path']
        current = read(path)
        if current != entry['after']:
            raise RuntimeError('Admission control changed before restoration')
        restored = dict(current)
        for key in ('paused', 'pause_reason'):
            if key in entry['before']:
                restored[key] = entry['before'][key]
            else:
                restored.pop(key, None)
        # Atomic replacement; compare again immediately before writing. All
        # other settings retain their values from our exact applied control.
        if read(path) != entry['after']:
            raise RuntimeError('Admission control changed during restoration')
        dump(path, restored)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activation', required=True, type=Path)
    parser.add_argument('--validation', type=Path)
    parser.add_argument('--runner-pid', required=True, type=int)
    parser.add_argument('--supervisor-pid', action='append', required=True, type=int)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-wait-sec', type=int, default=10800)
    parser.add_argument('--poll-sec', type=int, default=20)
    args = parser.parse_args()
    if args.max_wait_sec < 1 or not 1 <= args.poll_sec <= 60:
        parser.error('Use a positive bounded wait and poll interval of 1–60 seconds')
    if args.apply and args.validation is None:
        parser.error('--apply requires --validation')
    activation = read(args.activation)
    controls_unchanged(activation)
    campaigns = set(activation['controls'])
    runner = identify(args.runner_pid, 'runner', campaigns)
    supervisors = [identify(pid, 'supervisor', campaigns) for pid in args.supervisor_pid]
    if len(supervisors) != len(campaigns) or {s['campaign'] for s in supervisors} != campaigns:
        parser.error('Name exactly one supervisor for each recorded campaign')
    validation_digest = validate_suite(args.validation) if args.validation else None
    workload_hashes = workload_snapshot(activation)
    status = {'created_at': now(), 'watcher_pid': os.getpid(), 'apply': args.apply,
              'runner': {**safe_identity(runner), 'campaign': runner['campaign']},
              'supervisors': [{**safe_identity(s), 'campaign': s['campaign']} for s in supervisors],
              'validation_sha256': validation_digest, 'workload_sha256': workload_hashes, 'events': [],
              'drain': drain_state(runner, activation), 'status': 'inspected'}
    if not args.apply:
        print(json.dumps(status, indent=2))
        return 0
    state_path = args.activation.with_name('activation-watcher.json')
    lock = state_path.with_suffix('.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if state_path.exists():
        raise RuntimeError('Watcher state already exists; review it before launching again')

    def check():
        controls_unchanged(activation)
        validate_suite(args.validation, validation_digest)
        if workload_snapshot(activation) != workload_hashes:
            raise RuntimeError('Approved launchers, campaign plans, or configs changed')

    def save():
        status['updated_at'] = now()
        dump(state_path, status)

    try:
        status['status'] = 'restarting_supervisors'
        save()
        for supervisor in supervisors:
            status['events'].append({'supervisor_restarted': restart_supervisor(supervisor, check)})
            save()
        status['status'] = 'draining'
        deadline = time.monotonic() + args.max_wait_sec
        consecutive_ready = 0
        while time.monotonic() < deadline:
            check()
            status['drain'] = drain_state(runner, activation)
            consecutive_ready = consecutive_ready + 1 if status['drain']['ready'] else 0
            save()
            if consecutive_ready >= 2:
                break
            time.sleep(args.poll_sec)
        else:
            raise RuntimeError('Drain wait expired; admission remains paused')
        check()
        # Recheck actual containers immediately before signaling the old queue.
        if not drain_state(runner, activation)['ready']:
            raise RuntimeError('Drain state changed before runner stop')
        stop_named(runner, signal.SIGINT, check)
        status['events'].append({'old_runner_stopped': runner['pid'], 'at': now()})
        check()
        if not drain_state(runner, activation)['ready']:
            raise RuntimeError('Drain state changed after runner stop')
        restore_controls(activation)
        status['status'] = 'activated'
        status['events'].append({'prior_pause_settings_restored': sorted(campaigns), 'at': now()})
        save()
        return 0
    except Exception as error:
        status['status'] = 'blocked'
        status['error_type'] = type(error).__name__
        status['reason'] = str(error) if isinstance(error, RuntimeError) else 'Activation precondition or operation failed; inspect locally'
        save()
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

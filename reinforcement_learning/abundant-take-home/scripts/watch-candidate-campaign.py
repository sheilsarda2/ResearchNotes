#!/usr/bin/env python3
"""Keep the explicitly selected campaign supervised; preserve pauses and evidence."""
from datetime import datetime, timezone
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from benchmark_recovery import job_containers, memory_snapshot, memory_block_reason
from benchmark_interleaving import held_cell_keys
from benchmark_shared_admission import process_identity

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'complete', 'cancelled', 'superseded'}
CHILDREN = []


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {} if default is None else default


def dump(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def flag(args, name, default=None):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else default


def inventory(base, job_names):
    """Identify only our scripts. Do not serialize arbitrary argv or environments."""
    found = {'supervisors': [], 'runners': {}}
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            pid = int(directory.name)
            identity = process_identity(pid)
            if not identity:
                continue
            args = (directory/'cmdline').read_bytes().decode().rstrip('\0').split('\0')
            scripts = {(ROOT/arg).resolve() for arg in args[1:3] if arg.endswith('.py')}
            relevant = {ROOT/'scripts/run-candidate-screen.py', ROOT/'scripts/harbor-resource-runner.py'}
            if not scripts & relevant:
                continue
            cwd = (directory/'cwd').resolve()
            if cwd == directory/'cwd':
                raise RuntimeError('Run watchdog with permission to inspect benchmark processes')
            if cwd != ROOT:
                continue
            info = {'pid': pid, 'identity': identity}
            if ROOT/'scripts/run-candidate-screen.py' in scripts:
                name = '{}-efforts-{}-{}'.format(flag(args, '--campaign', 'candidates-top5'),
                                               flag(args, '--attempts', '3'), flag(args, '--run-id'))
                if name == base:
                    found['supervisors'].append(info)
            elif ROOT/'scripts/harbor-resource-runner.py' in scripts:
                name = flag(args, '--job-name') or Path(flag(args, '--job-path', '')).name
                if name in job_names:
                    found['runners'][name] = info
        except (OSError, UnicodeError):
            continue
    return found


def eligible(base, user_plan, control, summary):
    excluded = set(user_plan.get('cancelled_do_not_resume', [])) | set(user_plan.get('superseded_do_not_resume', []))
    active = {user_plan.get('active_full_campaign'), *user_plan.get('active_revision_campaigns', [])}
    return (base in active and base not in excluded
            and not control.get('cancelled') and summary.get('status') not in TERMINAL)


def supervisor_action(count, heartbeat_age, activation_busy, recent_restarts):
    if count > 1:
        return 'duplicate_supervisors'
    if activation_busy:
        return None
    if count == 1 and heartbeat_age < 180:
        return None
    if recent_restarts >= 3:
        return 'restart_limit'
    return 'restart_stale' if count else 'start_missing'


def stale_idle_runner(paused, memory_blocked, active, containers, finished, idle_seconds):
    return not (paused or memory_blocked or active or containers or finished) and idle_seconds >= 300


def confirmed_interleaving_wait(process, participant, shared, job_name):
    """Prove the current round policy prevents this primary job from admitting.

    The priority loop runs before the resource heartbeat. Neither that heartbeat
    nor the shared state timestamp must advance while all its cells are ahead.
    This establishes an admission barrier, not general worker health.
    """
    from benchmark_interleaving import held_cell_keys
    from benchmark_coverage_priority import priority_decision
    if not isinstance(job_name, str) or not re.fullmatch(r'[\w-]+', job_name):
        return None
    try:
        if (process_identity(process['pid']) != process['identity'] or
                participant.get('trials') != {} or
                Path(participant['control']).resolve() != ROOT/'jobs'/f'{job_name}.control.json'):
            return None
        policy = shared.get('interleaving') or {}
        # Repair jobs deliberately bypass primary round accounting.
        if policy.get('version') != 1 or policy.get('campaigns', []).count(job_name) != 1:
            return None
        cells, counts = policy['cells'], policy['counts']
        if not cells or set(cells) != set(counts):
            return None
        held = held_cell_keys(policy)
        own_pending, pending, own_held, own_keys = [], [], [], []
        for key, cell in cells.items():
            target, count = cell['target'], counts[key]
            if (type(target) is not int or type(count) is not int or
                    target <= 0 or not 0 <= count <= target or
                    key != json.dumps([cell['task'], cell['model'], cell['effort']], separators=(',', ':'))):
                return None
            if count < target:
                if key in held:
                    if cell['job'] == job_name:
                        own_held.append(count)
                    continue
                pending.append(count)
                if cell['job'] == job_name:
                    own_pending.append(count)
                    own_keys.append(key)
        if policy.get('first_sweep') and own_keys:
            decisions = [priority_decision(shared, key, held) for key in own_keys]
            if any(priority for priority, _ in decisions):
                return None
            if all(reason for _, reason in decisions):
                return decisions[0][1]
        if own_pending and min(own_pending) > min(pending):
            return 'interleaving: waiting for remaining cells in round'
        if own_held and not own_pending:
            return 'interleaving: task temporarily held'
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        return None
    return None


def confirmed_shared_wait(process, shared_control, shared, now=None, *, job_name=None):
    """Recognize a live runner waiting on the shared pool, not a stalled queue."""
    participants = shared.get('participants', {})
    matches = [p for p in participants.values()
               if p['pid'] == process['pid'] and p['identity'] == process['identity']]
    if len(matches) != 1:
        return None
    participant = matches[0]
    control_path = Path(participant['control']).resolve()
    if not control_path.is_relative_to(ROOT / 'jobs'):
        return None
    round_wait = confirmed_interleaving_wait(process, participant, shared, job_name)
    if round_wait:
        return round_wait
    resources = read(control_path.with_suffix('.resources.json'))
    # The admission loop keeps writing while Docker inventory is collected.
    # Sample after reading its newest timestamp, not at the cycle's start.
    now = time.time() if now is None else now
    try:
        local_age = now - datetime.fromisoformat(resources['updated_at'].replace('Z', '+00:00')).timestamp()
        shared_age = now - shared['updated_at']
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not (0 <= local_age < 90 and 0 <= shared_age < 90):
        return None
    reason = resources.get('admission_wait_reason')
    limit = shared_control.get('max_active', 32)
    claims = sum(len(p['trials']) for p in participants.values())
    external = shared.get('external_trials', 0)
    if reason == 'shared concurrency' and claims + external >= limit:
        return reason
    if reason == 'shared campaign allocation':
        external_peers = sum(process_identity(p['pid']) == p['identity']
                             for p in shared_control.get('external_peers', []))
        allocation = max(1, limit // (len(participants) + external_peers))
        if len(participant['trials']) >= allocation:
            return reason
    return None


def signal_named(info, sig):
    if process_identity(info['pid']) != info['identity']:
        return False
    descriptor = os.pidfd_open(info['pid'])
    try:
        if process_identity(info['pid']) != info['identity']:
            return False
        signal.pidfd_send_signal(descriptor, sig)
        return True
    finally:
        os.close(descriptor)


def launch_supervisor(base, plan):
    marker = f'-efforts-{plan["attempts"]}-'
    campaign, run_id = base.rsplit(marker, 1)
    command = [sys.executable, str(ROOT/'scripts/run-candidate-screen.py'), '--run-id', run_id,
               '--attempts', str(plan['attempts']), '--workers', str(plan['workers']),
               '--models', *plan['models'], '--campaign', campaign,
               '--task-manifest', str(ROOT/'jobs'/f'{base}.tasks.json')]
    if plan.get('after_summary'):
        command += ['--after-summary', plan['after_summary']]
    with (ROOT/'jobs'/f'{base}.supervisor.log').open('ab') as output:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    CHILDREN.append(child)
    return child.pid


def running_under(containers, directory):
    directory = directory.resolve()
    return [c for c in containers if c['state']['Running'] and any(
        Path(m.get('Source', '/')).is_relative_to(directory) for m in c['mounts'])]


def reclaim_dead_claims(shared_path, allowed_controls, containers, apply):
    """Free only dead owners' reservations whose exact trial containers stopped."""
    state_path = shared_path.with_suffix('.state.json')
    released = []
    if not state_path.exists():
        return released
    with shared_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(state_path)
        for participant in state.get('participants', {}).values():
            if participant['control'] not in allowed_controls or process_identity(participant['pid']) == participant['identity']:
                continue
            name = Path(participant['control']).name.removesuffix('.control.json')
            for trial in list(participant['trials']):
                if not running_under(containers, ROOT/'jobs'/name/trial):
                    released.append({'owner_pid': participant['pid'], 'trial': trial})
                    if apply:
                        del participant['trials'][trial]
        if apply and released:
            state['updated_at'] = time.time()
            dump(state_path, state)
    return released


def cycle(base, state, apply):
    CHILDREN[:] = [child for child in CHILDREN if child.poll() is None]
    prefix = ROOT/'jobs'/base
    user_plan = read(ROOT/'jobs/benchmark-user-plan.json')
    plan = read(prefix.with_suffix('.plan.json'))
    control = read(prefix.with_suffix('.control.json'))
    summary_path = prefix.with_suffix('.summary.json')
    summary = read(summary_path)
    now = time.time()
    report = {'updated_at': timestamp(), 'watchdog_pid': os.getpid(), 'campaign': base,
              'completed': summary.get('completed'), 'target': summary.get('target'),
              'status': 'monitoring', 'alerts': [], 'actions': []}
    if not eligible(base, user_plan, control, summary):
        report['status'] = 'stopped'
        return report
    if set(control.get('excluded_tasks', [])) != set(plan.get('excluded_tasks', [])):
        raise RuntimeError('Campaign and runner task selections disagree')
    activation = read(ROOT/user_plan['activation_status_path']) if user_plan.get('activation_status_path') else {}
    activation_active = activation.get('status') in {'draining', 'restarting_supervisors'}
    activation_alive = bool(activation.get('watcher_pid') and process_identity(activation['watcher_pid']))
    report['activation'] = {'status': activation.get('status'), 'alive': activation_alive,
                            'remaining_old_trials': activation.get('drain', {}).get('active_trials')}
    if activation.get('status') == 'blocked' or (activation_active and not activation_alive):
        report['alerts'].append('Runtime activation needs attention; its pause remains in force.')
    names = {job['name'] for job in plan['jobs']}
    processes = inventory(base, names)
    report['supervisors'] = processes['supervisors']
    report['runner_pids'] = {n: p['pid'] for n, p in processes['runners'].items()}
    shared_path = Path(control['shared_pool'])
    shared_control = read(shared_path)
    containers = job_containers(ROOT/'jobs')
    allowed_controls = {str((ROOT/'jobs'/f'{name}.control.json').resolve()) for name in names}
    released = reclaim_dead_claims(shared_path, allowed_controls, containers, apply)
    if released:
        report['actions'].append({'released_stopped_reservations': released})
    shared = read(shared_path.with_suffix('.state.json'))
    active = []
    for participant in shared.get('participants', {}).values():
        if participant['control'] not in allowed_controls:
            continue
        job_name = Path(participant['control']).name.removesuffix('.control.json')
        for trial in participant['trials']:
            directory = ROOT/'jobs'/job_name/trial
            progress_paths = [directory/'agent/mini-swe-agent.trajectory.json',
                              directory/'agent/mini-swe-agent.txt', directory/'trial.log',
                              directory/'verifier/test-stdout.txt']
            updated = max((p.stat().st_mtime for p in progress_paths if p.exists()), default=now)
            active.append({'job': job_name, 'trial': trial,
                           'owner_alive': process_identity(participant['pid']) == participant['identity'],
                           'last_output_seconds_ago': round(now-updated)})
    report['active_trials'] = len(active)
    report['trials'] = active
    report['concurrency_limit'] = min(control['max_active'], shared_control['max_active'])
    report['memory'] = snapshot = memory_snapshot()
    report['paused'] = bool(control.get('paused'))
    report['pause_reason'] = control.get('pause_reason')
    if control.get('paused') and not (activation_active and activation_alive):
        report['alerts'].append('New starts paused: ' + control.get('pause_reason', 'unspecified reason'))
    if summary.get('health', {}).get('unclassified_results'):
        report['alerts'].append('Unclassified result requires evidence review.')
    if any(not trial['owner_alive'] for trial in active):
        report['alerts'].append('A stopped runner still has live trial containers; recovery must preserve them.')
    restarts = [t for t in state.get('supervisor_restarts', []) if now-t < 600]
    heartbeat_age = now-summary_path.stat().st_mtime if summary_path.exists() else float('inf')
    action = supervisor_action(len(processes['supervisors']), heartbeat_age,
                               activation_active and activation_alive and activation.get('status') == 'restarting_supervisors', len(restarts))
    report['summary_age_seconds'] = round(heartbeat_age) if heartbeat_age != float('inf') else None
    if action in {'duplicate_supervisors', 'restart_limit'}:
        report['alerts'].append(action)
    elif action:
        if apply:
            for process in processes['supervisors']:
                signal_named(process, signal.SIGTERM)
                deadline = time.monotonic()+5
                while process_identity(process['pid']) == process['identity'] and time.monotonic() < deadline:
                    time.sleep(0.1)
                if process_identity(process['pid']) == process['identity']:
                    raise RuntimeError('Stale supervisor did not exit; refusing duplicate launch')
            # Its own exclusive lock and task/config checks protect startup.
            pid = launch_supervisor(base, plan)
            restarts.append(now)
            report['actions'].append({action: pid})
        else:
            report['actions'].append({'would': action})
    state['supervisor_restarts'] = restarts
    idle = state.setdefault('runner_idle_since', {})
    attempts = state.setdefault('runner_restarts', {})
    report['admission_waits'] = {}
    for name, process in processes['runners'].items():
        occupied = [trial for trial in active if trial['job'] == name]
        owned = running_under(containers, ROOT/'jobs'/name)
        job_control = read(ROOT/'jobs'/f'{name}.control.json') or control
        gated = bool(memory_block_reason(job_control, snapshot) or memory_block_reason(shared_control, snapshot))
        shared_wait = confirmed_shared_wait(process, shared_control, shared, job_name=name)
        if shared_wait:
            report['admission_waits'][name] = shared_wait
        finished = bool(read(ROOT/'jobs'/name/'result.json').get('finished_at'))
        if occupied or owned or job_control.get('paused') or gated or shared_wait or finished:
            idle.pop(name, None)
            continue
        idle.setdefault(name, now)
        if stale_idle_runner(job_control.get('paused'), gated, occupied, owned, finished, now-idle[name]):
            history = [t for t in attempts.get(name, []) if now-t < 3600]
            if len(history) >= 2:
                report['alerts'].append(f'Idle runner restart limit: {name}')
            elif apply:
                # Recheck ownership just before signaling; never interrupt an
                # admitted trial because it is slow or silent during compilation.
                latest_control = read(ROOT/'jobs'/f'{name}.control.json') or read(prefix.with_suffix('.control.json'))
                if not latest_control.get('paused') and not running_under(job_containers(ROOT/'jobs'/name), ROOT/'jobs'/name):
                    with shared_path.with_suffix('.lock').open('a') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        latest = read(shared_path.with_suffix('.state.json'))
                        claims = [p for p in latest.get('participants', {}).values()
                                  if p['pid'] == process['pid'] and p['identity'] == process['identity'] and p['trials']]
                        shared_wait = confirmed_shared_wait(process, read(shared_path), latest, job_name=name)
                        if shared_wait:
                            idle.pop(name, None)
                            report['admission_waits'][name] = shared_wait
                        elif not claims and signal_named(process, signal.SIGINT):
                            history.append(now)
                            report['actions'].append({'restarted_empty_runner': name})
            else:
                report['actions'].append({'would_restart_empty_runner': name})
            attempts[name] = history
    state['runner_idle_since'] = {n: t for n, t in idle.items() if n in processes['runners']}
    if report['alerts']:
        report['status'] = 'attention'
    elif activation_active and activation_alive:
        report['status'] = 'draining_for_activation'
    elif control.get('paused'):
        report['status'] = 'paused'
    elif active:
        report['status'] = 'running'
    elif memory_block_reason(control, snapshot) or memory_block_reason(shared_control, snapshot):
        report['status'] = 'waiting_for_memory'
    elif report['admission_waits']:
        report['status'] = 'waiting_for_shared_resources'
    else:
        report['status'] = 'starting'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    if not re.fullmatch(r'[\w-]+', args.campaign) or not 10 <= args.interval <= 60:
        parser.error('Invalid campaign or interval')
    os.chdir(ROOT)
    prefix = ROOT/'jobs'/args.campaign
    lock = prefix.with_suffix('.watchdog.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    state_path = prefix.with_suffix('.watchdog-state.json')
    state = read(state_path)
    while True:
        try:
            report = cycle(args.campaign, state, args.apply)
        except Exception as error:
            # Do not copy arbitrary exception messages: library/process errors
            # may contain credentials. Continue observing after transient faults.
            report = {'updated_at': timestamp(), 'watchdog_pid': os.getpid(),
                      'campaign': args.campaign, 'status': 'attention',
                      'alerts': [f'Watchdog check failed: {type(error).__name__}'], 'actions': []}
        report['interval_seconds'] = args.interval
        report['automatic_recovery'] = args.apply
        dump(prefix.with_suffix('.watchdog.json'), report)
        dump(state_path, state)
        with prefix.with_suffix('.watchdog-history.jsonl').open('a') as output:
            output.write(json.dumps(report)+'\n')
        print(json.dumps({k: report.get(k) for k in ['updated_at', 'status', 'active_trials', 'completed', 'target', 'alerts', 'actions']}), flush=True)
        if args.once or report['status'] == 'stopped':
            return 0
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())

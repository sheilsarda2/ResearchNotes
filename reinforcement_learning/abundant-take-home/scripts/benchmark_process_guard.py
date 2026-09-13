#!/usr/bin/env python3
"""Linux-only execution owner, uploaded to disposable benchmark containers.

The subreaper owns descendants even when they create new sessions or double fork.
It never records command text or environment variables. This is lifecycle control,
not a security boundary against an agent deliberately attacking its supervisor.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
    temporary.replace(path)


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19]  # starttime, PID reuse protection
    except (FileNotFoundError, ProcessLookupError):
        return None


def descendants(root):
    parents = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[int(path.name)] = int(fields[1])
        except (FileNotFoundError, ProcessLookupError):
            pass
    owned = {root}
    while True:
        found = {pid for pid, parent in parents.items() if parent in owned}
        if found <= owned:
            return owned - {root}
        owned |= found


def quiesce():
    killed = set()
    until = time.monotonic() + 10
    while True:
        children = descendants(os.getpid())
        # Stop before killing to limit concurrent forks; repeat after adoption.
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for pid in children:
                try:
                    os.kill(pid, sig)
                    killed.add(pid)
                except ProcessLookupError:
                    pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if not pid:
                    break
            except ChildProcessError:
                break
        if not descendants(os.getpid()):
            return len(killed)
        if time.monotonic() > until:
            raise RuntimeError('Agent descendants did not become quiescent')
        time.sleep(.005)


def run(args):
    phase = Path(args.phase)
    state_path = phase / (args.execution + '.json')
    # Fail closed if Linux cannot provide orphan adoption.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('PR_SET_CHILD_SUBREAPER failed')
    stopped = []

    def stop(signum, frame):
        if not stopped:
            stopped.append(('deadline' if signum == signal.SIGALRM else 'cancelled', time.time()))

    for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(sig, stop)
    state = {'pid': os.getpid(), 'identity': identity(os.getpid()),
             'started_at_epoch': time.time(), 'deadline_epoch': args.deadline,
             'quiescent': False}
    save(state_path, state)
    child = None
    reason = 'completed'
    code = 0
    try:
        if (phase / 'closed').exists() or (args.deadline and time.time() >= args.deadline):
            reason, code = 'closed_before_start', 124
        else:
            if args.deadline:
                signal.setitimer(signal.ITIMER_REAL, max(.000001, args.deadline - time.time()))
            child = subprocess.Popen(['bash', '-c', args.command], start_new_session=True)
            # Check again after spawn: close can race with process creation.
            while child.poll() is None and not stopped and not (phase / 'closed').exists():
                time.sleep(.005)
            if stopped or (phase / 'closed').exists():
                reason = stopped[0][0] if stopped else 'cancelled'
                code = 124
            else:
                code = child.returncode
        state['stop_requested_at_epoch'] = stopped[0][1] if stopped else time.time()
        state['reason'] = reason
        state['descendants_killed'] = quiesce()
        if child is not None:
            child.wait()
        state['quiescent'] = True
        state['quiescent_at_epoch'] = time.time()
        state['return_code'] = code
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        save(state_path, state)
    return code if code >= 0 else 128 - code


def close(args):
    phase = Path(args.phase)
    (phase / 'closed').touch()
    until = time.monotonic() + 15
    while True:
        records = [json.loads(path.read_text()) for path in sorted(phase.glob('*.json'))]
        pending = []
        for record in records:
            if record.get('quiescent'):
                continue
            pid = record['pid']
            if identity(pid) != record['identity']:
                raise RuntimeError('Execution supervisor disappeared without quiescence proof')
            pending.append(pid)
            try:
                os.kill(pid, signal.SIGUSR1)
            except ProcessLookupError:
                pass
        if not pending:
            print(json.dumps({'quiescent': True, 'closed_at_epoch': time.time(), 'executions': records}))
            return 0
        if time.monotonic() >= until:
            raise RuntimeError('Timed out waiting for execution quiescence')
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['run', 'close'])
    parser.add_argument('--phase', required=True)
    parser.add_argument('--execution')
    parser.add_argument('--deadline', type=float)
    parser.add_argument('--command')
    args = parser.parse_args()
    return run(args) if args.operation == 'run' else close(args)


if __name__ == '__main__':
    raise SystemExit(main())

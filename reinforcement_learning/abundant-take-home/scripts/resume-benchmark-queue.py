#!/usr/bin/env python3
"""Restore our saved supervisors and viewer after a Docker Desktop restart.

Run with Harbor's Python. Supervisors wait for the configured VM memory minimum
before starting trials; completed trials retain their original results.
"""
import argparse
import fcntl
import json
from pathlib import Path
import socket
import subprocess
import sys

from benchmark_recovery import memory_snapshot

ROOT = Path(__file__).resolve().parents[1]


def supervisor_running(run_id, model):
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().decode().split('\0')
        except (OSError, UnicodeError):
            continue
        if not any(arg.endswith('/run-sonnet-confirmation.py') for arg in args[:3]):
            continue
        if '--run-id' not in args or args[args.index('--run-id') + 1] != run_id:
            continue
        actual_model = args[args.index('--model') + 1] if '--model' in args else 'sonnet-5'
        if actual_model == model:
            return int(path.parent.name)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', type=Path,
                        default=ROOT / 'jobs/model-effort-queue-20260913T095218Z.json')
    args = parser.parse_args()
    queue_path = args.queue.resolve()
    queue = json.loads(queue_path.read_text())
    with queue_path.with_suffix('.launch.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for entry in queue['entries']:
            plan_path = ROOT / entry['plan']
            plan = json.loads(plan_path.read_text())
            base = plan_path.name.removesuffix('.plan.json')
            model = plan['model'].removeprefix('anthropic/claude-')
            label = 'sonnet' if model == 'sonnet-5' else model
            run_id = base.removeprefix(f"{label}-efforts-{plan['attempts_per_effort']}-")
            control = json.loads((ROOT / 'jobs' / f'{base}.control.json').read_text())
            assert control.get('min_total_mb', 0) >= 30000, '32 GB memory gate must be configured first'
            pid = supervisor_running(run_id, model)
            if pid:
                print(f'{base}: supervisor already running ({pid})')
                continue
            command = [sys.executable, str(ROOT / 'scripts/run-sonnet-confirmation.py'),
                       '--run-id', run_id, '--model', model,
                       '--attempts', str(plan['attempts_per_effort']),
                       '--workers', str(plan['workers'])]
            if plan.get('after_summary'):
                command += ['--after-summary', plan['after_summary']]
            with (ROOT / 'jobs' / f'{base}.supervisor.log').open('ab') as output:
                child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                         stdout=output, stderr=subprocess.STDOUT,
                                         start_new_session=True)
            print(f'{base}: supervisor started ({child.pid})')
        try:
            with socket.create_connection(('127.0.0.1', 8080), timeout=2):
                print('Port 8080 is already serving; leaving it in place')
        except OSError:
            with (ROOT / 'jobs/viewer-failed-checks.log').open('ab') as output:
                child = subprocess.Popen([str(Path(sys.executable).parent / 'harbor'),
                                          'view', 'jobs', '--port', '8080'], cwd=ROOT,
                                         stdin=subprocess.DEVNULL, stdout=output,
                                         stderr=subprocess.STDOUT, start_new_session=True)
            (ROOT / 'jobs/viewer-failed-checks.pid').write_text(str(child.pid) + '\n')
            print(f'Jobs viewer started ({child.pid})')
        print('Current memory:', json.dumps(memory_snapshot()))
        print('Trials remain blocked until Docker exposes the configured memory minimum.')


if __name__ == '__main__':
    main()

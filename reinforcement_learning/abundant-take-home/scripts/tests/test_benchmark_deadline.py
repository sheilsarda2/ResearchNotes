#!/usr/bin/env python3
"""Exercise the real Harbor/Docker timeout boundary without contacting a model.

Run inside the existing development container's Harbor Python environment:
  PYTHONPATH=scripts:scripts/tests <harbor-python> \
    scripts/tests/test_benchmark_deadline.py --docker --output <new-directory>

Only containers created for these uniquely named temporary tasks are managed.
The generated tasks and their raw Harbor outputs are retained in --output.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid


BASE_IMAGE = "python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a"

WRITER = r'''import json, os, pathlib, subprocess, sys, time
root = pathlib.Path('/workspace/deadline-evidence')
root.mkdir(parents=True, exist_ok=True)
logs = pathlib.Path('/logs/agent')
logs.mkdir(parents=True, exist_ok=True)
mode = sys.argv[1]
child = r"""import json, os, pathlib, sys, time
root = pathlib.Path('/workspace/deadline-evidence')
label = sys.argv[1]
root.joinpath(label + '.pid').write_text(str(os.getpid()))
root.joinpath(label + '.ready').write_text(str(time.monotonic_ns()))
started = time.monotonic()
while time.monotonic() - started < 8:
    data = json.dumps({'label': label, 'ns': time.monotonic_ns()})
    root.joinpath(label + '.progress').write_text(data)
    pathlib.Path('/logs/agent/' + label + '-trajectory.json').write_text(data)
    if time.monotonic() - started > 1.6:
        root.joinpath(label + '.late').write_text(data)
    time.sleep(.025)
"""
for label, detached in [('child', False), ('new-session', True)]:
    subprocess.Popen([sys.executable, '-c', child, label],
                     start_new_session=detached, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
daemon = r"""import os, subprocess, sys
if os.fork():
    sys.exit(0)
os.setsid()
if os.fork():
    sys.exit(0)
os.execl(sys.executable, sys.executable, '-c', sys.argv[1], 'daemon')
"""
subprocess.run([sys.executable, '-c', daemon, child],
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, check=True)
root.joinpath('parent.pid').write_text(str(os.getpid()))
root.joinpath('parent.ready').write_text(str(time.monotonic_ns()))
print('no-model writer started', flush=True)
if mode == 'normal-background':
    time.sleep(.25)
    sys.exit(0)
time.sleep(8)
root.joinpath('parent.late').write_text(str(time.monotonic_ns()))
'''

VERIFIER = r'''import hashlib, json, pathlib, time
root = pathlib.Path('/workspace/deadline-evidence')
output = pathlib.Path('/logs/verifier')
output.mkdir(parents=True, exist_ok=True)
def snapshot():
    found = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*')) if p.is_file()}
    found.update({'agent/' + p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(pathlib.Path('/logs/agent').glob('*-trajectory.json'))})
    return found
started = time.time_ns()
before = snapshot()
time.sleep(2.0)
after = snapshot()
late = sorted(root.glob('*.late'))
ready = sorted(p.name for p in root.glob('*.ready'))
expected_ready = ['child.ready', 'daemon.ready', 'new-session.ready', 'parent.ready']
checks = {'all_writer_types_started': ready == expected_ready,
          'no_delayed_writes': not late,
          'artifact_stable_during_verifier': before == after}
output.joinpath('deadline-checks.json').write_text(json.dumps({
    'verifier_started_unix_ns': started, 'before': before, 'after': after,
    'late_files': [p.name for p in late], 'ready_files': ready,
    'checks': checks}, indent=2) + '\n')
output.joinpath('reward.txt').write_text(str(int(all(checks.values()))))
'''


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_clients(project: str) -> list[int]:
    """Match only our project; never return or log process arguments."""
    found = []
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = path.read_bytes().split(b'\0')
            if b'docker' in [Path(a.decode(errors='ignore')).name.encode() for a in args[:2]]:
                if project.encode() in args and b'exec' in args:
                    status = path.with_name('status').read_text()
                    if 'Z (zombie)' not in status:
                        found.append(int(path.parent.name))
        except (OSError, ValueError):
            pass
    return sorted(found)


def make_task(path: Path, mode: str) -> None:
    (path / 'environment').mkdir(parents=True)
    (path / 'tests').mkdir()
    (path / 'instruction.md').write_text('Internal no-model orchestration regression fixture.\n')
    (path / 'task.toml').write_text(f'''version = "1.0"
[metadata]
name = "Internal deadline regression fixture"
[agent]
timeout_sec = {0.001 if mode == 'startup-timeout' else 2.0 if mode in ('normal-background', 'cleanup-failure') else 1.0}
[verifier]
timeout_sec = 15
environment_mode = "shared"
[environment]
docker_image = "{BASE_IMAGE}"
network_mode = "no-network"
cpus = 1
memory_mb = 256
build_timeout_sec = 30
workdir = "/workspace"
[[artifacts]]
source = "/workspace/deadline-evidence"
destination = "deadline-evidence"
''')
    (path / 'writer.py').write_text(WRITER)
    verifier = VERIFIER
    if mode == 'startup-timeout':
        verifier = verifier.replace("ready == expected_ready", "ready == []")
        verifier = verifier.replace("'all_writer_types_started'", "'startup_timeout_spawned_no_writer'")
    (path / 'tests' / 'verify.py').write_text(verifier)
    (path / 'tests' / 'test.sh').write_text('#!/bin/bash\nset -eu\npython /tests/verify.py\n')


def load_agent_class():
    """Define the model-free adapter only in a Harbor-enabled interpreter."""
    from harbor.agents.base import BaseAgent

    class DelayedWriterAgent(BaseAgent):
        @staticmethod
        def name():
            return 'deadline-regression-no-model'

        def version(self):
            return '1'

        def __init__(self, *, writer_path, mode, **kwargs):
            super().__init__(**kwargs)
            self.writer_path = Path(writer_path)
            self.mode = mode

        async def setup(self, environment):
            setup_result = await environment.exec('mkdir -p /workspace', cwd='/')
            if setup_result.return_code:
                raise RuntimeError('Fixture setup could not create /workspace')
            await environment.upload_file(self.writer_path, '/workspace/deadline-writer.py')

        async def run(self, instruction, environment, context):
            del instruction, context
            if self.mode == 'buffered-timeout':
                callback = environment._output_callback
                environment._output_callback = lambda: None
                try:
                    await environment.exec('python /workspace/deadline-writer.py timeout')
                finally:
                    environment._output_callback = callback
            else:
                await environment.exec(f'python /workspace/deadline-writer.py {self.mode}')

    return DelayedWriterAgent


try:
    DelayedWriterAgent = load_agent_class()
except ImportError:
    DelayedWriterAgent = None


async def run_case(output: Path, mode: str, guarded: bool) -> dict:
    from harbor.models.trial.config import TrialConfig
    from harbor.trial.trial import Trial

    folder = output / mode
    task = folder / 'task'
    make_task(task, mode)
    name = 'deadline-test-' + mode[:12] + '-' + uuid.uuid4().hex[:8]
    config = TrialConfig.model_validate({
        'task': {'path': task}, 'trials_dir': folder / 'trials', 'trial_name': name,
        'agent': {'import_path': 'test_benchmark_deadline:DelayedWriterAgent',
                  'kwargs': {'writer_path': str(task / 'writer.py'),
                             'mode': 'normal-background' if mode == 'cleanup-failure' else mode}},
        'environment': {'type': 'docker', 'delete': True},
    })
    trial = await Trial.create(config)
    boundaries = []
    for method_name in ('_sync_agent_output', '_collect_artifacts', '_run_verifier'):
        original = getattr(trial, method_name)

        async def observed_boundary(*args, _original=original, _name=method_name, **kwargs):
            boundaries.append({'method': _name, 'unix_ns': time.time_ns(),
                               'host_exec_client_pids': host_clients(name)})
            return await _original(*args, **kwargs)

        setattr(trial, method_name, observed_boundary)
    started = now()
    before = time.monotonic()
    if mode == 'cleanup-failure':
        import benchmark_deadline
        original_close = benchmark_deadline.close_phase

        async def reject_cleanup_proof(*args, **kwargs):
            # Actually clean our fixture children, then exercise fail-closed
            # handling as if the controller could not validate that proof.
            await original_close(*args, **kwargs)
            raise benchmark_deadline.AgentQuiescenceError('Injected missing cleanup proof')

        benchmark_deadline.close_phase = reject_cleanup_proof
        try:
            try:
                result = await trial.run()
            except benchmark_deadline.AgentQuiescenceError:
                # Harbor recovery may propagate the same fail-closed error.
                # Finalization must still have written an unscored result.
                result = trial.result
        finally:
            benchmark_deadline.close_phase = original_close
    else:
        result = await trial.run()
    elapsed = time.monotonic() - before
    result_path = trial.paths.trial_dir / 'result.json'
    check_path = trial.paths.verifier_dir / 'deadline-checks.json'
    check = json.loads(check_path.read_text()) if check_path.exists() else {}
    clients = host_clients(name)
    reward = (result.verifier_result.rewards or {}).get('reward') if result.verifier_result else None
    exception = result.exception_info.exception_type if result.exception_info else None
    cleanup_failure = mode == 'cleanup-failure'
    expected_exception = ('AgentQuiescenceError' if cleanup_failure else
                          'AgentTimeoutError' if mode in ('timeout', 'startup-timeout', 'buffered-timeout') else None)
    assertions = {
        'verifier_reward_expected': reward is None if cleanup_failure else reward == (1 if guarded else 0),
        'no_surviving_host_exec_clients': not clients,
        'expected_agent_outcome': exception == expected_exception,
        'verifier_execution_expected': not check if cleanup_failure else bool(check),
    }
    if guarded:
        assertions.update(check.get('checks', {}))
        assertions['no_host_exec_clients_at_collection_or_verifier'] = all(
            not boundary['host_exec_client_pids'] for boundary in boundaries)
        deadline_path = trial.paths.trial_dir / 'benchmark-deadline.json'
        deadline_state = json.loads(deadline_path.read_text()) if deadline_path.exists() else {}
        assertions['cleanup_proof_expected'] = deadline_state.get('quiescent') is (not cleanup_failure)
        if not cleanup_failure:
            cleanup_time = deadline_state.get('cleanup_finished_at_epoch', float('inf')) * 1e9
            assertions['cleanup_preceded_collection_and_verification'] = all(
                cleanup_time <= boundary['unix_ns'] for boundary in boundaries)
            executions = deadline_state.get('executions', [])
            assertions['all_executions_proved_quiescent'] = all(
                execution.get('quiescent') is True for execution in executions)
            if mode != 'startup-timeout':
                assertions['execution_proof_present'] = bool(executions)
        else:
            assertions['verifier_never_entered'] = all(
                boundary['method'] != '_run_verifier' for boundary in boundaries)
            assertions['grading_snapshot_withheld'] = not (
                trial.paths.trial_dir / 'benchmark-snapshot.json').exists()
    row = {'mode': mode, 'guarded': guarded, 'started_at': started,
           'elapsed_sec': elapsed, 'trial_name': name,
           'reward': reward, 'exception': exception,
           'phase_boundaries': boundaries,
           'surviving_host_exec_client_pids': clients, 'assertions': assertions,
           'passed': all(assertions.values()),
           'result_path': str(result_path), 'result_sha256': sha256(result_path),
           'verifier_evidence': str(check_path), 'verifier_checks': check}
    (folder / 'summary.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


async def main_async(args) -> int:
    if args.output.exists():
        raise SystemExit('--output must be a fresh directory; raw evidence is never overwritten')
    args.output.mkdir(parents=True)
    repository = Path(__file__).resolve().parents[2]
    sources = ['scripts/benchmark_deadline.py', 'scripts/benchmark_process_guard.py',
               'scripts/benchmark_evidence.py', 'scripts/tests/test_benchmark_deadline.py']
    source_hashes = {name: sha256(repository / name) for name in sources}
    for name in sources:
        target = args.output / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repository / name).read_bytes())
    if not args.unguarded:
        from benchmark_deadline import install
        install()
    rows = []
    modes = ('timeout', 'normal-background', 'buffered-timeout') if args.unguarded else (
        'timeout', 'normal-background', 'buffered-timeout', 'startup-timeout', 'cleanup-failure')
    for mode in modes:
        row = await run_case(args.output.resolve(), mode, guarded=not args.unguarded)
        rows.append(row)
        print(json.dumps({'mode': mode, 'passed': row['passed'],
                          'reward': row['reward'], 'exception': row['exception']}), flush=True)
    sources_unchanged = all(sha256(repository / name) == digest for name, digest in source_hashes.items())
    summary = {'created_at': now(), 'model_calls': 0, 'base_image': BASE_IMAGE,
               'guarded': not args.unguarded, 'cases': rows,
               'passed': all(row['passed'] for row in rows) and sources_unchanged,
               'overall_passed': all(row['passed'] for row in rows) and sources_unchanged,
               'source_files_unchanged': sources_unchanged,
               'source_sha256': source_hashes,
               'harness_sha256': source_hashes['scripts/tests/test_benchmark_deadline.py']}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['passed'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true', help='Explicitly run disposable Docker trials')
    parser.add_argument('--unguarded', action='store_true', help='Demonstrate the upstream failure in a separate process')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not args.docker:
        parser.error('--docker is required; this script does not contact any model')
    if DelayedWriterAgent is None:
        parser.error('Use the Harbor-installed Python interpreter')
    return asyncio.run(main_async(args))


if __name__ == '__main__':
    raise SystemExit(main())

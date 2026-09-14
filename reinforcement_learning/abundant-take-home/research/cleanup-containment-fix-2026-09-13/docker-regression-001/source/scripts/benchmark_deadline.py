"""Process-local Harbor fixes for single-step Linux Docker benchmarks.

Install before starting trials. Supplied tasks, budgets and Harbor package files
are untouched. Evidence is additive; old result directories are never rewritten.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import functools
import hashlib
import json
from pathlib import Path
import shlex
import time
import uuid


class AgentQuiescenceError(RuntimeError):
    """Grading is unsafe because agent process cleanup was not confirmed."""


async def _finish(coroutine):
    """Complete bounded cleanup even if the caller is cancelled again."""
    task = asyncio.create_task(coroutine)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def close_phase(execute, command):
    result = await execute(command=command, user='root', timeout_sec=20)
    if result.return_code:
        raise AgentQuiescenceError('Container cleanup command failed; grading withheld')
    data = json.loads(result.stdout)
    if data.get('quiescent') is not True:
        raise AgentQuiescenceError('Container did not confirm quiescence; grading withheld')
    return data


def require_quiescence(trial):
    state = getattr(trial, '_benchmark_deadline_state', None)
    if state is not None and state.get('quiescent') is not True:
        raise AgentQuiescenceError('Agent quiescence unconfirmed; evidence collection and grading withheld')


def install():
    from harbor.environments.docker.docker import DockerEnvironment
    from harbor.trial.single_step import SingleStepTrial
    from harbor.trial.trial import Trial
    from harbor.trial.errors import AgentTimeoutError

    if getattr(Trial, '_benchmark_deadline_installed', False):
        return

    buffered = DockerEnvironment._collect_buffered_output

    @staticmethod
    async def buffered_with_cleanup(process, *, timeout_sec):
        try:
            return await buffered(process, timeout_sec=timeout_sec)
        except BaseException:
            if process.returncode is None:
                await _finish(DockerEnvironment._terminate_process(process))
            raise

    DockerEnvironment._collect_buffered_output = buffered_with_cleanup
    original_phase = Trial._run_agent_phase

    @functools.wraps(original_phase)
    async def guarded_phase(trial, *, target, instruction, timeout_sec, user, step_cfg=None):
        if not isinstance(trial, SingleStepTrial):
            raise AgentQuiescenceError('Deadline guard currently supports single-step trials only')
        environment = trial.agent_environment
        if not isinstance(environment, DockerEnvironment) or environment._is_windows_container:
            raise AgentQuiescenceError('Deadline guard requires a Linux Docker environment')
        execute = environment.exec
        state = {'version': 1, 'quiescent': False, 'timeout_sec': timeout_sec}
        trial._benchmark_deadline_state = state
        remote = '/tmp/benchmark-guard-' + uuid.uuid4().hex
        helper = Path(__file__).with_name('benchmark_process_guard.py')
        state['helper_sha256'] = hashlib.sha256(helper.read_bytes()).hexdigest()
        setup = await execute(command=f'mkdir -m 1777 {remote}', user='root', timeout_sec=20)
        if setup.return_code:
            raise AgentQuiescenceError('Could not prepare execution guard')
        await environment.upload_file(helper, remote + '/guard.py')
        probe = await execute(command='command -v python3 || command -v python', user='root', timeout_sec=20)
        python = (probe.stdout or '').strip()
        if probe.return_code or not python.startswith('/') or '\n' in python:
            raise AgentQuiescenceError('Execution guard requires Python in the container')
        prefix = shlex.quote(python) + ' ' + remote + '/guard.py'
        # Start the watchdog inside Harbor's existing wait_for, after network
        # setup, so its setup overhead does not consume the agent's time budget.
        deadline = None
        agent_run = trial.agent.run

        async def timed_agent_run(*args, **kwargs):
            nonlocal deadline
            started = time.time()
            deadline = started + timeout_sec if timeout_sec is not None else None
            state.update(started_at_epoch=started, deadline_epoch=deadline)
            try:
                return await agent_run(*args, **kwargs)
            finally:
                state['agent_run_finished_at_epoch'] = time.time()

        trial.agent.run = timed_agent_run
        active_calls = set()

        async def guarded_exec(command, **kwargs):
            execution = uuid.uuid4().hex
            wrapped = f'{prefix} run --phase {remote} --execution {execution}'
            if deadline is not None:
                wrapped += f' --deadline {deadline!r}'
            wrapped += ' --command ' + shlex.quote(command)
            active_calls.add(asyncio.current_task())
            try:
                result = await execute(command=wrapped, **kwargs)
                if result.return_code == 124 and deadline is not None and time.time() >= deadline:
                    raise AgentTimeoutError(f'Agent execution timed out after {timeout_sec} seconds')
                return result
            finally:
                active_calls.discard(asyncio.current_task())

        environment.exec = guarded_exec
        try:
            await original_phase(trial, target=target, instruction=instruction,
                                 timeout_sec=timeout_sec, user=user, step_cfg=step_cfg)
            if deadline is not None and state.get('agent_run_finished_at_epoch', 0) >= deadline:
                raise AgentTimeoutError(f'Agent execution timed out after {timeout_sec} seconds')
        finally:
            # Cancel any exec launched as a background asyncio task as well.
            environment.exec = execute
            trial.agent.run = agent_run
            for task in list(active_calls):
                task.cancel()
            try:
                state.update(await _finish(close_phase(execute, f'{prefix} close --phase {remote}')))
                if active_calls:
                    await _finish(asyncio.wait_for(asyncio.gather(*list(active_calls), return_exceptions=True), 10))
            except BaseException as exc:
                state.update(quiescent=False, cleanup_error=type(exc).__name__)
                raise AgentQuiescenceError('Agent cleanup failed; grading withheld') from exc
            finally:
                state['cleanup_finished_at_epoch'] = time.time()
                path = trial.paths.trial_dir / 'benchmark-deadline.json'
                with path.open('x') as output:
                    json.dump(state, output, indent=2)
                    output.write('\n')

    Trial._run_agent_phase = guarded_phase
    original_run = Trial.run

    async def run_with_marker(trial):
        marker = {'version': 1, 'scope': 'single-step-linux-docker',
                  'runtime_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'trial_containment_sha256': hashlib.sha256(Path(__file__).with_name('benchmark_trial_containment.py').read_bytes()).hexdigest(),
                  'process_guard_sha256': hashlib.sha256(Path(__file__).with_name('benchmark_process_guard.py').read_bytes()).hexdigest()}
        with (trial.paths.trial_dir / 'benchmark-runtime.json').open('x') as output:
            json.dump(marker, output, indent=2)
            output.write('\n')
        from benchmark_trial_containment import run_contained
        return await run_contained(trial, original_run)

    Trial.run = run_with_marker
    for name in ('_sync_agent_output', '_collect_artifacts_phased'):
        original = getattr(Trial, name)

        def wrap(method):
            @functools.wraps(method)
            async def checked(trial, *args, **kwargs):
                require_quiescence(trial)
                return await method(trial, *args, **kwargs)
            return checked

        setattr(Trial, name, wrap(original))

    original_verifier = SingleStepTrial._run_verifier

    async def checked_verifier(trial):
        require_quiescence(trial)
        from benchmark_evidence import snapshot_paths, write_snapshot
        snapshot = snapshot_paths(trial.paths.trial_dir, ['agent', 'artifacts'])
        write_snapshot(trial.paths.trial_dir, snapshot)
        trial._benchmark_evidence_snapshot = snapshot
        await original_verifier(trial)
        if snapshot_paths(trial.paths.trial_dir, ['agent', 'artifacts']) != snapshot:
            raise AgentQuiescenceError('Evidence changed during verification; result requires exclusion')

    SingleStepTrial._run_verifier = checked_verifier
    original_finalize = Trial._finalize

    async def finalize_with_evidence(trial):
        await original_finalize(trial)
        if not isinstance(trial, SingleStepTrial):
            return
        from benchmark_evidence import write_evidence
        state = getattr(trial, '_benchmark_deadline_state', {})
        deadline = state.get('deadline_epoch')
        write_evidence(trial.paths.trial_dir,
                       expected_snapshot=getattr(trial, '_benchmark_evidence_snapshot', None),
                       deadline_at=datetime.fromtimestamp(deadline, timezone.utc).isoformat() if deadline else None)

    Trial._finalize = finalize_with_evidence
    Trial._benchmark_deadline_installed = True

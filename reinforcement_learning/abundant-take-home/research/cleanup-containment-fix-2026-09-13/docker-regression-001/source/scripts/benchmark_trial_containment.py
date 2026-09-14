"""Keep a finalized, ungraded cleanup failure local to its Harbor trial.

Harbor's recovery path can raise the same guard error that caused recovery.
Only that error is contained, after END hooks, persisted failure, and independent
Docker teardown confirmation. Unknown errors and cancellation still propagate.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json


async def confirm_project_stopped(trial):
    from harbor.environments.docker.docker import (
        DockerEnvironment, _sanitize_docker_compose_project_name,
    )

    environment = trial.agent_environment
    if not isinstance(environment, DockerEnvironment):
        return None
    project = _sanitize_docker_compose_project_name(environment.session_id)
    if not project:
        return None
    process = await asyncio.create_subprocess_exec(
        'docker', 'ps', '--filter', f'label=com.docker.compose.project={project}',
        '--format', '{{.ID}}', stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode or stdout.strip():
        return None
    return project


def finished_ungraded_failure(trial, end_emitted):
    """Return the exact saved result only when finalization is complete."""
    state = getattr(trial, '_benchmark_deadline_state', None)
    if (not end_emitted or not isinstance(state, dict)
            or state.get('quiescent') is not False
            or not getattr(trial, '_is_agent_environment_stopped', False)):
        return None
    result = trial.result
    saved_bytes = trial.paths.result_path.read_bytes()
    saved = json.loads(saved_bytes)
    current = json.loads(result.model_dump_json())
    if saved != current:
        return None
    if (not saved.get('finished_at') or not saved.get('exception_info')
            or saved.get('verifier_result') is not None
            or (saved.get('verifier') or {}).get('started_at')):
        return None
    return saved_bytes


async def run_contained(trial, original_run):
    from benchmark_deadline import AgentQuiescenceError
    from harbor.trial.hooks import TrialEvent

    end_emitted = False

    async def ended(_event):
        nonlocal end_emitted
        end_emitted = True

    # This is appended after the queue's hooks, so its completion also proves
    # the normal job bookkeeping already consumed exactly one END event.
    trial.add_hook(TrialEvent.END, ended)
    try:
        return await original_run(trial)
    except AgentQuiescenceError:
        saved_bytes = finished_ungraded_failure(trial, end_emitted)
        if saved_bytes is None:
            raise
        project = await confirm_project_stopped(trial)
        if project is None:
            raise
        marker = {
            'version': 1,
            'recorded_at': datetime.now(timezone.utc).isoformat(),
            'reason': 'finalized_ungraded_guard_failure',
            'escaped_exception_type': 'AgentQuiescenceError',
            'result_sha256': hashlib.sha256(saved_bytes).hexdigest(),
            'end_hooks_completed': True,
            'compose_project': project,
            'running_project_containers': 0,
            'grading_withheld': True,
        }
        with (trial.paths.trial_dir / 'benchmark-containment.json').open('x') as output:
            json.dump(marker, output, indent=2)
            output.write('\n')
        return trial.result

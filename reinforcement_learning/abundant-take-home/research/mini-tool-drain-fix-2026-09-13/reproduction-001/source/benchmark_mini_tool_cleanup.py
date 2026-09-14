"""Bound Mini 2.4.6's output drain after its existing tool timeout.

Install inside the agent interpreter. No package file is changed. The original
command timeout and process-group kill are preserved; unrelated/background
process groups are left to the benchmark's outer quiescence guard.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import signal
import subprocess
import sys
import time


REVIEWED_RUN_SHA256 = '47f7f72e224b860180c0b6ff6ddb205fe52fe5e66f07145ce3d95c7375df8e6b'
POST_KILL_DRAIN_SEC = 1.0


def _text(output):
    if isinstance(output, bytes):
        return output.decode('utf-8', errors='replace')
    return output or ''


def bounded_run(command: str, cwd: str, env: dict[str, str],
                timeout: int) -> subprocess.CompletedProcess[str]:
    """Keep the upstream behavior except for its unbounded post-kill drain."""
    process = subprocess.Popen(
        command, shell=True, text=True, cwd=cwd, env=env,
        encoding='utf-8', errors='replace', stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=os.name == 'posix',
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as original_timeout:
        # Use precisely the same signal scope as upstream. A detached service
        # can retain the pipe after this group dies; do not widen the kill.
        os.killpg(process.pid, signal.SIGKILL) if os.name == 'posix' else process.kill()
        drain_deadline = time.monotonic() + POST_KILL_DRAIN_SEC
        try:
            stdout, _ = process.communicate(timeout=POST_KILL_DRAIN_SEC)
        except subprocess.TimeoutExpired as drain_timeout:
            # communicate's exception contains the cumulative output, including
            # the first call's bytes. Concatenating the two would duplicate it.
            partial = drain_timeout.output
            stdout = _text(partial if partial is not None else original_timeout.output)
            if process.stdout is not None:
                process.stdout.close()
            # Reap the shell within the same cleanup budget. A surviving child
            # cannot make Popen cleanup wait indefinitely on an output pipe.
            try:
                process.wait(timeout=max(0, drain_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        original_timeout.output = stdout
        raise original_timeout
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)


def install():
    """Patch only the reviewed Linux Mini function, in this interpreter."""
    if sys.platform != 'linux':
        raise RuntimeError('Mini tool-drain guard requires Linux')
    from minisweagent.environments import local

    if local._run is bounded_run:
        return
    source = inspect.getsource(local._run).encode('utf-8')
    if hashlib.sha256(source).hexdigest() != REVIEWED_RUN_SHA256:
        raise RuntimeError('Unsupported Mini LocalEnvironment._run implementation')
    local._run = bounded_run

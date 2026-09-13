"""Install mini-swe-agent with a compatible, explicit Python interpreter.

This process-local hook changes the agent's uv tool environment only. The task's
system Python, source files, verifier and time/resource limits are unchanged.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
import shlex


PYTHON_VERSION = '3.12.11'
PREFLIGHT_PREFIX = 'BENCHMARK_AGENT_RUNTIME='
PREFLIGHT_CODE = '''import importlib.metadata, json, platform, sys
from minisweagent.models.litellm_model import LitellmModel
assert platform.python_version() == "3.12.11", "Unexpected agent Python version"
packages = {name: importlib.metadata.version(name) for name in ("mini-swe-agent", "litellm")}
for name in ("anthropic", "openai", "pydantic"):
    try:
        packages[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        packages[name] = None
print("BENCHMARK_AGENT_RUNTIME=" + json.dumps({"python": platform.python_version(),
      "executable": sys.executable, "packages": packages, "litellm_import_ok": True}))
'''


class BenchmarkAgentPreflightError(RuntimeError):
    """Agent dependencies could not load before model execution."""


def pin_install_command(command: str) -> str:
    needle = 'uv tool install mini-swe-agent'
    if command.count(needle) != 1:
        raise BenchmarkAgentPreflightError('Unsupported Harbor mini-swe-agent install command')
    return command.replace(needle, f'uv tool install --python {PYTHON_VERSION} mini-swe-agent', 1)


def preflight_command() -> str:
    return ('set -euo pipefail; . "$HOME/.local/bin/env"; '
            'benchmark_tool_dir="$(uv tool dir)"; '
            '"$benchmark_tool_dir/mini-swe-agent/bin/python" -c ' + shlex.quote(PREFLIGHT_CODE))


def install():
    from harbor.agents.installed.mini_swe_agent import MiniSweAgent

    if getattr(MiniSweAgent, '_benchmark_python_installed', False):
        return
    original = MiniSweAgent.install

    @functools.wraps(original)
    async def install_with_python(agent, environment):
        execute = agent.exec_as_agent
        patched_commands = 0

        async def pinned_exec(environment, command, *args, **kwargs):
            nonlocal patched_commands
            if 'uv tool install ' in command:
                command = pin_install_command(command)
                patched_commands += 1
            return await execute(environment, command, *args, **kwargs)

        agent.exec_as_agent = pinned_exec
        try:
            await original(agent, environment)
        finally:
            agent.exec_as_agent = execute
        if patched_commands != 1:
            raise BenchmarkAgentPreflightError('Agent installation did not use the pinned Python')
        metadata = {'schema_version': 1, 'requested_python': PYTHON_VERSION,
                    'runtime_patch_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'preflight_passed': False}
        try:
            # Import the actual backend, not just CLI --help. No model is
            # constructed, and no generation/API request is made by this check.
            result = await execute(environment, command=preflight_command(),
                                   env={'LITELLM_LOCAL_MODEL_COST_MAP': 'True'})
            lines = [line[len(PREFLIGHT_PREFIX):] for line in (result.stdout or '').splitlines()
                     if line.startswith(PREFLIGHT_PREFIX)]
            if len(lines) != 1:
                raise BenchmarkAgentPreflightError('Agent preflight metadata is missing or ambiguous')
            observed = json.loads(lines[0])
            if observed.get('python') != PYTHON_VERSION or observed.get('litellm_import_ok') is not True:
                raise BenchmarkAgentPreflightError('Agent preflight did not confirm compatible dependencies')
            metadata.update(observed, preflight_passed=True)
        except Exception as error:
            metadata['error_type'] = type(error).__name__
            raise BenchmarkAgentPreflightError('Agent backend import failed during setup; model execution withheld') from error
        finally:
            agent.logs_dir.mkdir(parents=True, exist_ok=True)
            with (agent.logs_dir / 'benchmark-agent-runtime.json').open('x') as output:
                json.dump(metadata, output, indent=2)
                output.write('\n')

    MiniSweAgent.install = install_with_python
    MiniSweAgent._benchmark_python_installed = True

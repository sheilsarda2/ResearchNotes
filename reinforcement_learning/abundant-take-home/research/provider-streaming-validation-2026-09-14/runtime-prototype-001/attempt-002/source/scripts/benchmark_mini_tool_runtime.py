"""Load the bounded Mini tool cleanup fix in future agent processes only.

This wraps our existing task-file transport without changing that module or the
installed Harbor/Mini files. Helper payloads and hashes are frozen at install().
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
import shlex
import tempfile

from benchmark_agent_runtime import (BenchmarkAgentPreflightError, LAUNCH_PREFIX,
                                     LAUNCH_SUFFIX, install as install_base_runtime)

PREFLIGHT_PREFIX = 'BENCHMARK_MINI_TOOL_RUNTIME='
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def runtime_location(command: str) -> str:
    """Require the base hook's final private task config, with no inline task."""
    if not command.startswith(LAUNCH_PREFIX) or not command.endswith(LAUNCH_SUFFIX):
        raise BenchmarkAgentPreflightError('Unsupported file-based Mini launch')
    tokens = shlex.split(command)
    if any(t == '--task' or t.startswith('--task=') or t == '-t' for t in tokens):
        raise BenchmarkAgentPreflightError('Tool runtime requires prior task-file transport')
    configs = [tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token in ('-c', '--config')]
    if not configs:
        raise BenchmarkAgentPreflightError('Missing private Mini task config')
    path = Path(configs[-1])
    if (path.name != 'task.yaml' or path.parent.parent != Path('/tmp') or
            not path.parent.name.startswith('benchmark-agent-input-') or
            len(path.parent.name) != len('benchmark-agent-input-') + 32 or
            any(c not in '0123456789abcdef' for c in path.parent.name[-32:])):
        raise BenchmarkAgentPreflightError('Expected neutral private Mini task config')
    return str(path.parent)


def bootstrap_command(remote: str) -> str:
    return ('. "$HOME/.local/bin/env"; benchmark_tool_dir="$(uv tool dir)"; '
            '"$benchmark_tool_dir/mini-swe-agent/bin/python" ' + shlex.quote(remote + '/b.py'))


def launch_command(command: str, remote: str) -> str:
    if runtime_location(command) != remote:
        raise BenchmarkAgentPreflightError('Task and runtime directories differ')
    return bootstrap_command(remote) + ' ' + command[len(LAUNCH_PREFIX):]


def install():
    from harbor.agents.installed.mini_swe_agent import MiniSweAgent

    install_base_runtime()
    if getattr(MiniSweAgent, '_benchmark_tool_runtime_installed', False):
        return
    original_run = MiniSweAgent.run
    folder = Path(__file__).parent
    payloads = {'b.py': (folder / 'benchmark_mini_tool_bootstrap.py').read_bytes(),
                'c.py': (folder / 'benchmark_mini_tool_cleanup.py').read_bytes()}
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}

    @functools.wraps(original_run)
    async def run_with_tool_runtime(agent, *args, **kwargs):
        execute = agent.exec_as_agent
        launches = 0

        async def runtime_exec(environment, command, *exec_args, **exec_kwargs):
            nonlocal launches
            if not command.startswith(LAUNCH_PREFIX):
                return await execute(environment, command, *exec_args, **exec_kwargs)
            if launches:
                raise BenchmarkAgentPreflightError('Unexpected second Mini runtime launch')
            remote = runtime_location(command)
            launches += 1
            metadata = {'schema_version': 1, 'runtime_wrapper_sha256': SOURCE_SHA256,
                        'bootstrap_sha256': digests['b.py'], 'helper_sha256': digests['c.py'],
                        'scope': 'agent_process_only', 'phase': 'tool_runtime_preflight_before_mini_cli',
                        'preflight_passed': False}
            try:
                with tempfile.TemporaryDirectory(prefix='benchmark-tool-runtime-') as temporary:
                    for name, data in payloads.items():
                        path = Path(temporary) / name
                        path.write_bytes(data)
                        path.chmod(0o644)
                        await environment.upload_file(path, remote + '/' + name)
                result = await execute(environment,
                                       command=bootstrap_command(remote) + ' --benchmark-preflight',
                                       env={'LITELLM_LOCAL_MODEL_COST_MAP': 'True'}, timeout_sec=30)
                lines = [line[len(PREFLIGHT_PREFIX):] for line in (result.stdout or '').splitlines()
                         if line.startswith(PREFLIGHT_PREFIX)]
                if result.return_code != 0 or len(lines) != 1:
                    raise BenchmarkAgentPreflightError('Tool runtime preflight did not complete')
                observed = json.loads(lines[0])
                if (observed.get('installed') is not True or observed.get('python') != '3.12.11' or
                        observed.get('package_version') != '2.4.6' or
                        observed.get('bootstrap_sha256') != digests['b.py'] or
                        observed.get('helper_sha256') != digests['c.py']):
                    raise BenchmarkAgentPreflightError('Tool runtime preflight identity mismatch')
                metadata.update(observed, preflight_passed=True)
            except Exception as error:
                metadata['error_type'] = type(error).__name__
                raise BenchmarkAgentPreflightError('Tool runtime preflight failed; model execution withheld') from error
            finally:
                agent.logs_dir.mkdir(parents=True, exist_ok=True)
                with (agent.logs_dir / 'benchmark-agent-tool-runtime.json').open('x') as output:
                    json.dump(metadata, output, indent=2)
                    output.write('\n')
            return await execute(environment, launch_command(command, remote), *exec_args, **exec_kwargs)

        agent.exec_as_agent = runtime_exec
        try:
            result = await original_run(agent, *args, **kwargs)
            if launches != 1:
                raise BenchmarkAgentPreflightError('Mini tool runtime was not loaded')
            return result
        finally:
            agent.exec_as_agent = execute

    MiniSweAgent.run = run_with_tool_runtime
    MiniSweAgent._benchmark_tool_runtime_installed = True

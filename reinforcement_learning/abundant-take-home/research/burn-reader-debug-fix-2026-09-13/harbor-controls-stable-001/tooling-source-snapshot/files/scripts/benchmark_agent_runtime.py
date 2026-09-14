"""Pin agent Python and keep task instructions out of process arguments.

Process-local hooks change the agent's uv environment and input transport. Task
text, system Python, source files, verifier and time/resource limits are preserved.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import uuid


PYTHON_VERSION = '3.12.11'
PREFLIGHT_PREFIX = 'BENCHMARK_AGENT_RUNTIME='
LAUNCH_PREFIX = '. "$HOME/.local/bin/env"; mini-swe-agent '
LAUNCH_SUFFIX = '--exit-immediately 2>&1 </dev/null | tee /logs/agent/mini-swe-agent.txt'
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


def task_file_command(command: str, config_path: str) -> tuple[str, bytes]:
    """Move the exact CLI task into mini 2.4.6's final config specification.

    Mini has no task-file flag. Its YAML config loader accepts JSON syntax, and
    run.task is used verbatim when --task is absent. The last config preserves
    --task's precedence over user-supplied run.task settings. A neutral filename
    keeps task words out of the agent, bash, Docker and process-guard argv.
    """
    if not command.startswith(LAUNCH_PREFIX) or not command.endswith(LAUNCH_SUFFIX):
        raise BenchmarkAgentPreflightError('Unsupported Harbor mini-swe-agent launch command')
    tokens = shlex.split(command)
    tasks = [token[len('--task='):] for token in tokens if token.startswith('--task=')]
    if len(tasks) != 1 or not tasks[0]:
        raise BenchmarkAgentPreflightError('Expected one nonempty inline agent task')
    instruction = tasks[0]
    inline = '--task=' + shlex.quote(instruction)
    if command.count(inline) != 1:
        raise BenchmarkAgentPreflightError('Agent task shell quoting is unsupported')
    if not config_path.startswith('/tmp/benchmark-agent-input-') or not config_path.endswith('/task.yaml'):
        raise BenchmarkAgentPreflightError('Expected a neutral agent task config path')
    has_config = any(token in ('-c', '--config') or token.startswith('--config=') for token in tokens)
    rewritten = command.replace(inline, '' if has_config else '-c mini', 1)
    rewritten = rewritten[:-len(LAUNCH_SUFFIX)] + '-c ' + shlex.quote(config_path) + ' ' + LAUNCH_SUFFIX
    # Keep non-BMP characters literal: YAML's JSON-compatible parser does not
    # necessarily recombine JSON surrogate-pair escapes into one character.
    payload = json.dumps({'run': {'task': instruction}}, ensure_ascii=False).encode('utf-8') + b'\n'
    return rewritten, payload


def install():
    from harbor.agents.installed.mini_swe_agent import MiniSweAgent

    if getattr(MiniSweAgent, '_benchmark_python_installed', False):
        return
    original = MiniSweAgent.install
    original_run = MiniSweAgent.run

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

    @functools.wraps(original_run)
    async def run_with_task_file(agent, *args, **kwargs):
        execute = agent.exec_as_agent
        rewritten_launches = 0

        async def file_exec(environment, command, *exec_args, **exec_kwargs):
            nonlocal rewritten_launches
            if not command.startswith(LAUNCH_PREFIX):
                return await execute(environment, command, *exec_args, **exec_kwargs)
            if rewritten_launches:
                raise BenchmarkAgentPreflightError('Unexpected second agent launch')
            remote_dir = '/tmp/benchmark-agent-input-' + uuid.uuid4().hex
            remote = remote_dir + '/task.yaml'
            rewritten, payload = task_file_command(command, remote)
            rewritten_launches += 1
            await execute(environment, command='install -d -m 700 ' + shlex.quote(remote_dir))
            with tempfile.TemporaryDirectory(prefix='benchmark-agent-input-') as temporary:
                source = Path(temporary) / 'task.yaml'
                source.write_bytes(payload)
                # upload_file may copy as root; the private directory belongs
                # to the agent, and readable file mode supports non-root users.
                source.chmod(0o644)
                await environment.upload_file(source, remote)
            task_bytes = json.loads(payload)['run']['task'].encode('utf-8')
            metadata = {'schema_version': 1, 'delivery': 'final_run_task_config',
                        'task_sha256': hashlib.sha256(task_bytes).hexdigest(),
                        'task_utf8_bytes': len(task_bytes),
                        'config_sha256': hashlib.sha256(payload).hexdigest()}
            agent.logs_dir.mkdir(parents=True, exist_ok=True)
            with (agent.logs_dir / 'benchmark-agent-input.json').open('x') as output:
                json.dump(metadata, output, indent=2)
                output.write('\n')
            return await execute(environment, rewritten, *exec_args, **exec_kwargs)

        agent.exec_as_agent = file_exec
        try:
            result = await original_run(agent, *args, **kwargs)
            if rewritten_launches != 1:
                raise BenchmarkAgentPreflightError('Agent task was not delivered through a file')
            return result
        finally:
            agent.exec_as_agent = execute

    MiniSweAgent.run = run_with_task_file
    MiniSweAgent._benchmark_python_installed = True

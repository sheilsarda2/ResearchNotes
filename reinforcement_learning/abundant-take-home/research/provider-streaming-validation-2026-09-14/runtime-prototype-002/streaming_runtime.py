"""Explicit outer wrapper for a newly launched process, never live activation."""
import functools
import hashlib
import json
from pathlib import Path
import re
import shlex
import tempfile

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def freeze_payloads():
    files = {name: (HERE / name).read_bytes() for name in
             ('checked_streaming_model.py', 'streaming_bootstrap.py')}
    files['checked_anthropic_iterator.py'] = (BASE / 'isolated-correction/checked_anthropic_iterator.py').read_bytes()
    packages = dict(mini_platformdirs_file_sha256=json.loads((BASE / 'source-identity.json').read_text())['file_sha256'],
                    litellm_file_sha256=json.loads((BASE / 'execution-identity.json').read_text())['litellm_file_sha256'])
    files['package-identity.json'] = (json.dumps(packages, sort_keys=True) + '\n').encode()
    hashes = {name: digest(data) for name, data in files.items()}
    hashes.update({remote: digest((ROOT / 'scripts' / source).read_bytes()) for remote, source in
                   [('b.py', 'benchmark_mini_tool_bootstrap.py'), ('c.py', 'benchmark_mini_tool_cleanup.py')]})
    files['streaming-manifest.json'] = (json.dumps(dict(schema_version=1, private_file_sha256=hashes), sort_keys=True) + '\n').encode()
    return files


def final_location(command, tool):
    candidates = re.findall(r'/tmp/benchmark-agent-input-[0-9a-f]{32}/b\.py', command)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise RuntimeError('Ambiguous private bootstrap command')
    remote = str(Path(candidates[0]).parent)
    prefix = tool.bootstrap_command(remote)
    if command == prefix + ' --benchmark-preflight':
        return None
    if not command.startswith(prefix + ' ') or not command.endswith(tool.LAUNCH_SUFFIX):
        raise RuntimeError('Unsupported final tool-bootstrap launch')
    if '--model-class' in shlex.split(command) or 'model.model_class=' in command:
        raise RuntimeError('Do not silently replace an existing model class')
    return remote


def install(*, agent_class=None, tool_runtime=None):
    if agent_class is None:
        from harbor.agents.installed.mini_swe_agent import MiniSweAgent
        agent_class = MiniSweAgent
    if tool_runtime is None:
        import benchmark_mini_tool_runtime as tool_runtime
    if not getattr(agent_class, '_benchmark_tool_runtime_installed', False):
        raise RuntimeError('Install the unchanged tool runtime before the explicit outer wrapper')
    if getattr(agent_class, '_checked_streaming_runtime_installed', False):
        raise RuntimeError('Streaming runtime installation is single-use')
    payloads = freeze_payloads()
    original_run = agent_class.run

    @functools.wraps(original_run)
    async def run(agent, *args, **kwargs):
        original_exec = agent.exec_as_agent
        launches = 0

        async def execute(environment, command, *values, **options):
            nonlocal launches
            remote = final_location(command, tool_runtime)
            if remote is None:
                return await original_exec(environment, command, *values, **options)
            if launches:
                raise RuntimeError('Unexpected second streaming Mini launch')
            launches += 1
            with tempfile.TemporaryDirectory(prefix='streaming-payload-') as temporary:
                for name, data in payloads.items():
                    path = Path(temporary) / name
                    path.write_bytes(data)
                    path.chmod(0o644)
                    await environment.upload_file(path, remote + '/' + name)
            old_prefix = tool_runtime.bootstrap_command(remote)
            new_prefix = old_prefix.replace(remote + '/b.py', remote + '/streaming_bootstrap.py')
            metadata = dict(schema_version=1, scope='new_agent_process_only', preflight_passed=False,
                            payload_sha256={name: digest(data) for name, data in payloads.items()},
                            wrapper_sha256=digest(Path(__file__).read_bytes()))
            try:
                result = await original_exec(environment, new_prefix + ' --streaming-preflight',
                                             env={'LITELLM_LOCAL_MODEL_COST_MAP': 'True'}, timeout_sec=30)
                rows = [json.loads(line.split('=', 1)[1]) for line in (result.stdout or '').splitlines()
                        if line.startswith('BENCHMARK_STREAMING_RUNTIME=')]
                if result.return_code != 0 or len(rows) != 1 or rows[0].get('installed') is not True:
                    raise RuntimeError('Streaming preflight failed before Mini CLI')
                if rows[0]['manifest_sha256'] != digest(payloads['streaming-manifest.json']):
                    raise RuntimeError('Streaming preflight manifest identity mismatch')
                metadata.update(rows[0], preflight_passed=True)
            finally:
                agent.logs_dir.mkdir(parents=True, exist_ok=True)
                with (agent.logs_dir / 'benchmark-agent-streaming-runtime.json').open('x') as output:
                    json.dump(metadata, output, indent=2, sort_keys=True)
                    output.write('\n')
            command = new_prefix + command[len(old_prefix):-len(tool_runtime.LAUNCH_SUFFIX)]
            command += '--model-class checked_streaming_model.CheckedStreamingModel ' + tool_runtime.LAUNCH_SUFFIX
            return await original_exec(environment, command, *values, **options)

        agent.exec_as_agent = execute
        try:
            result = await original_run(agent, *args, **kwargs)
            if launches != 1:
                raise RuntimeError('Streaming wrapper did not reach the actual Mini CLI launch')
            return result
        finally:
            agent.exec_as_agent = original_exec

    agent_class.run = run
    agent_class._checked_streaming_runtime_installed = True

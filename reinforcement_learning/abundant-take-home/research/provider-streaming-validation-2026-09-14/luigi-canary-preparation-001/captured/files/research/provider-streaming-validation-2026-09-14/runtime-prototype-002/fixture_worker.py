"""OFFLINE ONLY: use unchanged runtime hooks with an explicit Harbor exec double.

The double replaces Harbor's Docker transport only. Its generated launch passes
through the real task-file/tool/stream wrappers to the actual installed Mini CLI.
"""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import types

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(HERE))


async def execute_case(config):
    output = Path(config['output'])
    trace = []

    class Environment:
        async def upload_file(self, source, target):
            shutil.copyfile(source, target)
            Path(target).chmod(0o644)
            trace.append(dict(action='upload', name=Path(target).name))

    class Mini:
        def __init__(self):
            self.logs_dir = output

        async def install(self, environment):
            raise AssertionError('Fixture must not install packages')

        async def exec_as_agent(self, environment, command, *args, **kwargs):
            trace.append(dict(action='exec', command_sha256=__import__('hashlib').sha256(command.encode()).hexdigest(),
                              phase=('stream_preflight' if '--streaming-preflight' in command else
                                     'tool_preflight' if '--benchmark-preflight' in command else
                                     'final_cli' if '--exit-immediately' in command else 'setup')))
            env = os.environ.copy()
            env.update(kwargs.get('env') or {})
            result = subprocess.run(['bash', '-c', command], env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=kwargs.get('timeout_sec', 90))
            if '--exit-immediately' in command:
                (output / 'cli.stdout.log').write_text(result.stdout)
            return types.SimpleNamespace(stdout=result.stdout, return_code=result.returncode)

        async def run(self, instruction, environment):
            from benchmark_agent_runtime import LAUNCH_PREFIX, LAUNCH_SUFFIX
            command = (LAUNCH_PREFIX + '--yolo --model=anthropic/claude-fable-5-1 --task=' + shlex.quote(instruction)
                       + ' --output=' + shlex.quote(str(output / 'trajectory.json'))
                       + ' -c mini -c ' + shlex.quote(config['mini_config']) + ' ' + LAUNCH_SUFFIX)
            return await self.exec_as_agent(environment, command=command)

    # Only the Harbor transport/class import is doubled. The original hooks are
    # read directly from frozen script paths and execute their real logic.
    module = types.ModuleType('harbor.agents.installed.mini_swe_agent')
    module.MiniSweAgent = Mini
    sys.modules[module.__name__] = module
    import benchmark_mini_tool_runtime as tool
    tool.install()
    if config['streaming']:
        import streaming_runtime
        streaming_runtime.install(agent_class=Mini, tool_runtime=tool)
    agent = Mini()
    try:
        result = await agent.run(config['instruction'], Environment())
        (output / 'transport-result.json').write_text(json.dumps(dict(return_code=result.return_code)) + '\n')
    finally:
        (output / 'wrapper-order.json').write_text(json.dumps(trace, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', type=Path, required=True)
    config = json.loads(parser.parse_args().case.read_text())
    # Fixture-only overrides. Production runtime does not set these variables.
    os.environ.update(MSWEA_CONFIGURED='true', MSWEA_SILENT_STARTUP='1',
                      LITELLM_LOCAL_MODEL_COST_MAP='True', ANTHROPIC_API_KEY='offline-fixture-key',
                      ANTHROPIC_API_BASE=config['api_base'], ANTHROPIC_BASE_URL=config['api_base'],
                      MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=str(config['fixture_retry_attempts']),
                      MSWEA_GLOBAL_CONFIG_DIR=str(Path(config['output']) / 'mini-config'))
    asyncio.run(execute_case(config))


if __name__ == '__main__':
    main()

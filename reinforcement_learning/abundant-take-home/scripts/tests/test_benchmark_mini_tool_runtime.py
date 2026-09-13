"""The runtime shim must preserve Harbor's actual task/config/credential transport."""
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_mini_tool_runtime as runtime
from benchmark_agent_runtime import BenchmarkAgentPreflightError, LAUNCH_PREFIX, LAUNCH_SUFFIX, task_file_command


class CommandTests(unittest.TestCase):
    def rewritten(self):
        original = LAUNCH_PREFIX + '--model=anthropic/example --task=' + shlex.quote('zenohd 🦀') + ' -c mini -c model.model_kwargs.output_config.effort=high ' + LAUNCH_SUFFIX
        return task_file_command(original, '/tmp/benchmark-agent-input-' + 'a'*32 + '/task.yaml')[0]

    def test_only_executable_prefix_changes(self):
        command = self.rewritten()
        remote = runtime.runtime_location(command)
        patched = runtime.launch_command(command, remote)
        self.assertEqual(patched[len(runtime.bootstrap_command(remote)) + 1:], command[len(LAUNCH_PREFIX):])
        self.assertNotIn('zenohd', patched)
        self.assertNotIn('PYTHONPATH', patched)

    def test_rejects_inline_task(self):
        with self.assertRaises(BenchmarkAgentPreflightError):
            runtime.runtime_location(self.rewritten().replace('-c mini', '--task=inline -c mini'))

    def test_rejects_nonprivate_final_config(self):
        with self.assertRaises(BenchmarkAgentPreflightError):
            runtime.runtime_location(self.rewritten().replace('/tmp/benchmark-agent-input-' + 'a'*32 + '/task.yaml', '/workspace/task.yaml'))

    def test_rejects_unknown_launch_suffix(self):
        with self.assertRaises(BenchmarkAgentPreflightError):
            runtime.runtime_location(self.rewritten() + ' altered')


class HarborAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, failure=None):
        from harbor.agents.installed.mini_swe_agent import MiniSweAgent
        from harbor.models.agent.context import AgentContext
        from harbor.environments.base import ExecResult

        class Environment:
            def __init__(self):
                self.uploads = {}

            async def upload_file(self, source, target):
                self.uploads[target] = Path(source).read_bytes()

        instruction = "Don't expand $HOME; zenohd advanced_pub_sub 🦀\r\n keep spacing  "
        with tempfile.TemporaryDirectory() as temporary:
            environment = Environment()
            logs = Path(temporary)
            agent = MiniSweAgent(logs_dir=logs, version='2.4.6',
                                 model_name='anthropic/claude-sonnet-5', reasoning_effort='high')
            calls = []

            async def execute(environment, command, **kwargs):
                calls.append((command, kwargs))
                if command.endswith(' --benchmark-preflight'):
                    observed = {'installed': True, 'python': '3.12.11', 'package_version': '2.4.6'}
                    for name, key in [('b.py', 'bootstrap_sha256'), ('c.py', 'helper_sha256')]:
                        data = next(data for path, data in environment.uploads.items() if path.endswith('/'+name))
                        observed[key] = hashlib.sha256(data).hexdigest()
                    if failure == 'wrong_hash':
                        observed['helper_sha256'] = 'wrong'
                    if failure == 'missing_metadata':
                        return ExecResult(return_code=0, stdout='', stderr='')
                    return ExecResult(return_code=1 if failure == 'nonzero' else 0,
                                      stdout=runtime.PREFLIGHT_PREFIX + json.dumps(observed), stderr='')
                return ExecResult(return_code=0, stdout='', stderr='')

            with patch.object(MiniSweAgent, 'install', MiniSweAgent.install), \
                    patch.object(MiniSweAgent, 'run', MiniSweAgent.run), \
                    patch.object(MiniSweAgent, '_benchmark_python_installed', False, create=True), \
                    patch.object(MiniSweAgent, '_benchmark_tool_runtime_installed', False, create=True):
                runtime.install()
                installed_run = MiniSweAgent.run
                runtime.install()
                self.assertIs(MiniSweAgent.run, installed_run)
                agent.exec_as_agent = execute
                with patch.object(agent, '_get_env', return_value='synthetic-no-network-value'):
                    if failure:
                        with self.assertRaisesRegex(BenchmarkAgentPreflightError,
                                                    '^Tool runtime preflight failed; model execution withheld$'):
                            await agent.run(instruction=instruction, environment=environment, context=AgentContext())
                    else:
                        await agent.run(instruction=instruction, environment=environment, context=AgentContext())
                self.assertIs(agent.exec_as_agent, execute)
            payload = next(data for path, data in environment.uploads.items() if path.endswith('/task.yaml'))
            self.assertEqual(json.loads(payload)['run']['task'], instruction)
            self.assertEqual(len(environment.uploads), 3)
            metadata = json.loads((logs/'benchmark-agent-tool-runtime.json').read_text())
            task_metadata = json.loads((logs/'benchmark-agent-input.json').read_text())
            self.assertEqual(task_metadata['task_sha256'], hashlib.sha256(instruction.encode()).hexdigest())
            self.assertEqual(metadata['preflight_passed'], failure is None)
            self.assertEqual(metadata['phase'], 'tool_runtime_preflight_before_mini_cli')
            self.assertEqual(metadata['runtime_wrapper_sha256'], runtime.SOURCE_SHA256)
            launches = [(command, kwargs) for command, kwargs in calls if command.endswith(LAUNCH_SUFFIX)]
            if failure:
                self.assertEqual(launches, [])
                return
            self.assertEqual(len(launches), 1)
            command, options = launches[0]
            self.assertNotIn('zenohd', command)
            self.assertNotIn('advanced_pub_sub', command)
            self.assertNotIn('--task=', command)
            self.assertNotIn('PYTHONPATH', command)
            self.assertIn('output_config.effort=high', command)
            self.assertIn('thinking.type=adaptive', command)
            self.assertIn('max_tokens=64000', command)
            self.assertEqual(options['env']['MSWEA_API_KEY'], 'synthetic-no-network-value')
            self.assertNotIn('PYTHONPATH', options['env'])
            preflight = next(kwargs for command, kwargs in calls if command.endswith(' --benchmark-preflight'))
            self.assertEqual(preflight['timeout_sec'], 30)
            self.assertEqual(preflight['env'], {'LITELLM_LOCAL_MODEL_COST_MAP': 'True'})

    async def test_actual_harbor_adapter_preserves_inputs_and_config(self):
        await self.exercise()

    async def test_wrong_hash_withholds_model_launch(self):
        await self.exercise('wrong_hash')

    async def test_missing_preflight_withholds_model_launch(self):
        await self.exercise('missing_metadata')

    async def test_nonzero_preflight_withholds_model_launch(self):
        await self.exercise('nonzero')


if __name__ == '__main__':
    unittest.main()

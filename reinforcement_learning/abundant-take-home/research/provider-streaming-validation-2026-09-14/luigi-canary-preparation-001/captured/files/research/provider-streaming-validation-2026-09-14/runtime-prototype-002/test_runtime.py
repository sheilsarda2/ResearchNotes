"""Pure no-model wrapper regressions; actual CLI/lifecycle proof is separate."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / 'scripts'))
import streaming_runtime as runtime
import benchmark_mini_tool_runtime as tool

REMOTE = '/tmp/benchmark-agent-input-' + 'a' * 32
COMMAND = tool.bootstrap_command(REMOTE) + ' --model=anthropic/claude-fable-5-1 -c ' + REMOTE + '/task.yaml ' + tool.LAUNCH_SUFFIX


class RuntimeTests(unittest.TestCase):
    def test_only_final_bootstrap_intercepted(self):
        self.assertEqual(runtime.final_location(COMMAND, tool), REMOTE)
        self.assertIsNone(runtime.final_location(tool.bootstrap_command(REMOTE) + ' --benchmark-preflight', tool))
        self.assertIsNone(runtime.final_location('install -d /tmp/fixture', tool))

    def test_existing_model_class_rejected(self):
        for option in ('--model-class other.Model ', '-c model.model_class=litellm '):
            with self.subTest(option=option), self.assertRaises(RuntimeError):
                runtime.final_location(COMMAND.replace(tool.LAUNCH_SUFFIX, option + tool.LAUNCH_SUFFIX), tool)

    def test_malformed_launch_rejected(self):
        for command in (COMMAND + ' extra', COMMAND.replace(' --model=', ' badprefix --model=').replace('. "$HOME', 'echo "$HOME'),
                        COMMAND + ' ' + REMOTE + '/b.py'):
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                runtime.final_location(command, tool)

    def test_payload_manifest_binds_frozen_b_c_and_adapter(self):
        files = runtime.freeze_payloads()
        manifest = json.loads(files['streaming-manifest.json'])
        for name, expected in manifest['private_file_sha256'].items():
            data = files.get(name)
            if data is None:
                original = 'benchmark_mini_tool_bootstrap.py' if name == 'b.py' else 'benchmark_mini_tool_cleanup.py'
                data = (HERE.parents[2] / 'scripts' / original).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def exercise(self, *, preflight_ok=True, double=False):
        with tempfile.TemporaryDirectory() as temporary:
            received = []
            uploads = {}

            class Env:
                async def upload_file(self, path, target):
                    uploads[Path(target).name] = Path(path).read_bytes()

            class Agent:
                _benchmark_tool_runtime_installed = True
                logs_dir = Path(temporary)

                async def exec_as_agent(self, environment, command, *args, **kwargs):
                    received.append((command, kwargs))
                    if command.endswith('--streaming-preflight'):
                        proof = dict(installed=True, manifest_sha256=hashlib.sha256(uploads['streaming-manifest.json']).hexdigest())
                        return types.SimpleNamespace(return_code=0 if preflight_ok else 1,
                            stdout='BENCHMARK_STREAMING_RUNTIME=' + json.dumps(proof))
                    return types.SimpleNamespace(return_code=0, stdout='fixture')

                async def run(self, environment):
                    await self.exec_as_agent(environment, COMMAND, env={'inherited_fixture': 'unchanged'}, timeout_sec=123)
                    if double:
                        await self.exec_as_agent(environment, COMMAND)

            agent = Agent()
            previous = agent.exec_as_agent
            runtime.install(agent_class=Agent, tool_runtime=tool)
            if preflight_ok and not double:
                asyncio.run(agent.run(Env()))
                final, options = received[-1]
                self.assertIn('streaming_bootstrap.py', final)
                self.assertIn('--model-class checked_streaming_model.CheckedStreamingModel', final)
                self.assertTrue(final.endswith(tool.LAUNCH_SUFFIX))
                self.assertIn(REMOTE + '/task.yaml', final)
                self.assertEqual(options, {'env': {'inherited_fixture': 'unchanged'}, 'timeout_sec': 123})
                self.assertEqual(len(received), 2)
            else:
                with self.assertRaises(RuntimeError):
                    asyncio.run(agent.run(Env()))
                self.assertEqual(len(received), 2 if double else 1)
            self.assertEqual(agent.exec_as_agent, previous)
            metadata = json.loads((Path(temporary) / 'benchmark-agent-streaming-runtime.json').read_text())
            self.assertEqual(metadata['preflight_passed'], preflight_ok)
            with self.assertRaises(RuntimeError):
                runtime.install(agent_class=Agent, tool_runtime=tool)

    def test_argv_environment_timeout_preserved(self):
        self.exercise()

    def test_failed_preflight_withholds_final_cli(self):
        self.exercise(preflight_ok=False)

    def test_second_launch_rejected_and_exec_restored(self):
        self.exercise(double=True)

    def test_wrong_wrapper_order_rejected(self):
        with self.assertRaises(RuntimeError):
            runtime.install(agent_class=type('NotInstalled', (), {}), tool_runtime=tool)


if __name__ == '__main__':
    unittest.main(verbosity=2)

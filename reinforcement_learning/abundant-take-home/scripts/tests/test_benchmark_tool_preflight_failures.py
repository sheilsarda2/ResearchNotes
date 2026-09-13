"""Reviewed preflight evidence is distinct from a model attempt or unknown crash."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_evidence import snapshot_paths, write_snapshot, write_evidence
from benchmark_startup_failures import classify_startup_failure, _TOOL_RUNTIME_HASHES


class ToolPreflightFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tool-preflight-test-', dir=ROOT / 'research')
        self.root = Path(self.temp.name)
        for name in ('agent', 'artifacts', 'verifier'):
            (self.root / name).mkdir()
        self.metadata = dict(_TOOL_RUNTIME_HASHES, schema_version=1,
                             scope='agent_process_only',
                             phase='tool_runtime_preflight_before_mini_cli',
                             preflight_passed=False, error_type='BenchmarkAgentPreflightError')
        phase = {'started_at': '2026-09-13T22:00:00Z', 'finished_at': '2026-09-13T22:00:01Z'}
        self.result = {
            'task_name': 'test-task', 'task_checksum': 'test-checksum',
            'config': {'agent': {'name': 'mini-swe-agent', 'model_name': 'anthropic/claude-sonnet-5',
                                 'kwargs': {'version': '2.4.6', 'reasoning_effort': 'high'}},
                       'environment': {}, 'verifier': {}, 'timeout_multiplier': 1},
            'agent_info': {'name': 'mini-swe-agent', 'version': '2.4.6'},
            'agent_result': {'n_input_tokens': None, 'n_cache_tokens': None,
                             'n_output_tokens': None, 'cost_usd': None},
            'exception_info': {'exception_type': 'BenchmarkAgentPreflightError',
                               'exception_message': 'Tool runtime preflight failed; model execution withheld'},
            'verifier_result': {'rewards': {'reward': 0.0}},
            'agent_execution': phase, 'verifier': phase, **phase,
        }

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, data):
        (self.root / relative).write_text(json.dumps(data) + '\n')

    def freeze(self):
        # These are disposable fixtures. Rebuild their evidence so each negative
        # case proves the semantic rejection, not merely a stale snapshot.
        for name in ('benchmark-snapshot.json', 'benchmark-evidence.json'):
            (self.root / name).unlink(missing_ok=True)
        self.write('agent/benchmark-agent-tool-runtime.json', self.metadata)
        self.write('config.json', self.result['config'])
        self.write('result.json', self.result)
        self.write('benchmark-runtime.json', {'version': 1})
        self.write('benchmark-deadline.json', {'started_at_epoch': 1.0, 'closed_at_epoch': 2.0,
                   'cleanup_finished_at_epoch': 3.0, 'quiescent': True,
                   'executions': [{'quiescent': True}]})
        snapshot = snapshot_paths(self.root, ['agent', 'artifacts'])
        write_snapshot(self.root, snapshot)
        write_evidence(self.root, expected_snapshot=snapshot)

    def classify(self):
        return classify_startup_failure(self.root, self.result)

    def test_reviewed_failed_preflight_is_infrastructure_with_frozen_hash(self):
        self.freeze()
        value = self.classify()
        self.assertEqual(value['code'], 'mini_tool_runtime_preflight_failed')
        self.assertTrue(value['no_model_trajectory'])
        self.assertEqual(value['evidence']['sha256'], hashlib.sha256(
            (self.root / value['evidence']['path']).read_bytes()).hexdigest())
        self.assertEqual(self.result['verifier_result']['rewards']['reward'], 0.0)

    def test_hash_pins_match_reviewed_sources(self):
        for key, name in [('runtime_wrapper_sha256', 'benchmark_mini_tool_runtime.py'),
                          ('bootstrap_sha256', 'benchmark_mini_tool_bootstrap.py'),
                          ('helper_sha256', 'benchmark_mini_tool_cleanup.py')]:
            self.assertEqual(_TOOL_RUNTIME_HASHES[key], hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest())

    def test_wrong_phase_success_flag_or_unreviewed_hash_stays_review(self):
        self.freeze()
        original = copy.deepcopy(self.metadata)
        for key, value in [('phase', 'after_mini_cli'), ('preflight_passed', True),
                           ('preflight_passed', 0), ('scope', 'global'), ('schema_version', 2),
                           ('error_type', ''), *[(key, 'unknown') for key in _TOOL_RUNTIME_HASHES]]:
            with self.subTest(key=key, value=value):
                self.metadata = dict(original, **{key: value})
                self.freeze()
                self.assertIsNone(self.classify())

    def test_metadata_tampering_missing_and_symlink_stay_review(self):
        self.freeze()
        path = self.root/'agent/benchmark-agent-tool-runtime.json'
        self.write('agent/benchmark-agent-tool-runtime.json', dict(self.metadata, unexpected='changed'))
        self.assertIsNone(self.classify())
        path.unlink()
        self.assertIsNone(self.classify())
        path.symlink_to(self.root/'missing')
        self.assertIsNone(self.classify())

    def test_any_cli_log_or_trajectory_stays_review(self):
        self.freeze()
        for name in ('mini-swe-agent.txt', 'mini-swe-agent.trajectory.json', 'trajectory.json'):
            with self.subTest(name=name):
                path = self.root/'agent'/name
                path.write_text('')
                self.assertIsNone(self.classify())
                path.unlink()

    def test_recorded_usage_or_rollout_stays_review(self):
        self.freeze()
        for key, value in [('n_input_tokens', 1), ('n_cache_tokens', 1), ('n_output_tokens', 1),
                           ('cost_usd', 0.001), ('rollout_details', [{'step': 1}])]:
            with self.subTest(key=key):
                self.result['agent_result'][key] = value
                self.freeze()
                self.assertIsNone(self.classify())
                self.result['agent_result'][key] = None

    def test_wrong_exception_message_or_agent_stays_review(self):
        self.freeze()
        original = copy.deepcopy(self.result)
        for container, key, value in [('exception_info', 'exception_type', 'AgentTimeoutError'),
                                      ('exception_info', 'exception_message', 'Another preflight')]:
            self.result = copy.deepcopy(original)
            self.result[container][key] = value
            self.freeze()
            self.assertIsNone(self.classify())
        self.result = copy.deepcopy(original)
        self.result['config']['agent']['name'] = 'other-agent'
        self.freeze()
        self.assertIsNone(self.classify())

    def test_missing_frozen_evidence_and_altered_result_stay_review(self):
        self.freeze()
        self.write('result.json', dict(self.result, unexpected='changed'))
        self.assertIsNone(self.classify())
        self.write('result.json', self.result)
        (self.root/'benchmark-snapshot.json').unlink()
        self.assertIsNone(self.classify())

    def test_real_screen_inspection_accepts_missing_trace_and_preserves_raw_cost_reward(self):
        self.freeze()
        spec = importlib.util.spec_from_file_location('preflight_screen', ROOT/'scripts/run-candidate-screen.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result_bytes = (self.root/'result.json').read_bytes()
        record = module.inspect(self.root/'result.json', {'test-task': {'harbor_task_checksum': 'test-checksum'}})
        self.assertEqual(record['status'], 'infrastructure')
        self.assertEqual(record['infrastructure_detail'], 'mini_tool_runtime_preflight_failed')
        self.assertEqual(record['raw_reward'], 0.0)
        self.assertIsNone(record['reward'])
        self.assertIsNone(record['cost_usd'])
        self.assertTrue(record['guarded_evidence'])
        self.assertNotIn('evidence_issue', record)
        self.assertEqual(record['benchmark_evidence']['completeness']['native'], 'missing')
        self.assertEqual(result_bytes, (self.root/'result.json').read_bytes())
        self.write('agent/benchmark-agent-tool-runtime.json', dict(self.metadata, unexpected='changed'))
        self.assertEqual(module.inspect(self.root/'result.json', {'test-task': {'harbor_task_checksum': 'test-checksum'}})['status'], 'review')


if __name__ == '__main__':
    unittest.main()

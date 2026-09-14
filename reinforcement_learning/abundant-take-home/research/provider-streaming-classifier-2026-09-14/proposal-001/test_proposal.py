#!/usr/bin/env python3
"""Offline copied-source regression; original jobs and runtime are read-only."""
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
COPIED = BASE / 'copied/scripts'
MANIFEST = json.loads((BASE / 'input-manifest.json').read_text())
TRIAL = ROOT / MANIFEST['original_trial_path']
TASKS = {'luigi-generation-target': {
    'harbor_task_checksum': '38e96eee424a85cde604fcd77cf11adfd6d541302f53e1c065990a9967c86170',
    'check_format': 'individual_checks'}}
sys.path.insert(0, str(COPIED))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, COPIED / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASELINE = load('baseline_screen', 'run-candidate-screen.py')
PROPOSED = load('proposed_screen', 'run-candidate-screen.proposed.py')
import benchmark_evidence as evidence
import benchmark_incidents as incidents


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def classify(module, path, root):
    # Relocate captured module roots only; imported source bytes are untouched.
    with patch.object(module, 'ROOT', root), patch.object(incidents, 'ROOT', root):
        return module.inspect(path, TASKS)


def projection(record):
    return {key: record.get(key) for key in (
        'status', 'raw_reward', 'reward', 'exception', 'infrastructure_detail',
        'evidence_issue', 'agent_exit_abnormal', 'checks_passed', 'checks_total')}


@contextlib.contextmanager
def fixture(exit_status='RuntimeError', outer='NonZeroAgentExitCodeError', reward=0,
            misleading_text=False, guarded=True):
    """Small synthetic evidence, using original public config and phase timings.

    No real prompt, response, thinking, signatures, commands or submitted source
    is copied. Frozen helper creates self-consistent snapshots in a temp dir.
    """
    with tempfile.TemporaryDirectory(prefix='classifier-offline-') as temporary:
        root = Path(temporary)
        trial = root / 'jobs/synthetic/trial'
        original = json.loads((TRIAL / 'result.json').read_text())
        result = {key: original[key] for key in (
            'task_name', 'task_checksum', 'config', 'agent_info', 'started_at',
            'finished_at', 'environment_setup', 'agent_setup', 'agent_execution', 'verifier')}
        result.update(trial_name='synthetic', agent_result={
            'cost_usd': 0.1, 'n_input_tokens': 1, 'n_output_tokens': 1, 'n_cache_tokens': 0},
            exception_info=({'exception_type': outer,
                'exception_message': 'Agent process exited with nonzero status'} if outer else None),
            verifier_result={'rewards': {'reward': reward}})
        text = ('A fictional example says MidStreamFallbackError; this is task text.'
                if misleading_text else 'Synthetic fixture content.')
        trace = {'info': {'exit_status': exit_status, 'submission': text,
            'config': {'model': {'model_kwargs': {'output_config': {'effort': 'max'},
                'thinking': {'type': 'adaptive'}, 'max_tokens': 64000}}},
            'model_stats': {'instance_cost': 0.1, 'api_calls': 2}},
            'messages': [
                {'role': 'assistant', 'content': text, 'extra': {'timestamp': '2026-09-14T09:00:00Z'},
                 'tool_calls': [{'id': 'synthetic'}]},
                {'role': 'tool', 'content': text, 'extra': {'timestamp': '2026-09-14T09:00:01Z'}},
                {'role': 'exit', 'content': text, 'extra': {'exit_status': exit_status}}]}
        write(trial / 'result.json', result)
        write(trial / 'config.json', result['config'])
        write(trial / 'agent/mini-swe-agent.trajectory.json', trace)
        write(trial / 'agent/trajectory.json', {'steps': [
            {'source': 'agent', 'message': text, 'tool_calls': [{'id': 'synthetic'}]}]})
        (trial / 'artifacts/submission').mkdir(parents=True)
        (trial / 'artifacts/submission/synthetic.txt').write_text('Synthetic submission\n')
        (trial / 'verifier').mkdir()
        failure = '<failure message="synthetic task failure" />' if not reward else ''
        (trial / 'verifier/results.xml').write_text(
            f'<testsuite><testcase name="a"/><testcase name="b">{failure}</testcase></testsuite>')
        (trial / 'verifier/reward.txt').write_text(str(reward) + '\n')
        if guarded:
            # Only lifecycle metadata is reused; it contains no prompt or signature.
            for name in ('benchmark-runtime.json', 'benchmark-deadline.json'):
                write(trial / name, json.loads((TRIAL / name).read_text()))
            snapshot = evidence.snapshot_paths(trial, ['agent', 'artifacts'])
            evidence.write_snapshot(trial, snapshot)
            evidence.write_evidence(trial, snapshot, deadline_at='2026-09-14T10:40:06.679328Z')
        yield root, trial


class ClassifierRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = contextlib.ExitStack()
        # No external request, child process, Docker, or model call may occur.
        for target in ('socket.socket.connect', 'socket.create_connection',
                       'subprocess.Popen', 'os.system'):
            cls.blocks.enter_context(patch(target, side_effect=AssertionError('offline call prohibited')))

    @classmethod
    def tearDownClass(cls):
        cls.blocks.close()

    def test_exact_saved_baseline_reproduces_wrong_scored_zero(self):
        record = classify(BASELINE, TRIAL / 'result.json', ROOT)
        self.assertEqual((record['status'], record['reward']), ('scored', 0))
        self.assertEqual(record['exception'], 'NonZeroAgentExitCodeError')

    def test_exact_saved_proposed_transport_failure_keeps_raw_reward(self):
        record = classify(PROPOSED, TRIAL / 'result.json', ROOT)
        self.assertEqual((record['status'], record['raw_reward'], record['reward']),
                         ('infrastructure', 0.0, None))
        self.assertEqual(record['infrastructure_detail'], 'MidStreamFallbackError')
        self.assertTrue(record['benchmark_evidence']['usage']['usage_censored'])

    def test_ordinary_completed_verifier_abnormal_failure_still_counts(self):
        with fixture() as (root, trial):
            before = classify(BASELINE, trial / 'result.json', root)
            after = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual(projection(before), projection(after))
            self.assertEqual((after['status'], after['reward']), ('scored', 0))
            self.assertTrue(after['agent_exit_abnormal'])

    def test_ordinary_completed_verifier_abnormal_success_still_counts(self):
        with fixture(reward=1, misleading_text=True) as (root, trial):
            before = classify(BASELINE, trial / 'result.json', root)
            after = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual(projection(before), projection(after))
            self.assertEqual((after['status'], after['reward']), ('scored', 1))

    def test_submitted_success_ignores_misleading_assistant_tool_and_submission_text(self):
        with fixture(exit_status='Submitted', outer=None, reward=1, misleading_text=True) as (root, trial):
            after = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual((after['status'], after['reward']), ('scored', 1))

    def test_near_match_terminal_name_does_not_create_free_retry(self):
        for value in ('ExampleMidStreamFallbackError', 'MidStreamFallbackErrorExample',
                      'example: MidStreamFallbackError'):
            with self.subTest(value=value), fixture(exit_status=value) as (root, trial):
                self.assertEqual(classify(PROPOSED, trial / 'result.json', root)['status'], 'scored')

    def test_original_agent_timeout_policy_unchanged(self):
        with fixture(exit_status='MidStreamFallbackError', outer='AgentTimeoutError') as (root, trial):
            before = classify(BASELINE, trial / 'result.json', root)
            after = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual(projection(before), projection(after))
            self.assertEqual(after['status'], 'timeout')

    def test_existing_known_infrastructure_failure_unchanged(self):
        with fixture(exit_status='APIConnectionError') as (root, trial):
            before = classify(BASELINE, trial / 'result.json', root)
            after = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual(projection(before), projection(after))
            self.assertEqual(after['status'], 'infrastructure')

    def test_stream_transport_exclusion_is_independent_of_verifier_reward(self):
        for reward in (0, 1):
            with self.subTest(reward=reward), fixture(
                    exit_status='MidStreamFallbackError', reward=reward) as (root, trial):
                after = classify(PROPOSED, trial / 'result.json', root)
                self.assertEqual((after['status'], after['raw_reward'], after['reward']),
                                 ('infrastructure', reward, None))

    def test_unbound_legacy_stream_status_cannot_create_exclusion(self):
        with fixture(exit_status='MidStreamFallbackError', guarded=False) as (root, trial):
            with self.assertRaisesRegex(AssertionError, 'Stream failure requires guarded evidence'):
                classify(PROPOSED, trial / 'result.json', root)

    def test_corrupt_result_evidence_stays_review(self):
        with fixture(exit_status='MidStreamFallbackError') as (root, trial):
            raw = json.loads((trial / 'result.json').read_text())
            raw['unbound_change'] = True
            write(trial / 'result.json', raw)
            record = classify(PROPOSED, trial / 'result.json', root)
            self.assertEqual(record['status'], 'review')
            self.assertEqual(record['evidence_issue'], 'result_hash_mismatch')

    def test_changed_native_terminal_status_cannot_bypass_snapshot_integrity(self):
        with fixture() as (root, trial):
            path = trial / 'agent/mini-swe-agent.trajectory.json'
            raw = json.loads(path.read_text())
            raw['info']['exit_status'] = 'MidStreamFallbackError'
            write(path, raw)
            with self.assertRaisesRegex(AssertionError, 'Stream failure evidence changed'):
                classify(PROPOSED, trial / 'result.json', root)

    def test_imports_and_all_original_source_and_trial_hashes_preserved(self):
        for name, module in sys.modules.copy().items():
            if name.startswith('benchmark_') and getattr(module, '__file__', None):
                self.assertTrue(Path(module.__file__).is_relative_to(COPIED), name)
        for relative, expected in MANIFEST['original_source_sha256'].items():
            self.assertEqual(sha(ROOT / relative), expected, relative)
        actual = {str(path.relative_to(TRIAL)): sha(path)
                  for path in sorted(TRIAL.rglob('*')) if path.is_file()}
        self.assertEqual(actual, MANIFEST['original_trial_file_sha256'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

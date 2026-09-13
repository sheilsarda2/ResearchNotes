"""Audit exceptions remain narrow across real inspect and supervisor polling."""
from datetime import datetime, timedelta, timezone
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import benchmark_incidents as incidents

spec = importlib.util.spec_from_file_location('candidate_incident_screen', SCRIPTS / 'run-candidate-screen.py')
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


class StopPolling(Exception):
    pass


class CandidateIncidentIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = 'fixture-efforts-1-test'
        self.trial = self.root / 'jobs' / self.base / 'trial'
        self.trial.mkdir(parents=True)
        self.task = {'id': 'synthetic-task', 'path': 'candidates/synthetic-task',
                     'harbor_task_checksum': 'task-checksum'}
        self.result = {'task_name': self.task['id'], 'task_checksum': 'task-checksum',
                       'finished_at': datetime.now(timezone.utc).isoformat(),
                       'exception_info': {'exception_type': 'CancelledError'},
                       'verifier': None, 'verifier_result': None, 'agent_result': {'cost_usd': 0.1},
                       'config': {'timeout_multiplier': 1, 'environment': {}, 'verifier': {},
                                  'agent': {'name': 'mini-swe-agent', 'model_name': 'anthropic/claude-sonnet-5',
                                            'kwargs': {'version': '2.4.6', 'reasoning_effort': 'medium'}}}}
        self.write(self.trial / 'result.json', self.result)
        self.write(self.trial / 'benchmark-evidence.json', {'quiescent': False})
        self.write(self.trial / 'benchmark-deadline.json', {'quiescent': False})
        relative = str(self.trial.relative_to(self.root))
        self.manifest_path = self.root / 'research/benchmark-incidents/fixture/incident.json'
        self.manifest = {'kind': 'guard_failure_cancelled_sibling_trials', 'trigger_trial': relative,
                         'trigger_result_sha256': self.digest(self.trial / 'result.json'),
                         'trials': {relative: {'exception_type': 'CancelledError',
                                             'files': {name: self.digest(self.trial / name) for name in
                                                       ('result.json', 'benchmark-evidence.json', 'benchmark-deadline.json')}}}}
        self.write(self.manifest_path, self.manifest)
        for target in (screen, incidents):
            active = patch.object(target, 'ROOT', self.root)
            active.start()
            self.addCleanup(active.stop)
        attach = patch.object(screen, 'attach_to_record', side_effect=self.attach)
        attach.start()
        self.addCleanup(attach.stop)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def attach(record, directory):
        record.update(guarded_evidence=True, evidence_issue='agent_quiescence_unconfirmed')
        return False

    def inspect(self):
        return screen.inspect(self.trial / 'result.json', {self.task['id']: self.task})

    def resolve(self):
        proof = self.root / 'research/proof.json'
        self.write(proof, {'passed': True, 'model_calls': 0})
        self.manifest['resolution'] = {'proof': 'research/proof.json', 'proof_sha256': self.digest(proof)}
        self.write(self.manifest_path, self.manifest)

    def guard_failure(self, age=0):
        self.manifest_path.unlink()
        self.result['exception_info']['exception_type'] = 'AgentQuiescenceError'
        self.result['finished_at'] = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
        self.write(self.trial / 'result.json', self.result)
        self.write(self.trial / 'benchmark-runtime.json', {
            'trial_containment_sha256': next(iter(incidents.APPROVED_CONTAINMENT_HELPERS))})

    def marker(self):
        self.write(self.trial / 'benchmark-containment.json', {
            'version': 1, 'reason': 'finalized_ungraded_guard_failure',
            'escaped_exception_type': 'AgentQuiescenceError',
            'result_sha256': self.digest(self.trial / 'result.json'),
            'end_hooks_completed': True, 'grading_withheld': True,
            'compose_project': 'synthetic-project', 'running_project_containers': 0})

    def poll_twice(self, between):
        prefix = self.root / 'jobs' / self.base
        self.write(self.root / 'manifest.json', {'tasks': [self.task]})
        self.write(prefix.with_suffix('.plan.json'), {
            'tasks': [self.task], 'models': ['sonnet-5'], 'efforts': screen.EFFORTS,
            'attempts': 1, 'workers': 1, 'target': 3, 'after_summary': None,
            'jobs': [{'name': self.base}]})
        self.write(prefix.with_suffix('.control.json'), {'max_active': 1, 'paused': False})
        summaries = []

        def next_poll(_seconds):
            summaries.append(json.loads(prefix.with_suffix('.summary.json').read_text()))
            if len(summaries) == 1:
                between()
            else:
                raise StopPolling

        old_cwd = Path.cwd()
        lock_handles = []
        open_path = Path.open

        def tracked_open(path, *args, **kwargs):
            handle = open_path(path, *args, **kwargs)
            if path == prefix.with_suffix('.lock'):
                lock_handles.append(handle)
            return handle

        try:
            with patch.object(sys, 'argv', ['screen', '--run-id', 'test', '--attempts', '1', '--workers', '1',
                                           '--models', 'sonnet-5', '--campaign', 'fixture',
                                           '--task-manifest', str(self.root / 'manifest.json')]), \
                    patch.object(screen, 'verify_tasks'), patch.object(screen, 'load_dotenv'), \
                    patch.dict(os.environ, {'TAKE_HOME_TOKEN': 'synthetic-no-network-value'}), \
                    patch.object(screen.common, 'active_pid', return_value=123456), \
                    patch.object(screen, 'memory_snapshot', return_value={
                        'total_mb': 32768, 'available_mb': 24000, 'memory_pressure_pct': 0}), \
                    patch.object(screen, 'memory_block_reason', return_value=None), \
                    patch.object(screen.time, 'monotonic', return_value=0), \
                    patch.object(screen.time, 'sleep', side_effect=next_poll), \
                    patch.object(Path, 'open', tracked_open), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(StopPolling):
                    screen.main()
        finally:
            for handle in lock_handles:
                handle.close()
            os.chdir(old_cwd)
        return summaries

    def test_exact_audited_ungraded_incident_overrides_only_expected_evidence_gap(self):
        record = self.inspect()
        self.assertEqual(record['status'], 'infrastructure')
        self.assertEqual(record['exception'], 'CancelledError')
        self.assertIsNone(record['reward'])
        self.assertFalse(record['resolved_incident'])
        self.assertTrue(record['usage_censored'])
        self.assertEqual(record['cost_usd'], 0.1)

    def test_unknown_cancellation_stays_review(self):
        self.manifest_path.unlink()
        self.assertEqual(self.inspect()['status'], 'review')

    def test_changed_hash_bound_evidence_is_rejected(self):
        self.write(self.trial / 'benchmark-evidence.json', {'different': True})
        with self.assertRaises(AssertionError):
            self.inspect()

    def test_completed_verifier_cannot_use_incident_exception(self):
        self.result['verifier'] = {'started_at': self.result['finished_at']}
        self.write(self.trial / 'result.json', self.result)
        self.assertEqual(self.inspect()['status'], 'review')

    def test_resolution_invalidates_cached_record_and_removes_it_from_recent_health(self):
        before, after = self.poll_twice(self.resolve)
        self.assertEqual(before['health']['recent_infrastructure'], 1)
        self.assertFalse(before['trials'][0]['resolved_incident'])
        self.assertEqual(after['health']['recent_infrastructure'], 0)
        self.assertEqual(after['health']['recent_results'], 0)
        self.assertTrue(after['trials'][0]['resolved_incident'])
        self.assertEqual(after['completed'], 0)
        self.assertEqual(after['settings'][0]['cost_usd'], 0.1)

    def test_changed_resolution_proof_invalidates_cache_and_requires_review(self):
        self.resolve()
        before, after = self.poll_twice(lambda: self.write(self.root / 'research/proof.json', {'passed': False, 'model_calls': 0}))
        self.assertEqual(before['trials'][0]['status'], 'infrastructure')
        self.assertEqual(after['trials'][0]['status'], 'review')

    def test_late_containment_marker_invalidates_cached_review(self):
        self.guard_failure(age=60)
        before, after = self.poll_twice(self.marker)
        self.assertEqual(before['trials'][0]['status'], 'review')
        self.assertEqual(after['trials'][0]['status'], 'infrastructure')
        self.assertFalse(after['trials'][0]['resolved_incident'])

    def test_qualified_marker_race_is_pending_then_classified(self):
        self.guard_failure()
        before, after = self.poll_twice(self.marker)
        self.assertEqual(before['trials'], [])
        self.assertEqual(before['health']['unclassified_results'], 0)
        self.assertEqual(after['trials'][0]['status'], 'infrastructure')

    def test_pending_grace_is_bounded_and_requires_approved_source(self):
        self.guard_failure(age=31)
        self.assertEqual(self.inspect()['status'], 'review')
        self.result['finished_at'] = datetime.now(timezone.utc).isoformat()
        self.write(self.trial / 'result.json', self.result)
        self.write(self.trial / 'benchmark-runtime.json', {'trial_containment_sha256': 'unknown'})
        self.assertEqual(self.inspect()['status'], 'review')

    def test_malformed_registry_fingerprint_is_safe_and_inspection_rejects_it(self):
        self.write(self.manifest_path, [])
        self.assertTrue(incidents.registry_cache_stamp())
        with self.assertRaises(AssertionError):
            self.inspect()


if __name__ == '__main__':
    unittest.main()

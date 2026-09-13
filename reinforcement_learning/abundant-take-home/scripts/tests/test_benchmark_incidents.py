"""Only exact audited incidents or qualified containment proofs get classified."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_incidents as incidents


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial = self.root / 'jobs/campaign/trial'
        self.trial.mkdir(parents=True)
        self.patch = patch.object(incidents, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.result = {'finished_at': '2026-09-13T21:54:16Z', 'verifier': None,
                       'verifier_result': None, 'exception_info': {'exception_type': 'CancelledError'}}
        self.write(self.trial / 'result.json', self.result)
        for name in ('benchmark-evidence.json', 'benchmark-deadline.json'):
            self.write(self.trial / name, {'quiescent': True})
        self.manifest_path = self.root / 'research/benchmark-incidents/incident/incident.json'
        self.manifest = {'kind': 'guard_failure_cancelled_sibling_trials',
                         'trigger_trial': 'jobs/campaign/trial',
                         'trigger_result_sha256': self.hash(self.trial / 'result.json'),
                         'trials': {'jobs/campaign/trial': {'exception_type': 'CancelledError',
                                    'files': {n: self.hash(self.trial / n) for n in (
                                        'result.json', 'benchmark-evidence.json', 'benchmark-deadline.json')}}}}
        self.write(self.manifest_path, self.manifest)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def hash(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def classify(self):
        return incidents.classify_incident(self.trial, self.result)

    def test_audited_incident_is_unresolved_until_fix_proof_passes(self):
        self.assertFalse(self.classify()['resolved'])
        proof = self.root / 'research/proof.json'
        self.write(proof, {'passed': True, 'model_calls': 0})
        self.manifest['resolution'] = {'proof': 'research/proof.json', 'proof_sha256': self.hash(proof)}
        self.write(self.manifest_path, self.manifest)
        self.assertTrue(self.classify()['resolved'])

    def test_unaudited_cancellation_is_not_automatically_retried(self):
        self.manifest_path.unlink()
        self.assertIsNone(self.classify())

    def test_changed_result_or_evidence_cannot_use_the_exception(self):
        for name in ('result.json', 'benchmark-evidence.json', 'benchmark-deadline.json'):
            with self.subTest(name=name):
                path = self.trial / name
                saved = path.read_bytes()
                path.write_bytes(saved + b' ')
                with self.assertRaises(AssertionError):
                    self.classify()
                path.write_bytes(saved)

    def test_graded_and_ordinary_model_failures_are_not_reclassified(self):
        self.result['verifier'] = {'started_at': '2026-09-13T21:54:00Z'}
        self.assertIsNone(self.classify())
        self.result['verifier'] = None
        self.result['exception_info']['exception_type'] = 'NonZeroAgentExitCodeError'
        self.assertIsNone(self.classify())

    def test_failed_resolution_proof_cannot_clear_the_health_gate(self):
        proof = self.root / 'research/proof.json'
        self.write(proof, {'passed': False, 'model_calls': 0})
        self.manifest['resolution'] = {'proof': 'research/proof.json', 'proof_sha256': self.hash(proof)}
        self.write(self.manifest_path, self.manifest)
        with self.assertRaises(AssertionError):
            self.classify()

    def containment(self):
        self.result['exception_info']['exception_type'] = 'AgentQuiescenceError'
        self.write(self.trial / 'result.json', self.result)
        self.write(self.trial / 'benchmark-runtime.json', {
            'trial_containment_sha256': next(iter(incidents.APPROVED_CONTAINMENT_HELPERS))})
        self.marker = {'version': 1, 'reason': 'finalized_ungraded_guard_failure',
                       'escaped_exception_type': 'AgentQuiescenceError',
                       'result_sha256': self.hash(self.trial / 'result.json'),
                       'end_hooks_completed': True, 'grading_withheld': True,
                       'compose_project': 'exact-trial-project', 'running_project_containers': 0}
        self.write(self.trial / 'benchmark-containment.json', self.marker)

    def test_confirmed_teardown_is_infrastructure_but_not_a_resolved_incident(self):
        self.containment()
        result = self.classify()
        self.assertEqual(result['code'], 'agent_cleanup_failure_contained')
        self.assertFalse(result['resolved'])

    def test_live_containers_and_unfinished_hooks_cannot_be_accepted(self):
        self.containment()
        for key, value in (('running_project_containers', 1), ('end_hooks_completed', False),
                           ('grading_withheld', False), ('result_sha256', 'changed')):
            with self.subTest(key=key):
                marker = {**self.marker, key: value}
                self.write(self.trial / 'benchmark-containment.json', marker)
                with self.assertRaises(AssertionError):
                    self.classify()

    def test_unqualified_containment_source_is_rejected(self):
        self.containment()
        self.write(self.trial / 'benchmark-runtime.json', {'trial_containment_sha256': 'unknown'})
        with self.assertRaises(AssertionError):
            self.classify()


if __name__ == '__main__':
    unittest.main()

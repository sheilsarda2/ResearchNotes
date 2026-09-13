"""No process control or model calls: exact-trial intervention evidence gates."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_incidents as incidents
import benchmark_trial_intervention as intervention

REPO = Path(__file__).resolve().parents[2]


def epoch(stamp):
    return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()


class InterventionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.relative = 'jobs/campaign/huey-sqlite-leases__LAUZTSQ'
        self.trial = self.root / self.relative
        self.registry = self.root / 'research/benchmark-interventions/huey/intervention.json'
        self.observation_path = self.root / 'research/observation.json'
        self.snapshots_path = self.root / 'research/snapshots.json'
        proof_relative = 'research/mini-tool-drain-fix-2026-09-13/reproduction-final'
        self.proof_path = self.root / proof_relative / 'summary.json'
        self.proof_path.parent.mkdir(parents=True)
        shutil.copyfile(REPO / proof_relative / 'summary.json', self.proof_path)
        shutil.copytree(REPO / proof_relative / 'source', self.proof_path.parent / 'source')
        self.config = {'trial_name': self.trial.name, 'task': {'path': 'candidates/huey-sqlite-leases'},
                       'agent': {'name': 'mini-swe-agent', 'kwargs': {'version': '2.4.6'}}}
        self.write(self.trial / 'config.json', self.config)
        self.write(self.trial / 'benchmark-runtime.json', {
            'version': 1, 'scope': 'single-step-linux-docker',
            'process_guard_sha256': intervention.GUARD_SHA256})
        self.write(self.trial / 'agent/benchmark-agent-runtime.json', {
            'preflight_passed': True, 'python': '3.12.11', 'requested_python': '3.12.11',
            'packages': {'mini-swe-agent': '2.4.6'}})
        source = {'mini_local_sha256': intervention.MINI_LOCAL_SHA256,
                  'mini_run_sha256': intervention.MINI_RUN_SHA256, 'tool_timeout_sec': 30}
        processes = [
            {'pid': 1776, 'start_ticks': '2199885', 'state': 'S', 'parent_pid': 1,
             'cpu_ticks': ['200', '3'], 'wait_channel': 'hrtimer_nanosleep', 'pipes': {}},
            {'pid': 1783, 'start_ticks': '2199888', 'state': 'S', 'parent_pid': 1776,
             'cpu_ticks': ['300', '11'], 'wait_channel': 'do_sys_poll', 'pipes': {'4': 'pipe:[3938292]'}},
            {'pid': 2096, 'start_ticks': '2272250', 'state': 'S', 'parent_pid': 1776,
             'cpu_ticks': ['10', '2'], 'wait_channel': 'futex_wait',
             'pipes': {'1': 'pipe:[3938292]', '2': 'pipe:[3938292]'}},
        ]
        self.snapshots = [dict(source, recorded_turns=39, processes=deepcopy(processes), observed_at=t)
                          for t in ('2026-09-13T22:44:20Z', '2026-09-13T22:46:50Z')]
        self.write(self.snapshots_path, self.snapshots)
        identity_files = {name: self.hash(self.trial / name) for name in intervention.IDENTITY_FILES}
        self.observation = dict(source, schema_version=1, kind='mini_tool_pipe_drain_hang',
            trial=self.relative, observed_at='2026-09-13T22:46:50Z',
            pid_namespace='trial_container', agent={'pid': 1783, 'start_ticks': '2199888'},
            guard={'pid': 1776, 'start_ticks': '2199885', 'phase': '/tmp/benchmark-guard-' + 'a' * 32},
            stalled_for_sec=150, pending_step=40, recorded_turns=39,
            pipe={'inode': 3938292, 'agent_read_fd': 4, 'holder_pids': [2096]},
            files=identity_files, observations=self.link(self.snapshots_path))
        self.write(self.observation_path, self.observation)
        self.entry = {'schema_version': 1, 'kind': intervention.KIND, 'status': 'planned',
            'trial': self.relative, 'registered_at': '2026-09-13T22:47:00Z',
            'identity': {'trial_name': self.trial.name, 'task_name': 'huey-sqlite-leases',
                         'task_checksum': 'a' * 64, 'files': identity_files},
            'observation': self.link(self.observation_path), 'proof': self.link(self.proof_path),
            'expected_exceptions': sorted(intervention.EXPECTED_EXCEPTIONS), 'finalization': None}
        self.write(self.registry, self.entry)
        self.result = {'trial_name': self.trial.name, 'task_name': 'huey-sqlite-leases',
            'task_checksum': 'a' * 64, 'config': self.config, 'finished_at': '2026-09-13T22:48:00Z',
            'exception_info': {'exception_type': 'NonZeroAgentExitCodeError'},
            'verifier': {'started_at': '2026-09-13T22:47:10Z'},
            'verifier_result': {'rewards': {'reward': 1}}}
        self.write(self.trial / 'result.json', self.result)
        self.deadline = {'version': 1, 'quiescent': True, 'helper_sha256': intervention.GUARD_SHA256,
            'started_at_epoch': epoch('2026-09-13T22:11:56Z'),
            'agent_run_finished_at_epoch': epoch('2026-09-13T22:47:05Z'),
            'closed_at_epoch': epoch('2026-09-13T22:47:06Z'),
            'cleanup_finished_at_epoch': epoch('2026-09-13T22:47:07Z'),
            'executions': [{'pid': 1776, 'identity': '2199885', 'reason': 'cancelled',
                'return_code': 124, 'quiescent': True,
                'started_at_epoch': epoch('2026-09-13T22:11:57Z'),
                'stop_requested_at_epoch': epoch('2026-09-13T22:47:02Z'),
                'quiescent_at_epoch': epoch('2026-09-13T22:47:04Z')}]}
        self.write(self.trial / 'benchmark-deadline.json', self.deadline)
        self.write(self.trial / 'benchmark-evidence.json', {'diagnostic': 'preserved'})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def hash(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def link(self, path):
        return {'path': str(path.relative_to(self.root)), 'sha256': self.hash(path)}

    def classify(self):
        return intervention.classify_intervention(self.trial, self.result, root=self.root)

    def finalize(self):
        self.entry.update(status='finalized', finalization={
            'finished_at': self.result['finished_at'],
            'files': {name: self.hash(self.trial / name) for name in intervention.FINAL_FILES}})
        self.write(self.registry, self.entry)

    def rebind_observation(self):
        self.write(self.snapshots_path, self.snapshots)
        self.observation['observations'] = self.link(self.snapshots_path)
        self.write(self.observation_path, self.observation)
        self.entry['observation'] = self.link(self.observation_path)
        self.write(self.registry, self.entry)

    def audit_alias(self):
        self.result['exception_info'] = {'exception_type': 'ApiRateLimitError',
            'exception_message': 'Command failed (exit 124): synthetic-no-model-command'}
        self.write(self.trial / 'result.json', self.result)
        transcript = self.trial / 'agent/mini-swe-agent.txt'
        transcript.write_text('The task supports rate limits.\n')
        planned = self.registry.with_name('intervention-planned.json')
        planned.write_bytes(self.registry.read_bytes())
        intent = self.root / 'research/guard-close-intent.json'
        self.write(intent, {'started_at': '2026-09-13T22:47:01Z', 'trial': self.relative,
                            'registry_sha256': self.hash(planned)})
        proof = self.registry.with_name('harbor-classifier-proof.json')
        source = REPO / 'research/benchmark-interventions/2026-09-13-huey-mini-pipe-drain/harbor-classifier-proof.json'
        shutil.copyfile(source, proof)
        self.audit_path = self.registry.with_name('exception-audit.json')
        self.audit = {'schema_version': 1, 'kind': 'harbor_transcript_exception_mapping',
            'trial': self.relative, 'audited_at': '2026-09-13T22:49:00Z',
            'exception_type': 'ApiRateLimitError', 'actual_exit_code': 124, 'matched_pattern': 'rate.?limit',
            'planned_intervention': self.link(planned), 'guard_close_intent': self.link(intent),
            'classifier_proof': self.link(proof),
            'files': {name: self.hash(self.trial / name) for name in (
                'result.json', 'benchmark-deadline.json', 'agent/mini-swe-agent.txt')}}
        self.write(self.audit_path, self.audit)

    def test_registration_can_be_checked_before_any_terminal_artifacts_exist(self):
        for name in intervention.FINAL_FILES:
            (self.trial / name).unlink()
        entry, observation, _ = intervention.validate_registration(self.registry, root=self.root)
        self.assertEqual(entry['status'], 'planned')
        self.assertEqual(observation['guard']['pid'], 1776)

    def test_planned_terminal_intervention_is_infrastructure_without_mutating_grade(self):
        before = deepcopy(self.result)
        raw = (self.trial / 'result.json').read_bytes()
        with patch.object(incidents, 'ROOT', self.root):
            classification = incidents.classify_incident(self.trial, self.result)
        self.assertTrue(classification['pending_finalization'])
        self.assertFalse(classification['resolved'])
        self.assertTrue(classification['interrupted'])
        self.assertEqual(self.result, before)
        self.assertEqual((self.trial / 'result.json').read_bytes(), raw)

    def test_finalized_intervention_remains_unresolved_and_binds_all_raw_artifacts(self):
        self.finalize()
        self.assertFalse(self.classify()['pending_finalization'])
        self.assertFalse(self.classify()['resolved'])
        for name in intervention.FINAL_FILES:
            path = self.trial / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self.classify()
            path.write_bytes(original)

    def test_unrelated_ordinary_failure_or_unfinished_trial_is_not_reclassified(self):
        self.assertIsNone(intervention.classify_intervention(self.root / 'jobs/other', self.result, root=self.root))
        self.assertIsNone(intervention.classify_intervention(self.root.parent / 'outside-jobs', self.result, root=self.root))
        self.result['finished_at'] = None
        self.assertIsNone(self.classify())

    def test_all_frozen_identity_files_are_bound(self):
        for name in intervention.IDENTITY_FILES:
            path = self.trial / name
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self.classify()
            path.write_bytes(original)

    def test_wrong_trial_task_config_exception_or_retroactive_registration_is_rejected(self):
        variants = [('trial_name', 'other'), ('task_name', 'other'), ('task_checksum', 'b' * 64),
                    ('config', {}), ('finished_at', '2026-09-13T22:46:55Z'),
                    ('exception_info', {'exception_type': 'CancelledError'})]
        for key, value in variants:
            original = deepcopy(self.result)
            self.result[key] = value
            self.write(self.trial / 'result.json', self.result)
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.classify()
            self.result = original
        self.write(self.trial / 'result.json', self.result)
        self.result['exception_info']['exception_type'] = 'AgentTimeoutError'
        self.write(self.trial / 'result.json', self.result)
        self.assertTrue(self.classify()['interrupted'])

    def test_unknown_failed_or_changed_reproduction_is_rejected(self):
        original = self.proof_path.read_bytes()
        proof = json.loads(original)
        for field, value in [('passed', False), ('model_calls', 1), ('source_files_unchanged', False)]:
            changed = dict(proof, **{field: value})
            self.write(self.proof_path, changed)
            self.entry['proof'] = self.link(self.proof_path)
            self.write(self.registry, self.entry)
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.classify()
        self.proof_path.write_bytes(original)
        self.entry['proof'] = self.link(self.proof_path)
        self.write(self.registry, self.entry)
        source = self.proof_path.parent / 'source/mini_local.py'
        source.write_bytes(source.read_bytes() + b'\n')
        with self.assertRaises(AssertionError):
            self.classify()

    def test_incomplete_or_different_guard_cleanup_is_rejected(self):
        variants = [('pid', 16974), ('identity', 'changed'), ('reason', 'deadline'),
                    ('return_code', 0), ('quiescent', False),
                    ('stop_requested_at_epoch', epoch('2026-09-13T22:46:59Z'))]
        for key, value in variants:
            changed = deepcopy(self.deadline)
            changed['executions'][0][key] = value
            self.write(self.trial / 'benchmark-deadline.json', changed)
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.classify()
        for key, value in [('quiescent', False), ('cleanup_error', 'AgentQuiescenceError'),
                           ('helper_sha256', 'changed')]:
            self.write(self.trial / 'benchmark-deadline.json', dict(self.deadline, **{key: value}))
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.classify()

    def test_short_observation_progress_and_wrong_pipe_or_owner_are_rejected(self):
        original = deepcopy(self.snapshots)
        for field, value in [('cpu_ticks', ['301', '11']), ('start_ticks', '999'),
                             ('pipes', {'4': 'pipe:[42]'})]:
            self.snapshots = deepcopy(original)
            self.snapshots[-1]['processes'][1][field] = value
            self.rebind_observation()
            with self.subTest(field=field), self.assertRaises(AssertionError):
                self.classify()
        self.snapshots = deepcopy(original)
        self.snapshots[-1]['processes'][2]['parent_pid'] = 1
        self.rebind_observation()
        with self.assertRaises(AssertionError):
            self.classify()
        self.snapshots = deepcopy(original)
        self.snapshots[0]['observed_at'] = '2026-09-13T22:46:40Z'
        self.rebind_observation()
        with self.assertRaises(AssertionError):
            self.classify()

    def test_observation_and_recursive_sources_invalidate_registry_cache(self):
        with patch.object(incidents, 'ROOT', self.root):
            before = incidents.registry_cache_stamp()
            for path in (self.observation_path, self.snapshots_path, self.proof_path,
                         self.proof_path.parent / 'source/benchmark_mini_tool_cleanup.py'):
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.subTest(path=path.name):
                    self.assertNotEqual(incidents.registry_cache_stamp(), before)
                    with self.assertRaises(AssertionError):
                        self.classify()
                path.write_bytes(original)

    def test_duplicate_registration_and_outside_evidence_paths_are_rejected(self):
        other = self.registry.parent.parent / 'duplicate/intervention.json'
        self.write(other, self.entry)
        with self.assertRaises(AssertionError):
            self.classify()
        other.unlink()
        self.entry['observation']['path'] = 'research/../../outside.json'
        self.write(self.registry, self.entry)
        with self.assertRaises(AssertionError):
            self.classify()

    def test_trial_artifact_changes_invalidate_supervisor_cache(self):
        self.finalize()
        with patch.object(incidents, 'ROOT', self.root):
            before = incidents.registry_cache_stamp()
            for name in sorted(intervention.IDENTITY_FILES | intervention.FINAL_FILES):
                path = self.trial / name
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.subTest(name=name):
                    self.assertNotEqual(incidents.registry_cache_stamp(), before)
                    with self.assertRaises(AssertionError):
                        self.classify()
                path.write_bytes(original)

    def test_audited_transcript_alias_preserves_preaction_expectations_and_raw_grade(self):
        before = self.registry.read_bytes()
        self.audit_alias()
        raw = (self.trial / 'result.json').read_bytes()
        self.assertTrue(self.classify()['pending_finalization'])
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(self.entry['expected_exceptions'], sorted(intervention.EXPECTED_EXCEPTIONS))
        self.finalize()
        result = self.classify()
        self.assertTrue(result['interrupted'])
        self.assertFalse(result['resolved'])
        self.assertFalse(result['pending_finalization'])
        self.assertIn('exception_audit_sha256', result)
        self.assertEqual((self.trial / 'result.json').read_bytes(), raw)
        self.assertEqual(self.result['verifier_result']['rewards']['reward'], 1)

    def test_unaudited_rate_error_is_not_accepted_or_generalized(self):
        self.result['exception_info']['exception_type'] = 'ApiRateLimitError'
        self.write(self.trial / 'result.json', self.result)
        with self.assertRaises((AssertionError, RuntimeError)):
            self.classify()
        self.assertIsNone(intervention.classify_intervention(self.root / 'jobs/unrelated', self.result, root=self.root))

    def test_alias_requires_original_intent_and_approved_classifier_source_and_patterns(self):
        self.audit_alias()
        for link_name in ('planned_intervention', 'guard_close_intent', 'classifier_proof'):
            path = self.root / self.audit[link_name]['path']
            original = path.read_bytes()
            changed = json.loads(original)
            if link_name == 'planned_intervention':
                changed['expected_exceptions'].append('ApiRateLimitError')
            elif link_name == 'guard_close_intent':
                changed['registry_sha256'] = '0' * 64
            else:
                changed['patterns'][0]['pattern'] = '.*'
            self.write(path, changed)
            saved_link = self.audit[link_name]
            self.audit[link_name] = self.link(path)
            self.write(self.audit_path, self.audit)
            with self.subTest(link_name=link_name), self.assertRaises(AssertionError):
                self.classify()
            path.write_bytes(original)
            self.audit[link_name] = saved_link
            self.write(self.audit_path, self.audit)
        proof_path = self.root / self.audit['classifier_proof']['path']
        proof = json.loads(proof_path.read_text())
        proof['class_source'] += '\n# changed\n'
        self.write(proof_path, proof)
        self.audit['classifier_proof'] = self.link(proof_path)
        self.write(self.audit_path, self.audit)
        with self.assertRaises(AssertionError):
            self.classify()

    def test_alias_rejects_non_guard_exit_or_missing_transcript_match(self):
        self.audit_alias()
        self.result['exception_info']['exception_message'] = 'Command failed (exit 1): synthetic'
        self.write(self.trial / 'result.json', self.result)
        self.audit['files']['result.json'] = self.hash(self.trial / 'result.json')
        self.write(self.audit_path, self.audit)
        with self.assertRaises(AssertionError):
            self.classify()
        self.result['exception_info']['exception_message'] = 'Command failed (exit 124): synthetic'
        self.write(self.trial / 'result.json', self.result)
        (self.trial / 'agent/mini-swe-agent.txt').write_text('No matching phrase.\n')
        self.audit['files'] = {name: self.hash(self.trial / name) for name in self.audit['files']}
        self.write(self.audit_path, self.audit)
        with self.assertRaises(AssertionError):
            self.classify()

    def test_alias_audit_links_and_transcript_invalidate_supervisor_cache(self):
        self.audit_alias()
        before = intervention.registry_cache_stamp(root=self.root)
        paths = [self.audit_path, self.trial / 'agent/mini-swe-agent.txt'] + [
            self.root / self.audit[key]['path'] for key in (
                'planned_intervention', 'guard_close_intent', 'classifier_proof')]
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b' ')
            with self.subTest(path=path.name):
                self.assertNotEqual(intervention.registry_cache_stamp(root=self.root), before)
                if path != self.audit_path:
                    with self.assertRaises(AssertionError):
                        self.classify()
            path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()

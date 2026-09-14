"""Failure-path and once-only tests; no containers, claims or model calls."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import recovery_common as common
import continue_confirmed_validation as continuation


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous = common.read(common.BASE / 'continuation-confirmed-003/summary.json')
        self.failed = common.read(common.OLD_DIAGNOSTICS / 'summary.json')
        self.entries = {'summary.json', 'omit-zero-config-rejection.controller.log'}
        self.gate = {'controls_reference': common.reference(common.BASE / 'controls-confirmed-001/summary.json'),
                     'image_proof': {'path': str(common.BASE / 'oracle-independent-validation-001/verifier-image-proof.json')},
                     'verifier_image_id': 'sha256:' + 'a' * 64, 'task': {}}

    def validate(self):
        common.validate_prior_documents(self.previous, self.failed, common.ERROR_TEXT, self.entries)

    def test_exact_prior_failure_is_reusable(self):
        self.validate()

    def test_nonterminal_prior_is_rejected(self):
        self.previous['finished_at'] = None
        with self.assertRaises(ValueError): self.validate()

    def test_failed_quality_cannot_be_reused(self):
        self.previous['stages']['quality-review']['exit_code'] = 1
        with self.assertRaises(ValueError): self.validate()

    def test_unexpected_later_stage_is_rejected(self):
        self.previous['stages']['paired-full-regrades'] = {'exit_code': 0, 'finished_at': 'x'}
        with self.assertRaises(ValueError): self.validate()

    def test_executed_case_cannot_be_called_pre_admission(self):
        self.failed['cases'] = [{'name': 'omit-zero-config-rejection'}]
        with self.assertRaises(ValueError): self.validate()

    def test_claim_or_result_or_case_file_is_rejected(self):
        for extra in ['result.json', 'control.json', 'omit-zero-config-rejection']:
            with self.subTest(extra=extra):
                with self.assertRaises(ValueError):
                    common.validate_prior_documents(self.previous, self.failed, common.ERROR_TEXT, self.entries | {extra})

    def test_unknown_failure_is_rejected(self):
        with self.assertRaises(ValueError):
            common.validate_prior_documents(self.previous, self.failed, 'Other failure\n', self.entries)

    def test_changed_prior_harness_is_rejected(self):
        self.failed['harness_sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.validate()

    def test_changed_quality_binding_is_rejected(self):
        self.previous['quality_gate']['audit']['sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.validate()

    def test_source_drift_rejected(self):
        with patch.object(common.prior, 'frozen_sources', side_effect=ValueError('frozen source drift')):
            with self.assertRaisesRegex(ValueError, 'source drift'): common.frozen_sources()

    def test_exact_evidence_hash_drift_rejected(self):
        with self.assertRaises(ValueError): common.exact(common.AUDIT, '0' * 64)

    def test_changed_controls_rejected_before_delegation(self):
        with patch.object(common.prior, 'combined_controls') as delegate:
            with self.assertRaises(ValueError): common.combined_controls('anything', '0' * 64)
            delegate.assert_not_called()

    def test_live_prior_pid_rejected(self):
        original = Path.exists
        def exists(p):
            if str(p) == '/proc/self/stat' or str(p) == '/proc/82357': return True
            return original(p)
        with patch.object(Path, 'exists', exists):
            with self.assertRaisesRegex(ValueError, 'still present'): common.prior_failure_gate()

    def test_nonlinux_check_rejected(self):
        original = Path.exists
        with patch.object(Path, 'exists', lambda p: False if str(p) == '/proc/self/stat' else original(p)):
            with self.assertRaisesRegex(ValueError, 'Linux Harbor'): common.prior_failure_gate()

    def test_repair_requires_all_eight_cases(self):
        proof = common.read(common.BASE / 'diagnostic-path-preflight-fix-001/actual-preflight.json')
        proof['cases'].pop()
        with patch.object(common, 'exact', return_value=proof):
            with self.assertRaisesRegex(ValueError, 'eight'): common.repair_preflight_gate()

    def test_repair_preflight_rejects_execution(self):
        proof = common.read(common.BASE / 'diagnostic-path-preflight-fix-001/actual-preflight.json')
        proof['container_launches'] = 1
        with patch.object(common, 'exact', return_value=proof):
            with self.assertRaises(ValueError): common.repair_preflight_gate()

    def test_no_quality_command_and_exact_outputs(self):
        stages = continuation.stage_commands(self.gate)
        self.assertEqual([row[0] for row in stages], ['focused-diagnostics', 'paired-file-gate', 'paired-full-regrades'])
        self.assertEqual(Path(stages[0][1][0]).name, 'run_followup_diagnostics_v2.py')
        self.assertEqual(stages[0][1][-1], common.DIAGNOSTICS)
        for label, argv in stages:
            self.assertNotIn('quality', str(argv))
            self.assertNotIn('mini', str(argv))
        self.assertIn('--check-only', stages[1][1])
        self.assertIn('--run', stages[2][1])
        self.assertIn(common.PAIRED, stages[2][1])

    def test_existing_output_or_third_review_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            b = Path(directory)
            with patch.multiple(common, BASE=b, OUTPUT=b/'continuation', DIAGNOSTICS=b/'diagnostics', PAIRED=b/'paired'):
                common.fresh_outputs()
                for name in ['continuation', 'diagnostics', 'paired', 'reviews-final-003']:
                    (b/name).mkdir()
                    with self.assertRaises(ValueError): common.fresh_outputs()
                    (b/name).rmdir()

    def test_symlink_output_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            b = Path(directory)
            (b/'continuation').symlink_to(b/'missing')
            with patch.multiple(common, BASE=b, OUTPUT=b/'continuation', DIAGNOSTICS=b/'diagnostics', PAIRED=b/'paired'):
                with self.assertRaises(ValueError): common.fresh_outputs()

    def test_quality_runs_official_packager_gate_without_subprocess(self):
        validator = Mock()
        with patch.object(common, 'module', return_value=validator), patch.object(continuation.subprocess, 'Popen') as launch:
            common.quality_gate(self.gate)
            validator.verify_revision_quality.assert_called_once()
            launch.assert_not_called()

    def test_check_only_does_not_write_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            b = Path(directory)
            with patch.object(continuation, 'preflight', return_value=(self.gate, {})) as preflight, \
                 patch.object(continuation.subprocess, 'Popen') as launch, patch.object(common, 'OUTPUT', b/'new'), \
                 patch.object(sys, 'argv', ['script', '--controls-summary', 'controls', '--controls-sha256', common.CONTROLS_SHA]), \
                 patch('sys.stdout', new_callable=io.StringIO):
                continuation.main()
                preflight.assert_called_once_with('controls', common.CONTROLS_SHA, fresh=True)
                launch.assert_not_called()
                self.assertEqual(list(b.iterdir()), [])

    def test_failed_first_child_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            b = Path(directory)
            child = Mock(pid=23456)
            child.wait.return_value = 1
            child.poll.return_value = 1
            with patch.multiple(common, BASE=b, ROOT=b, OUTPUT=b/'output'), \
                 patch.object(continuation, 'preflight', return_value=(self.gate, {})), \
                 patch.object(continuation.subprocess, 'Popen', return_value=child) as launch:
                self.assertEqual(continuation.run_once('controls', common.CONTROLS_SHA), 1)
                self.assertEqual(launch.call_count, 1)
                summary = json.loads((b/'output/summary.json').read_text())
                self.assertEqual(set(summary['stages']), {'focused-diagnostics'})
                self.assertEqual(summary['new_quality_reviews'], 0)
                self.assertFalse(summary['passed'])

    def test_same_continuation_lock_blocks_second_owner(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            b = Path(directory)
            with (b/'continuation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(common, 'BASE', b), patch.object(continuation, 'preflight') as preflight:
                    with self.assertRaises(BlockingIOError): continuation.run_once('controls', common.CONTROLS_SHA)
                    preflight.assert_not_called()

    def test_paired_gate_rejects_incomplete_or_counted_runs(self):
        valid = dict(validation_passed=True, all_regrades_complete=True, regrades=[{}]*9,
                     model_calls=0, counted_sweep_trials=0, controls_summary_sha256=common.CONTROLS_SHA,
                     diagnostics_summary_sha256='a'*64)
        for field, value in [('all_regrades_complete', False), ('regrades', [{}]*8), ('model_calls', 1),
                             ('counted_sweep_trials', 1), ('diagnostics_summary_sha256', 'b'*64)]:
            bad = copy.deepcopy(valid); bad[field] = value
            with self.subTest(field=field), patch.object(common, 'read', return_value=bad), patch.object(common, 'digest', return_value='a'*64):
                with self.assertRaises(ValueError): common.paired_gate(common.CONTROLS_SHA)


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Pure failure gates and single-use controller tests; no Docker/model calls."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import recovery_common as common
import continue_confirmed_validation as continuation


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.previous = common.read(common.OLD_OUTPUT / 'summary.json')
        self.paired = common.read(common.OLD_PAIRED / 'summary.json')
        self.case = common.read(common.OLD_PAIRED / '01-D3tzpaW/result.json')
        self.error = (common.OLD_PAIRED / '01-D3tzpaW/artifacts/03-inspect-limits.log').read_text()
        self.gate = {'controls_reference': common.reference(common.BASE / 'controls-confirmed-001/summary.json'),
                     'image_proof': {'path': str(common.BASE / 'oracle-independent-validation-001/verifier-image-proof.json')},
                     'verifier_image_id': 'sha256:' + 'a' * 64}

    def validate(self):
        common.validate_prior_documents(self.previous, self.paired, self.case, self.error)

    def test_exact_preserved_pre_verifier_failure_is_reusable(self):
        self.validate()

    def test_nonterminal_or_successful_prior_controller_is_rejected(self):
        for key, value in [('finished_at', None), ('passed', True)]:
            with self.subTest(key=key):
                altered = copy.deepcopy(self.previous); altered[key] = value
                with self.assertRaises(ValueError):
                    common.validate_prior_documents(altered, self.paired, self.case, self.error)

    def test_failed_completed_diagnostics_are_not_reused(self):
        self.previous['stages']['focused-diagnostics']['exit_code'] = 1
        with self.assertRaises(ValueError): self.validate()

    def test_later_cases_or_prior_rewards_are_rejected(self):
        self.paired['regrades'].append(copy.deepcopy(self.paired['regrades'][0]))
        with self.assertRaises(ValueError): self.validate()
        self.paired['regrades'].pop(); self.case['reward'] = 0
        with self.assertRaises(ValueError): self.validate()

    def test_verifier_execution_is_not_called_setup_failure(self):
        self.case['lifecycle'].append({'event': 'full-verifier-start'})
        with self.assertRaises(ValueError): self.validate()

    def test_unreleased_claim_or_unremoved_container_is_rejected(self):
        for field in ['claim_absent', 'container_absent']:
            with self.subTest(field=field):
                case = copy.deepcopy(self.case); case['cleanup'][field] = False
                with self.assertRaises(ValueError):
                    common.validate_prior_documents(self.previous, self.paired, case, self.error)

    def test_missing_output_collection_errors_must_be_preserved(self):
        self.case['cleanup']['errors'] = []
        with self.assertRaises(ValueError): self.validate()

    def test_unknown_inspection_failure_is_rejected(self):
        self.error = 'Cannot connect to Docker daemon'
        with self.assertRaises(ValueError): self.validate()

    def test_only_two_paired_stages_no_new_quality_or_diagnostics(self):
        stages = continuation.stage_commands(self.gate)
        self.assertEqual([name for name, _ in stages], ['paired-file-gate', 'paired-full-regrades'])
        for _, argv in stages:
            self.assertEqual(Path(argv[0]).name, 'run_paired_regrades_v2.py')
            self.assertIn(common.PAIRED, argv)
            self.assertIn(common.DIAGNOSTICS / 'summary.json', argv)
        self.assertEqual(stages[0][1][-1], '--check-only')
        self.assertEqual(stages[1][1][-1], '--run')

    def test_fresh_output_guard_preserves_completed_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); (base/'diagnostics').mkdir()
            with patch.multiple(common, BASE=base, OUTPUT=base/'output', PAIRED=base/'paired', DIAGNOSTICS=base/'diagnostics'):
                common.fresh_outputs()
                (base/'output').mkdir()
                with self.assertRaises(ValueError): common.fresh_outputs()

    def test_check_only_does_not_create_output_or_launch_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.object(continuation, 'preflight', return_value=(self.gate, {})), \
                 patch.object(continuation.subprocess, 'Popen') as launch, \
                 patch.object(common, 'OUTPUT', base/'new'), \
                 patch.object(sys, 'argv', ['script', '--controls-summary', 'controls', '--controls-sha256', common.CONTROLS_SHA]), \
                 patch('sys.stdout', new_callable=io.StringIO):
                continuation.main(); launch.assert_not_called(); self.assertEqual(list(base.iterdir()), [])

    def test_failed_file_gate_stops_before_full_regrade_without_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary); child = Mock(pid=23456)
            child.wait.return_value = 1; child.poll.return_value = 1
            with patch.multiple(common, BASE=base, ROOT=base, OUTPUT=base/'output'), \
                 patch.object(continuation, 'preflight', return_value=(self.gate, {})), \
                 patch.object(common, 'diagnostics_gate', return_value={}), \
                 patch.object(continuation.subprocess, 'Popen', return_value=child) as launch:
                self.assertEqual(continuation.run_once('controls', common.CONTROLS_SHA), 1)
                self.assertEqual(launch.call_count, 1)
                summary = json.loads((base/'output/summary.json').read_text())
                self.assertEqual(set(summary['stages']), {'paired-file-gate'})
                self.assertEqual(summary['new_focused_diagnostics'], 0)

    def test_same_overall_lock_excludes_second_owner(self):
        import fcntl
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with (base/'continuation.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(common, 'BASE', base), patch.object(continuation, 'preflight') as preflight:
                    with self.assertRaises(BlockingIOError): continuation.run_once('controls', common.CONTROLS_SHA)
                    preflight.assert_not_called()

    def test_all_nine_complete_zero_reward_regrades_are_allowed(self):
        good = dict(validation_passed=True, all_regrades_complete=True,
                    regrades=[dict(validation_passed=True, reward=0) for _ in range(9)],
                    model_calls=0, counted_sweep_trials=0, controls_summary_sha256=common.CONTROLS_SHA,
                    diagnostics_summary_sha256='a'*64)
        with patch.object(common, 'read', return_value=good), patch.object(common, 'digest', return_value='a'*64), \
             patch.object(common, 'reference', return_value={'path': 'synthetic', 'sha256': 'a'*64}):
            common.paired_gate(common.CONTROLS_SHA)
            good['regrades'][0]['validation_passed'] = False
            with self.assertRaises(ValueError): common.paired_gate(common.CONTROLS_SHA)


if __name__ == '__main__':
    unittest.main(verbosity=2)

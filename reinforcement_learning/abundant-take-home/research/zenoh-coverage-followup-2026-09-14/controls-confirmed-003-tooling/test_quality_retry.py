"""Retry gates on private copies of the actual first failure; zero child launches."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('retry_gate_tests', HERE / 'recovery_common.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
REAL_BASE = c.BASE; REAL_ROOT = c.ROOT


class QualityRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve(); base = root / REAL_BASE.relative_to(REAL_ROOT)
        here = base / HERE.name; here.mkdir(parents=True)
        for name in ['reviews-final-001', 'continuation-confirmed-002', 'quality-output-path-diagnosis-001']:
            shutil.copytree(REAL_BASE / name, base / name)
        shutil.copyfile(HERE / 'prior-attempt-preservation.json', here / 'prior-attempt-preservation.json')
        task = base / c.TASK.name
        values = dict(ROOT=root, BASE=base, HERE=here, TASK=task,
            REVIEW_OUTPUT=base / 'reviews-final-002', AUDIT=base / 'completed-review-audits-002' / (task.name + '.json'),
            DIAGNOSTICS=base / 'harness/diagnostics-final', PAIRED=base / 'paired-regrades/run-001',
            OUTPUT=base / 'continuation-confirmed-003')
        self.patches = [patch.object(c, k, v) for k, v in values.items()]
        for p in self.patches: p.start()
        self.auth = c.BASE / 'quality-output-path-diagnosis-001/retry-authorization.json'
        self.diag = self.auth.with_name('diagnosis.json')

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def rewrite(self, path, value):
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

    def bind_changed_diagnosis(self, diagnosis):
        self.rewrite(self.diag, diagnosis)
        auth = c.read(self.auth); auth['diagnosis'] = c.reference(self.diag); self.rewrite(self.auth, auth)
        self.patches += [patch.object(c, 'DIAGNOSIS_SHA', c.digest(self.diag)), patch.object(c, 'RETRY_AUTH_SHA', c.digest(self.auth))]
        for p in self.patches[-2:]: p.start()

    def test_actual_first_missing_output_is_only_allowed_prior(self):
        prior = c.prior_review_exception()
        self.assertEqual(prior['prior_review'], c.BASE / 'reviews-final-001')
        c.fresh_outputs()

    def test_missing_authorization_rejected(self):
        self.auth.unlink()
        with self.assertRaises(FileNotFoundError): c.fresh_outputs()

    def test_authorization_hash_drift_rejected(self):
        self.auth.write_text(self.auth.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'hash drift'): c.fresh_outputs()

    def test_third_attempt_authorization_rejected_even_if_bound(self):
        auth = c.read(self.auth); auth['max_total_reviewer_attempts'] = 3; self.rewrite(self.auth, auth)
        with patch.object(c, 'RETRY_AUTH_SHA', c.digest(self.auth)), self.assertRaisesRegex(ValueError, 'no third review'):
            c.fresh_outputs()

    def test_unknown_diagnosed_error_rejected_even_if_bound(self):
        d = c.read(self.diag); d['actual_tool_write_path'] = '/some/other/failure'; self.bind_changed_diagnosis(d)
        with self.assertRaisesRegex(ValueError, 'Only the diagnosed output-placement'): c.fresh_outputs()

    def test_unknown_official_error_rejected_even_if_bound(self):
        d = c.read(self.diag); report = c.path(d['official_report']['path']); value = c.read(report)
        value['results'][0]['error'] = 'Provider error'; self.rewrite(report, value)
        d['official_report'] = c.reference(report); self.bind_changed_diagnosis(d)
        with self.assertRaisesRegex(ValueError, 'Missing official report'): c.fresh_outputs()

    def test_raw_reward_one_cannot_be_retry_basis(self):
        d = c.read(self.diag); raw = c.path(d['raw_result']['path']); value = c.read(raw)
        value['verifier_result']['rewards']['reward'] = 1; self.rewrite(raw, value)
        d['raw_result'] = c.reference(raw); self.bind_changed_diagnosis(d)
        with self.assertRaisesRegex(ValueError, 'Prior raw zero'): c.fresh_outputs()

    def test_nonterminal_prior_continuation_rejected(self):
        d = c.read(self.diag); p = c.path(d['stopped_continuation']['path']); value = c.read(p)
        value['finished_at'] = None; self.rewrite(p, value)
        d['stopped_continuation'] = c.reference(p); self.bind_changed_diagnosis(d)
        with self.assertRaisesRegex(ValueError, 'Prior continuation must be terminal'): c.fresh_outputs()

    def test_prior_started_diagnostics_rejected(self):
        d = c.read(self.diag); p = c.path(d['stopped_continuation']['path']); value = c.read(p)
        value['stages']['focused-diagnostics'] = {'finished_at': '2026-09-14T08:00:00Z', 'exit_code': 1}
        self.rewrite(p, value); d['stopped_continuation'] = c.reference(p); self.bind_changed_diagnosis(d)
        with self.assertRaisesRegex(ValueError, 'Prior continuation must be terminal'): c.fresh_outputs()

    def test_prior_tree_addition_rejected(self):
        (c.BASE / 'reviews-final-001/unbound-extra.txt').write_text('unbound')
        with self.assertRaisesRegex(ValueError, 'tree drift'): c.fresh_outputs()

    def test_synthesized_former_missing_report_rejected(self):
        d = c.read(self.diag); raw = c.path(d['raw_trial'])
        (raw / 'artifacts/check-result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'must remain missing'): c.fresh_outputs()

    def test_existing_second_review_is_durable_no_third_marker(self):
        c.REVIEW_OUTPUT.mkdir()
        with self.assertRaisesRegex(ValueError, 'existing review'): c.fresh_outputs()

    def test_unrecognized_prior_quality_job_rejected(self):
        (c.BASE / 'reviews-other/jobs' / (c.TASK.name + '-quality-other')).mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'prior final-task quality job'): c.fresh_outputs()

    def test_new_regenerated_wrapper_identical_passes(self):
        prior = c.prior_review_exception()['wrapper']
        record = copy.deepcopy(prior); record['ephemeral_wrapper_path'] = '/tmp/new-ephemeral-path'
        record['captured_wrapper'] = 'new-proof/generated-wrapper'
        self.assertTrue(c.validate_regenerated_wrapper(record))

    def test_instruction_rubric_resource_or_task_drift_stops_before_return(self):
        prior = c.prior_review_exception()['wrapper']
        for key in ['instruction.md', 'task.toml', 'tests/criteria.json', 'tests/validate.py', 'environment/task/instruction.md']:
            record = copy.deepcopy(prior); record['captured_wrapper_file_sha256'][key] = '0' * 64
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'no model launch'):
                c.validate_regenerated_wrapper(record)

    def test_wrapper_checksum_drift_rejected(self):
        record = copy.deepcopy(c.prior_review_exception()['wrapper']); record['wrapper_checksum'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'no model launch'): c.validate_regenerated_wrapper(record)

    def test_additive_audit_changes_only_base_and_output_paths(self):
        expected = (REAL_BASE / 'audit_final_quality_review.py').read_text().replace(
            'BASE = Path(__file__).resolve().parent', 'BASE = Path(__file__).resolve().parent.parent').replace(
            "BASE/'reviews-final-001'", "BASE/'reviews-final-002'").replace(
            "BASE/'completed-review-audits'", "BASE/'completed-review-audits-002'")
        self.assertEqual((HERE / 'audit_final_quality_review.py').read_text(), expected)

    def test_execute_copy_changes_only_base_and_prelaunch_identity_gate(self):
        expected = (REAL_BASE / 'execute_quality_check.py').read_text().replace(
            'BASE = Path(__file__).resolve().parent', 'BASE = Path(__file__).resolve().parent.parent').replace(
            '        wrapper_records.append(record)', '        import recovery_common as retry_gate\n        retry_gate.validate_regenerated_wrapper(record)\n        wrapper_records.append(record)')
        self.assertEqual((HERE / 'execute_quality_retry.py').read_text(), expected)

    def test_underlying_harbor_command_preserves_every_review_setting(self):
        spec = importlib.util.spec_from_file_location('quality_command_test', HERE / 'run_confirmed_quality_review.py')
        quality = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'recovery_common': c}): spec.loader.exec_module(quality)
        first = c.read(c.BASE / 'reviews-final-001/summary.json')['reviews'][c.TASK.name]['command']
        second = quality.command(c.REVIEW_OUTPUT / 'new-proof', c.TASK.name + '-quality-new')
        def normalized(argv):
            tail = list(map(str, argv[argv.index('--') + 1:]))
            self.assertEqual(tail[0], 'check'); tail[1] = '<same-final-task>'
            for flag in ('--jobs-dir', '--job-name'): tail[tail.index(flag) + 1] = '<new-evidence-identity>'
            return tail
        self.assertEqual(normalized(first), normalized(second))


if __name__ == '__main__': unittest.main(verbosity=2)

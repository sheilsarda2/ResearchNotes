"""Exercise sequencing and failure stops with inert children; no Docker/model calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('continuation', Path(__file__).with_name('continue_validation.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Continuation(unittest.TestCase):
    def exercise(self, quality_exit=0, rubric_pass=11, duplicate=False, stale_audit=False, audit_mismatch=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base = root / 'research/followup'
            base.mkdir(parents=True)
            for name in ('continue_validation.py', 'run_followup_diagnostics.py',
                         'run_final_quality_review.py', 'execute_quality_check.py',
                         'audit_final_quality_review.py', 'validate_controls_immutable.py',
                         'harness/focused_validation_v2.py', 'harness/case_inside_v2.py',
                         'paired-regrades/run_paired_regrades.py', 'paired-regrades/case_inside.py',
                         'task-manifest.json', 'revision.json', 'paired-inputs/manifest.json',
                         'mutants/manifest.json', 'saved-source-audit/full-source-preflight.json'):
                path = base / name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_text('{}')
            (base / 'task-manifest.json').write_text(json.dumps({'harbor_task_checksum': 'fixture'}))
            (base / 'mutants/manifest.json').write_text(json.dumps({'mutants': list(range(6))}))
            (base / 'saved-source-audit/full-source-preflight.json').write_text(json.dumps({'trials': list(range(2))}))
            audit_target = base / 'completed-review-audits/rs-zenoh-timestamp-instrumentation-v4-validation.json'
            if stale_audit:
                audit_target.parent.mkdir()
                audit_target.write_text('{}')
            controls = base / 'harbor-controls-final'
            controls.mkdir()
            (controls / 'summary.json').write_text(json.dumps({'passed': True, 'finished_at': 'done'}))
            (controls / 'verifier-image-proof.json').write_text(json.dumps({'verifier_image_id': 'sha256:fixture'}))
            (base / 'latest-controls.json').write_text(json.dumps({'output': str(controls.relative_to(root)), 'pid': -1}))
            if duplicate:
                (base / 'reviews-final-001').mkdir()
            calls, children = [], []

            class Child:
                pid = 123

                def __init__(self, code):
                    self.code, self.returncode = code, None

                def poll(self):
                    return self.returncode

                def wait(self):
                    self.returncode = self.code
                    return self.code

            def launch(command, **kwargs):
                self.assertFalse(any(child.poll() is None for child in children), 'Stages overlapped')
                name = Path(command[2]).name
                calls.append((name, '--check-only' in command))
                child = Child(quality_exit if name == 'run_final_quality_review.py' else 0)
                children.append(child)
                if name == 'run_final_quality_review.py':
                    output = base / 'reviews-final-001'
                    output.mkdir()
                    report = output / 'report.json'
                    report.write_text('{}')
                    quality_row = {'check_report': str(report.relative_to(root)),
                                   'check_report_sha256': module.digest(report),
                                   'original_task_checksum': 'fixture',
                                   'result': str((output / 'trial/result.json').relative_to(root))}
                    (output / 'summary.json').write_text(json.dumps({'passed': True, 'reviews': {
                        'rs-zenoh-timestamp-instrumentation-v4-validation': quality_row}}))
                if name == 'audit_final_quality_review.py':
                    output = base / 'completed-review-audits'
                    output.mkdir()
                    (output / 'rs-zenoh-timestamp-instrumentation-v4-validation.json').write_text(json.dumps({
                        'valid_review_report': True, 'rubric_pass': rubric_pass,
                        'check_report': str((base / 'reviews-final-001/report.json').relative_to(root)),
                        'check_report_sha256': 'tampered' if audit_mismatch else module.digest(base / 'reviews-final-001/report.json'),
                        'raw_trial': str((base / 'reviews-final-001/trial').relative_to(root)),
                        'original_task_checksum': 'fixture',
                        'rubric_fail': [] if rubric_pass == 11 else ['behavior_in_tests']}))
                if name == 'run_followup_diagnostics.py':
                    output = base / 'harness/diagnostics-final'
                    output.mkdir()
                    (output / 'summary.json').write_text(json.dumps({'passed': True}))
                return child

            with patch.object(module, 'BASE', base), patch.object(module, 'ROOT', root), \
                    patch.object(module, 'identity', return_value=None), \
                    patch.object(module.sys, 'argv', ['continue_validation.py', '--run']), \
                    patch.object(module.subprocess, 'Popen', side_effect=launch):
                if duplicate or stale_audit:
                    with self.assertRaises(AssertionError):
                        module.main()
                elif quality_exit or rubric_pass != 11 or audit_mismatch:
                    with self.assertRaises(SystemExit):
                        module.main()
                else:
                    module.main()
            self.assertTrue(all(child.poll() is not None for child in children))
            return calls

    def test_success_has_one_child_at_a_time_and_only_one_review(self):
        self.assertEqual(self.exercise(), [('run_final_quality_review.py', False),
                         ('audit_final_quality_review.py', False), ('run_followup_diagnostics.py', False),
                         ('run_paired_regrades.py', True), ('run_paired_regrades.py', False)])

    def test_failed_review_is_reaped_without_retry_or_regrade(self):
        self.assertEqual(self.exercise(quality_exit=1), [('run_final_quality_review.py', False)])

    def test_failed_criterion_stops_before_probes_and_regrades(self):
        self.assertEqual(len(self.exercise(rubric_pass=10)), 2)

    def test_existing_review_output_blocks_all_launches(self):
        self.assertEqual(self.exercise(duplicate=True), [])

    def test_existing_audit_blocks_all_launches(self):
        self.assertEqual(self.exercise(stale_audit=True), [])

    def test_audit_must_bind_actual_just_completed_report(self):
        self.assertEqual(len(self.exercise(audit_mismatch=True)), 2)


if __name__ == '__main__':
    unittest.main()

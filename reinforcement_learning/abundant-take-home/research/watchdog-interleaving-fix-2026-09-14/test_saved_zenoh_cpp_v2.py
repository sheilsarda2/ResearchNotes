import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

path = Path(__file__).with_name('run_saved_zenoh_cpp_v2.py')
spec = importlib.util.spec_from_file_location('zenoh_replay_v2_tested', path)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
TARGET = 'test_advanced_pub_sub_zenohpico'


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = Path(self.temp.name)
        self.names = [f'test_before_{i}' for i in range(9)] + [TARGET]
        self.rows = []
        self.save('registered-tests.json', self.names)
        self.save('inputs-unchanged.json', {})

    def save(self, name, value):
        (self.logs / name).write_text(json.dumps(value))

    def case(self, label, names, *, failed=False, skipped=False, status=None, code=None):
        root = ET.Element('testsuite')
        for name in names:
            node = ET.SubElement(root, 'testcase', name=name,
                                 status=status or ('fail' if failed else 'run'))
            if failed:
                ET.SubElement(node, 'failure')
            if skipped:
                ET.SubElement(node, 'skipped')
        ET.ElementTree(root).write(self.logs / (label + '.xml'))
        self.rows.append(dict(label=label, return_code=(8 if failed else 0) if code is None else code,
                              timed_out=False))
        self.save('outcomes.json', dict(results=self.rows, router_alive=True))

    def passing(self):
        for i in range(1, 4):
            self.case(f'isolated-{i}', [TARGET])
        self.case('suite-prefix', self.names)

    def test_three_isolated_and_exact_prefix_pass(self):
        self.passing()
        self.assertTrue(replay.classify(self.logs)['completed'])

    def test_reproduced_first_crash_is_complete_diagnostic(self):
        self.case('isolated-1', [TARGET], failed=True)
        (self.logs / 'isolated-1.log').write_text(TARGET + ' ...***Exception: SegFault')
        result = replay.classify(self.logs)
        self.assertTrue(result['completed'])
        self.assertTrue(result['regression_crash_reproduced'])

    def test_build_log_signal_text_is_not_a_regression_crash(self):
        self.passing()
        (self.logs / 'build.log').write_text('A diagnostic discusses SIGSEGV')
        result = replay.classify(self.logs)
        self.assertTrue(result['completed'])
        self.assertFalse(result['regression_crash_reproduced'])

    def test_failed_assertion_is_not_a_segfault(self):
        self.case('isolated-1', [TARGET], failed=True)
        (self.logs / 'isolated-1.log').write_text(TARGET + ' ... Failed')
        result = replay.classify(self.logs)
        self.assertTrue(result['completed'])
        self.assertFalse(result['regression_crash_reproduced'])

    def test_all_skipped_rejected(self):
        for i in range(1, 4):
            self.case(f'isolated-{i}', [TARGET], skipped=True, status='notrun')
        self.case('suite-prefix', self.names, skipped=True, status='notrun')
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_skipped_with_run_status_rejected(self):
        self.case('isolated-1', [TARGET], skipped=True)
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_no_outcomes_rejected(self):
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_empty_outcome_rows_rejected(self):
        self.passing()
        self.save('outcomes.json', dict(results=[], router_alive=True))
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_missing_prefix_rejected(self):
        for i in range(1, 4):
            self.case(f'isolated-{i}', [TARGET])
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_wrong_prefix_order_rejected(self):
        self.passing()
        self.save('registered-tests.json', list(reversed(self.names)))
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_wrong_target_rejected(self):
        self.case('isolated-1', ['other'], failed=True)
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_failing_xml_success_exit_rejected(self):
        self.case('isolated-1', [TARGET], failed=True, code=0)
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_harness_timeout_rejected(self):
        self.case('isolated-1', [TARGET], failed=True)
        self.rows[0]['timed_out'] = True
        self.save('outcomes.json', dict(results=self.rows, router_alive=True))
        self.assertFalse(replay.classify(self.logs)['completed'])

    def test_daemon_error_does_not_authorize_removal(self):
        error = subprocess.CompletedProcess([], 1, '', 'Cannot connect to Docker daemon')
        with patch.object(replay.subprocess, 'run', return_value=error), patch.object(replay, 'command') as command:
            self.assertFalse(replay.stop_own('ours'))
            self.assertFalse(replay.remove_own('ours'))
            command.assert_not_called()

    def test_stop_is_verified_before_success(self):
        outputs = [subprocess.CompletedProcess([], 0, 'true\n', ''),
                   subprocess.CompletedProcess([], 0, 'false\n', '')]
        with patch.object(replay.subprocess, 'run', side_effect=outputs), patch.object(replay, 'command') as command:
            self.assertTrue(replay.stop_own('ours'))
            self.assertEqual(command.call_args.args[0], ['docker', 'kill', 'ours'])

    def test_claim_state_detects_interrupted_acquisition(self):
        from contextlib import contextmanager
        class Admission:
            key = 'ours'
            @contextmanager
            def locked(self):
                yield {}, {'participants': {'ours': {'trials': {'acquired-before-error': {}}}}}
        self.assertTrue(replay.owns_claim(Admission(), 'acquired-before-error'))
        self.assertFalse(replay.owns_claim(Admission(), 'not-ours'))


if __name__ == '__main__':
    unittest.main()

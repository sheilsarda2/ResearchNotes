"""Pure local fail-closed regressions; never import admission or invoke Docker."""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('paired_runner', Path(__file__).with_name('run_paired_regrades_v2.py'))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class FileGates(unittest.TestCase):
    def score(self, failed=None):
        groups = {name: {'pass': name != failed} for name in runner.GROUPS}
        return {'required_groups': runner.GROUPS, 'groups': groups, 'missing_groups': [],
                'reward': int(failed is None)}

    def test_complete_reward_zero_is_valid(self):
        self.assertEqual(runner.validate_score(self.score('build_a'), '0\n'), 0)

    def test_missing_group_is_not_complete_failure(self):
        score = self.score('build_a')
        del score['groups']['admin_timestamp_tests']
        with self.assertRaises(ValueError):
            runner.validate_score(score, '0')

    def test_reward_disagreement_rejected(self):
        score = self.score('robustness_tests')
        score['reward'] = 1
        with self.assertRaises(ValueError):
            runner.validate_score(score, '1')

    def test_nonboolean_group_verdict_rejected(self):
        score = self.score()
        score['groups']['robustness_tests']['pass'] = 'true'
        with self.assertRaises(ValueError):
            runner.validate_score(score, '1')

    def test_reward_file_disagreement_rejected(self):
        with self.assertRaises(ValueError):
            runner.validate_score(self.score(), '0')

    def test_source_map_rejects_uncollected_path(self):
        files = {'artifacts/submission/zenoh/Cargo.toml': {'kind': 'file', 'sha256': 'a' * 64, 'size': 1}}
        record = {'source_matches_pre_verifier_capture': True, 'source_files': files,
                  'source_manifest_sha256': hashlib.sha256(runner.canonical(files)).hexdigest()}
        with self.assertRaises(ValueError):
            runner.source_map(record)

    def test_source_map_rejects_hash_drift(self):
        files = {'artifacts/submission/' + root + '/lib.rs': {'kind': 'file', 'sha256': 'a' * 64, 'size': 1}
                 for root in runner.SRC_DIRS}
        record = {'source_matches_pre_verifier_capture': True, 'source_files': files,
                  'source_manifest_sha256': hashlib.sha256(runner.canonical(files)).hexdigest()}
        self.assertEqual(len(runner.source_map(record)), 4)
        changed = copy.deepcopy(record)
        next(iter(changed['source_files'].values()))['sha256'] = 'b' * 64
        with self.assertRaises(ValueError):
            runner.source_map(changed)

    def test_hashes_reject_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source.rs').write_text('source')
            (root / 'alias.rs').symlink_to(root / 'source.rs')
            with self.assertRaises(ValueError):
                runner.hashes(root)

    def test_hashes_reject_fifo_without_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            os.mkfifo(root / 'fifo')
            with self.assertRaises(ValueError):
                runner.hashes(root)

    def test_cleanup_skips_collection_until_stop_proven(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'running'
            log.write_text('true\n')
            calls = []
            def invoke(label, command, cap=20):
                calls.append(label)
                return 0, log
            self.assertFalse(runner.collect_stopped_container('own-container', root, invoke))
            self.assertEqual(calls, ['stop', 'stopped', 'remove'])

    def test_cleanup_collects_only_after_stop_and_always_removes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root / 'stopped'
            log.write_text('false\n')
            calls = []
            def invoke(label, command, cap=20):
                calls.append(label)
                return (125 if label == 'collect-output' else 0), log
            self.assertTrue(runner.collect_stopped_container('own-container', root, invoke))
            self.assertEqual(calls, ['stop', 'stopped', 'collect-output', 'collect-verifier', 'remove'])

    def test_cleanup_missing_stop_log_still_removes_without_collecting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            calls = []
            def invoke(label, command, cap=20):
                calls.append(label)
                return 0, root / 'missing'
            self.assertFalse(runner.collect_stopped_container('own-container', root, invoke))
            self.assertEqual(calls, ['stop', 'stopped', 'remove'])

    def test_cleanup_exception_still_attempts_remove(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def invoke(label, command, cap=20):
                calls.append(label)
                if label == 'stop':
                    raise OSError('simulated observer failure')
                return 0, None
            with self.assertRaises(OSError):
                runner.collect_stopped_container('own-container', Path(folder), invoke)
            self.assertEqual(calls, ['stop', 'remove'])


if __name__ == '__main__':
    unittest.main()

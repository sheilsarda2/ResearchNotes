"""Temporary fixtures only; no Docker, model calls, or live control reads/writes."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('read_only_observer', Path(__file__).with_name('read_only_observer.py'))
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class Child:
    def __init__(self, polls=(None, 0)):
        self.polls = iter(polls)
        self.returncode = None
        self.waited = False

    def poll(self):
        self.returncode = next(self.polls)
        return self.returncode

    def wait(self):
        self.waited = True
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.shared = self.root / 'shared.control.json'
        self.state = self.shared.with_suffix('.state.json')
        self.lock = self.shared.with_suffix('.lock')
        self.control = self.root / 'own.control.json'
        self.samples = self.root / 'samples.jsonl'
        self.gaps = self.root / 'gaps.jsonl'
        self.shared.write_text('{"max_active":14}\n')
        self.control.write_text('{}\n')
        self.lock.write_bytes(b'original lock bytes\n')
        self.state.write_text(json.dumps({'participants': {'123:identity': {
            'control': str(self.control), 'pid': 123, 'identity': 'identity', 'trials': {'own': {}}}}}))

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, **kwargs):
        return m.observe_once(self.samples, self.control, 123, shared_control=self.shared,
                              gap_path=self.gaps, timeout=.025, retry_interval=.005, **kwargs)

    def test_success_preserves_shared_bytes_and_existing_schema(self):
        before = {p: p.read_bytes() for p in (self.shared, self.state, self.lock, self.control)}
        row = self.observe()
        self.assertEqual(row['shared_max_active'], 14)
        self.assertEqual(row['total_claims'], 1)
        self.assertEqual(len(row['own_participants']), 1)
        self.assertFalse(row['terminal_sample'])
        self.assertFalse(self.gaps.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_transient_missing_state_recovers_with_separate_gap_record(self):
        original = m._read_bytes
        missing = [True]
        def read(path):
            if path == self.state and missing[0]:
                missing[0] = False
                raise FileNotFoundError(2, 'fixture transient', str(path))
            return original(path)
        with patch.object(m, '_read_bytes', side_effect=read):
            row = self.observe(terminal=True)
        self.assertEqual(row['read_attempts'], 2)
        self.assertEqual(row['recovered_read_gaps'], 1)
        record = json.loads(self.gaps.read_text())
        self.assertTrue(record['recovered'])
        self.assertEqual(record['gaps'][0]['path'], str(self.state))
        self.assertEqual(len(self.samples.read_text().splitlines()), 1)

    def test_transient_json_decode_recovers(self):
        original = m._read_bytes
        invalid = [True]
        def read(path):
            if path == self.state and invalid[0]:
                invalid[0] = False
                return b'{'
            return original(path)
        with patch.object(m, '_read_bytes', side_effect=read):
            row = self.observe()
        self.assertEqual(row['read_attempts'], 2)
        self.assertEqual(json.loads(self.gaps.read_text())['gaps'][0]['phase'], 'state_decode')

    def test_persistent_missing_state_never_creates_empty_state_or_sample(self):
        self.state.unlink()
        with self.assertRaises(m.ObservationReadError):
            self.observe()
        self.assertFalse(self.state.exists())
        self.assertFalse(self.samples.exists())
        record = json.loads(self.gaps.read_text())
        self.assertFalse(record['recovered'])
        self.assertLess(record['elapsed_seconds'], .2)

    def test_missing_lock_is_not_created(self):
        self.lock.unlink()
        with self.assertRaises(m.ObservationReadError):
            self.observe()
        self.assertFalse(self.lock.exists())

    def test_lock_contention_is_bounded_and_releases_without_truncation(self):
        fd = os.open(self.lock, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(m.ObservationReadError):
                self.observe()
        finally:
            os.close(fd)
        self.assertEqual(self.lock.read_bytes(), b'original lock bytes\n')
        self.assertEqual(self.observe()['read_attempts'], 1)

    def test_read_lock_actually_excludes_exclusive_writer(self):
        original = m._read_bytes
        checked = []
        def read(path):
            if path == self.state:
                fd = os.open(self.lock, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    checked.append(True)
                finally:
                    os.close(fd)
            return original(path)
        with patch.object(m, '_read_bytes', side_effect=read):
            self.observe()
        self.assertEqual(checked, [True])

    def test_transient_lock_contention_recovers(self):
        fd = os.open(self.lock, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def release(_):
            fcntl.flock(fd, fcntl.LOCK_UN)
        try:
            with patch.object(m.time, 'sleep', side_effect=release):
                row = self.observe()
        finally:
            os.close(fd)
        self.assertEqual(row['read_attempts'], 2)
        self.assertEqual(json.loads(self.gaps.read_text())['gaps'][0]['phase'], 'lock_acquire')

    def test_symlink_lock_state_and_output_are_rejected(self):
        for victim in (self.lock, self.state, self.samples):
            with self.subTest(path=victim):
                prior = victim.read_bytes() if victim.exists() else None
                if victim.exists():
                    victim.unlink()
                victim.symlink_to(self.control)
                try:
                    with self.assertRaises(OSError):
                        self.observe()
                    self.assertEqual(self.control.read_text(), '{}\n')
                finally:
                    victim.unlink()
                    if prior is not None:
                        victim.write_bytes(prior)

    def test_permission_and_schema_errors_are_not_retried(self):
        with patch.object(m, '_read_bytes', side_effect=PermissionError(13, 'fixture denied')) as read:
            with self.assertRaises(PermissionError):
                self.observe()
            self.assertEqual(read.call_count, 1)
        self.state.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'never substitute empty state'):
            self.observe()
        self.assertFalse(self.samples.exists())

    def test_outputs_cannot_touch_controls_or_mix_gaps(self):
        for output in (self.shared, self.state, self.lock, self.control):
            with self.subTest(path=output), self.assertRaises(ValueError):
                m.observe_once(output, self.control, 123, shared_control=self.shared, gap_path=self.gaps)
        with self.assertRaises(ValueError):
            m.observe_once(self.samples, self.control, 123, shared_control=self.shared, gap_path=self.samples)

    def test_recovered_gap_does_not_permanently_fail_wait(self):
        child = Child()
        original = m._read_bytes
        invalid = [True]
        def read(path):
            if path == self.state and invalid[0]:
                invalid[0] = False
                raise FileNotFoundError(2, 'transient', str(path))
            return original(path)
        with patch.object(m, '_read_bytes', side_effect=read):
            result = m.wait_with_observation(child, self.observe, interval=.001)
        self.assertTrue(child.waited)
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['terminal_sample_valid'])
        self.assertEqual([r['terminal_sample'] for r in map(json.loads, self.samples.read_text().splitlines())], [False, True])

    def test_failed_terminal_read_cannot_reuse_prior_success(self):
        child = Child()
        def observe(*, terminal):
            if terminal:
                self.state.unlink()
            return self.observe(terminal=terminal)
        result = m.wait_with_observation(child, observe, interval=.001)
        self.assertTrue(child.waited)
        self.assertFalse(result['terminal_sample_valid'])
        self.assertEqual(len(result['errors']), 1)
        self.assertTrue(result['errors'][0]['terminal'])
        self.assertEqual(len(self.samples.read_text().splitlines()), 1)

    def test_persistent_errors_reap_child_and_remain_fatal(self):
        child = Child()
        result = m.wait_with_observation(child, lambda **_: (_ for _ in ()).throw(ValueError('fixture')), interval=.001)
        self.assertTrue(child.waited)
        self.assertEqual(len(result['errors']), 2)
        self.assertFalse(result['terminal_sample_valid'])

    def test_interrupt_reaps_child_and_does_not_swallow_interrupt(self):
        child = Child()
        with self.assertRaises(KeyboardInterrupt):
            m.wait_with_observation(child, lambda **_: (_ for _ in ()).throw(KeyboardInterrupt()), interval=.001)
        self.assertTrue(child.waited)

    def test_callback_cannot_return_an_old_nonterminal_sample(self):
        child = Child(polls=(0,))
        old = self.observe()
        result = m.wait_with_observation(child, lambda **_: old, interval=.001)
        self.assertTrue(child.waited)
        self.assertFalse(result['terminal_sample_valid'])
        self.assertEqual(result['errors'][0]['error_type'], 'ValueError')

    def test_persistent_earlier_error_remains_fatal_after_terminal_recovery(self):
        child = Child()
        def observe(*, terminal):
            if not terminal:
                raise m.ObservationReadError([], .025)
            return self.observe(terminal=True)
        result = m.wait_with_observation(child, observe, interval=.001)
        self.assertTrue(child.waited)
        self.assertTrue(result['terminal_sample_valid'])
        self.assertEqual(len(result['errors']), 1)

    def test_nonregular_lock_rejected_without_blocking(self):
        self.lock.unlink()
        os.mkfifo(self.lock)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.observe()


if __name__ == '__main__':
    unittest.main(verbosity=2)

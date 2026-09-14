"""Offline state/field gates; never operates the real campaign controls."""
import copy
from contextlib import nullcontext, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reserve_once', Path(__file__).with_name('reserve_once.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FieldRestoration(unittest.TestCase):
    def setUp(self):
        self.original = {'paused': False, 'max_active': 12, 'unrelated': {'x': 1}}
        self.held = dict(self.original, paused=True, pause_reason=m.OWNER)

    def test_own_fields_only_preserves_unrelated_concurrent_changes(self):
        current = dict(self.held, max_active=7, unrelated={'x': 2}, new_field='preserve')
        restored, status = m.merged_restore(current, self.original)
        self.assertEqual(restored, {'paused': False, 'max_active': 7, 'unrelated': {'x': 2}, 'new_field': 'preserve'})
        self.assertEqual(status, 'owned_fields_restored')
        self.assertEqual(current['pause_reason'], m.OWNER)

    def test_foreign_pause_is_never_cleared(self):
        current = dict(self.held, pause_reason='New incident hold')
        restored, status = m.merged_restore(current, self.original)
        self.assertEqual(restored, current)
        self.assertEqual(status, 'foreign_fields_preserved')

    def test_prior_field_presence_restored(self):
        original = dict(self.original, pause_reason=None)
        restored, _ = m.merged_restore(self.held, original)
        self.assertIn('pause_reason', restored)
        self.assertIsNone(restored['pause_reason'])

    def test_partial_application_untouched_controls_preserved(self):
        restored, status = m.merged_restore(self.original, self.original)
        self.assertEqual(restored, self.original)
        self.assertEqual(status, 'foreign_fields_preserved')

    def test_external_unpause_preserved_and_owner_reason_removed(self):
        restored, _ = m.merged_restore(dict(self.held, paused=False), self.original)
        self.assertFalse(restored['paused'])
        self.assertNotIn('pause_reason', restored)

    def test_health_pause_outlives_reservation_without_our_marker(self):
        restored, status = m.merged_restore(self.held, self.original, 'Unclassified result or evidence mismatch requires review')
        self.assertTrue(restored['paused'])
        self.assertNotEqual(restored['pause_reason'], m.OWNER)
        self.assertEqual(status, 'reservation_removed_health_pause_preserved')

    def test_paused_alone_is_not_a_health_failure(self):
        self.assertIsNone(m.health_reason({'status': 'paused', 'health': {'ok_to_expand': False, 'recent_infrastructure': 0}}))

    def test_distinct_health_failures(self):
        self.assertIn('Unclassified', m.health_reason({'health': {'unclassified_results': 1}}))
        self.assertIn('Sustained', m.health_reason({'health': {'recent_results': 8, 'recent_infrastructure': 5}}))
        self.assertIn('image', m.health_reason({}, {'failures': ['x']}))

    def test_atomic_compare_rejects_changed_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'control.json'
            path.write_text('{"external":true}')
            before = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, 'Concurrent control write'):
                m.atomic(path, {'paused': True}, expected='0' * 64)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(temp).iterdir()), [path])


class ReviewerGates(unittest.TestCase):
    def setUp(self):
        self.row = {'key': '83205:5750249', 'pid': 83205, 'identity': '5750249', 'live_identity': '5750249',
                    'control': str(m.REVIEW / 'control.json'), 'trials': []}
        self.observation = {'participants': [self.row], 'shared_control_sha256': 'same'}
        self.intent = {'deadline_monotonic': 110, 'shared_control_sha256': 'same',
                       'initial_observation': copy.deepcopy(self.observation)}

    def test_exact_reviewer_claim_stops_hold(self):
        self.row['trials'] = ['check-task__exact']
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'exact_reviewer_admitted')

    def test_pid_reuse_is_not_admission(self):
        self.row['live_identity'] = 'new-start'
        self.row['trials'] = ['check-task__exact']
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'reviewer_exited_or_identity_changed')

    def test_wrong_control_is_not_admission(self):
        self.row['control'] = '/other/control.json'
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'reviewer_exited_or_identity_changed')

    def test_fixed_deadline_wins(self):
        self.assertEqual(m.stop_reason(self.intent, self.observation, 110), 'fixed_deadline')
        self.assertLessEqual(m.HOLD_SECONDS, 110)

    def test_shared_changes_end_hold(self):
        self.observation['shared_control_sha256'] = 'external'
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'shared_control_changed')

    def test_new_participant_ends_hold(self):
        self.observation['participants'].append(dict(self.row, key='99:1', pid=99))
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'participant_inventory_changed')

    def test_multiple_claims_not_called_success(self):
        self.row['trials'] = ['a', 'b']
        self.assertEqual(m.stop_reason(self.intent, self.observation, 1), 'unexpected_reviewer_claim_count')


class RestorationIO(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = self.root / 'journal'
        self.journal.mkdir()
        self.paths = [self.root / ('control-' + str(i) + '.json') for i in range(4)]
        self.original = {'paused': False, 'max_active': 12}
        for p in self.paths:
            p.write_text(json.dumps(dict(self.original, paused=True, pause_reason=m.OWNER)))
        self.intent = {'deadline_monotonic': time.monotonic() + 1,
                       'changes': [{'path': p.name, 'original': self.original} for p in self.paths]}
        for name, value in [('ROOT', self.root), ('EXECUTION', self.journal),
                            ('shared_lock', lambda **kw: nullcontext()), ('campaign_health', lambda *args: None)]:
            patcher = patch.object(m, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_transient_shared_lock_error_retried_without_terminal_failure(self):
        calls = []
        def lock(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError('transient lock')
            self.assertFalse((self.journal / 'restoration.json').exists())
            return nullcontext()
        with patch.object(m, 'shared_lock', lock):
            result = m.restore(self.intent, 'fixture')
        self.assertTrue(result['passed'])
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(len(list(self.journal.glob('restoration-attempt-*'))), 2)

    def test_transient_control_CAS_preserves_external_edit_then_retries(self):
        real_atomic = m.atomic
        attempts = []
        def atomic(path, value, expected=None):
            if path == self.paths[0] and not attempts:
                attempts.append(1)
                changed = m.read(path)
                changed['external'] = 'new'
                path.write_text(json.dumps(changed))
            return real_atomic(path, value, expected)
        with patch.object(m, 'atomic', atomic):
            result = m.restore(self.intent, 'fixture')
        self.assertTrue(result['passed'])
        self.assertEqual(m.read(self.paths[0]), dict(self.original, external='new'))

    def test_partial_application_and_foreign_health_hold(self):
        self.paths[0].write_text(json.dumps(self.original))
        foreign = dict(self.original, paused=True, pause_reason='External incident')
        self.paths[1].write_text(json.dumps(foreign))
        with patch.object(m, 'campaign_health', lambda p, c: 'New health failure' if p == self.paths[2] else None):
            result = m.restore(self.intent, 'fixture')
        self.assertTrue(result['passed'])
        self.assertEqual(m.read(self.paths[0]), self.original)
        self.assertEqual(m.read(self.paths[1]), foreign)
        self.assertEqual(m.read(self.paths[2])['pause_reason'], 'New health failure')
        self.assertFalse(m.read(self.paths[3])['paused'])

    def test_persistent_error_is_not_terminal_success(self):
        self.intent['deadline_monotonic'] = time.monotonic() - 9
        with patch.object(m, 'shared_lock', side_effect=TimeoutError('persistent')):
            result = m.restore(self.intent, 'fixture')
        self.assertFalse(result['passed'])
        self.assertFalse((self.journal / 'restoration.json').exists())
        self.assertTrue(list(self.journal.glob('cleanup-incomplete-*')))
        # Explicit later restoration remains possible.
        self.assertTrue(m.restore(self.intent, 'explicit_recovery')['passed'])

    def test_reservation_lock_is_bounded(self):
        with m.reservation_lock():
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                with m.reservation_lock():
                    self.fail('Reentered a separately opened lock')
            self.assertLess(time.monotonic() - started, .8)

    def test_default_check_only_has_no_write_or_child(self):
        with patch.object(m, 'prepare', return_value={'check': True}), patch.object(m, 'atomic') as atomic, \
                patch.object(m.subprocess, 'Popen') as launch, patch.object(m.sys, 'argv', ['reserve_once.py']), \
                redirect_stdout(io.StringIO()) as stdout:
            m.main()
        atomic.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(json.loads(stdout.getvalue()), {'check': True})


if __name__ == '__main__':
    unittest.main()

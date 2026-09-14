from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location('watchdog', Path(__file__).resolve().parents[1]/'watch-candidate-campaign.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def test_explicit_revision_campaigns_are_monitored_without_reviving_old_work(self):
        plan = {'active_full_campaign': 'main', 'active_revision_campaigns': ['corrected', 'cancelled'],
                'cancelled_do_not_resume': ['cancelled'], 'superseded_do_not_resume': ['old']}
        for base in ('main', 'corrected'):
            self.assertTrue(watchdog.eligible(base, plan, {}, {}))
        for base in ('cancelled', 'old', 'unrelated'):
            self.assertFalse(watchdog.eligible(base, plan, {}, {}))
        self.assertFalse(watchdog.eligible('corrected', plan, {'cancelled': True}, {}))
        self.assertFalse(watchdog.eligible('corrected', plan, {}, {'status': 'complete'}))

    def test_cancelled_superseded_and_completed_work_never_restarts(self):
        plan = {'active_full_campaign': 'active', 'cancelled_do_not_resume': ['pilot'],
                'superseded_do_not_resume': ['old']}
        self.assertTrue(watchdog.eligible('active', plan, {}, {}))
        for base in ['pilot', 'old', 'unrelated']:
            self.assertFalse(watchdog.eligible(base, plan, {}, {}))
        self.assertFalse(watchdog.eligible('active', plan, {'cancelled': True}, {}))
        for status in watchdog.TERMINAL:
            self.assertFalse(watchdog.eligible('active', plan, {}, {'status': status}))

    def test_healthy_supervisor_and_activation_restart_are_left_alone(self):
        self.assertIsNone(watchdog.supervisor_action(1, 10, False, 0))
        self.assertIsNone(watchdog.supervisor_action(0, 1000, True, 0))
        self.assertEqual(watchdog.supervisor_action(0, 1000, False, 0), 'start_missing')
        self.assertEqual(watchdog.supervisor_action(1, 181, False, 0), 'restart_stale')

    def test_restarts_are_bounded_and_duplicates_are_reported(self):
        self.assertEqual(watchdog.supervisor_action(0, 1000, False, 3), 'restart_limit')
        self.assertEqual(watchdog.supervisor_action(2, 1, False, 0), 'duplicate_supervisors')

    def test_active_paused_memory_gated_and_finished_runners_never_recycle(self):
        args = [False, False, 0, 0, False, 301]
        self.assertTrue(watchdog.stale_idle_runner(*args))
        for index in range(5):
            guarded = args[:]
            guarded[index] = True
            self.assertFalse(watchdog.stale_idle_runner(*guarded))
        self.assertFalse(watchdog.stale_idle_runner(False, False, 0, 0, False, 299))

    def test_dead_reservations_are_retained_until_exact_trial_container_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'jobs').mkdir()
            shared = root/'jobs/shared.control.json'
            control = str(root/'jobs/campaign.control.json')
            state_path = shared.with_suffix('.state.json')
            state_path.write_text(json.dumps({'participants': {'old': {
                'pid': 999999, 'identity': 'old', 'control': control,
                'trials': {'trial-a': {'started_at': 1}}}}}))
            container = {'state': {'Running': True}, 'mounts': [
                {'Source': str(root/'jobs/campaign/trial-a/agent')}]}
            with patch.object(watchdog, 'ROOT', root), patch.object(watchdog, 'process_identity', return_value=None):
                self.assertEqual(watchdog.reclaim_dead_claims(shared, {control}, [container], True), [])
                self.assertEqual(watchdog.reclaim_dead_claims(shared, set(), [], True), [])
                self.assertEqual(len(watchdog.read(state_path)['participants']['old']['trials']), 1)
                reclaimed = watchdog.reclaim_dead_claims(shared, {control}, [], True)
                self.assertEqual(reclaimed, [{'owner_pid': 999999, 'trial': 'trial-a'}])
                self.assertEqual(watchdog.read(state_path)['participants']['old']['trials'], {})

    def test_reused_pid_is_never_signalled(self):
        with patch.object(watchdog, 'process_identity', return_value='new'), patch.object(watchdog.os, 'pidfd_open') as opening:
            self.assertFalse(watchdog.signal_named({'pid': 999999, 'identity': 'old'}, signal.SIGTERM))
            opening.assert_not_called()

    def test_recovers_real_missing_and_stale_supervisor_without_model_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir(); (root/'jobs').mkdir()
            base = 'fixture-efforts-20-test'
            script = root/'scripts/run-candidate-screen.py'
            script.write_text('import json, os, time\nfrom pathlib import Path\n'
                              'p=Path("jobs/fixture-efforts-20-test.summary.json")\n'
                              'p.write_text(json.dumps({"status":"running","completed":0,"target":20}))\n'
                              'Path("jobs/ready").write_text(str(os.getpid()))\n'
                              'time.sleep(60)\n')
            def write(name, value):
                (root/'jobs'/name).write_text(json.dumps(value))
            write('benchmark-user-plan.json', {'active_full_campaign': base})
            write(base+'.plan.json', {'jobs': [{'name': base}], 'attempts': 20,
                                     'workers': 12, 'models': ['sonnet-5']})
            write(base+'.control.json', {'max_active': 12, 'paused': False,
                                        'shared_pool': str(root/'jobs/shared.control.json')})
            write('shared.control.json', {'max_active': 12})
            write('shared.control.state.json', {'participants': {}})
            pids = []
            state = {}
            with patch.object(watchdog, 'ROOT', root), patch.object(watchdog, 'job_containers', return_value=[]), patch.object(
                    watchdog, 'memory_snapshot', return_value={'total_mb': 32768, 'available_mb': 24000, 'memory_pressure_pct': 0}):
                try:
                    first = watchdog.cycle(base, state, True)
                    pids.append(first['actions'][0]['start_missing'])
                    deadline = time.monotonic()+5
                    while not (root/'jobs/ready').exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue((root/'jobs/ready').exists())
                    self.assertEqual(watchdog.cycle(base, state, True)['actions'], [])
                    os.utime(root/'jobs'/f'{base}.summary.json', (time.time()-200, time.time()-200))
                    second = watchdog.cycle(base, state, True)
                    pids.append(second['actions'][0]['restart_stale'])
                    self.assertNotEqual(pids[0], pids[1])
                    self.assertEqual(len(state['supervisor_restarts']), 2)
                    self.assertEqual(len(watchdog.inventory(base, {base})['supervisors']), 1)
                finally:
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGTERM)
                            os.waitpid(pid, 0)
                        except (ProcessLookupError, ChildProcessError):
                            pass
                    for child in watchdog.CHILDREN:
                        child.poll()
                    watchdog.CHILDREN.clear()


class SharedWaitWatchdogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'jobs').mkdir()
        self.base = 'waiting-efforts-20-test'
        self.control = self.root / 'jobs' / (self.base + '.control.json')
        self.shared_path = self.root / 'jobs/shared.control.json'
        self.process = {'pid': 900001, 'identity': 'waiting'}
        self.shared_control = {'max_active': 12}
        self.shared = {'updated_at': time.time(), 'participants': {
            'waiting': {**self.process, 'control': str(self.control), 'trials': {}},
            'other': {'pid': 900002, 'identity': 'other', 'control': str(self.root / 'jobs/other.control.json'),
                      'trials': {f'trial-{i}': {} for i in range(12)}}}}
        self.resources = {'updated_at': datetime.now(timezone.utc).isoformat(),
                          'active_trials': 0, 'admission_wait_reason': 'shared concurrency'}
        active = patch.object(watchdog, 'ROOT', self.root)
        active.start()
        self.addCleanup(active.stop)
        self.save()

    def save(self):
        for path, value in ((self.shared_path, self.shared_control),
                            (self.shared_path.with_suffix('.state.json'), self.shared),
                            (self.control.with_suffix('.resources.json'), self.resources)):
            path.write_text(json.dumps(value))

    def test_fresh_pool_full_wait_is_confirmed_without_own_admissions(self):
        self.assertEqual(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared),
                         'shared concurrency')

    def test_freshness_clock_is_sampled_after_reading_new_admission_status(self):
        clock = [100.0]
        self.shared['updated_at'] = 100.0
        self.resources['updated_at'] = datetime.fromtimestamp(101, timezone.utc).isoformat()

        def admission_read(_path):
            clock[0] = 102.0
            return self.resources

        with patch.object(watchdog, 'read', side_effect=admission_read), \
                patch.object(watchdog.time, 'time', side_effect=lambda: clock[0]):
            self.assertEqual(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared),
                             'shared concurrency')

    def test_stale_unregistered_and_no_longer_full_waits_are_not_protected(self):
        self.resources['updated_at'] = '2020-01-01T00:00:00Z'
        self.save()
        self.assertIsNone(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared))
        self.resources['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.save()
        self.assertIsNone(watchdog.confirmed_shared_wait({'pid': 900003, 'identity': 'unknown'}, self.shared_control, self.shared))
        self.shared['participants']['other']['trials'].pop('trial-0')
        self.assertIsNone(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared))
        self.shared['participants']['other']['trials']['trial-0'] = {}
        self.shared['updated_at'] = time.time() - 91
        self.assertIsNone(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared))

    def test_allocation_wait_requires_actual_fair_share_occupancy(self):
        self.resources['admission_wait_reason'] = 'shared campaign allocation'
        self.shared['participants']['other']['trials'] = {}
        self.shared['participants']['waiting']['trials'] = {f'trial-{i}': {} for i in range(6)}
        self.save()
        self.assertEqual(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared),
                         'shared campaign allocation')
        self.shared['participants']['waiting']['trials'].pop('trial-0')
        self.assertIsNone(watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared))

    def cycle(self, *, inventory_delay=False, before_signal=None):
        documents = {'benchmark-user-plan.json': {'active_full_campaign': self.base},
                     self.base + '.plan.json': {'jobs': [{'name': self.base}]},
                     self.base + '.summary.json': {'status': 'running'},
                     self.base + '.control.json': {'max_active': 12, 'shared_pool': str(self.shared_path)}}
        for name, value in documents.items():
            (self.root / 'jobs' / name).write_text(json.dumps(value))
        state = {'runner_idle_since': {} if inventory_delay else {self.base: time.time() - 400}}
        clock = [time.time()]
        inventory_calls = [0]

        def containers(_directory):
            inventory_calls[0] += 1
            if before_signal and inventory_calls[0] == 2:
                before_signal()
            if inventory_delay:
                self.resources['updated_at'] = datetime.fromtimestamp(clock[0] + 1, timezone.utc).isoformat()
                self.shared['updated_at'] = clock[0] + 1
                clock[0] += 2
                self.save()
            return []

        with patch.object(watchdog, 'inventory', return_value={
                'supervisors': [{'pid': 900004, 'identity': 'supervisor'}], 'runners': {self.base: self.process}}), \
                patch.object(watchdog, 'process_identity', side_effect=lambda pid: {900001: 'waiting', 900002: 'other'}.get(pid)), \
                patch.object(watchdog, 'job_containers', side_effect=containers), \
                patch.object(watchdog.time, 'time', side_effect=lambda: clock[0]), \
                patch.object(watchdog, 'memory_snapshot', return_value={
                    'total_mb': 32768, 'available_mb': 24000, 'memory_pressure_pct': 0}), \
                patch.object(watchdog, 'signal_named', return_value=True) as signal_call:
            report = watchdog.cycle(self.base, state, True)
        return report, state, signal_call

    def test_empty_runner_waiting_for_another_campaign_is_not_signalled(self):
        report, state, signal_call = self.cycle()
        signal_call.assert_not_called()
        self.assertEqual(report['status'], 'waiting_for_shared_resources')
        self.assertEqual(report['admission_waits'][self.base], 'shared concurrency')
        self.assertNotIn(self.base, state['runner_idle_since'])

    def test_cycle_start_time_does_not_make_new_wait_status_appear_future_dated(self):
        report, state, signal_call = self.cycle(inventory_delay=True)
        signal_call.assert_not_called()
        self.assertEqual(report['status'], 'waiting_for_shared_resources')
        self.assertEqual(report['admission_waits'][self.base], 'shared concurrency')
        self.assertNotIn(self.base, state['runner_idle_since'])

    def test_unknown_true_idle_runner_keeps_existing_recovery(self):
        self.shared['participants']['other']['trials'] = {}
        self.save()
        report, state, signal_call = self.cycle()
        signal_call.assert_called_once_with(self.process, signal.SIGINT)
        self.assertIn({'restarted_empty_runner': self.base}, report['actions'])

    def interleaving(self):
        own = [json.dumps(['own-task', f'model-{i}', 'medium'], separators=(',', ':')) for i in range(9)]
        other = json.dumps(['other-task', 'model-0', 'medium'], separators=(',', ':'))
        cells = {key: dict(task=json.loads(key)[0], model=json.loads(key)[1], effort='medium',
                           job=self.base if key in own else 'other', target=20) for key in [*own, other]}
        self.shared['interleaving'] = dict(version=1, campaigns=[self.base, 'other'],
                                           cells=cells, counts={key: 1 if key in own else 0 for key in cells}, receipts={})
        self.shared['updated_at'] = time.time() - 1000
        self.shared['participants']['other']['trials'] = {}
        self.resources.update(updated_at='2020-01-01T00:00:00Z', admission_wait_reason=None)
        self.save()
        return own, other

    def round_wait(self, *, identity='waiting', job_name=None):
        with patch.object(watchdog, 'process_identity', return_value=identity):
            return watchdog.confirmed_shared_wait(self.process, self.shared_control, self.shared,
                                                  job_name=self.base if job_name is None else job_name)

    def test_round_barrier_needs_no_admission_heartbeat_and_uses_all_unfinished_cells(self):
        own, other = self.interleaving()
        self.control.with_suffix('.resources.json').unlink()
        self.assertEqual(self.round_wait(), 'interleaving: waiting for remaining cells in round')
        self.shared['interleaving']['counts'][own[0]] = 0
        self.assertIsNone(self.round_wait())
        self.shared['interleaving']['counts'][own[0]] = 20
        self.assertEqual(self.round_wait(), 'interleaving: waiting for remaining cells in round')
        self.shared['interleaving']['counts'][other] = 1
        self.assertIsNone(self.round_wait())

    def test_round_wait_is_bound_to_live_primary_identity_and_control(self):
        self.interleaving()
        self.assertIsNone(self.round_wait(identity='reused-pid'))
        self.assertIsNone(self.round_wait(job_name=self.base + '-repair-000'))
        self.shared['participants']['waiting']['control'] = str(self.root/'jobs/other.control.json')
        self.assertIsNone(self.round_wait())

    def test_round_wait_does_not_cover_repairs_unregistered_or_completed_jobs(self):
        own, _ = self.interleaving()
        policy = self.shared['interleaving']
        policy['campaigns'].remove(self.base)
        self.assertIsNone(self.round_wait())
        policy['campaigns'].append(self.base)
        for key in own:
            policy['counts'][key] = 20
        self.assertIsNone(self.round_wait())
        self.shared['participants']['waiting']['trials'] = {'already-admitted': {}}
        self.assertIsNone(self.round_wait())

    def test_malformed_or_inconsistent_round_policy_is_not_a_health_signal(self):
        own, _ = self.interleaving()
        original = json.loads(json.dumps(self.shared['interleaving']))
        for change in ('missing-count', 'negative-count', 'boolean-count', 'zero-target',
                       'different-cell-key', 'no-owned-cell', 'unknown-version'):
            with self.subTest(change=change):
                policy = self.shared['interleaving'] = json.loads(json.dumps(original))
                if change == 'missing-count': del policy['counts'][own[0]]
                if change == 'negative-count': policy['counts'][own[0]] = -1
                if change == 'boolean-count': policy['counts'][own[0]] = True
                if change == 'zero-target': policy['cells'][own[0]]['target'] = 0
                if change == 'different-cell-key': policy['cells'][own[0]]['model'] = 'different'
                if change == 'no-owned-cell':
                    for key in own: policy['cells'][key]['job'] = 'other'
                if change == 'unknown-version': policy['version'] = 2
                self.assertIsNone(self.round_wait())

    def test_round_waiting_empty_runner_is_not_signalled_after_300_seconds(self):
        self.interleaving()
        report, state, signal_call = self.cycle()
        signal_call.assert_not_called()
        self.assertEqual(report['admission_waits'][self.base], 'interleaving: waiting for remaining cells in round')
        self.assertNotIn(self.base, state['runner_idle_since'])

    def test_newly_eligible_empty_runner_retains_idle_recovery(self):
        own, _ = self.interleaving()
        self.shared['interleaving']['counts'][own[0]] = 0
        self.save()
        report, state, signal_call = self.cycle()
        signal_call.assert_called_once_with(self.process, signal.SIGINT)
        self.assertIn({'restarted_empty_runner': self.base}, report['actions'])

    def test_round_policy_is_rechecked_under_lock_before_signalling(self):
        own, _ = self.interleaving()
        self.shared['interleaving']['counts'][own[0]] = 0
        self.save()
        def becomes_blocked():
            self.shared['interleaving']['counts'][own[0]] = 1
            self.save()
        report, state, signal_call = self.cycle(before_signal=becomes_blocked)
        signal_call.assert_not_called()
        self.assertEqual(report['admission_waits'][self.base], 'interleaving: waiting for remaining cells in round')
        self.assertNotIn(self.base, state['runner_idle_since'])


if __name__ == '__main__':
    unittest.main()

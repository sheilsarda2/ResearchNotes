from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()

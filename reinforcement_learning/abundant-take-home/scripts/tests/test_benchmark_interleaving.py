import asyncio
from collections import Counter
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_interleaving as rounds
from benchmark_shared_admission import SharedAdmission

SNAPSHOT = dict(total_mb=32768, available_mb=30000, memory_pressure_pct=0)


def candidate(cell, name, job='job'):
    rounds.TRIALS[name] = dict(cell=cell, job=job)
    rounds.QUEUES[cell].append(name)


def concurrent_dispatch(path, cell, output):
    rounds.TRIALS.clear()
    rounds.QUEUES.clear()
    shared = SharedAdmission(path, str(path) + cell)
    for i in range(2):
        candidate(cell, f'{cell}-{i}')
    for i in range(2):
        reason = shared.try_acquire(f'{cell}-{i}', SNAPSHOT, {})
        output.put((cell, i, reason))
        if reason is None:
            shared.release(f'{cell}-{i}')


class InterleavingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'shared.json'
        self.path.write_text(json.dumps(dict(max_active=12, min_total_mb=0,
                                             reserve_mb=0, start_interval_sec=0)))
        rounds.TRIALS.clear()
        rounds.QUEUES.clear()
        rounds._cache.clear()
        self.shared = SharedAdmission(self.path, self.root / 'local.json')

    def tearDown(self):
        self.temp.cleanup()

    def policy(self, counts, target=20):
        with self.shared.locked() as (_, state):
            state['interleaving'] = dict(campaigns=['job'],
                cells={k: dict(job='job', target=target) for k in counts},
                counts=counts.copy(), receipts={})

    def test_historical_repeats_wait_for_all_cells_to_catch_up(self):
        self.policy({'ahead': 2, 'middle': 1, 'behind': 0})
        for key in ['ahead', 'middle', 'behind']:
            candidate(key, key + '-new')
        self.assertIn('remaining cells', self.shared.try_acquire('ahead-new', SNAPSHOT, {}))
        self.assertIn('remaining cells', self.shared.try_acquire('middle-new', SNAPSHOT, {}))
        self.assertIsNone(self.shared.try_acquire('behind-new', SNAPSHOT, {}))
        self.assertIsNone(self.shared.try_acquire('middle-new', SNAPSHOT, {}))
        self.assertIn('remaining cells', self.shared.try_acquire('ahead-new', SNAPSHOT, {}))

    def test_pending_head_and_idempotent_reservation(self):
        self.policy({'a': 0})
        candidate('a', 'first'); candidate('a', 'second')
        self.assertIn('earlier attempt', self.shared.try_acquire('second', SNAPSHOT, {}))
        self.assertIsNone(self.shared.try_acquire('first', SNAPSHOT, {}))
        self.assertIsNone(self.shared.try_acquire('first', SNAPSHOT, {}))
        state = json.loads(self.shared.state_path.read_text())
        self.assertEqual(state['interleaving']['counts'], {'a': 1})

    def test_repair_does_not_advance_round_and_limits_still_apply(self):
        self.policy({'a': 0, 'b': 0})
        candidate('a', 'a'); candidate('b', 'b')
        self.assertIsNone(self.shared.try_acquire('repair', SNAPSHOT, {}))
        self.assertEqual(json.loads(self.shared.state_path.read_text())['interleaving']['counts'], {'a':0,'b':0})
        self.assertEqual(self.shared.try_acquire('a', {**SNAPSHOT, 'available_mb':0}, {}), 'shared memory reserve')
        self.path.write_text(json.dumps(dict(max_active=1, min_total_mb=0, reserve_mb=0, start_interval_sec=0)))
        self.assertEqual(self.shared.try_acquire('a', SNAPSHOT, {}), 'shared concurrency')

    def test_managed_rounds_can_fill_global_slots_across_uneven_campaigns(self):
        self.policy({'a': 0})
        self.path.write_text(json.dumps(dict(max_active=2, min_total_mb=0, reserve_mb=0, start_interval_sec=0)))
        with self.shared.locked() as (_, state):
            # A live but idle participant must not strand half of global capacity.
            state['participants']['other'] = {**state['participants'][self.shared.key], 'trials': {}}
        candidate('a', 'first'); candidate('a', 'second'); candidate('a', 'third')
        self.assertIsNone(self.shared.try_acquire('first', SNAPSHOT, {}))
        self.assertIsNone(self.shared.try_acquire('second', SNAPSHOT, {}))
        self.assertEqual(self.shared.try_acquire('third', SNAPSHOT, {}), 'shared concurrency')

    def test_cross_process_round_barrier_is_atomic(self):
        self.policy({'a': 0, 'b': 0})
        context = multiprocessing.get_context('spawn')
        output = context.Queue()
        workers = [context.Process(target=concurrent_dispatch, args=(self.path, k, output)) for k in ['a','b']]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        outcomes = [output.get(timeout=2) for _ in range(4)]
        self.assertTrue(any(reason is None for _,_,reason in outcomes))
        policy = json.loads(self.shared.state_path.read_text())['interleaving']
        seen = Counter()
        for receipt in sorted(policy['receipts'].values(), key=lambda r:r['started_at']):
            self.assertEqual(seen[receipt['cell']], min(seen.get(k,0) for k in ['a','b']))
            seen[receipt['cell']] += 1

    def test_resume_reorders_and_exposes_cells_outside_semaphore_window(self):
        async def exercise():
            configs = [NS(trial_name=name, task=NS(path=Path(task)), agent=NS(model_name='m', kwargs={'reasoning_effort':'high'}))
                       for name,task in [('ahead-next','ahead'),('behind-next','behind')]]
            a, b = [rounds.config_cell(c) for c in configs]
            self.policy({a:2,b:0})
            semaphore = asyncio.Semaphore(0)
            job = NS(config=NS(job_name='job'), _remaining_trial_configs=configs,
                     _trial_queue=NS(_semaphore=semaphore))
            waiter = asyncio.create_task(semaphore.acquire())
            await asyncio.sleep(0)
            rounds.register(job, self.shared)
            await asyncio.wait_for(waiter, 1)
            self.assertEqual(job._remaining_trial_configs[0].trial_name, 'behind-next')
            self.assertIs(job._trial_queue._semaphore, semaphore)
        asyncio.run(exercise())

    def test_crash_before_trial_creation_returns_slot_and_preserves_history(self):
        key = rounds.cell_key('task','m','max')
        self.policy({key:0})
        candidate(key, 'lost')
        self.assertIsNone(self.shared.try_acquire('lost', SNAPSHOT, {}))
        self.shared.release('lost')
        config = NS(trial_name='retry', task=NS(path=Path('task')), agent=NS(model_name='m', kwargs={'reasoning_effort':'max'}))
        job = NS(config=NS(job_name='job'), _remaining_trial_configs=[config], _trial_queue=NS(_semaphore=asyncio.Semaphore(1)))
        rounds.TRIALS.clear(); rounds.QUEUES.clear()
        rounds.register(job, self.shared)
        policy = json.loads(self.shared.state_path.read_text())['interleaving']
        self.assertEqual(policy['counts'][key], 0)
        self.assertIn('lost', policy['retired_receipts'])
        self.assertIsNone(self.shared.try_acquire('retry', SNAPSHOT, {}))

    def test_transient_state_read_never_cancels_waiting_trials(self):
        from unittest.mock import patch
        self.policy({'a':0})
        candidate('a', 'first')
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertEqual(rounds.wait_reason('first', rounds.cached_state(self.shared)),
                             'interleaving: waiting for shared state')
        rounds._cache.clear()
        before = rounds.cached_state(self.shared)
        with patch.object(rounds.time, 'monotonic', return_value=10**12), patch.object(Path, 'read_text', side_effect=OSError):
            self.assertEqual(rounds.cached_state(self.shared), before)

    def test_promoted_revision_keeps_current_progress_and_retires_old_version(self):
        old = rounds.cell_key('old','m','high')
        new = rounds.cell_key('new','m','high')
        self.policy({old:0})
        with self.shared.locked() as (_, state):
            state['interleaving']['installed_at'] = 1
        candidate(old,'old-start')
        self.assertIsNone(self.shared.try_acquire('old-start',SNAPSHOT,{}))
        self.shared.release('old-start')
        (self.root/'benchmark-user-plan.json').write_text(json.dumps(dict(active_full_campaign='replacement')))
        (self.root/'replacement.plan.json').write_text(json.dumps(dict(attempts=20)))
        (self.root/'replacement.config.json').write_text(json.dumps(dict(tasks=[{'path':'new'}],
            agents=[dict(model_name='m',kwargs={'reasoning_effort':'high'})])))
        rounds.refresh_selection(self.shared)
        policy=json.loads(self.shared.state_path.read_text())['interleaving']
        self.assertEqual(policy['counts'],{new:0})
        self.assertIn('old-start',policy['retired_receipts'])
        self.assertEqual(policy['installed_at'],1)
        candidate(old,'old-pending')
        self.assertIn('retired',self.shared.try_acquire('old-pending',SNAPSHOT,{}))


if __name__ == '__main__':
    unittest.main()

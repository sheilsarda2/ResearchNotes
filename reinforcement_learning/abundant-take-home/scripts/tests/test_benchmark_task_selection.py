"""Queue exclusions must preserve completed evidence and the remaining sweep."""
from collections import Counter
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_task_selection import exclude_pending, initialize_original_lock


class SelectionTests(unittest.TestCase):
    def test_multiple_excluded_revisions_preserve_history_and_other_cells(self):
        configs = [SimpleNamespace(task=SimpleNamespace(path=Path(task)),
                                   trial_name=f'{task}-{attempt}')
                   for task in ('saturated', 'invalid-verifier', 'keep') for attempt in range(20)]
        historical = configs[0]
        job = SimpleNamespace(_trial_configs=configs[:], _remaining_trial_configs=configs[1:])
        removed = exclude_pending(job, {'saturated', 'invalid-verifier'})
        self.assertEqual(removed, 39)
        self.assertEqual(len(job._remaining_trial_configs), 20)
        self.assertTrue(all(c.task.path.name == 'keep' for c in job._remaining_trial_configs))
        self.assertIn(historical, job._trial_configs)
        self.assertEqual(initialize_original_lock(job, lambda j: len(j._trial_configs)), 60)

    def test_excludes_only_pending_work_and_preserves_every_other_cell(self):
        tasks = ['burn-scoped-checkpoint-remap'] + [f'task-{i}' for i in range(13)]
        configs = [SimpleNamespace(task=SimpleNamespace(path=Path(task)),
                                   trial_name=f'{task}-{cell}-{attempt}', cell=cell)
                   for attempt in range(20) for cell in range(9) for task in tasks]
        historical = [c for c in configs if c.trial_name in
                      {'burn-scoped-checkpoint-remap-0-0', 'burn-scoped-checkpoint-remap-1-0', 'task-0-0-0'}]
        historical_names = {c.trial_name for c in historical}
        job = SimpleNamespace(_trial_configs=configs[:], _remaining_trial_configs=[
            c for c in configs if c.trial_name not in historical_names])
        removed = exclude_pending(job, {'burn-scoped-checkpoint-remap'})
        self.assertEqual(removed, 178)
        self.assertFalse(any(c.task.path.name == tasks[0] for c in job._remaining_trial_configs))
        self.assertTrue(all(any(c is old for c in job._trial_configs) for old in historical))
        retained = [c for c in job._trial_configs if c.task.path.name != tasks[0]]
        self.assertEqual(len(retained), 2340)
        self.assertEqual(set(Counter((c.task.path.name, c.cell) for c in retained).values()), {20})
        self.assertEqual(exclude_pending(job, {'burn-scoped-checkpoint-remap'}), 0)

    def test_empty_exclusion_preserves_queue_order(self):
        configs = [SimpleNamespace(task=SimpleNamespace(path=Path('keep')), trial_name=str(i)) for i in range(3)]
        job = SimpleNamespace(_trial_configs=configs[:], _remaining_trial_configs=configs[:])
        self.assertEqual(exclude_pending(job, []), 0)
        self.assertEqual(job._trial_configs, configs)
        self.assertEqual(job._remaining_trial_configs, configs)

    def test_original_lock_inputs_and_live_queue_survive_lock_failure(self):
        original = [SimpleNamespace(task=SimpleNamespace(path=Path(name)), trial_name=name)
                    for name in ['drop', 'keep']]
        job = SimpleNamespace(_trial_configs=original, _remaining_trial_configs=original[:])
        exclude_pending(job, {'drop'})
        selected = job._trial_configs
        self.assertEqual(initialize_original_lock(job, lambda j: len(j._trial_configs)), 2)
        self.assertIs(job._trial_configs, selected)
        def fail(j):
            self.assertIs(j._trial_configs, original)
            raise RuntimeError('lock error')
        with self.assertRaises(RuntimeError):
            initialize_original_lock(job, fail)
        self.assertIs(job._trial_configs, selected)


if __name__ == '__main__':
    unittest.main()

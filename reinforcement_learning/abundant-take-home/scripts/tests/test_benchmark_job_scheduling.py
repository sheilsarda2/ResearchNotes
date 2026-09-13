"""Explicit repair jobs may coexist; inferred deficits wait for drained work."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_job_scheduling import select_jobs


class JobSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.primary = {"name": "primary", "config": "primary.config.json", "sha256": "primary-hash"}
        self.repair = {"name": "repair-001", "config": "repair.config.json", "sha256": "repair-hash"}
        self.jobs = [self.primary, self.repair]

    def test_active_primary_allows_explicit_repair_without_relaunching_primary(self):
        pending, may_create = select_jobs(self.jobs, {"primary"}, set())
        self.assertEqual(pending, [self.repair])
        self.assertFalse(may_create)

    def test_active_repair_does_not_block_interrupted_primary_from_resuming(self):
        pending, may_create = select_jobs(self.jobs, {"repair-001"}, set())
        self.assertEqual(pending, [self.primary])
        self.assertFalse(may_create)

    def test_all_active_jobs_are_excluded_and_prevent_inferred_repairs(self):
        pending, may_create = select_jobs(self.jobs, {"primary", "repair-001"}, set())
        self.assertEqual(pending, [])
        self.assertFalse(may_create)

    def test_completed_primary_is_not_resumed_while_explicit_repair_is_pending(self):
        pending, may_create = select_jobs(self.jobs, set(), {"primary"})
        self.assertEqual(pending, [self.repair])
        self.assertFalse(may_create)

    def test_inferred_repairs_wait_for_completed_jobs_processes_to_exit(self):
        # A saved job result does not prove that its process has stopped. Keep
        # generation disabled until the active process inventory drains too.
        finished = {"primary", "repair-001"}
        pending, may_create = select_jobs(self.jobs, {"repair-001"}, finished)
        self.assertEqual(pending, [])
        self.assertFalse(may_create)
        pending, may_create = select_jobs(self.jobs, set(), finished)
        self.assertEqual(pending, [])
        self.assertTrue(may_create)

    def test_preserves_planned_order_and_does_not_mutate_job_records(self):
        original = [dict(job) for job in self.jobs]
        pending, may_create = select_jobs(self.jobs, set(), set())
        self.assertEqual(pending, original)
        self.assertIs(pending[0], self.primary)
        self.assertFalse(may_create)
        self.assertEqual(self.jobs, original)


if __name__ == "__main__":
    unittest.main()

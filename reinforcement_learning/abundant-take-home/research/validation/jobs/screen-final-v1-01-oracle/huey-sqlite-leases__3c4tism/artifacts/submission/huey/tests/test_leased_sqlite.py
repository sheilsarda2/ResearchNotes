import os
import tempfile
import unittest

from huey import LeasedSqliteHuey
from huey.exceptions import LeaseLost


class TestLeasedSqlite(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.clock = [100.0]
        self.huey = LeasedSqliteHuey(
            'lease-test', filename=os.path.join(self.directory.name, 'q.db'),
            lease_seconds=10, clock=lambda: self.clock[0])

    def tearDown(self):
        self.huey.storage.close()
        self.directory.cleanup()

    def test_generation_and_completion(self):
        @self.huey.task()
        def double(value):
            return value * 2
        result = double(4)
        expired = self.huey.dequeue()
        self.assertEqual(self.huey.pending_count(), 0)
        self.clock[0] = 110
        current = self.huey.dequeue()
        self.assertNotEqual(expired.lease_token, current.lease_token)
        self.assertRaises(LeaseLost, self.huey.execute, expired)
        self.assertEqual(self.huey.execute(current), 8)
        self.assertEqual(result.get(), 8)
        self.assertEqual(self.huey.storage.inflight_count(), 0)

    def test_retry_handoff_failure(self):
        @self.huey.task(retries=1, retry_delay=5)
        def fail():
            raise ValueError('failed')
        result = fail()
        task = self.huey.dequeue()
        original = self.huey.storage.add_to_schedule
        def fail_after_write(data, timestamp):
            original(data, timestamp)
            raise RuntimeError('storage failed')
        self.huey.storage.add_to_schedule = fail_after_write
        self.assertRaises(RuntimeError, self.huey.execute, task)
        self.assertEqual(self.huey.scheduled_count(), 0)
        self.assertIsNone(self.huey.result(result.id))
        self.clock[0] = 110
        recovered = self.huey.dequeue()
        self.assertEqual(recovered.id, result.id)
        self.assertEqual(recovered.retries, 1)

import os
import tempfile
import unittest

from huey import SqliteHuey
from huey.exceptions import CancelExecution


class TestFailureArchive(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.huey = SqliteHuey(filename=os.path.join(self.directory.name, 'app.db'),
                               failure_archive=True)

    def tearDown(self):
        self.huey.storage.close()
        self.directory.cleanup()

    def test_preserves_invocation_and_redrives_once(self):
        fixed = []

        @self.huey.task()
        def work(values):
            original = list(values)
            values.append(9)
            if not fixed:
                raise ValueError('broken')
            return original

        result = work([1, 2])
        self.huey.execute(self.huey.dequeue())
        failure = self.huey.failures()[0]
        self.assertEqual(failure.task_id, result.id)
        self.assertEqual(self.huey.deserialize_task(failure.data).args, ([1, 2],))
        self.huey.storage.flush_results()
        fixed.append(True)
        receipt = self.huey.redrive(failure.failure_id)
        self.assertNotEqual(receipt.task_id, result.id)
        self.assertEqual(self.huey.redrive(failure.failure_id).task_id, receipt.task_id)
        self.assertEqual(self.huey.pending_count(), 1)
        self.huey.execute(self.huey.dequeue())
        self.assertEqual(receipt.result.get(), [1, 2])

    def test_cancellation_is_not_a_terminal_failure(self):
        @self.huey.task(retries=2)
        def cancel():
            raise CancelExecution(retry=False)

        cancel()
        self.huey.execute(self.huey.dequeue())
        self.assertEqual(self.huey.failures(), [])
        self.assertEqual(self.huey.pending_count(), 0)

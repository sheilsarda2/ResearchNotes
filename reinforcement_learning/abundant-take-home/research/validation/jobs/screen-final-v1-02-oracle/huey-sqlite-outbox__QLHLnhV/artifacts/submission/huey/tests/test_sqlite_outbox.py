import os
import sqlite3
import tempfile
import unittest

from huey import SqliteHuey, chord
from huey.contrib.sqlite_outbox import SqliteOutbox, OutboxConflict


class TestSqliteOutbox(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'app.db')
        self.huey = SqliteHuey('app', filename=self.path)
        self.outbox = SqliteOutbox(self.huey)
        self.connection = sqlite3.connect(self.path)
        self.outbox.initialize_schema(self.connection)
        self.connection.execute('create table records (id integer)')

        @self.huey.task()
        def double(n):
            return n * 2

        @self.huey.task()
        def total(values):
            return sum(values)

        self.double, self.total = double, total

    def tearDown(self):
        self.connection.close()
        self.huey.storage.close()
        self.directory.cleanup()

    def test_business_transaction_rollback_and_receipt_identity(self):
        task = self.double.s(5, id='record-5')
        self.connection.execute('insert into records values (5)')
        self.outbox.stage(self.connection, task, 'created-5')
        self.connection.rollback()
        self.assertIsNone(self.outbox.get('created-5'))
        self.assertEqual(self.huey.pending_count(), 0)
        with self.connection:
            self.connection.execute('insert into records values (5)')
            first = self.outbox.stage(self.connection, task, 'created-5')
            second = self.outbox.stage(self.connection, task, 'created-5')
            self.assertEqual(first.id, second.id)
            with self.assertRaises(OutboxConflict):
                self.outbox.stage(self.connection, self.double.s(6), 'created-5')
        self.assertEqual([r.id for r in self.outbox.publish()], [first.id])
        self.huey.execute(self.huey.dequeue())
        self.assertEqual(first.result.get(), 10)
        self.assertEqual(self.outbox.publish(), [])

    def test_nested_chord_uses_normal_executor_and_callback_results(self):
        inner = chord([self.double.s(2)], self.total.s()).then(self.double.s())
        graph = chord([inner, self.double.s(3)], self.total.s()).then(self.double.s())
        self.connection.execute('begin')
        receipt = self.outbox.stage(self.connection, graph, 'workflow')
        self.connection.commit()
        self.outbox.publish()
        while self.huey.pending_count():
            self.huey.execute(self.huey.dequeue())
        self.assertEqual(receipt.result.get(preserve=True), 14)
        self.assertEqual(receipt.result.pipeline_results.get(), [14, 28])

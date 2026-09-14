"""Durable terminal failures and deliberate, detached replay for SQLite."""
import copy
from dataclasses import dataclass, field
import datetime
import json
import math
import time
import uuid


ARCHIVE_DDL = (
    'create table if not exists huey_failure ('
    'cursor integer primary key autoincrement, queue text not null, '
    'failure_id text not null unique, task_id text not null, name text not null, '
    'error text not null, traceback text not null, failed_at real not null, '
    'data blob not null, parent_failure_id text, receipt text)',
    'create index if not exists huey_failure_queue_cursor '
    'on huey_failure (queue, cursor)',
    'create table if not exists huey_failure_lineage ('
    'queue text not null, task_id text not null, parent_failure_id text not null, '
    'primary key(queue, task_id))')


@dataclass(frozen=True)
class FailureRecord:
    failure_id: str
    cursor: int
    task_id: str
    name: str
    error: str
    traceback: str
    failed_at: float
    data: bytes
    parent_failure_id: object
    redriven: bool
    replacement_id: object


@dataclass(frozen=True)
class RedriveReceipt:
    failure_id: str
    task_id: str
    task_ids: tuple
    result: object = field(compare=False, repr=False)


class FailureArchive:
    columns = ('failure_id, cursor, task_id, name, error, traceback, '
               'failed_at, data, parent_failure_id, receipt')

    def __init__(self, huey, storage):
        self.huey, self.storage = huey, storage
        self.reader = copy.copy(huey)
        self.reader.storage = storage
        self.reader._immediate = False

    def record(self, data, error):
        # Read identity from the frozen message, never from the possibly
        # mutated Task object. Reading metadata does not require registration.
        message = self.huey.serializer.deserialize(data)
        with self.storage.db(commit=True) as cursor:
            parent = cursor.execute(
                'select parent_failure_id from huey_failure_lineage '
                'where queue=? and task_id=?',
                (self.huey.name, message.id)).fetchone()
            cursor.execute(
                'insert into huey_failure (queue, failure_id, task_id, name, '
                'error, traceback, failed_at, data, parent_failure_id) '
                'values (?,?,?,?,?,?,?,?,?)',
                (self.huey.name, uuid.uuid4().hex, message.id, message.name,
                 error['error'], error['traceback'], time.time(),
                 self.storage.to_blob(data), parent[0] if parent else None))

    @staticmethod
    def _record(row):
        if row is None:
            return None
        receipt = json.loads(row[-1]) if row[-1] else None
        return FailureRecord(*row[:-1], bool(receipt),
                             receipt['task_id'] if receipt else None)

    @staticmethod
    def _integer(value, name, minimum):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError('%s must be an integer >= %s' % (name, minimum))

    @staticmethod
    def _number(value, name, minimum=None):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or
                (minimum is not None and value < minimum)):
            raise ValueError('invalid %s' % name)

    def failures(self, limit=100, after=None):
        self._integer(limit, 'limit', 1)
        if after is not None:
            self._integer(after, 'after', 0)
        rows = self.storage.sql(
            'select %s from huey_failure where queue=? and cursor>? '
            'order by cursor limit ?' % self.columns,
            (self.huey.name, after or 0, limit), results=True)
        return [self._record(row) for row in rows]

    def failure(self, failure_id):
        rows = self.storage.sql(
            'select %s from huey_failure where queue=? and failure_id=?' %
            self.columns, (self.huey.name, failure_id), results=True)
        return self._record(rows[0]) if rows else None

    def delete_failure(self, failure_id):
        with self.storage.db(commit=True) as cursor:
            cursor.execute('delete from huey_failure where queue=? and failure_id=?',
                           (self.huey.name, failure_id))
            return cursor.rowcount == 1

    def _receipt(self, data):
        from huey.api import Result, ResultGroup, Task
        payload = json.loads(data)
        result = None
        if self.huey.results:
            stages = [Result(self.reader, Task(id=tid))
                      for tid in payload['pipeline']]
            result = stages[0] if len(stages) == 1 else ResultGroup(stages)
        return RedriveReceipt(payload['failure_id'], payload['task_id'],
                              tuple(payload['task_ids']), result)

    def _replacement(self, data, retries, delay, priority):
        task = self.huey.deserialize_task(data)
        task_ids = []

        def fresh(value):
            if value is None:
                return
            value.id = str(uuid.uuid4())
            value.revoke_id = 'r:%s' % value.id
            task_ids.append(value.id)
            value.eta = None
            value.expires_resolved = None
            if isinstance(value.expires, datetime.datetime):
                # None would restore a Task class's default expiry when the
                # replacement is deserialized. False explicitly disables it.
                value.expires = False
            value.chord_config = None
            fresh(value.on_complete)
            fresh(value.on_error)

        fresh(task)
        now = self.huey._get_timestamp()
        task.retries = retries
        task.eta = now + datetime.timedelta(seconds=delay) if delay else None
        if priority is not None:
            task.priority = priority
        if task.expires:
            expiry = task.expires
            if not isinstance(expiry, datetime.timedelta):
                expiry = datetime.timedelta(seconds=expiry)
            task.expires_resolved = now + expiry
        pipeline, node = [], task
        while node is not None:
            pipeline.append(node.id)
            node = node.on_complete
        return task, task_ids, pipeline

    def redrive(self, failure_id, retries=0, delay=None, priority=None):
        self._integer(retries, 'retries', 0)
        if delay is not None:
            self._number(delay, 'delay', 0)
        if priority is not None:
            self._number(priority, 'priority')
        connection = self.storage._create_connection()
        try:
            connection.execute('begin immediate')
            row = connection.execute(
                'select data, receipt from huey_failure '
                'where queue=? and failure_id=?',
                (self.huey.name, failure_id)).fetchone()
            if row is None:
                raise KeyError(failure_id)
            if row[1] is not None:
                connection.rollback()
                return self._receipt(row[1])
            task, task_ids, pipeline = self._replacement(row[0], retries, delay, priority)
            message = self.huey.serialize_task(task)
            receipt = json.dumps(dict(failure_id=failure_id, task_id=task.id,
                                      task_ids=task_ids, pipeline=pipeline))
            connection.execute('insert into task (queue, data, priority) values (?,?,?)',
                               (self.huey.name, self.storage.to_blob(message),
                                task.priority or 0))
            connection.executemany(
                'insert into huey_failure_lineage (queue, task_id, parent_failure_id) '
                'values (?,?,?)', [(self.huey.name, tid, failure_id) for tid in task_ids])
            connection.execute('update huey_failure set receipt=? '
                               'where queue=? and failure_id=?',
                               (receipt, self.huey.name, failure_id))
            connection.commit()
            return self._receipt(receipt)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

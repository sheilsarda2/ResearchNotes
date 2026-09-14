"""Transactional staging of Huey task graphs in the application's SQLite file."""
import base64
import copy
from dataclasses import dataclass, field
import hashlib
import json
import uuid

from huey.api import Task, Result, ResultGroup, ChordResult, SqliteHuey
from huey.storage import SqliteStorage


class OutboxConflict(ValueError):
    """A submission key was reused for a different invocation graph."""


@dataclass(frozen=True)
class Submission:
    id: str
    key: str
    task_ids: tuple
    published: bool
    result: object = field(compare=False, repr=False)


class SqliteOutbox:
    """Stage with the caller's transaction; publish whole graphs atomically.

    Receipts are retained after publication, including when all queued tasks
    have been consumed. This makes submission keys idempotent across restarts.
    """
    ddl = (
        'create table if not exists huey_outbox ('
        'sequence integer primary key autoincrement, queue text not null, '
        'submission_key text not null, submission_id text not null unique, '
        'request_hash text not null, envelope text not null, '
        'published integer not null default 0, unique(queue, submission_key))',
        'create index if not exists huey_outbox_pending '
        'on huey_outbox (queue, published, sequence)')

    def __init__(self, huey):
        if not isinstance(huey, SqliteHuey):
            raise ValueError('SqliteOutbox requires a SqliteHuey')
        self.huey = huey
        # Immediate mode may use memory for debugging, but outbox staging and
        # publishing must always target the configured on-disk SQLite broker.
        self.storage = (huey.storage if isinstance(huey.storage, SqliteStorage)
                        else huey.get_storage(**huey.storage_kwargs))
        if not isinstance(self.storage, SqliteStorage):
            raise ValueError('SqliteOutbox requires SQLite storage')
        if str(self.storage.filename) == ':memory:':
            raise ValueError('SqliteOutbox requires an on-disk database')
        # Receipt reads always address the durable broker, even if the caller
        # continues using immediate-mode's memory store for ordinary tasks.
        self._result_huey = copy.copy(huey)
        self._result_huey.storage = self.storage
        self._result_huey._immediate = False

    def initialize_schema(self, connection):
        self.storage._validate_external_connection(connection)
        for sql in self.ddl:
            connection.execute(sql)

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key:
            raise ValueError('key must be a non-empty string')
        return key

    def _result(self, descriptor):
        if not self.huey.results:
            return None
        kind = descriptor['kind']
        if kind == 'task':
            return Result(self._result_huey, Task(id=descriptor['id']))
        if kind == 'group':
            return ResultGroup([self._result(t) for t in descriptor['results']])
        members = [self._result(t) for t in descriptor['members']]
        callback = self._result(descriptor['callback'])
        stages = descriptor['pipeline']
        pipeline = (ResultGroup([self._result(t) for t in stages])
                    if len(stages) > 1 else None)
        return ChordResult(members, callback, pipeline)

    def _receipt(self, row):
        submission_id, key, payload, published = row
        envelope = json.loads(payload)
        return Submission(submission_id, key, tuple(envelope['task_ids']),
                          bool(published), self._result(envelope['result']))

    def stage(self, connection, task_or_group_or_chord, key):
        key = self._key(key)
        self.storage._validate_external_connection(connection)
        if not connection.in_transaction:
            raise ValueError('stage requires an active caller transaction')
        plan = self.huey.prepare_task_graph(task_or_group_or_chord)
        request_hash = hashlib.sha256(plan.request).hexdigest()
        existing = connection.execute(
            'select request_hash, submission_id, submission_key, envelope, '
            'published from huey_outbox where queue=? and submission_key=?',
            (self.huey.name, key)).fetchone()
        if existing is not None:
            if existing[0] != request_hash:
                raise OutboxConflict('submission key already has another payload')
            return self._receipt(existing[1:])
        envelope = json.dumps({
            'version': 1,
            'messages': [(base64.b64encode(data).decode('ascii'), priority)
                         for data, priority in plan.messages],
            'result': plan.result, 'task_ids': plan.task_ids},
            separators=(',', ':'))
        submission_id = uuid.uuid4().hex
        connection.execute(
            'insert into huey_outbox (queue, submission_key, submission_id, '
            'request_hash, envelope) values (?,?,?,?,?)',
            (self.huey.name, key, submission_id, request_hash, envelope))
        return self._receipt((submission_id, key, envelope, 0))

    def get(self, key):
        key = self._key(key)
        connection = self.storage._create_connection()
        try:
            row = connection.execute(
                'select submission_id, submission_key, envelope, published '
                'from huey_outbox where queue=? and submission_key=?',
                (self.huey.name, key)).fetchone()
            return self._receipt(row) if row is not None else None
        finally:
            connection.close()

    def pending_count(self):
        connection = self.storage._create_connection()
        try:
            return connection.execute('select count(*) from huey_outbox '
                                      'where queue=? and published=0',
                                      (self.huey.name,)).fetchone()[0]
        finally:
            connection.close()

    def publish(self, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError('limit must be a positive integer')
        receipts = []
        connection = self.storage._create_connection()
        try:
            for _ in range(limit):
                connection.execute('begin immediate')
                try:
                    row = connection.execute(
                        'select sequence, submission_id, submission_key, '
                        'envelope, published from huey_outbox '
                        'where queue=? and published=0 order by sequence limit 1',
                        (self.huey.name,)).fetchone()
                    if row is None:
                        connection.rollback()
                        break
                    envelope = json.loads(row[3])
                    messages = [(base64.b64decode(data), priority)
                                for data, priority in envelope['messages']]
                    self.storage.enqueue_many(messages, connection)
                    connection.execute(
                        'update huey_outbox set published=1 where sequence=?',
                        (row[0],))
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                receipts.append(self._receipt((row[1], row[2], row[3], 1)))
        finally:
            connection.close()
        return receipts

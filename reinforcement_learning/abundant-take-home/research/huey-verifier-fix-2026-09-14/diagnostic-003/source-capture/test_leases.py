"""Behavioral acceptance tests; no expected source layout or private schema."""
import datetime as dt
import multiprocessing as mp
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from huey import LeasedSqliteHuey, SqliteHuey, CancelExecution, RetryTask, chord
from huey.api import Task
from huey.consumer import Worker, Scheduler
from huey.exceptions import LeaseLost, TaskException, TaskTimeout
from huey.storage import LeasedSqliteStorage


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


def huey_at(path, clock=None, **kwargs):
    return LeasedSqliteHuey('test', filename=str(path), lease_seconds=30,
                           clock=clock or Clock(), **kwargs)


def register(h):
    @h.task(context=True, name='compute')
    def compute(value, action='ok', task=None):
        if action == 'fail':
            raise ValueError('requested failure')
        if action == 'timeout':
            raise TaskTimeout('requested timeout')
        if action == 'cancel':
            raise CancelExecution(retry=False)
        if action == 'interrupt':
            raise KeyboardInterrupt()
        if action == 'exit':
            raise SystemExit(23)
        if action == 'retry' and task.retries == 0:
            raise RetryTask(delay=0)
        return value * 2
    return compute


def work(h, timestamp=None):
    Worker(h, threading.Event(), 0, 0, 1).loop(timestamp)


def promote(h, timestamp):
    class ImmediateScheduler(Scheduler):
        def sleep_for_interval(self, *args):
            pass
    scheduler = ImmediateScheduler(h, threading.Event(), 1, False)
    scheduler.loop(timestamp)


def child_assert(proc):
    proc.join(10)
    if proc.is_alive():
        proc.kill()
        proc.join(5)
        pytest.fail('child did not terminate')
    assert proc.exitcode == 0


def wait_message(conn, proc, expected):
    if not conn.poll(10):
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        pytest.fail('child did not reach barrier; exit=%r' % proc.exitcode)
    assert conn.recv() == expected


def stop_at_barrier(proc):
    os.kill(proc.pid, signal.SIGKILL)
    proc.join(10)
    assert proc.exitcode == -signal.SIGKILL


class SqliteInsertProbe:
    """Observe actual SQLite row inserts, including direct SQL and bulk writes.

    Discover the queue's write tables through the public enqueue operation on
    an empty queue, rather than requiring a table name or a Python call path.
    SQLite AFTER INSERT triggers reach the failpoint inside the transaction.
    """
    function = '__huey_verifier_insert_probe'

    def __init__(self, path, connect):
        self.path, self.connect = path, connect
        self.triggers = []
        self.queue_tables = set()
        self.identifying = False
        self.after = None
        self.callback = None
        self.hits = 0
        self.reached = False

    def inserted(self, table):
        if self.identifying:
            self.queue_tables.add(table)
        elif self.after is not None and table in self.queue_tables:
            self.hits += 1
            if self.hits == self.after:
                self.reached = True
                self.callback()
        return 0

    def watch_queue(self, storage):
        assert storage.queue_size() == storage.inflight_count() == 0
        with self.connect(self.path) as conn:
            tables = conn.execute("select name from sqlite_master where "
                                  "type = 'table' and name not like 'sqlite_%'").fetchall()
            for number, (table,) in enumerate(tables):
                trigger = '__huey_verifier_insert_probe_%s' % number
                quoted = '"' + table.replace('"', '""') + '"'
                literal = "'" + table.replace("'", "''") + "'"
                conn.execute('create trigger "%s" after insert on %s begin '
                             'select %s(%s); end' %
                             (trigger, quoted, self.function, literal))
                self.triggers.append(trigger)
        self.identifying = True
        try:
            storage.enqueue(b'verifier-sqlite-write-probe')
        finally:
            self.identifying = False
            storage.flush_queue()
        assert self.queue_tables, 'SQLite insertion probe did not observe enqueue'

    def arm(self, after, callback):
        self.after, self.callback = after, callback
        self.hits, self.reached = 0, False

    def disarm(self):
        self.after = None

    def close(self):
        self.disarm()
        with self.connect(self.path) as conn:
            for trigger in self.triggers:
                conn.execute('drop trigger if exists "%s"' % trigger)


@contextmanager
def sqlite_insert_probe(monkeypatch, path):
    connect = sqlite3.connect
    probe = SqliteInsertProbe(path, connect)

    def instrumented_connect(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.create_function(probe.function, 1, probe.inserted)
        return conn

    with monkeypatch.context() as patch:
        # Register on every connection, including a new connection after fork.
        # No cursor/execute/transaction behavior is replaced.
        patch.setattr(sqlite3, 'connect', instrumented_connect)
        patch.setattr(sqlite3.dbapi2, 'connect', instrumented_connect)
        try:
            yield probe
        finally:
            probe.close()


@pytest.mark.parametrize('duration', [0, -0.1, float('nan'), float('inf'), -float('inf'), True, False])
def test_duration_validation(tmp_path, duration):
    with pytest.raises(ValueError):
        LeasedSqliteStorage(filename=str(tmp_path/'q.db'), lease_seconds=duration)


def test_reservation_expiry_fencing_and_reopen(tmp_path):
    path = tmp_path/'q.db'
    c = Clock(100)
    s = huey_at(path, c).storage
    s.enqueue(b'payload')
    first = s.reserve()
    assert isinstance(first.token, str) and first.token
    assert first.data == b'payload' and first.expires_at == 130
    assert s.queue_size() == 0 and s.inflight_count() == 1
    assert s.enqueued_items() == [] and s.reserve() is None
    s.close()
    c.value = 129
    s = huey_at(path, c).storage
    assert s.reserve() is None and s.owns(first.token)
    c.value = 130
    assert not s.owns(first.token)
    assert not s.ack(first.token) and not s.renew(first.token)
    assert not s.release(first.token)
    assert s.queue_size() == 1 and s.inflight_count() == 0
    second = s.reserve()
    assert second.data == first.data and second.token != first.token
    assert not s.ack(first.token) and not s.renew(first.token) and not s.release(first.token)
    assert s.ack(second.token) and not s.ack(second.token)
    assert s.reserve() is None and s.inflight_count() == 0
    # Message identity reuse cannot revive a retired token.
    for i in range(8):
        s.enqueue(b'payload')
        current = s.reserve()
        assert current.token not in (first.token, second.token)
        assert not s.ack(first.token)
        assert s.ack(current.token)


def test_renew_release_priority_and_inspection(tmp_path):
    c = Clock(200)
    h = huey_at(tmp_path/'q.db', c, strict_fifo=True)
    fn = register(h)
    r1 = h.enqueue(fn.s(1, priority=1))
    r2 = h.enqueue(fn.s(2, priority=4))
    r3 = h.enqueue(fn.s(3, priority=1))
    assert [t.id for t in h.pending()] == [r2.id, r1.id, r3.id]
    t = h.dequeue()
    assert t.id == r2.id and t.lease_token
    assert h.pending_count() == 2 and h.storage.inflight_count() == 1
    c.value = 220
    assert t.renew_lease()
    c.value = 231
    assert h.storage.owns(t.lease_token)
    # A backwards explicit clock must not shorten an existing reservation.
    assert t.renew_lease(now=221)
    assert h.storage.owns(t.lease_token, now=249)
    assert h.storage.release(t.lease_token)
    assert not t.renew_lease()
    assert h.dequeue().id == r2.id
    assert [t.id for t in h.pending(1)] == [r1.id]
    assert h.storage.reserve(now=300).data
    with pytest.raises(ValueError):
        h.storage.reserve(now=float('nan'))


def test_scope_flush_and_unknown_tokens(tmp_path):
    path = tmp_path/'q.db'
    a = LeasedSqliteStorage('a', filename=str(path), clock=Clock(), lease_seconds=20)
    b = LeasedSqliteStorage('b', filename=str(path), clock=Clock(), lease_seconds=20)
    a.enqueue(b'a')
    b.enqueue(b'b')
    ra, rb = a.reserve(), b.reserve()
    assert not a.owns(rb.token) and not b.ack(ra.token)
    assert not a.release('missing') and not a.renew('missing')
    a.flush_queue()
    assert a.queue_size() == a.inflight_count() == 0
    assert not a.ack(ra.token)
    assert b.owns(rb.token) and b.ack(rb.token)


def test_legacy_schema_and_explicit_initialization(tmp_path):
    path = tmp_path/'q.db'
    legacy = SqliteHuey('test', filename=str(path), strict_fifo=True)
    legacy.storage.enqueue(b'old queue')
    legacy.storage.add_to_schedule(b'old schedule', dt.datetime(2030, 1, 1))
    legacy.put('old result', {'ok': 7})
    legacy.storage.close()
    with sqlite3.connect(path) as conn:
        conn.execute('create table business (id integer primary key, value text)')
        conn.execute("insert into business values (1, 'keep')")
        before = conn.execute('select type, name, sql from sqlite_master order by name').fetchall()
    h = huey_at(path, create_tables=False, strict_fifo=True)
    with sqlite3.connect(path) as conn:
        assert conn.execute('select type, name, sql from sqlite_master order by name').fetchall() == before
    h.storage.initialize_schema()
    h.storage.initialize_schema()
    assert h.storage.reserve().data == b'old queue'
    assert h.storage.scheduled_items() == [b'old schedule']
    assert h.get('old result', peek=True) == {'ok': 7}
    with sqlite3.connect(path) as conn:
        assert conn.execute('select * from business').fetchall() == [(1, 'keep')]
        assert 'AUTOINCREMENT' in conn.execute("select sql from sqlite_master where name='task'").fetchone()[0].upper()


def test_concurrent_process_reservers_and_thread_reservers(tmp_path):
    path = str(tmp_path/'q.db')
    h = huey_at(path)
    for i in range(97):
        h.storage.enqueue(('item-%d' % i).encode())
    ctx = mp.get_context('fork')
    start = ctx.Event()
    pipes, procs = [], []
    def consume(conn):
        local = huey_at(path)
        start.wait(5)
        items = []
        while True:
            r = local.storage.reserve()
            if r is None:
                break
            items.append((r.token, r.data))
        conn.send(items)
    for _ in range(4):
        parent, child = ctx.Pipe(False)
        p = ctx.Process(target=consume, args=(child,))
        p.start()
        pipes.append(parent)
        procs.append(p)
    start.set()
    items = []
    for conn, p in zip(pipes, procs):
        assert conn.poll(10)
        items.extend(conn.recv())
        child_assert(p)
    assert len(items) == 97
    assert len({token for token, _ in items}) == 97
    assert {data for _, data in items} == {('item-%d' % i).encode() for i in range(97)}
    assert h.storage.inflight_count() == 97 and h.pending_count() == 0
    for token, _ in items:
        assert h.storage.release(token)
    with ThreadPoolExecutor(max_workers=6) as pool:
        reserved = list(pool.map(lambda _: h.storage.reserve(), range(97)))
    assert len({r.token for r in reserved}) == 97
    assert len({r.data for r in reserved}) == 97


@pytest.mark.parametrize('results', [True, False])
def test_success_final_failure_and_cancel_are_acknowledged(tmp_path, results):
    c = Clock()
    h = huey_at(tmp_path/'q.db', c, results=results)
    fn = register(h)
    ids = []
    for action in ('ok', 'fail', 'timeout', 'cancel'):
        task = fn.s(4, action)
        ids.append(task.id)
        h.enqueue(task)
        work(h)
        assert h.pending_count() == 0 and h.storage.inflight_count() == 0
    c.value += 500
    assert h.dequeue() is None
    if results:
        assert h.result(ids[0]) == 8
        for tid in ids[1:3]:
            with pytest.raises(TaskException):
                h.result(tid)
    else:
        assert h.result_count() == 0


def test_delayed_retry_backoff_and_retrytask(tmp_path):
    h = huey_at(tmp_path/'q.db', store_intermediate_errors=False)
    fn = register(h)
    start = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    result = h.enqueue(fn.s(7, 'fail', retries=2, retry_delay=3, retry_backoff=2))
    work(h)
    first = h.scheduled()[0]
    assert first.id == result.id and first.retries == 1 and first.retry_delay == 6
    assert first.eta >= start + dt.timedelta(seconds=3)
    assert h.result(result.id) is None
    assert h.pending_count() == 0 and h.storage.inflight_count() == 0
    promote(h, first.eta)
    work(h, first.eta)
    second = h.scheduled()[0]
    assert second.retries == 0 and second.retry_delay == 12
    promote(h, second.eta)
    work(h, second.eta)
    assert h.scheduled_count() == h.pending_count() == h.storage.inflight_count() == 0
    with pytest.raises(TaskException):
        h.result(result.id)
    # Use an aware ETA: upstream interprets naive input as local time.
    retry_eta = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(days=2)
    expected_eta = retry_eta.replace(tzinfo=None)
    @h.task(name='explicit_retry', context=True)
    def explicit_retry(value, task=None):
        if task.eta is None:
            raise RetryTask(eta=retry_eta)
        return value
    result = explicit_retry(11)
    work(h)
    assert h.scheduled()[0].eta == expected_eta
    promote(h, expected_eta)
    work(h, expected_eta)
    assert result.get() == 11 and h.storage.inflight_count() == 0


def test_delays_revocations_expiration_and_pre_cancel(tmp_path):
    h = huey_at(tmp_path/'q.db')
    fn = register(h)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    future = (now + dt.timedelta(days=1)).replace(microsecond=0)
    delayed = fn.schedule((8,), eta=future.replace(tzinfo=dt.timezone.utc))
    work(h, now)
    assert h.scheduled_count() == 1 and h.pending_count() == 0
    assert h.storage.inflight_count() == 0
    promote(h, future)
    work(h, future)
    assert delayed.get() == 16
    revoked = fn(10)
    revoked.revoke()
    work(h)
    expired = fn.s(2)
    expired.expires_resolved = now - dt.timedelta(days=1)
    h.enqueue(expired)
    work(h, now)
    @h.pre_execute()
    def cancel(task):
        raise CancelExecution()
    fn(20)
    work(h)
    assert h.pending_count() == h.scheduled_count() == h.storage.inflight_count() == 0


def test_context_renewal_and_stale_execution(tmp_path):
    c = Clock(100)
    h = huey_at(tmp_path/'q.db', c)
    @h.task(context=True, name='renewing')
    def renewing(task=None):
        c.value = 120
        assert task.renew_lease()
        c.value = 140
        return task.lease_token
    result = renewing()
    t = h.dequeue()
    token = t.lease_token
    assert isinstance(token, str) and token
    assert h.execute(t) == token
    assert result.get() == token
    assert not h.storage.owns(token) and not t.renew_lease()
    c.value = 200
    register(h)(3)
    t = h.dequeue()
    c.value = 230
    with pytest.raises(LeaseLost):
        h.execute(t)
    newer = h.dequeue()
    with pytest.raises(LeaseLost):
        h.execute(t)
    assert h.storage.owns(newer.lease_token)
    h.execute(newer)
    assert h.storage.inflight_count() == 0


def test_expiry_during_task_does_not_commit_outcome(tmp_path):
    c = Clock(100)
    h = huey_at(tmp_path/'q.db', c)
    fn = register(h)
    @h.task(name='expire_in_body')
    def expire_in_body():
        c.value = 131
        return 10
    result = h.enqueue(expire_in_body.s().then(fn.s()))
    first = h.dequeue()
    with pytest.raises(LeaseLost):
        h.execute(first)
    assert h.result(first.id) is None
    assert [t.id for t in h.pending()] == [first.id]
    assert h.storage.inflight_count() == 0


@pytest.mark.parametrize('action', ['interrupt', 'exit'])
def test_interruption_keeps_original_message(tmp_path, action):
    c = Clock()
    h = huey_at(tmp_path/'q.db', c)
    fn = register(h)
    result = fn(9, action)
    t = h.dequeue()
    if action == 'exit':
        with pytest.raises(SystemExit):
            h.execute(t)
    else:
        h.execute(t)
    assert h.storage.inflight_count() == 1
    c.value += 30
    recovered = h.dequeue()
    assert recovered.id == result.id and recovered.data == ((9, action), {})
    assert recovered.lease_token != t.lease_token


def test_late_retry_insertion_error_rolls_back_outcome(tmp_path, monkeypatch):
    path = tmp_path/'q.db'
    c = Clock()
    h = huey_at(path, c)
    fn = register(h)
    result = h.enqueue(fn.s(9, 'fail', retries=1, retry_delay=5))
    t = h.dequeue()
    original = h.storage.add_to_schedule
    def write_then_fail(data, timestamp):
        original(data, timestamp)
        raise RuntimeError('failure after schedule insertion')
    monkeypatch.setattr(h.storage, 'add_to_schedule', write_then_fail)
    with pytest.raises(RuntimeError):
        h.execute(t)
    h.storage.close()
    reopened = huey_at(path, c)
    register(reopened)
    assert reopened.scheduled_count() == 0
    assert reopened.result(result.id) is None
    assert reopened.storage.inflight_count() == 1
    c.value += 30
    recovered = reopened.dequeue()
    assert recovered.id == t.id and recovered.retries == 1
    reopened.execute(recovered)
    assert reopened.scheduled_count() == 1 and reopened.storage.inflight_count() == 0


def test_callback_insertion_error_rolls_back_result_and_ack(tmp_path, monkeypatch):
    c = Clock()
    h = huey_at(tmp_path/'q.db', c)
    fn = register(h)
    results = h.enqueue(fn.s(3).then(fn.s()))
    t = h.dequeue()
    original = h.storage.enqueue
    def write_then_fail(data, priority=None):
        original(data, priority)
        raise RuntimeError('failure after callback insertion')
    monkeypatch.setattr(h.storage, 'enqueue', write_then_fail)
    with pytest.raises(RuntimeError):
        h.execute(t)
    assert h.result(t.id) is None and h.pending_count() == 0
    c.value += 30
    assert [x.id for x in h.pending()] == [t.id]
    monkeypatch.setattr(h.storage, 'enqueue', original)
    work(h)
    work(h)
    assert results.get() == [6, 12]


def test_due_batch_late_error_and_scheduler_integration(tmp_path, monkeypatch):
    with sqlite_insert_probe(monkeypatch, tmp_path/'q.db') as probe:
        h = huey_at(tmp_path/'q.db')
        probe.watch_queue(h.storage)
        fn = register(h)
        now = dt.datetime(2030, 1, 1)
        future = now + dt.timedelta(days=1)
        tasks = [fn.s(i, priority=i % 3) for i in range(11)]
        for t in tasks:
            t.eta = now
            h.add_schedule(t)
        t = fn.s(99); t.eta = future; h.add_schedule(t)
        def fail_after_insert():
            raise RuntimeError('mid-promotion')
        probe.arm(3, fail_after_insert)
        promote(h, now)
        assert probe.reached and probe.hits >= 3, 'insertion failpoint was not reached'
        assert h.pending_count() == 0 and h.scheduled_count() == 12
        probe.disarm()
        promote(h, now)
        assert h.pending_count() == 11 and h.scheduled_count() == 1
        assert {t.id for t in h.pending()} == {t.id for t in tasks}
        for _ in tasks:
            work(h, now)
        assert [h.result(t.id) for t in tasks] == [i * 2 for i in range(11)]


def test_bad_message_does_not_block_and_immediate_is_compatible(tmp_path):
    h = huey_at(tmp_path/'q.db')
    fn = register(h)
    h.storage.enqueue(b'not-a-pickle')
    result = fn(5)
    work(h)
    work(h)
    assert result.get() == 10 and h.storage.inflight_count() == 0
    immediate = huey_at(tmp_path/'immediate.db', immediate=True)
    fn = register(immediate)
    assert fn(6).get() == 12
    assert Task().lease_token is None and Task().renew_lease() is False
    direct = fn.s(8)
    assert immediate.execute(direct) == 16


def test_pipeline_error_callback_and_chord_compatibility(tmp_path):
    h = huey_at(tmp_path/'q.db', store_intermediate_errors=False)
    fn = register(h)
    @h.task(name='sum_results')
    def sum_results(values):
        return sum(values)
    result = h.enqueue(chord([fn.s(1), fn.s(2)], sum_results.s()))
    for _ in range(3):
        work(h)
    assert result.get() == 6
    @h.task(name='handle_error')
    def handle_error(exc):
        return str(exc)
    task = fn.s(4, 'fail', retries=1).error(handle_error.s())
    h.enqueue(task)
    work(h)
    assert h.pending_count() == 1
    work(h)
    assert h.pending_count() == 1
    work(h)
    assert h.result(task.on_error.id) == 'requested failure'
    assert h.storage.inflight_count() == 0


def test_real_consumer_sigkill_recovery_and_unlocked_task_body(tmp_path):
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe(False)
    gate = ctx.Event()
    clock_file, marker = tmp_path/'clock', tmp_path/'entered'
    clock_file.write_text('100')
    path = tmp_path/'q.db'
    def clock():
        return float(clock_file.read_text())
    def make():
        h = huey_at(path, clock)
        @h.task(name='worker_job')
        def job(value):
            if not marker.exists():
                marker.write_text('entered')
                child.send('running')
                gate.wait(20)
            return value + 1
        return h, job
    h, job = make()
    result = job(30)
    def run_consumer():
        consumer = h.create_consumer(workers=1, worker_type='thread', periodic=False,
                                     initial_delay=0.01, max_delay=0.01)
        consumer.run()
    p = ctx.Process(target=run_consumer)
    p.start()
    wait_message(parent, p, 'running')
    # A separate producer must be able to write while the task body waits.
    fresh, fresh_job = make()
    second = fresh_job(50)
    assert fresh.pending_count() == 1 and fresh.storage.inflight_count() == 1
    stop_at_barrier(p)
    clock_file.write_text('130')
    recovered, _ = make()
    work(recovered)
    work(recovered)
    assert recovered.result(result.id) == 31
    assert recovered.result(second.id) == 51
    assert recovered.pending_count() == recovered.storage.inflight_count() == 0


def test_kill_mid_retry_handoff_rolls_back_then_recovers(tmp_path):
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe(False)
    gate = ctx.Event()
    c = Clock()
    path = tmp_path/'q.db'
    h = huey_at(path, c)
    fn = register(h)
    result = h.enqueue(fn.s(5, 'fail', retries=1, retry_delay=20))
    def fail_mid_handoff():
        original = h.storage.add_to_schedule
        def staged(data, timestamp):
            original(data, timestamp)
            child.send('inserted')
            gate.wait(20)
        h.storage.add_to_schedule = staged
        work(h)
    p = ctx.Process(target=fail_mid_handoff)
    p.start()
    wait_message(parent, p, 'inserted')
    stop_at_barrier(p)
    fresh = huey_at(path, c)
    register(fresh)
    assert fresh.scheduled_count() == 0 and fresh.result(result.id) is None
    c.value += 30
    t = fresh.dequeue()
    assert t.id == result.id and t.retries == 1
    fresh.execute(t)
    assert fresh.scheduled_count() == 1 and fresh.storage.inflight_count() == 0


def test_kill_mid_due_transfer_restores_whole_batch(tmp_path, monkeypatch):
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe(False)
    gate = ctx.Event()
    path = tmp_path/'q.db'
    with sqlite_insert_probe(monkeypatch, path) as probe:
        h = huey_at(path)
        probe.watch_queue(h.storage)
        fn = register(h)
        due, future = dt.datetime(2030, 1, 1), dt.datetime(2031, 1, 1)
        for i in range(503):
            t = fn.s(i); t.eta = due; h.add_schedule(t)
        t = fn.s(1000); t.eta = future; h.add_schedule(t)
        other = LeasedSqliteHuey('other', filename=str(path), clock=Clock())
        other.storage.add_to_schedule(b'foreign', due)
        def transfer():
            def pause_after_insert():
                child.send(('inserted', probe.hits, probe.reached))
                gate.wait(20)
            probe.arm(501, pause_after_insert)
            h.enqueue_due(due)
        p = ctx.Process(target=transfer)
        p.start()
        wait_message(parent, p, ('inserted', 501, True))
        stop_at_barrier(p)
        fresh = huey_at(path)
        register(fresh)
        assert fresh.pending_count() == 0 and fresh.scheduled_count() == 504
        fresh.enqueue_due(due)
        assert fresh.pending_count() == 503 and fresh.scheduled_count() == 1
        assert other.storage.scheduled_items() == [b'foreign']


def test_actual_process_consumer_integration(tmp_path):
    h = huey_at(tmp_path/'q.db', Clock())
    @h.task(name='pid_value')
    def pid_value(value):
        return value + 10, os.getpid()
    # Queue before forking as well as afterward, exercising reopened storage.
    first = pid_value(1)
    consumer = h.create_consumer(workers=2, worker_type='process', periodic=False,
                                 initial_delay=0.01, max_delay=0.02)
    consumer.start()
    try:
        second = pid_value(2)
        a, pid_a = first.get(blocking=True, timeout=8)
        b, pid_b = second.get(blocking=True, timeout=8)
        assert (a, b) == (11, 12)
        assert pid_a != os.getpid() and pid_b != os.getpid()
    finally:
        consumer.stop(graceful=True)
    assert h.storage.inflight_count() == 0 and h.pending_count() == 0

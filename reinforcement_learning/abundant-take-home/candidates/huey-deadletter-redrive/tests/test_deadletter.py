"""Behavioral terminal-failure inventory and detached replay contracts."""
import contextlib
import datetime as dt
import multiprocessing as mp
import os
from pathlib import Path
import signal
import sqlite3
import threading
import time

import pytest

from huey import SqliteHuey, Error, chord, crontab
from huey.api import Result, ResultGroup
from huey.consumer import Worker
from huey.contrib.deadletter import FailureRecord, RedriveReceipt
from huey.exceptions import (CancelExecution, ConfigurationError, RetryTask,
                             TaskException, TaskLockedException, TaskTimeout,
                             RateLimitExceeded)
from huey.serializer import SignedSerializer
from huey.storage import MemoryStorage


def configured(path, name='app', **kwargs):
    h = SqliteHuey(name, filename=str(path), failure_archive=True, **kwargs)
    @h.task(name='always')
    def always(value='broken'):
        raise ValueError(value)
    @h.task(name='mutate')
    def mutate(payload, marker):
        original = list(payload['items'])
        payload['items'].append(99)
        if Path(marker).exists():
            return original
        raise RuntimeError('mutated before failure')
    @h.task(name='attempts')
    def attempts(history, count):
        history.append(len(history) + 1)
        if len(history) <= count:
            raise ValueError('attempt %s' % len(history))
        return history
    @h.task(name='mode')
    def mode(kind):
        if kind == 'timeout': raise TaskTimeout('deadline')
        if kind == 'locked': raise TaskLockedException('locked')
        if kind == 'rate-final': raise RateLimitExceeded('key', 5, retry=False)
        if kind == 'rate-retry': raise RateLimitExceeded('key', 5, retry=True)
        if kind == 'retry': raise RetryTask(delay=5)
        if kind == 'cancel': raise CancelExecution(retry=False)
        if kind == 'cancel-retry': raise CancelExecution(retry=True)
        if kind == 'interrupt': raise KeyboardInterrupt()
        return kind
    @h.task(name='echo')
    def echo(value): return value
    @h.task(name='size')
    def size(value): return len(value)
    @h.task(name='errtext')
    def errtext(exc): return str(exc)
    @h.task(name='aggregate')
    def aggregate(values):
        h.put('callback-count', (h.get('callback-count', peek=True) or 0) + 1)
        return ['error' if isinstance(v, Error) else v for v in values]
    return h, dict(always=always, mutate=mutate, attempts=attempts, mode=mode,
                   echo=echo, size=size, errtext=errtext, aggregate=aggregate)


def run_one(h, timestamp=None):
    Worker(h, threading.Event(), 0, 0, 1).loop(timestamp)


def drain(h, timestamp=None):
    for _ in range(100):
        if h.pending_count() == 0: return
        run_one(h, timestamp)
    pytest.fail('work did not finish')


def failed(h, f, value='failure'):
    task = f['always'].s(value)
    h.enqueue(task); run_one(h)
    record = next(r for r in h.failures() if r.task_id == task.id)
    return task, record


def wait_for(parent, proc):
    if not parent.poll(10):
        if proc.is_alive(): proc.kill()
        proc.join(5)
        pytest.fail('child did not reach state barrier')
    return parent.recv()


@pytest.mark.parametrize('results', [False, True])
def test_archive_survives_result_consumption_flush_and_reopen(tmp_path, results):
    path = tmp_path/'app.db'
    h, f = configured(path, results=results)
    started = time.time()
    task, record = failed(h, f, 'original-error')
    assert isinstance(record, FailureRecord)
    assert isinstance(record.failure_id, str) and isinstance(record.cursor, int)
    assert record.task_id == task.id and record.name.endswith('.always')
    assert 'original-error' in record.error and 'ValueError' in record.traceback
    assert started <= record.failed_at <= time.time()
    assert record.parent_failure_id is None and not record.redriven
    assert record.replacement_id is None
    assert h.deserialize_task(record.data).args == ('original-error',)
    if results:
        with pytest.raises(TaskException): h.result(task.id)
    h.storage.flush_results()
    # A fresh process need not register task functions to inspect metadata.
    ctx = mp.get_context('fork'); parent, child = ctx.Pipe(False)
    def inspect():
        fresh = SqliteHuey('app', filename=str(path), failure_archive=True)
        r = fresh.failure(record.failure_id)
        child.send((r.failure_id, r.data, r.error))
    proc = ctx.Process(target=inspect); proc.start()
    assert wait_for(parent, proc) == (record.failure_id, record.data, record.error)
    proc.join(10); assert proc.exitcode == 0
    assert h.failure(record.failure_id).data == record.data


def test_snapshot_precedes_pre_hooks_task_mutation_and_post_hooks(tmp_path):
    h, f = configured(tmp_path/'app.db')
    marker = str(tmp_path/'fixed')
    payload = {'items': [1, 2]}
    task = f['mutate'].s(payload, marker).then(f['size'].s())
    original_tail = task.on_complete.id
    @h.pre_execute()
    def alter(t):
        if t.id == task.id:
            t.args[0]['items'].append(7)
    @h.post_execute()
    def after(t, value, exc):
        if t.id == task.id:
            records = h.failures()
            assert len(records) == 1
            t.on_complete = None
            t.args[0]['items'].append(8)
    h.enqueue(task); drain(h)
    record = h.failures()[0]
    frozen = h.deserialize_task(record.data)
    assert frozen.args[0] == {'items': [1, 2]}
    assert frozen.on_complete.id == original_tail
    Path(marker).touch()
    receipt = h.redrive(record.failure_id)
    assert receipt.task_id != task.id and len(receipt.task_ids) == 2
    assert original_tail not in receipt.task_ids
    drain(h)
    assert receipt.result.get() == [[1, 2], 2]
    assert h.failure(record.failure_id).data == record.data


def test_terminal_attempt_snapshot_tracks_retries_and_backoff(tmp_path, monkeypatch):
    h, f = configured(tmp_path/'app.db', store_intermediate_errors=False)
    base = dt.datetime(2030, 1, 1)
    monkeypatch.setattr(h, '_get_timestamp', lambda: base)
    task = f['attempts'].s([], 3, retries=2, retry_delay=2, retry_backoff=3)
    h.enqueue(task); run_one(h, base)
    assert h.failures() == []
    scheduled = h.scheduled()[0]
    assert scheduled.retry_delay == 6 and scheduled.retries == 1
    for t in h.read_schedule(base + dt.timedelta(seconds=2)): h.enqueue(t)
    run_one(h, base + dt.timedelta(seconds=2))
    assert h.failures() == []
    for t in h.read_schedule(base + dt.timedelta(seconds=6)): h.enqueue(t)
    run_one(h, base + dt.timedelta(seconds=6))
    record = h.failures()[0]
    snapshot = h.deserialize_task(record.data)
    assert snapshot.args[0] == [1, 2] and snapshot.retries == 0
    assert snapshot.retry_delay == 18 and snapshot.retry_backoff == 3
    # A different task recovers within its retry budget and is not archived.
    result = h.enqueue(f['attempts'].s([], 1, retries=1))
    drain(h)
    assert result.get() == [1, 2] and len(h.failures()) == 1


@pytest.mark.parametrize('kind,archived', [('timeout', True), ('locked', True),
    ('rate-final', True), ('rate-retry', False), ('retry', False),
    ('cancel', False), ('cancel-retry', False), ('interrupt', False)])
def test_exact_terminal_classification(tmp_path, kind, archived):
    h, f = configured(tmp_path/'app.db')
    h.enqueue(f['mode'].s(kind)); run_one(h)
    assert len(h.failures()) == int(archived)


def test_timeout_handler_and_retry_budget_are_respected(tmp_path):
    h, f = configured(tmp_path/'app.db')
    @contextlib.contextmanager
    def timeout(seconds):
        raise TaskTimeout('handler timeout')
        yield
    h.set_timeout_handler(timeout)
    task = f['echo'].s('ok', timeout=1, retries=1)
    h.enqueue(task); run_one(h)
    assert h.failures() == [] and h.pending_count() == 1
    run_one(h)
    assert len(h.failures()) == 1 and 'handler timeout' in h.failures()[0].error


@pytest.mark.parametrize('kind', ['locked', 'rate-final'])
def test_exhausted_control_errors_archive_only_after_retry(tmp_path, kind):
    h, f = configured(tmp_path/'app.db')
    h.enqueue(f['mode'].s(kind, retries=1)); run_one(h)
    assert h.failures() == []
    if h.scheduled_count():
        for t in h.read_schedule(dt.datetime(2040, 1, 1)): h.enqueue(t)
    run_one(h, dt.datetime(2040, 1, 1))
    assert len(h.failures()) == 1


def test_revocation_expiration_precancel_periodic_are_not_failures(tmp_path):
    h, f = configured(tmp_path/'app.db')
    revoked = f['always'].s(); h.revoke(revoked); h.enqueue(revoked)
    expired = f['always'].s(expires=dt.datetime(2000, 1, 1)); h.enqueue(expired)
    canceled = f['always'].s(); h.enqueue(canceled)
    @h.pre_execute()
    def cancel(t):
        if t.id == canceled.id: raise CancelExecution()
    @h.periodic_task(crontab())
    def periodic(): raise ValueError('periodic')
    h.enqueue(periodic.s())
    drain(h)
    assert h.failures() == []


def test_redrive_is_detached_fresh_and_idempotent_after_consumption(tmp_path):
    path = tmp_path/'app.db'; h, f = configured(path)
    marker = str(tmp_path/'fixed')
    task = f['mutate'].s({'items': [3]}, marker).then(f['size'].s()).error(f['errtext'].s())
    old_ids = {task.id, task.on_complete.id, task.on_error.id}
    h.enqueue(task); drain(h)
    record = next(r for r in h.failures() if r.task_id == task.id)
    old_error = h.get(task.id, peek=True)
    old_handler_value = h.result(task.on_error.id, preserve=True)
    Path(marker).touch()
    receipt = h.redrive(record.failure_id, retries=2, priority=9)
    assert isinstance(receipt, RedriveReceipt) and isinstance(receipt.result, ResultGroup)
    assert len(receipt.task_ids) == 3 and len(set(receipt.task_ids)) == 3
    assert not old_ids.intersection(receipt.task_ids)
    replacement = h.pending()[0]
    assert replacement.retries == 2 and replacement.priority == 9
    second = h.redrive(record.failure_id, retries=7, priority=1)
    assert second.task_id == receipt.task_id and h.pending_count() == 1
    assert h.pending()[0].priority == 9
    drain(h)
    assert receipt.result.get() == [[3], 1]
    assert h.get(task.id, peek=True) == old_error
    assert h.result(task.on_error.id, preserve=True) == old_handler_value
    fresh, _ = configured(path)
    later = fresh.redrive(record.failure_id)
    assert later.task_ids == receipt.task_ids and fresh.pending_count() == 0
    r = fresh.failure(record.failure_id)
    assert r.redriven and r.replacement_id == receipt.task_id


def test_delay_expiry_options_refresh_and_absolute_deadlines_clear(tmp_path, monkeypatch):
    h, f = configured(tmp_path/'app.db', serializer=SignedSerializer('secret', compression=True))
    initial = dt.datetime(2030, 1, 1)
    task = f['always'].s('refresh', retries=0, retry_delay=4, retry_backoff=2,
                         priority=3, timeout=9, expires=60)
    task.on_complete = f['echo'].s(expires=dt.datetime(2031, 1, 1),
                                  eta=dt.datetime(2031, 1, 1), retries=2)
    # Execute a serialized attempt with a known unexpired resolved timestamp.
    task.expires_resolved = initial + dt.timedelta(seconds=60)
    h.execute(h.deserialize_task(h.serialize_task(task)), initial)
    record = h.failures()[0]
    fresh_time = initial + dt.timedelta(days=10)
    monkeypatch.setattr(h, '_get_timestamp', lambda: fresh_time)
    receipt = h.redrive(record.failure_id, retries=3, delay=12, priority=0)
    replacement = h.pending()[0]
    assert replacement.eta == fresh_time + dt.timedelta(seconds=12)
    assert replacement.expires_resolved == fresh_time + dt.timedelta(seconds=60)
    assert (replacement.retry_delay, replacement.retry_backoff, replacement.timeout,
            replacement.priority, replacement.retries) == (4, 2, 9, 0, 3)
    assert replacement.on_complete.eta is None
    assert not replacement.on_complete.expires
    assert replacement.on_complete.expires_resolved is None
    assert replacement.on_complete.retries == 2
    run_one(h, fresh_time)
    assert h.scheduled_count() == 1 and h.pending_count() == 0
    assert h.scheduled()[0].id == receipt.task_id
    absolute = f['always'].s(expires=dt.datetime(2031, 1, 1))
    h.execute(absolute, initial)
    other = next(r for r in h.failures() if r.task_id == absolute.id)
    h.redrive(other.failure_id)
    assert not h.pending()[0].expires


def test_all_chord_bindings_detach_and_old_callback_does_not_repeat(tmp_path):
    h, f = configured(tmp_path/'app.db')
    marker = str(tmp_path/'fixed')
    member = f['mutate'].s({'items': [1]}, marker).then(f['size'].s())
    member.error(f['errtext'].s())
    graph = chord([member, f['echo'].s(2)], f['aggregate'].s())
    result = h.enqueue(graph); drain(h)
    assert result.get(preserve=True) == ['error', 2]
    assert h.get('callback-count', peek=True) == 1
    record = next(r for r in h.failures() if r.task_id == member.id)
    archived = h.deserialize_task(record.data)
    assert archived.on_complete.chord_config is not None
    Path(marker).touch()
    receipt = h.redrive(record.failure_id)
    replacement = h.pending()[0]
    assert replacement.chord_config is None and replacement.on_complete.chord_config is None
    assert replacement.on_error.chord_config is None
    drain(h)
    assert receipt.result.get() == [[1], 1]
    assert h.get('callback-count', peek=True) == 1
    assert result.get(preserve=True) == ['error', 2]


def test_task_class_absolute_expiry_default_stays_cleared_after_deserialization(tmp_path, monkeypatch):
    h, f = configured(tmp_path/'app.db')
    marker = tmp_path/'fixed'
    @h.task(expires=dt.datetime(2031, 1, 1), name='absolute-default')
    def absolute_default(value):
        if not marker.exists(): raise ValueError('broken')
        return value
    task = absolute_default.s(42).then(absolute_default.s())
    h.execute(h.deserialize_task(h.serialize_task(task)), dt.datetime(2030, 1, 1))
    record = h.failures()[0]
    monkeypatch.setattr(h, '_get_timestamp', lambda: dt.datetime(2040, 1, 1))
    marker.touch()
    receipt = h.redrive(record.failure_id)
    replacement = h.pending()[0]
    assert not replacement.expires and not replacement.on_complete.expires
    drain(h, dt.datetime(2040, 1, 1))
    assert receipt.result.get() == [42, 42]


def test_snapshot_and_archive_storage_errors_surface_without_false_receipt(tmp_path, monkeypatch):
    path = tmp_path/'app.db'; h, f = configured(path)
    task = f['always'].s()
    actual = h.serialize_task
    def fail_snapshot(t): raise RuntimeError('snapshot rejected')
    monkeypatch.setattr(h, 'serialize_task', fail_snapshot)
    with pytest.raises(RuntimeError, match='snapshot rejected'): h.execute(task)
    assert h.failures() == []
    monkeypatch.setattr(h, 'serialize_task', actual)
    # Standard SQLite triggers reject persistence regardless of archive layout
    # or whether the implementation reuses a connection or opens another one.
    with sqlite3.connect(path) as connection:
        tables = connection.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'").fetchall()
        for index, (table,) in enumerate(tables):
            quoted = '"' + table.replace('"', '""') + '"'
            connection.execute('create trigger deny_%d before insert on %s begin select raise(abort, \'storage unavailable\'); end' % (index, quoted))
    with pytest.raises(sqlite3.DatabaseError): h.execute(task)
    assert h.failures() == []


def test_failure_lineage_across_retries_generations_and_parent_deletion(tmp_path):
    path = tmp_path/'app.db'; h, f = configured(path)
    _, original = failed(h, f)
    one = h.redrive(original.failure_id, retries=1)
    assert h.delete_failure(original.failure_id)
    fresh, _ = configured(path)
    run_one(fresh)
    assert fresh.failures() == []
    run_one(fresh)
    child = fresh.failures()[0]
    assert child.parent_failure_id == original.failure_id and child.task_id == one.task_id
    two = fresh.redrive(child.failure_id)
    drain(fresh)
    grandchild = next(r for r in fresh.failures() if r.task_id == two.task_id)
    assert grandchild.parent_failure_id == child.failure_id
    assert grandchild.failure_id != child.failure_id
    assert fresh.failure(child.failure_id).replacement_id == two.task_id


def test_fresh_error_callback_failures_have_lineage(tmp_path):
    h, f = configured(tmp_path/'app.db')
    @h.task(name='bad-handler')
    def bad_handler(exc): raise RuntimeError('handler failed')
    task = f['always'].s().error(bad_handler.s())
    h.enqueue(task); drain(h)
    original = next(r for r in h.failures() if r.task_id == task.id)
    receipt = h.redrive(original.failure_id)
    drain(h)
    new = [r for r in h.failures() if r.task_id in receipt.task_ids]
    assert len(new) == 2
    assert all(r.parent_failure_id == original.failure_id for r in new)


@pytest.mark.parametrize('immediate,results', [(False, False), (True, True), (True, False)])
def test_disabled_results_and_immediate_mode_use_durable_archive(tmp_path, immediate, results):
    path = tmp_path/'app.db'; h, f = configured(path, immediate=immediate, results=results)
    marker = str(tmp_path/'fixed')
    task = f['mutate'].s({'items': [1]}, marker)
    h.enqueue(task)
    if not immediate: drain(h)
    record = h.failures()[0]
    Path(marker).touch()
    receipt = h.redrive(record.failure_id)
    assert (receipt.result is None) == (not results)
    normal, _ = configured(path, results=results)
    assert [t.id for t in normal.pending()] == [receipt.task_id]
    drain(normal)
    if results: assert receipt.result.get() == [1]
    assert h.immediate is immediate


def test_schema_opt_out_explicit_init_legacy_data_and_queue_isolation(tmp_path):
    path = tmp_path/'app.db'
    ordinary = SqliteHuey('app', filename=str(path))
    ordinary.put_result('old-result', 123)
    conn = sqlite3.connect(path)
    before = conn.execute('select name, sql from sqlite_master order by name').fetchall()
    disabled = SqliteHuey('app', filename=str(path), failure_archive=False)
    assert conn.execute('select name, sql from sqlite_master order by name').fetchall() == before
    for name, args in [('failures', ()), ('failure', ('x',)), ('redrive', ('x',)), ('delete_failure', ('x',))]:
        with pytest.raises(ConfigurationError): getattr(disabled, name)(*args)
    h, f = configured(path, create_tables=False)
    assert conn.execute('select name, sql from sqlite_master order by name').fetchall() == before
    h.storage.initialize_schema()
    assert h.result('old-result') == 123
    _, first = failed(h, f)
    other, ff = configured(path, name='other')
    _, second = failed(other, ff)
    assert other.failure(first.failure_id) is None
    assert not h.delete_failure(second.failure_id)
    with pytest.raises(KeyError): h.redrive(second.failure_id)
    assert [r.failure_id for r in h.failures()] == [first.failure_id]
    with pytest.raises(ConfigurationError): SqliteHuey(filename=':memory:', failure_archive=True)
    with pytest.raises(ConfigurationError):
        SqliteHuey(filename=str(path), failure_archive=True, storage_class=MemoryStorage)


def test_cursor_pagination_delete_and_argument_validation(tmp_path):
    h, f = configured(tmp_path/'app.db')
    records = [failed(h, f, str(i))[1] for i in range(5)]
    assert h.failures(limit=2) == records[:2]
    assert h.failures(limit=2, after=records[1].cursor) == records[2:4]
    assert h.delete_failure(records[-1].failure_id)
    assert not h.delete_failure(records[-1].failure_id)
    extra = failed(h, f, 'extra')[1]
    assert extra.cursor > records[-1].cursor
    for opts in [{'limit': 0}, {'limit': True}, {'limit': 1.5}, {'after': -1}, {'after': True}]:
        with pytest.raises(ValueError): h.failures(**opts)
    for opts in [{'retries': -1}, {'retries': True}, {'retries': 1.5},
                 {'delay': -1}, {'delay': True}, {'delay': float('nan')},
                 {'delay': float('inf')}, {'priority': True}, {'priority': float('inf')}]:
        with pytest.raises(ValueError): h.redrive(extra.failure_id, **opts)
    assert not h.failure(extra.failure_id).redriven and h.pending_count() == 0


def test_insertion_and_serialization_failures_rollback_claim(tmp_path, monkeypatch):
    path = tmp_path/'app.db'; h, f = configured(path)
    _, record = failed(h, f)
    with sqlite3.connect(path) as conn:
        conn.execute("create trigger reject_replay before insert on task begin select raise(abort, 'reject'); end")
    with pytest.raises(sqlite3.IntegrityError): h.redrive(record.failure_id)
    assert not h.failure(record.failure_id).redriven and h.pending_count() == 0
    with sqlite3.connect(path) as conn: conn.execute('drop trigger reject_replay')
    original = h.serialize_task
    def fail(task): raise RuntimeError('serializer refused')
    monkeypatch.setattr(h, 'serialize_task', fail)
    with pytest.raises(RuntimeError): h.redrive(record.failure_id)
    assert not h.failure(record.failure_id).redriven and h.pending_count() == 0
    monkeypatch.setattr(h, 'serialize_task', original)
    receipt = h.redrive(record.failure_id)
    assert h.pending_count() == 1 and h.failure(record.failure_id).replacement_id == receipt.task_id


def test_competing_processes_get_one_durable_replacement(tmp_path):
    path = tmp_path/'app.db'; h, f = configured(path)
    _, record = failed(h, f)
    ctx = mp.get_context('fork'); gate = ctx.Event()
    def replay(child):
        local, _ = configured(path)
        gate.wait(10)
        r = local.redrive(record.failure_id, retries=2)
        child.send((r.task_id, r.task_ids))
    pairs = []
    for _ in range(5):
        parent, child = ctx.Pipe(False)
        proc = ctx.Process(target=replay, args=(child,)); proc.start()
        pairs.append((parent, proc))
    gate.set()
    receipts = []
    for parent, proc in pairs:
        receipts.append(wait_for(parent, proc)); proc.join(10); assert proc.exitcode == 0
    assert len(set(receipts)) == 1
    assert [t.id for t in h.pending()] == [receipts[0][0]]


def test_sigkill_during_sqlite_insertion_rolls_back_entire_redrive(tmp_path):
    path = tmp_path/'app.db'; h, f = configured(path)
    _, record = failed(h, f)
    with sqlite3.connect(path) as conn:
        conn.execute('create trigger replay_barrier after insert on task begin select pause_redrive(); end')
    ctx = mp.get_context('fork'); parent, child = ctx.Pipe(False); gate = ctx.Event()
    def replay():
        actual_connect = sqlite3.connect
        def connect(*args, **kwargs):
            connection = actual_connect(*args, **kwargs)
            def pause():
                child.send('inserted')
                gate.wait(20)
                return 0
            connection.create_function('pause_redrive', 0, pause)
            return connection
        sqlite3.connect = connect
        local, _ = configured(path)
        local.redrive(record.failure_id)
    proc = ctx.Process(target=replay); proc.start()
    assert wait_for(parent, proc) == 'inserted'
    os.kill(proc.pid, signal.SIGKILL); proc.join(10)
    assert proc.exitcode == -signal.SIGKILL
    fresh, _ = configured(path)
    assert fresh.pending_count() == 0 and not fresh.failure(record.failure_id).redriven
    with sqlite3.connect(path) as conn: conn.execute('drop trigger replay_barrier')
    receipt = fresh.redrive(record.failure_id)
    assert [t.id for t in fresh.pending()] == [receipt.task_id]
    drain(fresh)
    child_failure = next(r for r in fresh.failures() if r.task_id == receipt.task_id)
    assert child_failure.parent_failure_id == record.failure_id

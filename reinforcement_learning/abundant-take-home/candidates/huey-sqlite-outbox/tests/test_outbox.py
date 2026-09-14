"""Black-box outbox contracts plus the public bulk enqueue integration point."""
import datetime as dt
import gzip
import itertools
import multiprocessing as mp
import os
import signal
import sqlite3
import threading

import pytest

from huey import SqliteHuey, MemoryHuey, group, chord, Error, SKIPPED
from huey.api import Result, ResultGroup, ChordResult, Task
from huey.consumer import Worker
from huey.contrib.sqlite_outbox import SqliteOutbox, Submission, OutboxConflict
from huey.serializer import SignedSerializer


def registered(path, name='app', **options):
    h = SqliteHuey(name, filename=str(path), **options)
    @h.task(name='double')
    def double(value):
        return value * 2
    @h.task(name='plus')
    def plus(a, b):
        return a + b
    @h.task(name='sum_values')
    def sum_values(values):
        return sum(values)
    @h.task(name='echo')
    def echo(value):
        return value
    @h.task(name='fail')
    def fail():
        raise ValueError('expected failure')
    @h.task(name='classify')
    def classify(values):
        return [('error' if isinstance(x, Error) else
                 'skipped' if x is SKIPPED else x) for x in values]
    return h, dict(double=double, plus=plus, sum=sum_values, echo=echo,
                   fail=fail, classify=classify)


def setup_db(tmp_path, **options):
    path = tmp_path/'app.db'
    h, f = registered(path, **options)
    outbox = SqliteOutbox(h)
    conn = sqlite3.connect(path, isolation_level=None)
    outbox.initialize_schema(conn)
    conn.execute('create table if not exists business (id integer primary key, value text)')
    return path, h, f, outbox, conn


def submit(outbox, conn, task, key):
    conn.execute('begin')
    try:
        receipt = outbox.stage(conn, task, key)
        conn.commit()
        return receipt
    except BaseException:
        conn.rollback()
        raise


def drain(h, timestamp=None):
    worker = Worker(h, threading.Event(), 0, 0, 1)
    for _ in range(300):
        if h.pending_count() == 0:
            return
        worker.loop(timestamp)
    pytest.fail('graph did not finish its runnable work')


def stop(proc):
    os.kill(proc.pid, signal.SIGKILL)
    proc.join(10)
    assert proc.exitcode == -signal.SIGKILL


def wait_for(parent, proc):
    if not parent.poll(10):
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        pytest.fail('process did not reach durable-state barrier')
    return parent.recv()


def test_staging_is_part_of_business_commit_and_rollback(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    task = f['double'].s(6, id='committed-work')
    conn.execute('begin')
    conn.execute("insert into business values (1, 'committed')")
    receipt = box.stage(conn, task, 'business-1')
    assert isinstance(receipt, Submission) and isinstance(receipt.id, str)
    assert receipt.key == 'business-1' and list(receipt.task_ids) == ['committed-work']
    assert not receipt.published and isinstance(receipt.result, Result)
    assert conn.in_transaction and h.pending_count() == 0
    assert box.get('business-1') is None and box.pending_count() == 0
    conn.commit()
    assert box.pending_count() == 1 and box.get('business-1').id == receipt.id
    conn.execute('begin')
    conn.execute("insert into business values (2, 'rollback')")
    box.stage(conn, f['double'].s(9), 'business-2')
    conn.rollback()
    assert box.get('business-2') is None
    with sqlite3.connect(path) as reader:
        assert reader.execute('select * from business').fetchall() == [(1, 'committed')]
    published = box.publish()
    assert [x.id for x in published] == [receipt.id] and published[0].published
    drain(h)
    assert receipt.result.get() == 12 and box.pending_count() == 0


def test_nested_savepoint_and_failed_stage_preserve_outer_transaction(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    conn.execute('begin')
    conn.execute("insert into business values (1, 'outer')")
    outer = box.stage(conn, f['double'].s(3), 'outer')
    conn.execute('savepoint inner')
    conn.execute("insert into business values (2, 'inner')")
    box.stage(conn, f['double'].s(7), 'inner')
    conn.execute('rollback to inner')
    conn.execute('release inner')
    with pytest.raises(ValueError):
        box.stage(conn, object(), 'invalid')
    assert conn.in_transaction
    conn.commit()
    assert box.get('outer').id == outer.id and box.get('inner') is None
    assert box.get('invalid') is None
    assert conn.execute('select * from business').fetchall() == [(1, 'outer')]
    assert len(box.publish()) == 1
    drain(h)
    assert outer.result.get() == 6


def test_schema_initialization_does_not_commit_caller_and_is_repeatable(tmp_path):
    path = tmp_path/'q.db'
    h, f = registered(path, create_tables=False)
    h.storage.initialize_schema()
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('create table business (id integer)')
    before = conn.execute('select name, sql from sqlite_master order by name').fetchall()
    box = SqliteOutbox(h)
    assert conn.execute('select name, sql from sqlite_master order by name').fetchall() == before
    conn.execute('begin')
    conn.execute('insert into business values (1)')
    box.initialize_schema(conn)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('select name, sql from sqlite_master order by name').fetchall() == before
    box.initialize_schema(conn)
    box.initialize_schema(conn)
    assert box.pending_count() == 0


def test_wrong_database_autocommit_and_attached_database_rejected(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    with pytest.raises(ValueError):
        box.stage(conn, f['double'].s(1), 'autocommit')
    wrong = sqlite3.connect(tmp_path/'wrong.db', isolation_level=None)
    wrong.execute('attach database ? as broker', (str(path),))
    wrong.execute('begin')
    with pytest.raises(ValueError):
        box.stage(wrong, f['double'].s(2), 'wrong-main')
    assert wrong.in_transaction
    wrong.rollback()
    with pytest.raises(ValueError):
        box.initialize_schema(wrong)
    with pytest.raises(ValueError):
        SqliteOutbox(MemoryHuey())
    with pytest.raises(ValueError):
        SqliteOutbox(SqliteHuey(filename=':memory:'))
    alias = tmp_path/'alias.db'
    alias.symlink_to(path)
    alias_conn = sqlite3.connect(alias, isolation_level=None)
    receipt = submit(box, alias_conn, f['double'].s(3), 'same-file')
    assert box.get('same-file').id == receipt.id


def test_bulk_enqueue_participates_and_rolls_back_only_its_batch(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    data = h.serialize_task(f['double'].s(5))
    with pytest.raises(ValueError):
        h.storage.enqueue_many([(data, 0)], conn)
    conn.execute("create trigger reject_priority before insert on task when new.priority=13 begin select raise(abort, 'rejected'); end")
    conn.execute('begin')
    conn.execute("insert into business values (1, 'keep')")
    with pytest.raises(sqlite3.IntegrityError):
        h.storage.enqueue_many([(data, 1), (data, 13)], conn)
    assert conn.in_transaction
    assert conn.execute('select count(*) from task').fetchone()[0] == 0
    conn.commit()
    assert conn.execute('select * from business').fetchall() == [(1, 'keep')]
    conn.execute('begin')
    h.storage.enqueue_many([(data, 1)], conn)
    assert conn.in_transaction and h.pending_count() == 0
    conn.rollback()
    assert h.pending_count() == 0


def test_idempotency_survives_reopen_consumption_and_conflict(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    task = f['double'].s(7, id='stable-7')
    one = submit(box, conn, task, 'event-7')
    two = submit(box, conn, task, 'event-7')
    assert one.id == two.id and one.task_ids == two.task_ids
    fresh, ff = registered(path)
    reopened = SqliteOutbox(fresh)
    reconstructed = ff['double'].s(7, id='stable-7')
    assert submit(reopened, conn, reconstructed, 'event-7').id == one.id
    assert len(reopened.publish()) == 1
    drain(fresh)
    assert reopened.get('event-7').result.get() == 14
    # Consumption of the ordinary result/message does not release the key.
    assert submit(reopened, conn, reconstructed, 'event-7').published
    assert reopened.publish() == [] and fresh.pending_count() == 0
    conn.execute('begin')
    conn.execute("insert into business values (4, 'still mine')")
    with pytest.raises(OutboxConflict):
        reopened.stage(conn, ff['double'].s(8, id='stable-7'), 'event-7')
    assert conn.in_transaction
    conn.commit()
    assert reopened.get('event-7').id == one.id
    assert conn.execute('select value from business where id=4').fetchone()[0] == 'still mine'


def test_frozen_arguments_graph_and_stable_chord_ids(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    mutable = {'items': [1, 2]}
    first = f['echo'].s(mutable, id='first')
    graph = chord([first, f['double'].s(3, id='second')], f['echo'].s(id='callback'))
    one = submit(box, conn, graph, 'frozen')
    assert graph.tasks[0].chord_config is None and graph.callback.args == ()
    mutable['items'].append(99)
    graph.tasks.append(f['double'].s(100))
    first.on_complete = f['double'].s()
    fresh, _ = registered(path)
    reopened = SqliteOutbox(fresh)
    assert reopened.get('frozen').task_ids == one.task_ids
    reopened.publish()
    pending = fresh.pending()
    assert {x.id for x in pending} == {'first', 'second'}
    assert pending[0].on_complete is None
    configs = [x.chord_config for x in pending]
    assert configs[0].cid == configs[1].cid
    assert {c.idx for c in configs} == {0, 1} and all(c.size == 2 for c in configs)
    drain(fresh)
    assert reopened.get('frozen').result.get() == [{'items': [1, 2]}, 6]


def test_groups_pipelines_nested_chords_and_callback_result_shape(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    inner = chord([f['double'].s(1), f['double'].s(2)], f['sum'].s()).then(f['double'].s())
    outer = chord([inner, f['double'].s(3)], f['sum'].s()).then(f['double'].s())
    receipt = submit(box, conn, outer, 'nested')
    assert submit(box, conn, outer, 'nested').id == receipt.id
    result = receipt.result
    assert isinstance(result, ChordResult)
    box.publish()
    drain(h)
    assert result.get(preserve=True) == 18
    assert result.results.get(preserve=True) == [12, 6]
    assert result.pipeline_results.get(preserve=True) == [18, 36]
    g = group([f['double'].s(2).then(f['plus'].s(10)),
               group([f['double'].s(3), f['double'].s(4)]),
               chord([f['double'].s(5)], f['sum'].s())])
    result = submit(box, conn, g, 'group').result
    assert isinstance(result, ResultGroup)
    box.publish()
    drain(h)
    assert result.get() == [[4, 14], [6, 8], 10]


def test_legacy_data_survives_initialization_and_graph_publication(tmp_path):
    path = tmp_path/'legacy.db'
    h, f = registered(path)
    ordinary = f['double'](7)
    future = f['double'].s(9, eta=dt.datetime(2040, 1, 1))
    h.add_schedule(future)
    h.put_result('existing-result', {'old': [1, 2]})
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute('create table business (value text)')
    conn.execute("insert into business values ('before outbox')")
    box = SqliteOutbox(h)
    box.initialize_schema(conn)
    receipt = submit(box, conn, f['double'].s(3), 'new')
    box.publish()
    assert {t.id for t in h.pending()} == {ordinary.id, receipt.task_ids[0]}
    assert [t.id for t in h.scheduled()] == [future.id]
    assert h.result('existing-result') == {'old': [1, 2]}
    assert conn.execute('select * from business').fetchall() == [('before outbox',)]
    drain(h)
    assert ordinary.get() == 14 and receipt.result.get() == 6


def test_empty_groups_chords_and_nested_empty_callback_pipeline(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    empty_group = submit(box, conn, group([]), 'empty-group')
    assert box.publish()[0].id == empty_group.id
    assert h.pending_count() == 0 and empty_group.result.get() == []
    graph = chord([chord([], f['sum'].s()).then(f['double'].s()), f['double'].s(5)], f['sum'].s())
    result = submit(box, conn, graph, 'nested-empty').result
    box.publish()
    assert h.pending_count() == 2
    drain(h)
    assert result.get() == 10


def test_chord_error_skipped_and_error_pipeline_keep_existing_semantics(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    fail_task = f['fail'].s().then(f['double'].s())
    revoked = f['double'].s(2)
    graph = chord([f['double'].s(1), fail_task, revoked], f['classify'].s())
    result = submit(box, conn, graph, 'outcomes').result
    h.revoke(revoked)
    box.publish()
    drain(h)
    assert result.get() == [2, 'error', 'skipped']
    @h.task(name='error_text')
    def error_text(exc):
        return str(exc)
    task = f['fail'].s().error(error_text.s())
    submit(box, conn, task, 'on-error')
    box.publish()
    drain(h)
    assert h.result(task.on_error.id) == 'expected failure'


def test_expiry_eta_options_and_compression_do_not_change_request_identity(tmp_path, monkeypatch):
    serializer = SignedSerializer(secret='outbox-test', compression=True)
    _, h, f, box, conn = setup_db(tmp_path, serializer=serializer)
    stamps = itertools.count(1)
    actual_compress = gzip.compress
    def compress(data, compresslevel=9, **kwargs):
        return actual_compress(data, compresslevel=compresslevel, mtime=next(stamps))
    monkeypatch.setattr(gzip, 'compress', compress)
    fixed = dt.datetime(2030, 1, 1)
    monkeypatch.setattr('huey.utils.utcnow', lambda: fixed)
    task = f['double'].s(4, id='options', priority=7, retries=2,
                         retry_delay=3, retry_backoff=2, timeout=9, expires=60)
    first = submit(box, conn, task, 'options')
    assert task.expires_resolved is None
    monkeypatch.setattr('huey.utils.utcnow', lambda: fixed + dt.timedelta(days=2))
    assert submit(box, conn, task, 'options').id == first.id
    box.publish()
    queued = h.pending()[0]
    assert queued.expires_resolved == fixed + dt.timedelta(seconds=60)
    assert (queued.priority, queued.retries, queued.retry_delay, queued.retry_backoff, queued.timeout) == (7, 2, 3, 2, 9)
    drain(h, fixed)
    assert first.result.get() == 8
    delayed = f['double'].s(6, eta=fixed + dt.timedelta(days=10))
    receipt = submit(box, conn, delayed, 'delayed')
    box.publish()
    drain(h, fixed)
    assert h.scheduled_count() == 1
    for t in h.read_schedule(delayed.eta):
        h.enqueue(t)
    drain(h, delayed.eta)
    assert receipt.result.get() == 12


@pytest.mark.parametrize('immediate,results', [(False, False), (True, True), (True, False)])
def test_disabled_results_and_immediate_mode_are_still_durable(tmp_path, immediate, results):
    path, h, f, box, conn = setup_db(tmp_path, immediate=immediate, results=results)
    task = f['double'].s(4)
    receipt = submit(box, conn, task, 'mode')
    assert receipt.result is None if not results else receipt.result is not None
    assert not receipt.published
    box.publish()
    normal, _ = registered(path, results=results)
    assert [t.id for t in normal.pending()] == [task.id]
    drain(normal)
    assert normal.result_count() == (1 if results else 0)
    if results:
        assert receipt.result.get(preserve=True) == 8
        assert box.get('mode').result.get() == 8
    assert h.immediate is immediate
    if immediate:
        assert h.pending_count() == 0 and h.result_count() == 0


def test_queue_isolation_limits_and_order(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    other, other_f = registered(path, name='other')
    other_box = SqliteOutbox(other)
    other_box.initialize_schema(conn)
    first = submit(box, conn, f['double'].s(1), 'shared')
    submit(other_box, conn, other_f['double'].s(9), 'shared')
    second = submit(box, conn, f['double'].s(2), 'two')
    assert box.pending_count() == 2 and other_box.pending_count() == 1
    assert [r.id for r in box.publish(limit=1)] == [first.id]
    assert [r.id for r in box.publish(limit=1)] == [second.id]
    assert other.pending_count() == 0 and other_box.pending_count() == 1
    other_box.publish()
    drain(other)
    assert other_box.get('shared').result.get() == 18
    for limit in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            box.publish(limit)
    for key in ('', None, 4):
        conn.execute('begin')
        with pytest.raises(ValueError):
            box.stage(conn, f['double'].s(1), key)
        conn.rollback()


@pytest.mark.parametrize('kind', ['cycle', 'shared', 'duplicate-id', 'invalid', 'unregistered', 'group-member', 'generator'])
def test_invalid_graphs_fail_without_touching_transaction(tmp_path, kind):
    _, h, f, box, conn = setup_db(tmp_path)
    t = f['double'].s(1)
    if kind == 'cycle':
        t.on_complete = t; graph = t
    elif kind == 'shared':
        graph = group([t, t])
    elif kind == 'duplicate-id':
        graph = group([t, f['double'].s(2, id=t.id)])
    elif kind == 'invalid':
        graph = group([t, object()])
    elif kind == 'unregistered':
        graph = Task(id='unregistered')
    elif kind == 'group-member':
        graph = chord([group([t])], f['sum'].s())
    else:
        graph = group(x for x in [t])
    conn.execute('begin')
    conn.execute("insert into business values (1, 'keep')")
    with pytest.raises(Exception):
        box.stage(conn, graph, kind)
    assert conn.in_transaction
    conn.commit()
    assert box.pending_count() == 0 and h.pending_count() == 0
    assert conn.execute('select count(*) from business').fetchone()[0] == 1


def test_serialization_failure_and_bound_chord_fragment_are_rejected(tmp_path):
    _, h, f, box, conn = setup_db(tmp_path)
    conn.execute('begin')
    with pytest.raises(Exception):
        box.stage(conn, f['echo'].s(lambda: None), 'unserializable')
    assert conn.in_transaction
    conn.commit()
    h.enqueue(chord([f['double'].s(2)], f['sum'].s()))
    fragment = h.dequeue()
    conn.execute('begin')
    with pytest.raises(ValueError):
        box.stage(conn, fragment, 'bound')
    conn.commit()
    assert box.pending_count() == 0


def test_failed_graph_publication_is_atomic_and_does_not_undo_earlier_graph(tmp_path, monkeypatch):
    _, h, f, box, conn = setup_db(tmp_path)
    first = submit(box, conn, f['double'].s(1), 'first')
    graph = group([f['double'].s(i) for i in range(5)])
    second = submit(box, conn, graph, 'second')
    original = h.storage.enqueue_many
    def write_then_fail(messages, connection):
        messages = list(messages)
        original(messages, connection)
        if len(messages) > 1:
            raise RuntimeError('after graph insertions')
    monkeypatch.setattr(h.storage, 'enqueue_many', write_then_fail)
    with pytest.raises(RuntimeError):
        box.publish()
    assert box.get('first').published and not box.get('second').published
    assert [t.id for t in h.pending()] == list(first.task_ids)
    monkeypatch.setattr(h.storage, 'enqueue_many', original)
    assert box.publish()[0].id == second.id
    assert {t.id for t in h.pending()} == set(first.task_ids + second.task_ids)
    drain(h)
    assert second.result.get() == [0, 2, 4, 6, 8]


def test_producer_killed_after_commit_is_recoverable(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe(False)
    gate = ctx.Event()
    def producer():
        local, ff = registered(path)
        local_box = SqliteOutbox(local)
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute('begin')
        connection.execute("insert into business values (1, 'committed')")
        receipt = local_box.stage(connection, ff['double'].s(21, id='after-commit'), 'event')
        connection.commit()
        child.send(receipt.id)
        gate.wait(20)
    proc = ctx.Process(target=producer)
    proc.start()
    sid = wait_for(parent, proc)
    stop(proc)
    fresh, _ = registered(path)
    fresh_box = SqliteOutbox(fresh)
    assert fresh_box.get('event').id == sid and fresh_box.pending_count() == 1
    assert conn.execute('select * from business').fetchall() == [(1, 'committed')]
    fresh_box.publish()
    drain(fresh)
    assert fresh_box.get('event').result.get() == 42


def test_competing_publishers_only_publish_each_graph_once(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    expected = []
    receipt_ids = set()
    for i in range(17):
        graph = group([f['double'].s(i * 3 + j, id='work-%d-%d' % (i, j)) for j in range(3)])
        receipt = submit(box, conn, graph, 'graph-%d' % i)
        receipt_ids.add(receipt.id)
        expected.extend(receipt.task_ids)
    ctx = mp.get_context('fork')
    start = ctx.Event()
    def publisher(child):
        local, _ = registered(path)
        local_box = SqliteOutbox(local)
        start.wait(5)
        child.send([r.id for r in local_box.publish()])
    pipes, procs = [], []
    for _ in range(4):
        parent, child = ctx.Pipe(False)
        proc = ctx.Process(target=publisher, args=(child,))
        proc.start(); pipes.append(parent); procs.append(proc)
    start.set()
    seen = []
    for parent, proc in zip(pipes, procs):
        seen.extend(wait_for(parent, proc))
        proc.join(10)
        assert proc.exitcode == 0
    assert len(seen) == 17 and set(seen) == receipt_ids
    pending = h.pending()
    assert len(pending) == 51 and {t.id for t in pending} == set(expected)
    assert box.pending_count() == 0 and box.publish() == []


def test_publisher_sigkill_mid_graph_restores_submission_and_identities(tmp_path):
    path, h, f, box, conn = setup_db(tmp_path)
    graph = chord([f['double'].s(i) for i in range(7)], f['sum'].s())
    receipt = submit(box, conn, graph, 'crash')
    ctx = mp.get_context('fork')
    parent, child = ctx.Pipe(False)
    gate = ctx.Event()
    def publisher():
        local, _ = registered(path)
        local_box = SqliteOutbox(local)
        original = local.storage.enqueue_many
        def partial(messages, connection):
            messages = list(messages)
            original(messages[:3], connection)
            child.send([local.deserialize_task(data).chord_config.cid for data, _ in messages])
            gate.wait(20)
        local.storage.enqueue_many = partial
        local_box.publish()
    proc = ctx.Process(target=publisher)
    proc.start()
    planned_cids = wait_for(parent, proc)
    assert len(set(planned_cids)) == 1
    stop(proc)
    fresh, _ = registered(path)
    reopened = SqliteOutbox(fresh)
    assert reopened.get('crash').id == receipt.id and not reopened.get('crash').published
    assert fresh.pending_count() == 0 and reopened.pending_count() == 1
    assert reopened.publish()[0].task_ids == receipt.task_ids
    pending = fresh.pending()
    assert len(pending) == 7
    assert {t.chord_config.cid for t in pending} == set(planned_cids)
    drain(fresh)
    assert reopened.get('crash').result.get() == 42

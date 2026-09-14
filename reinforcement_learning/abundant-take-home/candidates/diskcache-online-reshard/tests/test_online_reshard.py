"""Independent public-API state model and crash tests; no oracle imports."""
import hashlib
import io
import json
import multiprocessing as mp
import os
from pathlib import Path
import pickle
import random
import sqlite3

import pytest
import diskcache as dc


@pytest.fixture
def api():
    assert hasattr(dc, 'ReshardableFanoutCache'), 'missing managed online reshard API'
    return dc.ReshardableFanoutCache


def finish(cache, token, budget=3):
    for _ in range(1000):
        state = cache.step_reshard(token, max_items=budget)
        assert 0 <= state['processed'] <= budget
        if state['phase'] == 'idle':
            return state
    pytest.fail('quiescent migration did not finish')


def snapshot(cache):
    missing = object()
    result = {}
    for key in cache:
        value = cache.get(key, missing, expire_time=True, tag=True)
        if value[0] is not missing:
            result[key] = value
    return result


def test_exports_and_persisted_configuration(api, tmp_path):
    for name in ('ReshardConflictError', 'ReshardBusyError', 'ReshardPausedError', 'ReshardIntegrityError'):
        assert issubclass(getattr(dc, name), dc.ReshardError)
    c = api(tmp_path, shards=2, disk=dc.JSONDisk, size_limit=10**8, tag_index=True)
    c['value'] = {'a': [1, 2]}
    generation = c.reshard_status()['generation']
    c.close()
    c = api(tmp_path)
    assert c.reshard_status()['generation'] == generation
    assert c.reshard_status()['shards'] == 2
    assert isinstance(c.disk, dc.JSONDisk)
    assert c['value'] == {'a': [1, 2]}
    assert c.size_limit == 5 * 10**7
    assert c.reset('tag_index') == 1
    with pytest.raises(ValueError):
        c.reset('size_limit', 1)
    for kwargs in (dict(shards=3), dict(disk=dc.Disk), dict(size_limit=1000)):
        with pytest.raises(dc.ReshardConflictError):
            api(tmp_path, **kwargs)
    c.close()


@pytest.mark.parametrize('count', [0, -1, True, 1.2, '4'])
def test_invalid_counts(api, tmp_path, count):
    with pytest.raises(ValueError):
        api(tmp_path, shards=count)
    c = api(tmp_path, shards=2)
    with pytest.raises(ValueError):
        c.begin_reshard(count)
    token = c.begin_reshard(3)['migration_id']
    with pytest.raises(ValueError):
        c.step_reshard(token, count)
    c.close()


def test_fencing_pause_abort_and_idempotence(api, tmp_path):
    c = api(tmp_path, shards=2)
    c['a'] = 1
    assert c.begin_reshard(2)['migration_id'] is None
    state = c.begin_reshard(4, expected_epoch=0)
    token = state['migration_id']
    state['shards'] = 123
    assert c.reshard_status()['shards'] == 2
    assert c.begin_reshard(4)['migration_id'] == token
    with pytest.raises(dc.ReshardConflictError):
        c.begin_reshard(3)
    with pytest.raises(dc.ReshardConflictError):
        c.begin_reshard(4, expected_epoch=1)
    c.pause_reshard(token)
    c.pause_reshard(token)
    with pytest.raises(dc.ReshardPausedError):
        c.step_reshard(token)
    c['a'] = 2
    c.close()
    c = api(tmp_path)
    assert c.reshard_status()['phase'] == 'paused'
    assert c['a'] == 2
    c.resume_reshard(token)
    c.resume_reshard(token)
    c.step_reshard(token, 1)
    assert c.abort_reshard(token)['epoch'] == 0
    assert c['a'] == 2
    newer = c.begin_reshard(3)['migration_id']
    assert newer != token
    for method in (c.pause_reshard, c.resume_reshard, c.abort_reshard, c.step_reshard):
        with pytest.raises(dc.ReshardConflictError):
            method(token)
    assert finish(c, newer)['epoch'] == 1
    with pytest.raises(dc.ReshardConflictError):
        c.step_reshard(newer)
    with pytest.raises(dc.ReshardConflictError):
        c.begin_reshard(5, expected_epoch=0)
    c.close()


def test_online_mutations_after_baseline_was_copied(api, tmp_path):
    c = api(tmp_path, shards=2, eviction_policy='none', tag_index=True)
    for i in range(24):
        c.set(i, i * 10, tag='remove' if i % 3 == 0 else 'keep')
    token = c.begin_reshard(5)['migration_id']
    state = c.step_reshard(token, 23)
    assert state['phase'] != 'idle'
    c.set(0, 'changed', tag='new')
    c.delete(1)
    assert c.pop(2) == 20
    assert c.incr(4, 8) == 48
    assert c.decr(5, 2) == 48
    assert not c.add(6, 'wrong')
    assert c.add(40, 'added')
    c[1] = 'reinserted'
    c.evict('remove')
    before = snapshot(c)
    finish(c, token, 1)
    assert snapshot(c) == before
    assert len(c) == len(before)
    assert c.check() == []
    c.close()


def test_bulk_clear_does_not_resurrect_copied_rows(api, tmp_path):
    c = api(tmp_path, shards=2, eviction_policy='none')
    for i in range(20):
        c[i] = i
    token = c.begin_reshard(3)['migration_id']
    c.step_reshard(token, 19)
    assert c.clear() == 20
    c[100] = 'after clear'
    finish(c, token, 2)
    assert snapshot(c) == {100: ('after clear', None, None)}
    c.close()


def test_ttl_touch_expire_absolute_deadlines(api, tmp_path):
    # Use real, well-separated deadlines. A new module can import its own clock
    # or a time alias without disagreeing with patched upstream modules.
    c = api(tmp_path, shards=2, eviction_policy='none')
    c.set('expires', 'old', expire=3600, tag='a')
    c.set('touch', 'yes', expire=3600, tag='b')
    c.set('natural', 'gone', expire=-3600)
    c.set('remove', 'gone', expire=-7200)
    token = c.begin_reshard(3)['migration_id']
    c.step_reshard(token, 1)
    c.touch('touch', expire=7200)
    touched = c.get('touch', expire_time=True, tag=True)
    c.expire()
    # Leave another expired row without an explicit cleanup call, so migration
    # must preserve its invisibility rather than assigning it a fresh TTL.
    c.set('natural', 'gone', expire=-3600)
    expected = snapshot(c)
    finish(c, token)
    assert snapshot(c) == expected
    assert c.get('touch', expire_time=True, tag=True) == touched
    assert touched[0] == 'yes' and touched[2] == 'b'
    assert 'natural' not in c and 'remove' not in c
    c.close()


@pytest.mark.parametrize('mode', ['disk', 'json'])
def test_serialization_streams_and_large_payloads(api, tmp_path, mode):
    disk = dc.Disk if mode == 'disk' else dc.JSONDisk
    c = api(tmp_path, shards=3, disk=disk, disk_min_file_size=64, eviction_policy='none')
    if mode == 'disk':
        records = [('text', 'αβ' * 9000), (b'bytes', b'\x00\xff' * 100000), ((1, 'x'), {'nested': list(range(1000))}), (7, 1.25)]
    else:
        records = [('text', 'αβ' * 9000), ('mapping', {'nested': list(range(10000))}), (7, 1.25)]
    for key, value in records:
        c.set(key, value, tag='blob')
    expected = snapshot(c)
    token = c.begin_reshard(7)['migration_id']
    finish(c, token, 1)
    assert snapshot(c) == expected
    if mode == 'disk':
        c.set('stream', io.BytesIO(b'abcd' * 30000), read=True)
        stream = c.read('stream')
        token = c.begin_reshard(2)['migration_id']
        finish(c, token, 2)
        assert c.collect() == 2
        assert stream.read() == b'abcd' * 30000
        stream.close()
        with c.read('stream') as result:
            assert result.read() == b'abcd' * 30000
    assert c.check() == []
    c.close()


def test_transaction_rollback_and_lifecycle_rejection(api, tmp_path):
    c = api(tmp_path, shards=3, eviction_policy='none')
    for i in range(12):
        c[i] = i
    token = c.begin_reshard(5)['migration_id']
    c.step_reshard(token, 11)
    with pytest.raises(RuntimeError):
        with c.transact():
            c[0] = 'rollback'
            c.delete(1)
            c[99] = 'rollback'
            raise RuntimeError('abort')
    assert c[0] == 0 and c[1] == 1 and 99 not in c
    with c.transact():
        c[0] = 'commit'
        with c.transact():
            c.delete(1)
        for change in (lambda: c.step_reshard(token), lambda: c.pause_reshard(token), lambda: c.resume_reshard(token), lambda: c.abort_reshard(token), lambda: c.begin_reshard(5), c.collect):
            with pytest.raises(dc.ReshardBusyError):
                change()
    expected = snapshot(c)
    finish(c, token, 1)
    assert snapshot(c) == expected
    c.close()


@pytest.mark.parametrize('seed', [17, 43, 991])
def test_stateful_reference_model(api, tmp_path, seed):
    rng = random.Random(seed)
    c = api(tmp_path, shards=3, eviction_policy='none', tag_index=True)
    model = {}
    for key in range(60):
        c[key] = key
        model[key] = (key, None, None)
    token = c.begin_reshard(7)['migration_id']
    for turn in range(140):
        key = rng.randrange(80)
        op = rng.randrange(7)
        if op == 0:
            value, tag = rng.randrange(10000), str(key % 3)
            c.set(key, value, tag=tag)
            model[key] = (value, None, tag)
        elif op == 1:
            assert c.delete(key) == (key in model)
            model.pop(key, None)
        elif op == 2:
            prior = model.get(key)
            assert c.add(key, 0) == (prior is None)
            if prior is None:
                model[key] = (0, None, None)
        elif op == 3:
            prior = model.get(key, (0, None, None))
            assert c.incr(key, 2, default=0) == prior[0] + 2
            model[key] = (prior[0] + 2, prior[1], prior[2])
        elif op == 4:
            expected = model.pop(key, (None, None, None))[0]
            assert c.pop(key) == expected
        elif op == 5:
            tag = str(key % 3)
            count = sum(value[2] == tag for value in model.values())
            assert c.evict(tag) == count
            model = {k: v for k, v in model.items() if v[2] != tag}
        else:
            assert c.get(key) == model.get(key, (None,))[0]
        if turn % 11 == 0:
            state = c.step_reshard(token, 1)
            assert state['processed'] <= 1 and state['phase'] != 'idle'
        if turn % 23 == 0:
            assert snapshot(c) == model
            c.close()
            c = api(tmp_path)
    finish(c, token, 2)
    assert snapshot(c) == model
    c.close()


def test_existing_clients_memoize_pickle_named_stores(api, tmp_path):
    a = api(tmp_path, shards=2, eviction_policy='none')
    b = api(tmp_path)
    calls = []
    @a.memoize(name='calculation')
    def calculate(value):
        calls.append(value)
        return value * 2
    assert calculate(10) == 20
    a.cache('aux')['x'] = b'named'
    a.deque('queue').append('queued')
    a.index('index')['a'] = 'indexed'
    b['key'] = 'before'
    iterator = iter(b)
    token = a.begin_reshard(5)['migration_id']
    finish(a, token)
    assert b['key'] == 'before'
    b['key'] = 'after'
    assert a['key'] == 'after'
    assert calculate(10) == 20 and calls == [10]
    assert a.collect() == 1 and b.collect() == 0
    assert list(iterator)
    a.close()
    restored = pickle.loads(pickle.dumps(b))
    assert restored['key'] == 'after'
    assert restored.cache('aux')['x'] == b'named'
    assert restored.deque('queue').popleft() == 'queued'
    assert restored.index('index')['a'] == 'indexed'
    assert Path(restored.directory) == tmp_path
    restored.close()
    b.close()


def test_adoption_and_wrong_counts(api, tmp_path):
    legacy = dc.FanoutCache(tmp_path, shards=3, disk_min_file_size=128, eviction_policy='none', size_limit=90000000)
    for i in range(15):
        legacy.set(i, b'a' * (i + 150), expire=3600, tag='legacy')
    legacy.cache('keep')['x'] = 9
    expected = {key: legacy.get(key, expire_time=True, tag=True) for key in legacy}
    legacy.close()
    with pytest.raises(dc.ReshardConflictError):
        api(tmp_path, shards=3)
    with pytest.raises(dc.ReshardConflictError):
        api(tmp_path, shards=4, adopt=True)
    c = api(tmp_path, shards=3, adopt=True)
    assert snapshot(c) == expected
    assert c.size_limit == 30000000 and c.disk_min_file_size == 128
    token = c.begin_reshard(2)['migration_id']
    finish(c, token, 2)
    assert snapshot(c) == expected
    c.collect()
    assert c.cache('keep')['x'] == 9
    assert not (tmp_path / '000').exists()
    c.close()


def test_copy_does_not_cull_due_to_new_per_shard_limits(api, tmp_path):
    c = api(tmp_path, shards=1, size_limit=10**6)
    for i in range(8):
        c[i] = b'x' * 80000
    expected = snapshot(c)
    token = c.begin_reshard(80)['migration_id']
    finish(c, token, 2)
    assert snapshot(c) == expected
    c.close()


@pytest.mark.parametrize('damage', ['checksum', 'schema', 'truncated'])
def test_metadata_corruption_fails_closed(api, tmp_path, damage):
    c = api(tmp_path, shards=2)
    c['important'] = 'retained'
    c.close()
    path = tmp_path / 'RESHARD.json'
    raw = path.read_bytes()
    data = json.loads(raw)
    if damage == 'checksum':
        data['data']['active']['shards'] += 1
        path.write_text(json.dumps(data))
    elif damage == 'schema':
        data['schema'] = 999
        path.write_text(json.dumps(data))
    else:
        path.write_text('{')
    with pytest.raises(dc.ReshardIntegrityError):
        api(tmp_path)
    path.write_bytes(raw)
    c = api(tmp_path)
    assert c['important'] == 'retained'
    c.close()


def test_missing_payload_prevents_cutover(api, tmp_path):
    c = api(tmp_path, shards=1, disk_min_file_size=64, eviction_policy='none')
    c['file'] = b'payload' * 10000
    files = list(tmp_path.rglob('*.val'))
    assert len(files) == 1
    payload = files[0].read_bytes()
    files[0].unlink()
    token = c.begin_reshard(2)['migration_id']
    with pytest.raises(dc.ReshardIntegrityError):
        c.step_reshard(token)
    assert c.reshard_status()['epoch'] == 0
    files[0].write_bytes(payload)
    finish(c, token)
    assert c['file'] == payload
    c.close()


def _lease_holder(path, connection):
    cache = dc.ReshardableFanoutCache(path)
    with cache.transact():
        cache['x'] = 'committed'
        connection.send('held')
        connection.recv()
    cache.close()


def test_process_lease_is_nonblocking_and_released(api, tmp_path):
    cache = api(tmp_path, shards=2)
    cache['x'] = 'before'
    parent, child = mp.get_context('fork').Pipe()
    process = mp.get_context('fork').Process(target=_lease_holder, args=(str(tmp_path), child))
    process.start()
    assert parent.poll(10) and parent.recv() == 'held'
    try:
        with pytest.raises(dc.ReshardBusyError):
            cache['x'] = 'must not write'
        with pytest.raises(dc.ReshardBusyError):
            cache.begin_reshard(3)
    finally:
        parent.send('release')
        process.join(10)
        if process.is_alive():
            process.kill()
        assert process.exitcode == 0
    assert cache['x'] == 'committed'
    cache.close()


def _crash_on_publish(path, action, edge, pipe):
    cache = dc.ReshardableFanoutCache(path)
    original = os.replace
    def replace(source, destination, *args, **kwargs):
        if os.path.basename(destination) == 'RESHARD.json':
            record = json.loads(Path(source).read_text())['data']
            migration = record['migration']
            match = ((action == 'begin' and migration is not None)
                     or (action == 'cutover' and migration is None and record['epoch'] == 1)
                     or (action == 'abort' and ((migration is None and record['epoch'] == 0)
                                                or (migration is not None and migration['phase'] == 'aborting'))))
            if match:
                if edge == 'after':
                    original(source, destination, *args, **kwargs)
                pipe.send('barrier')
                pipe.recv()
                os._exit(98)
        return original(source, destination, *args, **kwargs)
    os.replace = replace
    if action == 'begin':
        cache.begin_reshard(5)
    elif action == 'abort':
        cache.abort_reshard(cache.reshard_status()['migration_id'])
    else:
        token = cache.reshard_status()['migration_id']
        for _ in range(100):
            cache.step_reshard(token, 1)
    pipe.send('missed')


@pytest.mark.parametrize('action', ['begin', 'cutover', 'abort'])
@pytest.mark.parametrize('edge', ['before', 'after'])
def test_killed_at_atomic_metadata_publication(api, tmp_path, action, edge):
    cache = api(tmp_path, shards=2, eviction_policy='none', disk_min_file_size=64)
    for i in range(8):
        cache[i] = b'a' * (100000 + i)
    if action != 'begin':
        token = cache.begin_reshard(5)['migration_id']
        if action == 'abort':
            cache.step_reshard(token, 3)
            cache[0] = b'online change' * 10000
            del cache[1]
    expected = snapshot(cache)
    cache.close()
    parent, child = mp.get_context('fork').Pipe()
    process = mp.get_context('fork').Process(target=_crash_on_publish, args=(str(tmp_path), action, edge, child))
    process.start()
    try:
        assert parent.poll(20), 'child did not reach publication barrier'
        assert parent.recv() == 'barrier'
    finally:
        process.kill()
        process.join(10)
    cache = api(tmp_path)
    assert snapshot(cache) == expected
    state = cache.reshard_status()
    if action == 'cutover' and edge == 'after':
        assert state['epoch'] == 1 and state['phase'] == 'idle'
    elif action == 'abort' and edge == 'after':
        assert state['epoch'] == 0 and state['phase'] == 'idle'
    if state['migration_id']:
        finish(cache, state['migration_id'], 2)
    elif state['epoch'] == 0:
        finish(cache, cache.begin_reshard(5)['migration_id'], 2)
    assert snapshot(cache) == expected
    assert cache.reshard_status()['epoch'] == 1
    assert cache.check() == []
    cache.close()


def _bounded_step_worker(path, pipe):
    cache = dc.ReshardableFanoutCache(path)
    status = cache.step_reshard(cache.reshard_status()['migration_id'], 1)
    pipe.send(status)
    pipe.recv()


def test_killed_copy_worker_replays_latest_source_payload(api, tmp_path):
    cache = api(tmp_path, shards=1, eviction_policy='none', disk_min_file_size=64)
    payload = bytes(range(256)) * 20000
    cache['large'] = payload
    cache['other'] = b'another file' * 10000
    token = cache.begin_reshard(3)['migration_id']
    cache.close()
    parent, child = mp.get_context('fork').Pipe()
    process = mp.get_context('fork').Process(target=_bounded_step_worker, args=(str(tmp_path), child))
    process.start()
    try:
        assert parent.poll(20), 'bounded step did not finish'
        status = parent.recv()
        assert status['processed'] <= 1 and status['migration_id'] == token
    finally:
        process.kill()
        process.join(10)
    cache = api(tmp_path)
    assert cache['large'] == payload
    cache['large'] = b'new version' * 30000
    cache['other'] = b'also updated' * 20000
    finish(cache, token, 1)
    assert cache['large'] == b'new version' * 30000
    assert cache['other'] == b'also updated' * 20000
    cache.collect()
    assert cache.check() == []
    cache.close()


def test_culling_during_migration_is_reconciled(api, tmp_path):
    cache = api(tmp_path, shards=2, size_limit=220000, disk_min_file_size=64, cull_limit=0)
    for key in range(12):
        cache[key] = b'blob' * 15000
    token = cache.begin_reshard(3)['migration_id']
    cache.step_reshard(token, 11)
    assert cache.cull() > 0
    expected = snapshot(cache)
    finish(cache, token, 1)
    assert snapshot(cache) == expected
    cache.close()


def _increment_worker(path, pipe):
    cache = dc.ReshardableFanoutCache(path)
    pipe.send('ready')
    pipe.recv()
    for _ in range(30):
        while True:
            try:
                cache.incr('counter')
                break
            except dc.ReshardBusyError:
                continue
    pipe.send('done')
    cache.close()


def test_competing_process_increment_and_migration(api, tmp_path):
    cache = api(tmp_path, shards=2, eviction_policy='none')
    cache['counter'] = 0
    for key in range(20):
        cache[key] = key
    token = cache.begin_reshard(3)['migration_id']
    peers = []
    for _ in range(2):
        parent, child = mp.get_context('fork').Pipe()
        process = mp.get_context('fork').Process(target=_increment_worker, args=(str(tmp_path), child))
        process.start()
        assert parent.poll(10) and parent.recv() == 'ready'
        peers.append((parent, process))
    for pipe, process in peers:
        pipe.send('go')
    try:
        for _ in range(15):
            try:
                cache.step_reshard(token, 1)
            except dc.ReshardBusyError:
                continue
        for pipe, process in peers:
            assert pipe.poll(20) and pipe.recv() == 'done'
            process.join(10)
            assert process.exitcode == 0
    finally:
        for pipe, process in peers:
            if process.is_alive():
                process.kill()
                process.join(10)
    finish(cache, token, 1)
    assert cache['counter'] == 60
    cache.close()


def test_copy_reads_file_payloads_incrementally(api, tmp_path, monkeypatch):
    import builtins
    cache = api(tmp_path, shards=1, eviction_policy='none', disk_min_file_size=64)
    payload = b'abcdefgh' * 700000
    cache['file'] = payload
    token = cache.begin_reshard(3)['migration_id']
    original = builtins.open
    reads = []
    class StreamingReader:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def read(self, size=-1):
            assert size > 0, 'migration read/deserialized the entire payload in one unbounded read'
            reads.append(size)
            return self.stream.read(size)
    def opened(file, mode='r', *args, **kwargs):
        stream = original(file, mode, *args, **kwargs)
        if mode == 'rb' and str(file).endswith('.val'):
            return StreamingReader(stream)
        return stream
    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', opened)
        finish(cache, token, 1)
    # An implementation may use an OS copy primitive and have no Python reads.
    assert cache['file'] == payload
    cache.close()


def test_plain_fanout_native_transactions_are_preserved(tmp_path):
    cache = dc.FanoutCache(tmp_path, shards=3, eviction_policy='none')
    cache[0] = 'old'
    with pytest.raises(RuntimeError):
        with cache.transact():
            cache[0] = 'wrong'
            cache[1] = 'wrong'
            raise RuntimeError('rollback')
    assert cache[0] == 'old' and 1 not in cache
    with cache.transact():
        cache[0] = 'new'
        with cache.transact():
            cache[1] = 'new'
    assert cache[0] == cache[1] == 'new'
    cache.close()

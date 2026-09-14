"""Public-contract checks independent of the reference snapshot implementation."""
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path
import select
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest
from diskcache import Cache, Disk, FanoutCache, JSONDisk


@pytest.fixture
def api():
    return importlib.import_module('diskcache.snapshot')


def manifest(path):
    return json.loads((path / 'manifest.json').read_text())


def rewrite_manifest(path, value):
    (path / 'manifest.json').write_text(json.dumps(value))


def rehash(path, member):
    data = (path / member).read_bytes()
    value = manifest(path)
    value['files'][member] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    rewrite_manifest(path, value)


def rows(path):
    with contextlib.closing(sqlite3.connect(path)) as db:
        return db.execute('SELECT * FROM Cache ORDER BY rowid').fetchall()


@pytest.mark.parametrize('shards', [None, 1, 4])
@pytest.mark.parametrize('codec', [Disk, JSONDisk])
def test_roundtrip_preserves_native_rows_files_settings_and_expiry(api, tmp_path, shards, codec):
    kwargs = dict(disk=codec, disk_min_file_size=20, eviction_policy='least-frequently-used',
                  size_limit=50_000_000, cull_limit=0, tag_index=True)
    if codec is JSONDisk:
        kwargs['disk_compress_level'] = 9
    c = Cache(tmp_path/'live', **kwargs) if shards is None else FanoutCache(tmp_path/'live', shards=shards, **kwargs)
    try:
        for i in range(25):
            assert c.set('key-%s' % i, {'i': i, 'payload': 'x' * (i * 400)}, tag='batch', retry=True)
        c.set('expired', 'gone', expire=-1, retry=True)
        c.set('future', 'present', expire=100000, tag='ttl', retry=True)
        expected = c.get('future', expire_time=True, tag=True)
        raw_shards = [c] if shards is None else list(c._shards)
        expected_rows = [rows(Path(s.directory)/'cache.db') for s in raw_shards]
        (Path(raw_shards[0].directory)/'unreferenced.val').write_bytes(b'orphan')
        events = []
        created = c.snapshot(tmp_path/'snapshot', progress=events.append)
        assert created == api.verify_snapshot(tmp_path/'snapshot')
        assert created['format_version'] == 1 and created['shards'] == (shards or 1)
        assert created['disk'] == codec.__name__
        assert sorted(e['path'] for e in events) == sorted(created['files'])
        assert all(e['phase'] == 'copy' for e in events)
        assert not any('unreferenced' in f for f in created['files'])
        c.set('key-1', 'subsequent mutation', retry=True)
        restored = api.restore_snapshot(tmp_path/'snapshot', tmp_path/'restored')
        try:
            assert type(restored) is type(c)
            restored_shards = [restored] if shards is None else list(restored._shards)
            assert [rows(Path(s.directory)/'cache.db') for s in restored_shards] == expected_rows
            assert restored.get('future', expire_time=True, tag=True) == expected
            assert restored.get('expired') is None
            assert restored['key-1']['i'] == 1
            assert restored.eviction_policy == 'least-frequently-used'
            assert sum(s.size_limit for s in restored_shards) == 50_000_000
            assert restored.disk_min_file_size == 20
            assert restored.evict('batch', retry=True) == 25
        finally:
            restored.close()
    finally:
        c.close()


def test_serialized_and_stream_values_and_read_only_verification(api, tmp_path):
    sentinel = tmp_path/'UNPICKLED'
    class Bomb:
        def __reduce__(self):
            return (os.system, ('touch ' + str(sentinel),))
    with Cache(tmp_path/'source', disk_min_file_size=1) as c:
        c[('tuple', 1)] = Bomb()
        with (tmp_path/'stream').open('wb') as f:
            f.write(bytes(range(256)) * 700)
        with (tmp_path/'stream').open('rb') as f:
            c.set(b'binary-key', f, read=True)
        c.snapshot(tmp_path/'snap')
    files = {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in (tmp_path/'snap').rglob('*') if p.is_file()}
    api.verify_snapshot(tmp_path/'snap')
    assert files == {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in (tmp_path/'snap').rglob('*') if p.is_file()}
    with api.restore_snapshot(tmp_path/'snap', tmp_path/'restore') as restored:
        with restored.read(b'binary-key') as f:
            assert f.read() == (tmp_path/'stream').read_bytes()
    assert not sentinel.exists()


@pytest.mark.parametrize('fanout', [False, True])
def test_empty_and_named_store_scope(api, tmp_path, fanout):
    with (FanoutCache(tmp_path/'source', shards=2) if fanout else Cache(tmp_path/'source')) as c:
        if fanout:
            c.cache('independent')['x'] = 'not part of main cache'
            c.deque('jobs').append('unrelated')
        c.snapshot(tmp_path/'snap')
    with api.restore_snapshot(tmp_path/'snap', tmp_path/'restored') as restored:
        assert len(restored) == 0
        if fanout:
            assert len(restored.cache('independent')) == 0


@pytest.mark.parametrize('operation', ['snapshot', 'restore'])
def test_callback_cancellation_and_existing_destination(api, tmp_path, operation):
    with Cache(tmp_path/'source') as c:
        c['x'] = 'y'
        c.snapshot(tmp_path/'snap')
        def cancel(event):
            assert event['phase'] == 'copy'
            raise LookupError('cancelled by caller')
        call = (lambda p, **kw: c.snapshot(p, **kw)) if operation == 'snapshot' else (lambda p, **kw: api.restore_snapshot(tmp_path/'snap', p, **kw))
        with pytest.raises(LookupError, match='cancelled'):
            call(tmp_path/'new', progress=cancel)
        assert not (tmp_path/'new').exists()
        (tmp_path/'existing').mkdir()
        (tmp_path/'existing'/'keep').write_text('retain')
        with pytest.raises(ValueError):
            call(tmp_path/'existing')
        assert (tmp_path/'existing'/'keep').read_text() == 'retain'
        (tmp_path/'empty').mkdir()
        with pytest.raises(ValueError):
            call(tmp_path/'empty')
        (tmp_path/'link').symlink_to(tmp_path/'missing')
        with pytest.raises(ValueError):
            call(tmp_path/'link')
        result = call(tmp_path/'new')
        if operation == 'restore':
            result.close()


def test_transaction_custom_codec_and_nested_destinations_rejected(api, tmp_path):
    with FanoutCache(tmp_path/'source', shards=3) as c:
        c['a'] = 'before'
        with c.transact():
            c['a'] = 'uncommitted'
            with pytest.raises(ValueError):
                c.snapshot(tmp_path/'bad')
            assert c['a'] == 'uncommitted'
        with c._shards[1].transact():
            with pytest.raises(ValueError):
                c.snapshot(tmp_path/'bad')
        assert not (tmp_path/'bad').exists()
        with pytest.raises(ValueError):
            c.snapshot(tmp_path/'source'/'nested')
        c.snapshot(tmp_path/'snap')
    with pytest.raises(ValueError):
        api.restore_snapshot(tmp_path/'snap', tmp_path/'snap'/'nested')
    class CustomDisk(Disk):
        pass
    with Cache(tmp_path/'custom', disk=CustomDisk) as c:
        with pytest.raises(ValueError):
            c.snapshot(tmp_path/'unsupported')


@pytest.mark.parametrize('damage', ['bytes', 'missing', 'extra', 'symlink', 'traversal', 'absolute',
                                    'version', 'codec', 'topology', 'database', 'payload-reference'])
def test_corruption_rejected_before_restore_publication(api, tmp_path, damage):
    with Cache(tmp_path/'source', disk_min_file_size=1) as c:
        c['data'] = b'x' * 2000
        c.snapshot(tmp_path/'snap')
    snap = tmp_path/'snap'
    m = manifest(snap)
    payload = next(n for n in m['files'] if not n.endswith('cache.db'))
    if damage == 'bytes':
        (snap/payload).write_bytes(b'damaged')
    elif damage == 'missing':
        (snap/payload).unlink()
    elif damage == 'extra':
        (snap/'surprise').write_text('not listed')
    elif damage == 'symlink':
        outside = tmp_path/'outside'
        shutil.copyfile(snap/payload, outside)
        (snap/payload).unlink()
        (snap/payload).symlink_to(outside)
    elif damage in ('traversal', 'absolute'):
        m['files']['../outside' if damage == 'traversal' else '/outside'] = m['files'].pop(payload)
        rewrite_manifest(snap, m)
    elif damage in ('version', 'codec', 'topology'):
        m[{'version':'format_version', 'codec':'disk', 'topology':'shards'}[damage]] = {'version':2, 'codec':'os.system', 'topology':2}[damage]
        rewrite_manifest(snap, m)
    elif damage == 'database':
        (snap/'data/cache.db').write_bytes(b'not sqlite')
        rehash(snap, 'data/cache.db')
    else:
        with sqlite3.connect(snap/'data/cache.db') as db:
            db.execute("UPDATE Cache SET filename = '../../outside'")
        rehash(snap, 'data/cache.db')
    with pytest.raises(ValueError):
        api.verify_snapshot(snap)
    with pytest.raises(ValueError):
        api.restore_snapshot(snap, tmp_path/'restore')
    assert not (tmp_path/'restore').exists()


def test_restore_detects_source_mutation_during_copy(api, tmp_path):
    with Cache(tmp_path/'source', disk_min_file_size=1) as c:
        for i in range(5):
            c[i] = b'x' * 100
        c.snapshot(tmp_path/'snap')
    seen = set()
    def mutate(event):
        seen.add(event['path'])
        for member in manifest(tmp_path/'snap')['files']:
            if member not in seen:
                (tmp_path/'snap'/member).write_bytes(b'concurrent replacement')
    with pytest.raises(ValueError):
        api.restore_snapshot(tmp_path/'snap', tmp_path/'restored', progress=mutate)
    assert not (tmp_path/'restored').exists()


def test_concurrent_writes_between_shard_copies_cannot_create_a_mixed_snapshot(api, tmp_path):
    with FanoutCache(tmp_path/'source', shards=4, timeout=0) as source:
        for i in range(60):
            source.set(str(i), 'old', retry=True)
        # Open the competitor before snapshotting: constructors may write schema.
        with FanoutCache(tmp_path/'source', shards=4, timeout=0) as competitor:
            attempted = []
            def concurrent_write(event):
                if attempted:
                    return
                attempted.append(True)
                for i in range(60):
                    # Non-retrying writes finish immediately when a shard is
                    # locked. Lock-free snapshot implementations may accept them
                    # but still have to produce a coherent snapshot.
                    competitor.set(str(i), 'new', retry=False)
            source.snapshot(tmp_path/'snap', progress=concurrent_write)
        with api.restore_snapshot(tmp_path/'snap', tmp_path/'restored') as restored:
            assert len({restored[str(i)] for i in range(60)}) == 1


def ready(process, expected='READY'):
    streams, _, _ = select.select([process.stdout], [], [], 15)
    assert streams, 'child failed to reach barrier'
    line = process.stdout.readline().strip()
    assert line == expected, line


def child(code, *args):
    return subprocess.Popen([sys.executable, '-u', '-c', code, *map(str,args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


@pytest.mark.parametrize('operation', ['snapshot', 'restore'])
def test_process_kill_leaves_no_published_destination_and_retry_succeeds(api, tmp_path, operation):
    with Cache(tmp_path/'source', disk_min_file_size=1) as c:
        c['key'] = b'x' * 10000
        c.snapshot(tmp_path/'snap')
    code = '''
import sys
from diskcache import Cache
from diskcache.snapshot import restore_snapshot
def pause(event):
    print('READY', flush=True)
    sys.stdin.read(1)
if sys.argv[1] == 'snapshot':
    with Cache(sys.argv[2]) as c: c.snapshot(sys.argv[4], progress=pause)
else: restore_snapshot(sys.argv[3], sys.argv[4], progress=pause)
'''
    process = child(code, operation, tmp_path/'source', tmp_path/'snap', tmp_path/'destination')
    try:
        ready(process)
        assert not (tmp_path/'destination').exists()
        process.kill()
        process.wait(timeout=10)
        assert not (tmp_path/'destination').exists()
        if operation == 'snapshot':
            with Cache(tmp_path/'source') as c:
                c.snapshot(tmp_path/'destination')
            restored = api.restore_snapshot(tmp_path/'destination', tmp_path/'restore')
        else:
            restored = api.restore_snapshot(tmp_path/'snap', tmp_path/'destination')
        with restored:
            assert restored['key'] == b'x' * 10000
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_fanout_snapshot_waits_for_transaction_and_never_mixes_generations(api, tmp_path):
    with FanoutCache(tmp_path/'source', shards=4) as c:
        for i in range(40):
            c.set(str(i), 'old', retry=True)
    writer = child('''
import sys
from diskcache import FanoutCache
with FanoutCache(sys.argv[1], shards=4) as c:
    with c.transact():
        for i in range(20): c[str(i)] = 'new'
        print('READY', flush=True)
        sys.stdin.read(1)
        for i in range(20, 40): c[str(i)] = 'new'
''', tmp_path/'source')
    snapshot = None
    try:
        ready(writer)
        snapshot = child('''
import sys
from diskcache import FanoutCache
with FanoutCache(sys.argv[1], shards=4) as c:
    print('READY', flush=True)
    c.snapshot(sys.argv[2])
''', tmp_path/'source', tmp_path/'snap')
        # Opening the second cache may itself wait on the active transaction.
        writer.stdin.write('x'); writer.stdin.flush()
        writer.wait(timeout=15)
        out, err = snapshot.communicate(timeout=20)
        assert snapshot.returncode == 0, (out, err)
        with api.restore_snapshot(tmp_path/'snap', tmp_path/'restored') as c:
            assert [c[str(i)] for i in range(40)] == ['new'] * 40
    finally:
        for process in (writer, snapshot):
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)


@pytest.mark.parametrize('fanout', [False, True])
def test_cli_end_to_end_and_failure_exit(api, tmp_path, fanout):
    with (FanoutCache(tmp_path/'source', shards=3, disk=JSONDisk) if fanout else Cache(tmp_path/'source', disk=JSONDisk)) as c:
        c['from-cli'] = [1, 2, 3]
    base = [sys.executable, '-m', 'diskcache.snapshot']
    args = ['--shards','3'] if fanout else []
    result = subprocess.run(base+['create',str(tmp_path/'source'),str(tmp_path/'snap'),'--disk','JSONDisk']+args, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['disk'] == 'JSONDisk'
    for args in (['verify',str(tmp_path/'snap')], ['restore',str(tmp_path/'snap'),str(tmp_path/'restore')]):
        result = subprocess.run(base+args, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['shards'] == (3 if fanout else 1)
    with (FanoutCache(tmp_path/'restore', shards=3, disk=JSONDisk) if fanout else Cache(tmp_path/'restore', disk=JSONDisk)) as c:
        assert c['from-cli'] == [1, 2, 3]
    result = subprocess.run(base+['restore',str(tmp_path/'snap'),str(tmp_path/'restore')], capture_output=True, timeout=20)
    assert result.returncode != 0

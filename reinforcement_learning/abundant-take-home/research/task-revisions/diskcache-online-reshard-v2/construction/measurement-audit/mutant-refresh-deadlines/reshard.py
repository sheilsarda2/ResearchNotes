"""Coordinated, recoverable online changes to a local fanout topology.

Only ReshardableFanoutCache clients may open managed shards. Named stores are
independent. SQLite transactions remain native per-database transactions.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import pickle
import shutil
import sqlite3
import tempfile
import threading
import uuid

from .core import Cache, DEFAULT_SETTINGS, Disk, JSONDisk, ENOVAL
from .fanout import FanoutCache


class ReshardError(Exception):
    """Base class for topology errors."""


class ReshardConflictError(ReshardError):
    """The requested epoch, token, or configuration is stale."""


class ReshardBusyError(ReshardError):
    """Another operation owns the root lease, or a transaction is active."""


class ReshardPausedError(ReshardError):
    """Migration is paused."""


class ReshardIntegrityError(ReshardError):
    """Durable metadata or a migration source is damaged."""


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError('expected a positive integer')
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


_COLUMNS = 'key,raw,store_time,expire_time,access_time,access_count,tag,size,mode,filename,value'
_DELEGATES = frozenset(('set get add incr decr touch pop delete read expire evict clear cull check stats volume').split())


class ReshardableFanoutCache:
    """Fanout cache with durable topology and bounded online migration.

    The root lease is nonblocking across instances/processes; callers may retry
    ReshardBusyError. Calls on one instance are serialized across threads.
    """

    memoize = Cache.memoize

    def __init__(self, directory=None, shards=None, timeout=0.010, disk=None, adopt=False, **settings):
        self._directory = os.path.abspath(os.path.expandvars(os.path.expanduser(str(directory)))) if directory is not None else tempfile.mkdtemp(prefix='diskcache-reshard-')
        os.makedirs(self._directory, exist_ok=True)
        self._timeout = timeout
        self._mutex = threading.RLock()
        self._local = threading.local()
        self._inner = None
        self._destination = None
        self._signature = None
        self._caches, self._deques, self._indexes = {}, {}, {}
        if shards is not None:
            _positive(shards)
        if disk not in (None, Disk, JSONDisk):
            raise ValueError('managed caches support Disk and JSONDisk')
        unknown = set(settings) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError('unknown settings: %s' % sorted(unknown))
        with self._lease(refresh=False):
            path = self._meta_path
            if not os.path.exists(path):
                entries = set(os.listdir(self.directory)) - {'RESHARD.lock'}
                count = shards or 8
                config = dict(DEFAULT_SETTINGS)
                config.update(settings)
                if entries:
                    if not adopt or shards is None:
                        raise ReshardConflictError('nonempty root requires adopt=True and exact shards')
                    numeric = {name for name in entries if name.isdigit()}
                    if numeric != {'%03d' % index for index in range(count)}:
                        raise ReshardConflictError('legacy shard layout does not match')
                    connection = sqlite3.connect('file:' + os.path.join(self.directory, '000', 'cache.db') + '?mode=ro', uri=True)
                    try:
                        config = {key: value for key, value in connection.execute('SELECT key,value FROM Settings') if key in DEFAULT_SETTINGS}
                    finally:
                        connection.close()
                    config['size_limit'] *= count
                    if any(config.get(key) != value for key, value in settings.items()):
                        raise ReshardConflictError('legacy settings differ')
                    legacy = FanoutCache(self.directory, shards=count, timeout=timeout, disk=disk or Disk, **config)
                    try:
                        for index, shard in enumerate(legacy._shards):
                            for key in shard:
                                if legacy._hash(key) % count != index:
                                    raise ReshardConflictError('legacy key routed to wrong shard')
                    finally:
                        legacy.close()
                    generation = 'legacy'
                else:
                    generation = uuid.uuid4().hex
                self._meta = dict(epoch=0, active=dict(generation=generation, shards=count), migration=None, retired=[], disk='json' if disk is JSONDisk else 'disk', settings=config)
                self._save()
            self._meta = self._load()
            if shards is not None and shards != self._meta['active']['shards']:
                raise ReshardConflictError('persisted shard count differs')
            if disk is not None and ('json' if disk is JSONDisk else 'disk') != self._meta['disk']:
                raise ReshardConflictError('persisted disk differs')
            if any(self._meta['settings'].get(key) != value for key, value in settings.items()):
                raise ReshardConflictError('persisted settings differ')
            self._activate()
            self._recover()

    @property
    def directory(self):
        return self._directory

    @property
    def _meta_path(self):
        return os.path.join(self.directory, 'RESHARD.json')

    def _load(self):
        try:
            with open(self._meta_path, 'rb') as stream:
                envelope = json.load(stream)
            data = envelope['data']
            if envelope['schema'] != 1 or hashlib.sha256(_json(data)).hexdigest() != envelope['sha256']:
                raise ValueError('checksum or schema')
            if type(data['epoch']) is not int or data['epoch'] < 0 or data['disk'] not in ('disk', 'json'):
                raise ValueError('epoch or disk')
            def topology(item):
                _positive(item['shards'])
                generation = item['generation']
                if generation != 'legacy' and (not isinstance(generation, str) or len(generation) != 32 or any(c not in '0123456789abcdef' for c in generation)):
                    raise ValueError('generation')
            topology(data['active'])
            for old in data['retired']:
                topology(old)
            migration = data['migration']
            if migration is not None:
                topology(migration['target'])
                if migration['target']['generation'] == data['active']['generation'] or migration['target']['shards'] == data['active']['shards']:
                    raise ValueError('target must differ from active')
                if migration['phase'] not in ('preparing', 'copy', 'reconcile', 'aborting') or not isinstance(migration['id'], str):
                    raise ValueError('migration')
                for name in ('cursor_shard', 'cursor_rowid', 'copied'):
                    if type(migration[name]) is not int or migration[name] < 0:
                        raise ValueError(name)
                if type(migration['paused']) is not bool:
                    raise ValueError('paused')
            if set(data['settings']) != set(DEFAULT_SETTINGS):
                raise ValueError('settings')
            return data
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ReshardIntegrityError('invalid RESHARD.json') from error

    def _save(self):
        data = self._meta
        envelope = dict(schema=1, data=data, sha256=hashlib.sha256(_json(data)).hexdigest())
        fd, temporary = tempfile.mkstemp(prefix='.reshard-', dir=self.directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(_json(envelope))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._meta_path)
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextlib.contextmanager
    def _lease(self, refresh=True):
        with self._mutex:
            if getattr(self._local, 'depth', 0):
                self._local.depth += 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            descriptor = os.open(os.path.join(self.directory, 'RESHARD.lock'), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ReshardBusyError('root is in use') from error
                self._local.depth = 1
                try:
                    if refresh:
                        self._meta = self._load()
                        self._activate()
                        self._recover()
                    yield
                finally:
                    self._local.depth = 0
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _path(self, topology):
        if topology['generation'] == 'legacy':
            return self.directory
        return os.path.join(self.directory, 'generations', topology['generation'])

    def _open(self, topology):
        return FanoutCache(self._path(topology), shards=topology['shards'], timeout=self._timeout, disk=JSONDisk if self._meta['disk'] == 'json' else Disk, **self._meta['settings'])

    def _activate(self):
        signature = self._meta['active']['generation']
        if self._inner is None or self._signature != signature:
            if self._inner is not None:
                self._inner.close()
            self._inner = self._open(self._meta['active'])
            self._signature = signature
        self._disk = JSONDisk if self._meta['disk'] == 'json' else Disk

    def _target(self):
        topology = self._meta['migration']['target']
        if self._destination is None or self._destination.directory != self._path(topology):
            if self._destination is not None:
                self._destination.close()
            self._destination = self._open(topology)
        return self._destination

    def _tracking(self, enable):
        for shard in self._inner._shards:
            with shard.transact():
                sql = shard._sql
                if enable:
                    sql('CREATE TABLE IF NOT EXISTS _reshard_dirty (id INTEGER PRIMARY KEY AUTOINCREMENT,key BLOB,raw INTEGER,UNIQUE(key,raw))')
                    for suffix, event, rows in [('i', 'INSERT', ['NEW']), ('u', 'UPDATE', ['OLD', 'NEW']), ('d', 'DELETE', ['OLD'])]:
                        body = ' '.join('INSERT OR REPLACE INTO _reshard_dirty(key,raw) VALUES (%s.key,%s.raw);' % (row, row) for row in rows)
                        sql('CREATE TRIGGER IF NOT EXISTS _reshard_%s AFTER %s ON Cache BEGIN %s END' % (suffix, event, body))
                else:
                    for suffix in ('i', 'u', 'd'):
                        sql('DROP TRIGGER IF EXISTS _reshard_' + suffix)
                    sql('DROP TABLE IF EXISTS _reshard_dirty')

    def _recover(self):
        migration = self._meta['migration']
        if migration and migration['phase'] == 'preparing':
            self._tracking(True)
            self._target()
            migration['phase'] = 'copy'
            self._save()
        elif migration and migration['phase'] == 'aborting':
            self._finish_abort()

    def _lifecycle(self):
        if getattr(self._local, 'transaction', 0):
            raise ReshardBusyError('topology changes are forbidden inside transact')

    def _token(self, token):
        migration = self._meta['migration']
        if migration is None or token != migration['id']:
            raise ReshardConflictError('stale migration id')
        return migration

    def _status(self):
        migration = self._meta['migration']
        pending = sum(shard._sql('SELECT COUNT(*) FROM _reshard_dirty').fetchone()[0] for shard in self._inner._shards) if migration else 0
        return dict(epoch=self._meta['epoch'], shards=self._meta['active']['shards'], generation=self._meta['active']['generation'], migration_id=migration['id'] if migration else None, phase=('paused' if migration['paused'] else migration['phase']) if migration else 'idle', target_shards=migration['target']['shards'] if migration else None, copied=migration['copied'] if migration else 0, pending=pending)

    def reshard_status(self):
        with self._lease():
            return self._status()

    def begin_reshard(self, shards, expected_epoch=None):
        _positive(shards)
        if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
            raise ValueError('expected_epoch must be a nonnegative integer')
        with self._lease():
            self._lifecycle()
            if expected_epoch is not None and expected_epoch != self._meta['epoch']:
                raise ReshardConflictError('stale epoch')
            migration = self._meta['migration']
            if migration:
                if migration['target']['shards'] != shards:
                    raise ReshardConflictError('different migration already active')
                return self._status()
            if shards == self._meta['active']['shards']:
                return self._status()
            self._meta['migration'] = dict(id=uuid.uuid4().hex, target=dict(generation=uuid.uuid4().hex, shards=shards), phase='preparing', paused=False, cursor_shard=0, cursor_rowid=0, copied=0)
            self._save()
            self._recover()
            return self._status()

    def _pause(self, token, paused):
        with self._lease():
            self._lifecycle()
            self._token(token)['paused'] = paused
            self._save()
            return self._status()

    def pause_reshard(self, migration_id):
        return self._pause(migration_id, True)

    def resume_reshard(self, migration_id):
        return self._pause(migration_id, False)

    def _remove_topology(self, topology):
        if topology['generation'] == 'legacy':
            for index in range(topology['shards']):
                path = os.path.join(self.directory, '%03d' % index)
                if os.path.exists(path):
                    shutil.rmtree(path)
        else:
            path = self._path(topology)
            if os.path.exists(path):
                shutil.rmtree(path)

    def _finish_abort(self):
        target = self._meta['migration']['target']
        self._tracking(False)
        if self._destination is not None:
            self._destination.close()
            self._destination = None
        self._remove_topology(target)
        self._meta['migration'] = None
        self._save()

    def abort_reshard(self, migration_id):
        with self._lease():
            self._lifecycle()
            self._token(migration_id)['phase'] = 'aborting'
            self._save()
            self._finish_abort()
            return self._status()

    def collect(self):
        with self._lease():
            self._lifecycle()
            retired = list(self._meta['retired'])
            for topology in retired:
                self._remove_topology(topology)
            self._meta['retired'] = []
            self._save()
            return len(retired)

    def _copy(self, source, key, raw, row):
        target = self._target()
        decoded = source.disk.get(key, raw)
        dest = target._shards[target._hash(decoded) % target._count]
        name_hash = hashlib.sha256(pickle.dumps((key, raw), protocol=4)).hexdigest()
        filename = os.path.join(name_hash[:2], name_hash[2:4], name_hash[4:] + '.val')
        path = os.path.join(dest.directory, filename)
        values = list(row) if row is not None else None
        if values is not None and values[3] is not None:
            values[3] += 3600
        if values is not None and values[9]:
            source_path = os.path.join(source.directory, values[9])
            real_root = os.path.realpath(source.directory) + os.sep
            if not os.path.realpath(source_path).startswith(real_root) or os.path.islink(source_path):
                raise ReshardIntegrityError('unsafe source payload')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                with open(source_path, 'rb') as incoming, open(path, 'wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            except OSError as error:
                raise ReshardIntegrityError('missing or unreadable source payload') from error
            values[9] = filename
        with dest.transact():
            previous = dest._sql('SELECT filename FROM Cache WHERE key=? AND raw=?', (key, raw)).fetchone()
            dest._sql('DELETE FROM Cache WHERE key=? AND raw=?', (key, raw))
            if values is not None:
                dest._sql('INSERT INTO Cache (%s) VALUES (%s)' % (_COLUMNS, ','.join('?' for _ in values)), values)
        keep = values[9] if values is not None else None
        for old in {filename, previous[0] if previous else None} - {None, keep}:
            dest.disk.remove(old)

    def step_reshard(self, migration_id, max_items=100):
        _positive(max_items)
        with self._lease():
            self._lifecycle()
            migration = self._token(migration_id)
            if migration['paused']:
                raise ReshardPausedError('migration is paused')
            processed = 0
            while processed < max_items:
                if migration['phase'] == 'copy':
                    index = migration['cursor_shard']
                    if index == len(self._inner._shards):
                        migration['phase'] = 'reconcile'
                        continue
                    source = self._inner._shards[index]
                    found = source._sql('SELECT rowid,%s FROM Cache WHERE rowid>? ORDER BY rowid LIMIT 1' % _COLUMNS, (migration['cursor_rowid'],)).fetchone()
                    if found is None:
                        migration['cursor_shard'] += 1
                        migration['cursor_rowid'] = 0
                        continue
                    self._copy(source, found[1], found[2], found[1:])
                    migration['cursor_rowid'] = found[0]
                else:
                    found = None
                    for source in self._inner._shards:
                        found = source._sql('SELECT id,key,raw FROM _reshard_dirty ORDER BY id LIMIT 1').fetchone()
                        if found is not None:
                            break
                    if found is None:
                        old = self._meta['active']
                        self._meta['active'] = migration['target']
                        self._meta['epoch'] += 1
                        self._meta['retired'].append(old)
                        self._meta['migration'] = None
                        self._save()
                        if self._destination is not None:
                            self._destination.close()
                            self._destination = None
                        self._activate()
                        result = self._status()
                        result['processed'] = processed
                        return result
                    identity, key, raw = found
                    row = source._sql('SELECT %s FROM Cache WHERE key=? AND raw=?' % _COLUMNS, (key, raw)).fetchone()
                    self._copy(source, key, raw, row)
                    with source.transact():
                        source._sql('DELETE FROM _reshard_dirty WHERE id=?', (identity,))
                migration['copied'] += 1
                processed += 1
                self._save()
            self._save()
            result = self._status()
            result['processed'] = processed
            return result

    @contextlib.contextmanager
    def transact(self, retry=True):
        with self._lease():
            self._local.transaction = getattr(self._local, 'transaction', 0) + 1
            try:
                with self._inner.transact(retry=retry):
                    yield
            finally:
                self._local.transaction -= 1

    def __getattr__(self, name):
        if name in _DELEGATES:
            def operation(*args, **kwargs):
                with self._lease():
                    return getattr(self._inner, name)(*args, **kwargs)
            return operation
        if name in DEFAULT_SETTINGS or name in ('timeout', 'disk'):
            with self._lease():
                return getattr(self._inner, name)
        raise AttributeError(name)

    def reset(self, key, value=ENOVAL):
        if value is not ENOVAL:
            raise ValueError('managed configuration is immutable')
        with self._lease():
            return self._inner.reset(key)

    def __getitem__(self, key):
        with self._lease():
            return self._inner[key]

    def __setitem__(self, key, value):
        with self._lease():
            self._inner[key] = value

    def __delitem__(self, key):
        with self._lease():
            del self._inner[key]

    def __contains__(self, key):
        with self._lease():
            return key in self._inner

    def __len__(self):
        with self._lease():
            return len(self._inner)

    def __iter__(self):
        with self._lease():
            return iter(list(self._inner))

    def __reversed__(self):
        with self._lease():
            return iter(list(reversed(self._inner)))

    def cache(self, name, timeout=60, **settings):
        with self._lease():
            return FanoutCache.cache(self, name, timeout=timeout, **settings)

    def deque(self, name, maxlen=None):
        with self._lease():
            return FanoutCache.deque(self, name, maxlen=maxlen)

    def index(self, name):
        with self._lease():
            return FanoutCache.index(self, name)

    def close(self):
        with self._mutex:
            for fanout in (self._inner, self._destination):
                if fanout is not None:
                    fanout.close()
            self._inner = self._destination = None
            for mapping in (self._caches, self._deques, self._indexes):
                for item in mapping.values():
                    item.close() if hasattr(item, 'close') else item.cache.close()
                mapping.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __getstate__(self):
        return self.directory, self._timeout

    def __setstate__(self, state):
        self.__init__(state[0], timeout=state[1])

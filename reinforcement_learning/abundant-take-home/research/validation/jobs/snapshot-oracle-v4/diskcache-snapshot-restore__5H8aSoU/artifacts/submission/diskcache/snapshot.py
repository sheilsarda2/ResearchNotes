"""Consistent, verified native snapshots of DiskCache databases and payloads."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import threading

from .core import Cache, Disk, JSONDisk
from .fanout import FanoutCache


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('invalid member path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        raise ValueError('invalid member path')
    return path


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(block)
            digest.update(block)
    return {'sha256': digest.hexdigest(), 'size': size}


def _sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sync_tree(path):
    for directory, _, files in os.walk(path, topdown=False):
        for filename in files:
            with open(os.path.join(directory, filename), 'rb') as stream:
                os.fsync(stream.fileno())
        _sync_dir(directory)


def _destination(destination, source):
    destination = Path(destination).absolute()
    if os.path.lexists(destination):
        raise ValueError('destination exists')
    if not destination.parent.is_dir():
        raise ValueError('destination parent must exist')
    destination = destination.parent.resolve() / destination.name
    source = Path(source).resolve()
    if destination == source or source in destination.parents:
        raise ValueError('destination is inside source')
    return destination


@contextlib.contextmanager
def _staging(destination):
    stage = Path(tempfile.mkdtemp(prefix='.' + destination.name + '.stage-',
                                 dir=destination.parent))
    try:
        yield stage
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _publish(stage, destination):
    _sync_tree(stage)
    if os.path.lexists(destination):
        raise ValueError('destination exists')
    try:
        os.rename(stage, destination)
    except FileExistsError as error:
        raise ValueError('destination exists') from error
    _sync_dir(destination.parent)


def _copy(source, destination, member, progress):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file() or source.is_symlink():
        raise ValueError('invalid payload file')
    shutil.copyfile(source, destination)
    if progress is not None:
        progress({'phase': 'copy', 'path': member})


def _database(path, immutable=False):
    suffix = '?mode=ro' + ('&immutable=1' if immutable else '')
    connection = sqlite3.connect(path.resolve().as_uri() + suffix, uri=True)
    connection.execute('PRAGMA trusted_schema=OFF')
    return connection


def _roots(manifest):
    if manifest['kind'] == 'cache':
        return ['data']
    return ['data/%03d' % index for index in range(manifest['shards'])]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key')
        result[key] = value
    return result


def verify_snapshot(path):
    """Validate a snapshot without modifying it or deserializing its values."""
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_dir():
            raise ValueError('invalid snapshot directory')
        if (path / 'manifest.json').is_symlink():
            raise ValueError('symlink manifest')
        manifest = json.loads((path / 'manifest.json').read_text('utf-8'),
                              object_pairs_hook=_unique_object)
        if (not isinstance(manifest, dict)
                or type(manifest.get('format_version')) is not int
                or manifest['format_version'] != 1
                or manifest.get('kind') not in ('cache', 'fanout')
                or type(manifest.get('shards')) is not int
                or manifest['shards'] < 1
                or manifest.get('disk') not in ('Disk', 'JSONDisk')
                or not isinstance(manifest.get('files'), dict)):
            raise ValueError('unsupported manifest')
        if manifest['kind'] == 'cache' and manifest['shards'] != 1:
            raise ValueError('invalid cache topology')
        files = manifest['files']
        for name, descriptor in files.items():
            parts = _relative(name).parts
            if len(parts) < 2 or parts[0] != 'data':
                raise ValueError('member outside data')
            if (not isinstance(descriptor, dict)
                    or type(descriptor.get('size')) is not int
                    or descriptor['size'] < 0
                    or not isinstance(descriptor.get('sha256'), str)
                    or re.fullmatch('[0-9a-f]{64}', descriptor['sha256']) is None):
                raise ValueError('invalid file descriptor')
        actual = set()
        for directory, dirs, names in os.walk(path, followlinks=False):
            for name in dirs + names:
                entry = Path(directory) / name
                if entry.is_symlink():
                    raise ValueError('symlink member')
            for name in names:
                entry = Path(directory) / name
                if not entry.is_file():
                    raise ValueError('non-regular member')
                actual.add(entry.relative_to(path).as_posix())
        if actual != set(files) | {'manifest.json'}:
            raise ValueError('snapshot file inventory differs')
        for name, descriptor in files.items():
            if _digest(path / name) != {key: descriptor[key] for key in ('sha256', 'size')}:
                raise ValueError('snapshot checksum differs: ' + name)
        referenced = set()
        for root in _roots(manifest):
            db_name = root + '/cache.db'
            if db_name not in files:
                raise ValueError('missing database')
            referenced.add(db_name)
            with contextlib.closing(_database(path / db_name, immutable=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('database integrity check failed')
                db.execute('SELECT key, value FROM Settings').fetchall()
                rows = db.execute('SELECT filename FROM Cache WHERE filename IS NOT NULL')
                for (filename,) in rows:
                    _relative(filename)
                    referenced.add(root + '/' + filename)
        if referenced != set(files):
            raise ValueError('payload inventory differs from databases')
        return manifest
    except (OSError, UnicodeError, json.JSONDecodeError, sqlite3.Error,
            KeyError, TypeError, OverflowError) as error:
        raise ValueError('invalid snapshot: ' + str(error)) from error


def create_snapshot(cache, destination, *, progress=None):
    """Publish a consistent native snapshot of a Cache or FanoutCache."""
    fanout = isinstance(cache, FanoutCache)
    shards = cache._shards if fanout else (cache,)
    codec = type(shards[0].disk)
    if codec not in (Disk, JSONDisk) or any(type(s.disk) is not codec for s in shards):
        raise ValueError('unsupported disk codec')
    if any(shard._txn_id == threading.get_ident() for shard in shards):
        raise ValueError('cannot snapshot the caller\'s transaction')
    destination = _destination(destination, cache.directory)
    manifest = {'format_version': 1, 'kind': 'fanout' if fanout else 'cache',
                'shards': len(shards), 'disk': codec.__name__, 'files': {}}
    with _staging(destination) as stage:
        with contextlib.ExitStack() as stack:
            for shard in shards:
                stack.enter_context(shard.transact(retry=True))
            for shard, root in zip(shards, _roots(manifest)):
                source = Path(shard.directory)
                db_name = root + '/cache.db'
                target = stage / db_name
                target.parent.mkdir(parents=True, exist_ok=True)
                # A separate reader sees committed WAL state while the write
                # transaction prevents changes and referenced-file deletion.
                with contextlib.closing(_database(source / 'cache.db')) as reader:
                    with contextlib.closing(sqlite3.connect(target)) as writer:
                        reader.backup(writer)
                        writer.execute('PRAGMA journal_mode=DELETE')
                    # Some SQLite versions leave an empty shared-memory sidecar
                    # when switching a backup from WAL to DELETE journaling.
                    for suffix in ('-shm', '-wal', '-journal'):
                        sidecar = Path(str(target) + suffix)
                        if sidecar.exists():
                            if suffix != '-shm' and sidecar.stat().st_size:
                                raise ValueError('backup still requires recovery')
                            sidecar.unlink()
                    payloads = reader.execute(
                        'SELECT DISTINCT filename FROM Cache WHERE filename IS NOT NULL'
                    ).fetchall()
                if progress is not None:
                    progress({'phase': 'copy', 'path': db_name})
                manifest['files'][db_name] = _digest(target)
                for (filename,) in payloads:
                    _relative(filename)
                    member = root + '/' + filename
                    _copy(source / filename, stage / member, member, progress)
                    manifest['files'][member] = _digest(stage / member)
        (stage / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True), 'utf-8')
        verify_snapshot(stage)
        _publish(stage, destination)
    return manifest


def restore_snapshot(path, destination, *, progress=None):
    """Atomically restore a checked snapshot and return its open cache."""
    path = Path(path)
    manifest = verify_snapshot(path)
    destination = _destination(destination, path)
    with _staging(destination) as stage:
        # Retain snapshot structure until the second verification. This checks
        # copied bytes against the original manifest even if input changes.
        for member in manifest['files']:
            _copy(path / member, stage / member, member, progress)
        (stage / 'manifest.json').write_text(json.dumps(manifest), 'utf-8')
        verify_snapshot(stage)
        settings = []
        for root in _roots(manifest):
            with contextlib.closing(_database(stage / root / 'cache.db', immutable=True)) as db:
                settings.append(dict(db.execute('SELECT key, value FROM Settings')))
        _publish(stage / 'data', destination)
    codec = {'Disk': Disk, 'JSONDisk': JSONDisk}[manifest['disk']]
    if manifest['kind'] == 'cache':
        return Cache(destination, disk=codec)
    restored = FanoutCache(destination, shards=manifest['shards'], disk=codec,
                           size_limit=sum(item['size_limit'] for item in settings))
    for shard, saved in zip(restored._shards, settings):
        shard.reset('size_limit', saved['size_limit'])
    return restored


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('create')
    create.add_argument('source')
    create.add_argument('destination')
    create.add_argument('--shards', type=int)
    create.add_argument('--disk', choices=['Disk', 'JSONDisk'], default='Disk')
    verify = sub.add_parser('verify')
    verify.add_argument('snapshot')
    restore = sub.add_parser('restore')
    restore.add_argument('snapshot')
    restore.add_argument('destination')
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            codec = {'Disk': Disk, 'JSONDisk': JSONDisk}[args.disk]
            if args.shards is None:
                cache = Cache(args.source, disk=codec)
            else:
                if args.shards < 1:
                    raise ValueError('shards must be positive')
                cache = FanoutCache(args.source, shards=args.shards, disk=codec)
            with cache:
                manifest = cache.snapshot(args.destination)
        elif args.command == 'verify':
            manifest = verify_snapshot(args.snapshot)
        else:
            restored = restore_snapshot(args.snapshot, args.destination)
            restored.close()
            manifest = verify_snapshot(args.snapshot)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()

"""Hidden differential tests: rosbag2 sqlite3 storage writer vs. the rosbag2_storage_sqlite3
schema (version 4) as recorded by ROS 2 rolling, checked with the stdlib sqlite3 module."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import zstandard
from mcap.reader import make_reader

from bagspec import CONNECTIONS_MSG_ONLY, BagInfo, make_bag, rewrite_bag
from rosbag2_verify import FIXTURES, bag_metadata, expected_qos_string, yaml_load
from rosbags.rosbag2 import CompressionFormat, CompressionMode, Reader, StoragePlugin, Writer, WriterError
from rosbags.rosbag2.storage_sqlite3 import Sqlite3Reader, Sqlite3Writer
from rosbags.typesys import Stores, get_typestore

TABLES = ('schema', 'metadata', 'topics', 'message_definitions', 'messages')
V4_FIXTURE = (
    FIXTURES
    / 'sqlite3'
    / 'bag_with_topics_and_service_events_and_action'
    / 'bag_with_topics_and_service_events_and_action.db3'
)


@pytest.fixture(scope='session')
def typestore():
    return get_typestore(Stores.LATEST)


@pytest.fixture(params=[9, 8], ids=['v9', 'v8'])
def bag(request, tmp_path: Path, typestore) -> BagInfo:
    return make_bag(tmp_path / 'bag', StoragePlugin.SQLITE3, typestore=typestore, version=request.param)


def db_path(info: BagInfo) -> Path:
    return info.path / f'{info.name}.db3'


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def table_info(con: sqlite3.Connection, table: str) -> list[tuple]:
    return con.execute(f'PRAGMA table_info({table})').fetchall()


def test_storage_file_layout(bag: BagInfo) -> None:
    assert sorted(x.name for x in bag.path.iterdir()) == ['bag.db3', 'metadata.yaml'], 'no journal or WAL left behind'
    meta = bag_metadata(bag.path)
    assert meta['storage_identifier'] == 'sqlite3'
    assert meta['relative_file_paths'] == ['bag.db3']
    with connect(db_path(bag)) as con:
        assert con.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_schema_matches_rosbag2_recorded_bag(bag: BagInfo) -> None:
    with connect(db_path(bag)) as con, connect(V4_FIXTURE) as ref:
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        tables = [r[0] for r in con.execute(query)]
        ref_tables = [r[0] for r in ref.execute(query)]
        assert tables == ref_tables == sorted(TABLES)
        for table in TABLES:
            assert table_info(con, table) == table_info(ref, table), table
        indexes = con.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchall()
        assert indexes == [('timestamp_idx', 'messages')]
        assert con.execute('PRAGMA index_info(timestamp_idx)').fetchall() == [(0, 2, 'timestamp')]
        assert con.execute('PRAGMA index_xinfo(timestamp_idx)').fetchone()[3] == 0, 'ascending index'


def test_schema_table_row(bag: BagInfo) -> None:
    with connect(db_path(bag)) as con:
        assert con.execute('SELECT schema_version, ros_distro FROM schema').fetchall() == [(4, 'rosbags')]


def test_metadata_table_row(bag: BagInfo) -> None:
    with connect(db_path(bag)) as con:
        rows = con.execute('SELECT id, metadata_version, metadata FROM metadata').fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] == bag.version
    assert yaml_load(rows[0][2]) == bag_metadata(bag.path)


def test_topics_rows(bag: BagInfo) -> None:
    expected = [
        (
            idx + 1,
            spec.topic,
            spec.msgtype,
            'cdr',
            expected_qos_string(list(spec.qos), bag.version),
            bag.expected_definition(spec)[2],
        )
        for idx, spec in enumerate(bag.specs)
    ]
    if bag.version >= 9:
        assert expected[1][4] == '[]'
    else:
        assert expected[1][4] == ''
        assert 'history: 1' in expected[0][4]
    with connect(db_path(bag)) as con:
        rows = con.execute(
            'SELECT id, name, type, serialization_format, offered_qos_profiles, type_description_hash '
            'FROM topics ORDER BY id'
        ).fetchall()
    assert rows == expected


def test_message_definitions_rows(bag: BagInfo) -> None:
    expected = []
    for sid, msgtype, encoding, msgdef in bag.expected_schemas():
        spec = next(s for s in bag.specs if s.msgtype == msgtype)
        expected.append((sid, msgtype, encoding, msgdef, bag.expected_definition(spec)[2]))
    assert [e[2] for e in expected] == ['ros2msg', 'ros2msg', 'ros2idl', 'ros2msg']
    with connect(db_path(bag)) as con:
        rows = con.execute(
            'SELECT id, topic_type, encoding, encoded_message_definition, type_description_hash '
            'FROM message_definitions ORDER BY id'
        ).fetchall()
    assert rows == expected


def test_messages_rows(bag: BagInfo) -> None:
    with connect(db_path(bag)) as con:
        rows = con.execute('SELECT id, topic_id, timestamp, data FROM messages ORDER BY id').fetchall()
    assert rows == [(i + 1, bag.cid(t), ts, d) for i, (t, ts, d) in enumerate(bag.written)]
    with connect(db_path(bag)) as con:
        by_time = con.execute('SELECT topic_id, timestamp FROM messages ORDER BY timestamp, id').fetchall()
    assert by_time == [(bag.cid(t), ts) for t, ts, _ in bag.written_by_time()]


def test_storage_compression_rejected(tmp_path: Path, typestore) -> None:
    bagdir = tmp_path / 'direct'
    bagdir.mkdir()
    with pytest.raises(WriterError, match='storage-side compression'):
        Sqlite3Writer(bagdir, CompressionMode.STORAGE)
    assert list(bagdir.iterdir()) == [], 'no storage file is created when the mode is rejected'
    writer = Writer(tmp_path / 'front', version=9)
    writer.set_compression(CompressionMode.STORAGE, CompressionFormat.ZSTD)
    with pytest.raises(WriterError, match='storage-side compression'):
        writer.open()


@pytest.fixture(params=[9, 8], ids=['v9', 'v8'])
def msg_bag(request, tmp_path: Path, typestore) -> BagInfo:
    """Bag without the IDL-defined type (rosbags' sqlite3 reader only parses ros2msg)."""
    return make_bag(
        tmp_path / 'bag', StoragePlugin.SQLITE3, typestore=typestore, version=request.param, connections=CONNECTIONS_MSG_ONLY
    )


def test_rosbags_reader_roundtrip(msg_bag: BagInfo) -> None:
    bag = msg_bag
    assert len(bag.expected_schemas()) == 3
    with Reader(bag.path) as reader:
        assert reader.message_count == len(bag.written)
        assert [(c.topic, ts, bytes(d)) for c, ts, d in reader.messages()] == bag.written_by_time()
        assert {c.topic: c.msgcount for c in reader.connections} == {
            s.topic: sum(1 for t, _, _ in bag.written if t == s.topic) for s in bag.specs
        }
    storage = Sqlite3Reader(db_path(bag))
    storage.open()
    assert storage.schema == 4
    assert [(m['name'], m['encoding'], m['msgdef'], m['digest']) for m in storage.msgtypes] == [
        (name, enc, msgdef, bag.expected_definition(next(s for s in bag.specs if s.msgtype == name))[2])
        for _, name, enc, msgdef in bag.expected_schemas()
    ]
    assert [(c.id, c.topic, c.msgtype, c.digest) for c in storage.connections] == [
        (i + 1, s.topic, s.msgtype, bag.expected_definition(s)[2]) for i, s in enumerate(bag.specs)
    ]
    storage.close()


@pytest.mark.parametrize('mode', [CompressionMode.FILE, CompressionMode.MESSAGE], ids=['file', 'message'])
def test_writer_compression_modes(tmp_path: Path, typestore, mode: CompressionMode) -> None:
    info = make_bag(
        tmp_path / 'bag', StoragePlugin.SQLITE3, typestore=typestore, compression=mode, connections=CONNECTIONS_MSG_ONLY
    )
    names = sorted(x.name for x in info.path.iterdir())
    if mode == CompressionMode.FILE:
        assert names == ['bag.db3.zstd', 'metadata.yaml']
        raw = zstandard.ZstdDecompressor().stream_reader((info.path / 'bag.db3.zstd').open('rb')).read()
        plain = tmp_path / 'plain.db3'
        plain.write_bytes(raw)
    else:
        assert names == ['bag.db3', 'metadata.yaml']
        plain = info.path / 'bag.db3'
        with connect(plain) as con:
            rows = con.execute('SELECT topic_id, timestamp, data FROM messages ORDER BY id').fetchall()
        decomp = zstandard.ZstdDecompressor().decompress
        assert [(t, ts, decomp(d)) for t, ts, d in rows] == [(info.cid(t), ts, d) for t, ts, d in info.written]
    with connect(plain) as con:
        assert con.execute('SELECT count(*) FROM messages').fetchone() == (len(info.written),)
        assert con.execute('SELECT schema_version FROM schema').fetchone() == (4,)
    with Reader(info.path) as reader:
        assert reader.compression_mode == mode.name.lower()
        assert [(c.topic, ts, bytes(d)) for c, ts, d in reader.messages()] == info.written_by_time()


def fixture_dir(fmt: str, name: str) -> Path:
    return FIXTURES / fmt / name


def db_messages(path: Path) -> set[tuple[str, int, bytes]]:
    with connect(path) as con:
        return {
            (t, ts, bytes(d))
            for t, ts, d in con.execute(
                'SELECT topics.name, messages.timestamp, messages.data FROM messages '
                'JOIN topics ON topics.id = messages.topic_id'
            )
        }


@pytest.mark.parametrize('name', ['talker', 'cdr_test'])
def test_mcap_fixture_rewritten_as_sqlite3_matches_sqlite3_fixture(tmp_path: Path, name: str) -> None:
    src = fixture_dir('mcap', name)
    dst = tmp_path / name
    written, types = rewrite_bag(src, dst, StoragePlugin.SQLITE3)
    reference = next(fixture_dir('sqlite3', name).glob('*.db3'))
    produced = dst / f'{name}.db3'
    assert db_messages(produced) == db_messages(reference) == set(written)
    with connect(produced) as con, connect(reference) as ref:
        got = con.execute('SELECT name, type, serialization_format FROM topics ORDER BY name').fetchall()
        exp = ref.execute('SELECT name, type, serialization_format FROM topics ORDER BY name').fetchall()
        assert got == exp
        defs = con.execute('SELECT topic_type, encoding FROM message_definitions ORDER BY id').fetchall()
        assert {d[0] for d in defs} == set(types.values())
        assert all(d[1] == 'ros2msg' for d in defs)
    with Reader(dst) as reader:
        assert reader.message_count == len(written)


def test_convert_cli_writes_sqlite3(tmp_path: Path) -> None:
    src = fixture_dir('mcap', 'cdr_test')
    dst = tmp_path / 'converted'
    result = subprocess.run(
        [sys.executable, '-m', 'rosbags.convert', '--src', str(src), '--dst', str(dst), '--dst-storage', 'sqlite3'],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert bag_metadata(dst)['storage_identifier'] == 'sqlite3'
    assert db_messages(dst / 'converted.db3') == db_messages(fixture_dir('sqlite3', 'cdr_test') / 'cdr_test_0.db3')
    with fixture_dir('mcap', 'cdr_test').joinpath('cdr_test_0.mcap').open('rb') as fh:
        ref = {(ch.topic, m.log_time, m.data) for _, ch, m in make_reader(fh).iter_messages()}
    assert db_messages(dst / 'converted.db3') == ref

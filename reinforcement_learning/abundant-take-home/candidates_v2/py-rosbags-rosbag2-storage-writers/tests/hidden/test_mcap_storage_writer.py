"""Hidden differential tests: rosbag2 MCAP storage writer vs. the MCAP spec, mcap-python
and rosbag2-recorded fixture bags."""

from __future__ import annotations

import subprocess
import sys
import zlib
from importlib.metadata import version as pkg_version
from pathlib import Path

import pytest
import zstandard
from mcap import records as rec
from mcap.reader import NonSeekingReader, SeekingReader, make_reader

from bagspec import (
    CONNECTIONS,
    IDL_DIGEST,
    IDL_MSGDEF,
    LATCH,
    BagInfo,
    make_bag,
    rewrite_bag,
    rewrite_mcap_with_reference_reader,
)
from rosbag2_verify import (
    CHUNK_THRESHOLD,
    FIXTURES,
    MAGIC,
    MCAP_CLI,
    OP_CHANNEL,
    OP_CHUNK,
    OP_CHUNK_INDEX,
    OP_DATA_END,
    OP_FOOTER,
    OP_HEADER,
    OP_MESSAGE,
    OP_MESSAGE_INDEX,
    OP_METADATA,
    OP_METADATA_INDEX,
    OP_SCHEMA,
    OP_STATISTICS,
    OP_SUMMARY_OFFSET,
    RawRecord,
    bag_metadata,
    channel_record_size,
    chunk_records,
    expected_qos_string,
    message_record_size,
    parse,
    read_mcap,
    schema_record_size,
    to_plain,
    yaml_load,
)
from rosbags.highlevel import AnyReader
from rosbags.interfaces import Connection, ConnectionExtRosbag2, MessageDefinition, MessageDefinitionFormat
from rosbags.rosbag2 import CompressionFormat, CompressionMode, Reader, StoragePlugin, Writer
from rosbags.rosbag2.storage_mcap import McapReader, McapWriter
from rosbags.typesys import Stores, get_typestore

SENTINEL_START = 2**63 - 1


@pytest.fixture(scope='session')
def typestore():
    return get_typestore(Stores.LATEST)


@pytest.fixture(params=[None, CompressionMode.STORAGE], ids=['plain', 'storage-zstd'])
def bag(request, tmp_path: Path, typestore) -> BagInfo:
    return make_bag(tmp_path / 'bag', StoragePlugin.MCAP, typestore=typestore, compression=request.param)


def mcap_path(info: BagInfo) -> Path:
    return info.path / f'{info.name}.mcap'


def split_sections(records: list[RawRecord]) -> tuple[list[RawRecord], list[RawRecord], list[RawRecord], RawRecord]:
    """Return (data section records incl. DataEnd, summary records, summary offsets, footer)."""
    assert records[0].opcode == OP_HEADER
    assert records[-1].opcode == OP_FOOTER
    data_end = [i for i, r in enumerate(records) if r.opcode == OP_DATA_END]
    assert len(data_end) == 1, 'exactly one Data End record'
    idx = data_end[0]
    data = records[1 : idx + 1]
    rest = records[idx + 1 : -1]
    offsets = [r for r in rest if r.opcode == OP_SUMMARY_OFFSET]
    summary = [r for r in rest if r.opcode != OP_SUMMARY_OFFSET]
    if offsets:
        first = rest.index(offsets[0])
        assert all(r.opcode == OP_SUMMARY_OFFSET for r in rest[first:]), 'summary offsets are last'
        assert all(r.opcode != OP_SUMMARY_OFFSET for r in rest[:first])
    return data, summary, offsets, records[-1]


def messages_in_data_section(data: list[RawRecord]) -> list[tuple[int, rec.Message]]:
    """All Message records of the data section, in file order, with the chunk index."""
    out: list[tuple[int, rec.Message]] = []
    chunk_no = -1
    for record in data:
        if record.opcode == OP_CHUNK:
            chunk_no += 1
            for inner in chunk_records(parse(record)):
                if inner.opcode == OP_MESSAGE:
                    out.append((chunk_no, parse(inner)))
    return out


# ---------------------------------------------------------------- file layout


def test_storage_file_layout(bag: BagInfo) -> None:
    path = mcap_path(bag)
    assert path.is_file()
    assert sorted(x.name for x in bag.path.iterdir()) == ['bag.mcap', 'metadata.yaml']
    meta = bag_metadata(bag.path)
    assert meta['storage_identifier'] == 'mcap'
    assert meta['relative_file_paths'] == ['bag.mcap']
    data = path.read_bytes()
    assert data[:8] == MAGIC
    assert data[-8:] == MAGIC


def test_header_record(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    header = parse(records[0])
    assert isinstance(header, rec.Header)
    assert header.profile == 'ros2'
    assert header.library == f'rosbags-{pkg_version("rosbags")}'


def test_data_section_shape(bag: BagInfo) -> None:
    data_bytes, records = read_mcap(mcap_path(bag))
    data, _summary, _offsets, _footer = split_sections(records)
    opcodes = [r.opcode for r in data]
    assert opcodes[-1] == OP_DATA_END
    assert set(opcodes) <= {OP_CHUNK, OP_MESSAGE_INDEX, OP_METADATA, OP_DATA_END}, (
        'schema, channel and message records must live inside chunks'
    )
    assert opcodes.count(OP_METADATA) == 1
    for prev, cur in zip(opcodes, opcodes[1:]):
        if cur == OP_MESSAGE_INDEX:
            assert prev in {OP_CHUNK, OP_MESSAGE_INDEX}, 'message index records follow their chunk'
    data_end = parse(data[-1])
    if data_end.data_section_crc:
        assert data_end.data_section_crc == zlib.crc32(data_bytes[: data[-1].offset])


def test_footer_and_summary_offsets(bag: BagInfo) -> None:
    data_bytes, records = read_mcap(mcap_path(bag))
    _data, summary, offsets, footer_raw = split_sections(records)
    footer = parse(footer_raw)
    assert len(footer_raw.content) == 20
    assert summary, 'summary section present'
    assert footer.summary_start == summary[0].offset
    assert offsets, 'summary offset section present'
    assert footer.summary_offset_start == offsets[0].offset
    if footer.summary_crc:
        assert footer.summary_crc == zlib.crc32(data_bytes[footer.summary_start : footer_raw.offset + 9 + 16])

    groups: list[tuple[int, int, int]] = []
    for record in summary:
        if groups and groups[-1][0] == record.opcode:
            op, start, length = groups[-1]
            groups[-1] = (op, start, length + record.length)
        else:
            groups.append((record.opcode, record.offset, record.length))
    group_ops = [g[0] for g in groups]
    assert len(group_ops) == len(set(group_ops)), 'summary records grouped by opcode'
    assert group_ops == [
        op for op in (OP_SCHEMA, OP_CHANNEL, OP_CHUNK_INDEX, OP_METADATA_INDEX, OP_STATISTICS) if op in group_ops
    ]
    assert {OP_SCHEMA, OP_CHANNEL, OP_CHUNK_INDEX, OP_METADATA_INDEX, OP_STATISTICS} <= set(group_ops)
    parsed = [parse(r) for r in offsets]
    assert [(o.group_opcode, o.group_start, o.group_length) for o in parsed] == groups


# ---------------------------------------------------------------- schemas and channels


def test_schema_records(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, summary, _, _ = split_sections(records)
    expected = bag.expected_schemas()
    assert expected[2][2] == 'ros2idl'  # sanity of the spec itself
    summary_schemas = [parse(r) for r in summary if r.opcode == OP_SCHEMA]
    assert [(s.id, s.name, s.encoding, s.data.decode()) for s in summary_schemas] == expected
    in_data: list[rec.Schema] = []
    for record in data:
        if record.opcode == OP_CHUNK:
            in_data += [parse(x) for x in chunk_records(parse(record)) if x.opcode == OP_SCHEMA]
    assert [(s.id, s.name, s.encoding, s.data.decode()) for s in in_data] == expected


def test_channel_records(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, summary, _, _ = split_sections(records)
    schema_ids = {name: sid for sid, name, _, _ in bag.expected_schemas()}
    expected = [
        (
            idx + 1,
            schema_ids[spec.msgtype],
            spec.topic,
            'cdr',
            {'offered_qos_profiles': expected_qos_string(list(spec.qos), bag.version)},
        )
        for idx, spec in enumerate(bag.specs)
    ]
    assert 'history: keep_last' in expected[0][4]['offered_qos_profiles']
    assert expected[1][4] == {'offered_qos_profiles': '[]'}
    summary_channels = [parse(r) for r in summary if r.opcode == OP_CHANNEL]
    assert [(c.id, c.schema_id, c.topic, c.message_encoding, c.metadata) for c in summary_channels] == expected

    seen_schemas: set[int] = set()
    in_data: list[rec.Channel] = []
    for record in data:
        if record.opcode == OP_CHUNK:
            for inner in chunk_records(parse(record)):
                if inner.opcode == OP_SCHEMA:
                    seen_schemas.add(parse(inner).id)
                elif inner.opcode == OP_CHANNEL:
                    channel = parse(inner)
                    assert channel.schema_id in seen_schemas, 'schema precedes channel in the data stream'
                    in_data.append(channel)
    assert [(c.id, c.schema_id, c.topic, c.message_encoding, c.metadata) for c in in_data] == expected


# ---------------------------------------------------------------- messages and indexes


def test_message_records_in_write_order(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, _, _, _ = split_sections(records)
    messages = [m for _, m in messages_in_data_section(data)]
    assert [(m.channel_id, m.log_time, m.publish_time, m.sequence, m.data) for m in messages] == [
        (bag.cid(topic), ts, ts, 0, payload) for topic, ts, payload in bag.written
    ]


def test_chunk_records_and_chunk_indexes(bag: BagInfo) -> None:
    data_bytes, records = read_mcap(mcap_path(bag))
    data, summary, _, _ = split_sections(records)
    chunks = [r for r in data if r.opcode == OP_CHUNK]
    chunk_indexes = [parse(r) for r in summary if r.opcode == OP_CHUNK_INDEX]
    assert len(chunks) == len(chunk_indexes) >= 1
    expected_compression = 'zstd' if bag.compression == CompressionMode.STORAGE else ''
    for raw, index in zip(chunks, chunk_indexes):
        chunk = parse(raw)
        assert chunk.compression == expected_compression
        inner = chunk_records(chunk)
        log_times = [parse(x).log_time for x in inner if x.opcode == OP_MESSAGE]
        assert index.chunk_start_offset == raw.offset
        assert index.chunk_length == raw.length
        assert index.compression == chunk.compression
        assert index.compressed_size == len(chunk.data)
        assert index.uncompressed_size == chunk.uncompressed_size == sum(x.length for x in inner)
        if expected_compression:
            assert index.compressed_size < index.uncompressed_size
        else:
            assert index.compressed_size == index.uncompressed_size
        if log_times:
            assert (chunk.message_start_time, chunk.message_end_time) == (min(log_times), max(log_times))
            assert (index.message_start_time, index.message_end_time) == (min(log_times), max(log_times))
        # message index records follow the chunk immediately and are covered exactly
        pos = data.index(raw) + 1
        following: list[RawRecord] = []
        while pos < len(data) and data[pos].opcode == OP_MESSAGE_INDEX:
            following.append(data[pos])
            pos += 1
        assert index.message_index_length == sum(x.length for x in following)
        assert index.message_index_offsets == {parse(x).channel_id: x.offset for x in following}


def test_message_index_records(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, _, _, _ = split_sections(records)
    chunk_no = -1
    current: list[RawRecord] = []
    inner_by_offset: dict[int, tuple[int, int]] = {}
    indexed: list[tuple[int, int, int]] = []
    order_of_first_message: list[int] = []
    index_order: list[int] = []

    def flush() -> None:
        nonlocal indexed, order_of_first_message, index_order
        assert index_order == order_of_first_message, 'one index per channel, in order of first message'
        assert sorted(indexed) == sorted((cid, ts, off) for off, (cid, ts) in inner_by_offset.items())
        indexed, order_of_first_message, index_order = [], [], []

    for record in data:
        if record.opcode == OP_CHUNK:
            if chunk_no >= 0:
                flush()
            chunk_no += 1
            inner_by_offset = {}
            for inner in chunk_records(parse(record)):
                if inner.opcode == OP_MESSAGE:
                    msg = parse(inner)
                    inner_by_offset[inner.offset] = (msg.channel_id, msg.log_time)
                    if msg.channel_id not in order_of_first_message:
                        order_of_first_message.append(msg.channel_id)
        elif record.opcode == OP_MESSAGE_INDEX:
            mi = parse(record)
            assert mi.channel_id not in index_order, 'exactly one message index per channel per chunk'
            index_order.append(mi.channel_id)
            assert mi.records, 'message index records are non-empty'
            for log_time, offset in mi.records:
                assert inner_by_offset.get(offset) == (mi.channel_id, log_time), (
                    'message index entry points at a message record of that channel and log time'
                )
                indexed.append((mi.channel_id, log_time, offset))
    flush()
    assert chunk_no >= 0


def test_statistics_record(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, summary, _, _ = split_sections(records)
    stats = [parse(r) for r in summary if r.opcode == OP_STATISTICS]
    assert len(stats) == 1
    st = stats[0]
    counts = {c.id: 0 for c in bag.connections}
    for topic, _, _ in bag.written:
        counts[bag.cid(topic)] += 1
    assert st.message_count == len(bag.written)
    assert st.schema_count == len(bag.expected_schemas())
    assert st.channel_count == len(bag.connections)
    assert st.attachment_count == 0
    assert st.metadata_count == 1
    assert st.chunk_count == sum(1 for r in data if r.opcode == OP_CHUNK)
    assert st.message_start_time == min(ts for _, ts, _ in bag.written)
    assert st.message_end_time == max(ts for _, ts, _ in bag.written)
    assert st.channel_message_counts == counts
    assert counts[bag.cid('/silent')] == 0
    # channels must be present in the summary before the statistics record
    stat_pos = next(i for i, r in enumerate(summary) if r.opcode == OP_STATISTICS)
    assert all(r.opcode != OP_CHANNEL for r in summary[stat_pos:])


def test_metadata_record_and_index(bag: BagInfo) -> None:
    _, records = read_mcap(mcap_path(bag))
    data, summary, _, _ = split_sections(records)
    meta_raw = [r for r in data if r.opcode == OP_METADATA]
    assert len(meta_raw) == 1
    meta = parse(meta_raw[0])
    assert meta.name == 'rosbag2'
    assert set(meta.metadata) == {'serialized_metadata'}
    assert yaml_load(meta.metadata['serialized_metadata']) == bag_metadata(bag.path)
    indexes = [parse(r) for r in summary if r.opcode == OP_METADATA_INDEX]
    assert [(m.name, m.offset, m.length) for m in indexes] == [('rosbag2', meta_raw[0].offset, meta_raw[0].length)]


# ---------------------------------------------------------------- reference readers


def test_mcap_python_seeking_reader(bag: BagInfo) -> None:
    with mcap_path(bag).open('rb') as fh:
        reader = make_reader(fh, validate_crcs=True)
        assert isinstance(reader, SeekingReader)
        summary = reader.get_summary()
        assert summary is not None and summary.chunk_indexes
        got = [(ch.topic, m.log_time, m.data) for _, ch, m in reader.iter_messages()]
        assert got == bag.written_by_time()
        got = [(ch.topic, m.log_time, m.data) for _, ch, m in reader.iter_messages(topics=['/chatter', '/blob'])]
        assert got == [x for x in bag.written_by_time() if x[0] in {'/chatter', '/blob'}]
        got = [(ch.topic, m.log_time, m.data) for _, ch, m in reader.iter_messages(start_time=120, end_time=300)]
        assert got == [x for x in bag.written_by_time() if 120 <= x[1] < 300]
        assert {s.name for s in summary.schemas.values()} == {s.msgtype for s in bag.specs}
        assert {c.topic for c in summary.channels.values()} == {s.topic for s in bag.specs}


def test_mcap_python_streaming_reader_validates_crcs(bag: BagInfo) -> None:
    with mcap_path(bag).open('rb') as fh:
        reader = NonSeekingReader(fh, validate_crcs=True)
        got = [(ch.topic, m.log_time, m.data) for _, ch, m in reader.iter_messages(log_time_order=False)]
    assert got == bag.written


def test_rosbags_reader_roundtrip(bag: BagInfo) -> None:
    with Reader(bag.path) as reader:
        assert reader.message_count == len(bag.written)
        assert {(c.topic, c.msgtype, c.msgcount) for c in reader.connections} == {
            (s.topic, s.msgtype, sum(1 for t, _, _ in bag.written if t == s.topic)) for s in bag.specs
        }
        got = [(c.topic, ts, bytes(data)) for c, ts, data in reader.messages()]
    assert got == bag.written_by_time()
    with Reader(mcap_path(bag)) as reader:  # standalone storage file
        assert reader.message_count == len(bag.written)
        assert [(c.topic, ts, bytes(data)) for c, ts, data in reader.messages()] == bag.written_by_time()
        for conn in reader.connections:
            spec = next(s for s in bag.specs if s.topic == conn.topic)
            assert conn.ext == ConnectionExtRosbag2('cdr', list(spec.qos))
            encoding, msgdef, _ = bag.expected_definition(spec)
            fmt = MessageDefinitionFormat.IDL if encoding == 'ros2idl' else MessageDefinitionFormat.MSG
            assert conn.msgdef == MessageDefinition(fmt, msgdef)
    storage = McapReader(mcap_path(bag))
    storage.open()
    assert storage.chunks and storage.statistics is not None
    assert storage.statistics.message_count == len(bag.written)
    storage.close()


# ---------------------------------------------------------------- Writer front-end modes


@pytest.mark.parametrize('mode', [CompressionMode.FILE, CompressionMode.MESSAGE], ids=['file', 'message'])
def test_writer_compression_modes(tmp_path: Path, typestore, mode: CompressionMode) -> None:
    info = make_bag(tmp_path / 'bag', StoragePlugin.MCAP, typestore=typestore, compression=mode)
    names = sorted(x.name for x in info.path.iterdir())
    if mode == CompressionMode.FILE:
        assert names == ['bag.mcap.zstd', 'metadata.yaml']
        raw = zstandard.ZstdDecompressor().stream_reader((info.path / 'bag.mcap.zstd').open('rb')).read()
        assert raw[:8] == MAGIC and raw[-8:] == MAGIC
        target = tmp_path / 'plain.mcap'
        target.write_bytes(raw)
    else:
        assert names == ['bag.mcap', 'metadata.yaml']
        target = info.path / 'bag.mcap'
        _, records = read_mcap(target)
        data, _, _, _ = split_sections(records)
        decomp = zstandard.ZstdDecompressor().decompress
        payloads = [(m.channel_id, m.log_time, decomp(m.data)) for _, m in messages_in_data_section(data)]
        assert payloads == [(info.cid(t), ts, d) for t, ts, d in info.written]
    with target.open('rb') as fh:
        assert make_reader(fh).get_summary().statistics.message_count == len(info.written)
    with Reader(info.path) as reader:
        assert reader.compression_mode == mode.name.lower()
        assert [(c.topic, ts, bytes(d)) for c, ts, d in reader.messages()] == info.written_by_time()


def expected_chunk_partition(info: BagInfo) -> list[list[int]]:
    """Simulate the 1 MiB chunking rule from record sizes alone."""
    buffer = 0
    for spec in info.specs:
        encoding, msgdef, _ = info.expected_definition(spec)
        if spec == next(s for s in info.specs if s.msgtype == spec.msgtype):
            buffer += schema_record_size(spec.msgtype, encoding, msgdef)
        buffer += channel_record_size(spec.topic, 'cdr', expected_qos_string(list(spec.qos), info.version))
    chunks: list[list[int]] = [[]]
    for idx, (_, _, data) in enumerate(info.written):
        buffer += message_record_size(len(data))
        chunks[-1].append(idx)
        if buffer > CHUNK_THRESHOLD:
            buffer = 0
            chunks.append([])
    if not chunks[-1]:
        chunks.pop()
    return chunks


@pytest.mark.parametrize('compression', [None, CompressionMode.STORAGE], ids=['plain', 'storage-zstd'])
def test_chunking_threshold(tmp_path: Path, typestore, compression: CompressionMode | None) -> None:
    string = typestore.types['std_msgs/msg/String']
    big = bytes(typestore.serialize_cdr(string('x' * 70_000), 'std_msgs/msg/String'))
    small = b'\x00\x01\x00\x00\x2a\x00\x00\x00'
    messages: list[tuple[str, int, bytes]] = []
    for i in range(40):
        messages.append(('/chatter', 1_000 + i * 10, big))
        if i % 4 == 0:
            messages.append(('/blob', 1_005 + i * 10, small))
        if i % 7 == 0:
            messages.append(('/num', 1_003 + i * 10, small[:8]))
    specs = (CONNECTIONS[0], CONNECTIONS[1], CONNECTIONS[3])
    info = make_bag(tmp_path / 'bag', StoragePlugin.MCAP, typestore=typestore, compression=compression, connections=specs, messages=messages)
    partition = expected_chunk_partition(info)
    assert len(partition) == 3, 'test data sized for three chunks'
    _, records = read_mcap(mcap_path(info))
    data, summary, _, _ = split_sections(records)
    chunk_indexes = [parse(r) for r in summary if r.opcode == OP_CHUNK_INDEX]
    stats = next(parse(r) for r in summary if r.opcode == OP_STATISTICS)
    assert stats.chunk_count == len(chunk_indexes) == len(partition)
    per_chunk = messages_in_data_section(data)
    for chunk_no, (index, members) in enumerate(zip(chunk_indexes, partition)):
        got = [(m.channel_id, m.log_time, m.data) for no, m in per_chunk if no == chunk_no]
        assert got == [(info.cid(messages[i][0]), messages[i][1], messages[i][2]) for i in members]
        assert set(index.message_index_offsets) == {info.cid(messages[i][0]) for i in members}
        assert index.message_start_time == min(messages[i][1] for i in members)
        assert index.message_end_time == max(messages[i][1] for i in members)
        if compression == CompressionMode.STORAGE:
            assert index.compression == 'zstd' and index.compressed_size < index.uncompressed_size // 10
        else:
            assert index.compression == '' and index.compressed_size == index.uncompressed_size
    with Reader(info.path) as reader:
        assert [(c.topic, ts, bytes(d)) for c, ts, d in reader.messages()] == sorted(messages, key=lambda x: x[1])


# ---------------------------------------------------------------- edge cases


def test_empty_bag(tmp_path: Path) -> None:
    with Writer(tmp_path / 'empty', version=9, storage_plugin=StoragePlugin.MCAP):
        pass
    path = tmp_path / 'empty' / 'empty.mcap'
    _, records = read_mcap(path)
    data, summary, offsets, footer_raw = split_sections(records)
    assert [r.opcode for r in data] == [OP_METADATA, OP_DATA_END]
    assert [r.opcode for r in summary] == [OP_METADATA_INDEX, OP_STATISTICS]
    assert [parse(r).group_opcode for r in offsets] == [OP_METADATA_INDEX, OP_STATISTICS]
    assert parse(footer_raw).summary_start == summary[0].offset
    st = next(parse(r) for r in summary if r.opcode == OP_STATISTICS)
    assert (st.message_count, st.schema_count, st.channel_count, st.chunk_count, st.metadata_count) == (0, 0, 0, 0, 1)
    assert st.channel_message_counts == {}
    assert (st.message_start_time, st.message_end_time) == (SENTINEL_START, 0)
    with path.open('rb') as fh:
        summary_obj = make_reader(fh).get_summary()
        assert summary_obj is not None and summary_obj.statistics.message_count == 0
    with Reader(tmp_path / 'empty') as reader:
        assert reader.message_count == 0 and reader.connections == []


def test_schema_only_chunk_direct_storage_api(tmp_path: Path) -> None:
    bagdir = tmp_path / 'bag'
    bagdir.mkdir()
    storage = McapWriter(bagdir, CompressionMode.NONE)
    connection = Connection(
        1, '/blob', 'hidden_msgs/msg/Blob', MessageDefinition(MessageDefinitionFormat.IDL, IDL_MSGDEF), IDL_DIGEST, 0,
        ConnectionExtRosbag2('cdr', [LATCH]), None,
    )
    qos_string = expected_qos_string([LATCH], 9)
    storage.add_msgtype(connection)
    storage.add_connection(connection, qos_string)
    storage.close(9, 'version: 9\nstorage_identifier: mcap')
    path = bagdir / 'bag.mcap'
    _, records = read_mcap(path)
    data, summary, _, _ = split_sections(records)
    assert [r.opcode for r in data] == [OP_CHUNK, OP_METADATA, OP_DATA_END], 'schema/channel-only chunk, no message index'
    chunk = parse(data[0])
    inner = chunk_records(chunk)
    assert [r.opcode for r in inner] == [OP_SCHEMA, OP_CHANNEL]
    channel = parse(inner[1])
    assert channel.metadata == {'offered_qos_profiles': qos_string}, 'QoS string stored verbatim'
    assert parse(inner[0]).encoding == 'ros2idl'
    assert (chunk.message_start_time, chunk.message_end_time) == (SENTINEL_START, 0)
    index = next(parse(r) for r in summary if r.opcode == OP_CHUNK_INDEX)
    assert index.message_index_offsets == {} and index.message_index_length == 0
    assert (index.message_start_time, index.message_end_time) == (SENTINEL_START, 0)
    st = next(parse(r) for r in summary if r.opcode == OP_STATISTICS)
    assert st.channel_message_counts == {1: 0} and st.chunk_count == 1 and st.schema_count == 1
    meta = next(parse(r) for r in data if r.opcode == OP_METADATA)
    assert meta.metadata == {'serialized_metadata': 'version: 9\nstorage_identifier: mcap'}
    reader = McapReader(path)
    reader.open()
    assert len(reader.schemas) == 1 and len(reader.channels) == 1 and not list(reader.messages(reader.connections))
    reader.close()


# ---------------------------------------------------------------- rosbag2 fixtures


def fixture_dir(fmt: str, name: str) -> Path:
    return FIXTURES / fmt / name


def mcap_messages(path: Path) -> set[tuple[str, int, bytes]]:
    with path.open('rb') as fh:
        return {(ch.topic, m.log_time, m.data) for _, ch, m in make_reader(fh).iter_messages()}


def deserialized(bagdir: Path) -> list[tuple[str, int, object]]:
    out = []
    with AnyReader([bagdir], default_typestore=get_typestore(Stores.ROS2_JAZZY)) as reader:
        for conn, ts, raw in reader.messages():
            out.append((conn.topic, ts, to_plain(reader.deserialize(raw, conn.msgtype))))
    return sorted(out, key=lambda x: (x[1], x[0]))


@pytest.mark.parametrize(
    ('fmt', 'name'),
    [('sqlite3', 'cdr_test'), ('sqlite3', 'convert_a'), ('mcap', 'talker'), ('mcap', 'cdr_test')],
    ids=['sqlite3-cdr_test', 'sqlite3-convert_a', 'mcap-talker', 'mcap-cdr_test'],
)
def test_fixture_rewritten_as_mcap_matches_mcap_fixture(tmp_path: Path, fmt: str, name: str) -> None:
    """rosbags Reader (fixture, either storage) -> rosbags MCAP Writer -> mcap-python == the
    rosbag2/mcap-CLI produced MCAP fixture of the same recording."""
    src = fixture_dir(fmt, name)
    dst = tmp_path / name
    written, types = rewrite_bag(src, dst, StoragePlugin.MCAP, compression=CompressionMode.STORAGE)
    assert written
    produced = dst / f'{name}.mcap'
    reference = sorted(fixture_dir('mcap', name).glob('*.mcap'))[0]
    assert mcap_messages(produced) == set(written)
    assert mcap_messages(produced) >= mcap_messages(reference) if name == 'convert_a' else (
        mcap_messages(produced) == mcap_messages(reference)
    )
    with produced.open('rb') as fh:
        summary = make_reader(fh).get_summary()
    assert summary.statistics.message_count == len(written)
    assert {c.topic: summary.schemas[c.schema_id].name for c in summary.channels.values()} == types
    if name != 'convert_a':
        assert deserialized(dst) == deserialized(src)
        assert deserialized(dst) == deserialized(fixture_dir('mcap', name))


def test_rosbag2_recorded_mcap_fixture_roundtrip(tmp_path: Path) -> None:
    """Bag recorded by the ROS 2 rolling rosbag2 MCAP plugin (libmcap 1.4.0): mcap-python
    reads it, rosbags writes it, mcap-python and rosbags must read the copy identically."""
    name = 'bag_with_topics_and_service_events_and_action'
    src = fixture_dir('mcap', name) / f'{name}.mcap'
    dst = tmp_path / 'copy'
    written, types = rewrite_mcap_with_reference_reader(src, dst, compression=CompressionMode.STORAGE)
    with src.open('rb') as fh:
        source_summary = make_reader(fh).get_summary()
    assert len(written) == 218 == source_summary.statistics.message_count
    assert len(types) == len(source_summary.channels) == 17
    produced = dst / 'copy.mcap'
    assert mcap_messages(produced) == mcap_messages(src)
    with produced.open('rb') as fh:
        reader = make_reader(fh)
        summary = reader.get_summary()
        assert summary.statistics.message_count == 218
        assert summary.statistics.channel_count == 17
        assert summary.statistics.schema_count == len(source_summary.schemas) == 10
        assert {c.topic: summary.schemas[c.schema_id].name for c in summary.channels.values()} == types
        streamed = [(ch.topic, m.log_time, m.data) for _, ch, m in reader.iter_messages(log_time_order=False)]
        metas = list(reader.iter_metadata())
    assert streamed == written, 'file order equals write order'
    assert [m.name for m in metas] == ['rosbag2']
    with Reader(dst) as reader:
        assert reader.message_count == 218
        assert {c.topic: c.msgcount for c in reader.connections} == {
            t: sum(1 for x in written if x[0] == t) for t in types
        }
        assert [(c.topic, ts, bytes(d)) for c, ts, d in reader.messages()] == sorted(written, key=lambda x: x[1])


def test_convert_cli_writes_mcap(tmp_path: Path) -> None:
    src = fixture_dir('sqlite3', 'cdr_test')
    dst = tmp_path / 'converted'
    result = subprocess.run(
        [sys.executable, '-m', 'rosbags.convert', '--src', str(src), '--dst', str(dst), '--dst-storage', 'mcap'],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert bag_metadata(dst)['storage_identifier'] == 'mcap'
    assert mcap_messages(dst / 'converted.mcap') == mcap_messages(fixture_dir('mcap', 'cdr_test') / 'cdr_test_0.mcap')


# ---------------------------------------------------------------- independent implementation: mcap CLI


@pytest.fixture(scope='session')
def doctor_bags(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    typestore = get_typestore(Stores.LATEST)
    root = tmp_path_factory.mktemp('doctor')
    out: dict[str, Path] = {}
    out['plain'] = mcap_path(make_bag(root / 'plain', StoragePlugin.MCAP, typestore=typestore))
    out['storage-zstd'] = mcap_path(make_bag(root / 'zstd', StoragePlugin.MCAP, typestore=typestore, compression=CompressionMode.STORAGE))
    string = typestore.types['std_msgs/msg/String']
    big = bytes(typestore.serialize_cdr(string('y' * 90_000), 'std_msgs/msg/String'))
    msgs = [('/chatter', 10 + i, big) for i in range(30)]
    out['multichunk'] = mcap_path(make_bag(root / 'multi', StoragePlugin.MCAP, typestore=typestore, connections=(CONNECTIONS[0],), messages=msgs, compression=CompressionMode.STORAGE))
    with Writer(root / 'empty', version=9, storage_plugin=StoragePlugin.MCAP):
        pass
    out['empty'] = root / 'empty' / 'empty.mcap'
    with Writer(root / 'schemaonly', version=8, storage_plugin=StoragePlugin.MCAP) as writer:
        writer.add_connection('/chatter', 'std_msgs/msg/String', typestore=typestore)
    out['schema-only'] = root / 'schemaonly' / 'schemaonly.mcap'
    rewrite_bag(fixture_dir('sqlite3', 'cdr_test'), root / 'fixture', StoragePlugin.MCAP, compression=CompressionMode.STORAGE)
    out['fixture-rewrite'] = root / 'fixture' / 'fixture.mcap'
    name = 'bag_with_topics_and_service_events_and_action'
    rewrite_mcap_with_reference_reader(fixture_dir('mcap', name) / f'{name}.mcap', root / 'recorded')
    out['rosbag2-recorded-rewrite'] = root / 'recorded' / 'recorded.mcap'
    return out


@pytest.mark.parametrize(
    'which', ['plain', 'storage-zstd', 'multichunk', 'empty', 'schema-only', 'fixture-rewrite', 'rosbag2-recorded-rewrite']
)
def test_mcap_cli_doctor_accepts_file(doctor_bags: dict[str, Path], which: str) -> None:
    assert Path(MCAP_CLI).is_file(), f'mcap CLI missing at {MCAP_CLI}'
    path = doctor_bags[which]
    assert path.is_file()
    result = subprocess.run([MCAP_CLI, 'doctor', str(path)], capture_output=True, text=True, timeout=300, check=False)
    assert result.returncode == 0, f'mcap doctor failed:\n{result.stdout}\n{result.stderr}'
    assert 'Error' not in result.stdout and 'Error' not in result.stderr, result.stdout + result.stderr
    info = subprocess.run([MCAP_CLI, 'info', str(path)], capture_output=True, text=True, timeout=300, check=False)
    assert info.returncode == 0 and 'profile:     ros2' in info.stdout, info.stdout + info.stderr

"""Shared helpers for the hidden rosbags rosbag2 storage-writer tests.

The MCAP record walker below is deliberately independent of both rosbags and
mcap-python so that offsets and lengths are checked against the raw bytes.
Record *contents* are parsed with mcap-python (the reference implementation).
"""

from __future__ import annotations

import dataclasses
import io
import os
import struct
import zlib
from io import StringIO
from pathlib import Path
from typing import Any, NamedTuple

import lz4.frame
import numpy
import zstandard
from mcap import records as rec
from mcap.data_stream import ReadDataStream
from ruamel.yaml import YAML

from rosbags.interfaces import Qos
from rosbags.rosbag2.metadata import dump_qos_v8, dump_qos_v9

MAGIC = b'\x89MCAP0\r\n'
FIXTURES = Path(os.environ.get('ROSBAG2_FIXTURES', '/tests/fixtures'))
MCAP_CLI = os.environ.get('MCAP_CLI', '/usr/local/bin/mcap')
CHUNK_THRESHOLD = 2**20

OP_HEADER, OP_FOOTER, OP_SCHEMA, OP_CHANNEL, OP_MESSAGE = 0x01, 0x02, 0x03, 0x04, 0x05
OP_CHUNK, OP_MESSAGE_INDEX, OP_CHUNK_INDEX = 0x06, 0x07, 0x08
OP_STATISTICS, OP_METADATA, OP_METADATA_INDEX, OP_SUMMARY_OFFSET, OP_DATA_END = 0x0B, 0x0C, 0x0D, 0x0E, 0x0F

PARSERS = {
    OP_HEADER: rec.Header,
    OP_FOOTER: rec.Footer,
    OP_SCHEMA: rec.Schema,
    OP_CHANNEL: rec.Channel,
    OP_CHUNK: rec.Chunk,
    OP_MESSAGE_INDEX: rec.MessageIndex,
    OP_CHUNK_INDEX: rec.ChunkIndex,
    OP_STATISTICS: rec.Statistics,
    OP_METADATA: rec.Metadata,
    OP_METADATA_INDEX: rec.MetadataIndex,
    OP_SUMMARY_OFFSET: rec.SummaryOffset,
    OP_DATA_END: rec.DataEnd,
}

DECOMPRESS = {
    '': lambda data, _size: data,
    'zstd': lambda data, size: zstandard.ZstdDecompressor().decompress(data, max_output_size=size),
    'lz4': lambda data, _size: lz4.frame.decompress(data),
}


class RawRecord(NamedTuple):
    """One framed MCAP record."""

    opcode: int
    offset: int
    length: int
    content: bytes

    @property
    def end(self) -> int:
        return self.offset + self.length


def walk_records(data: bytes, *, start: int = 8, end: int | None = None) -> list[RawRecord]:
    """Split a byte string into framed records; validates framing exactly."""
    if end is None:
        end = len(data) - 8
    pos = start
    out: list[RawRecord] = []
    while pos < end:
        assert pos + 9 <= end, f'truncated record header at {pos}'
        opcode = data[pos]
        (length,) = struct.unpack_from('<Q', data, pos + 1)
        content = data[pos + 9 : pos + 9 + length]
        assert len(content) == length, f'record at {pos} runs past section end'
        out.append(RawRecord(opcode, pos, 9 + length, content))
        pos += 9 + length
    assert pos == end, f'records do not tile the section exactly ({pos} != {end})'
    return out


def parse(record: RawRecord) -> Any:
    """Parse record content with mcap-python."""
    stream = ReadDataStream(io.BytesIO(record.content))
    if record.opcode == OP_MESSAGE:
        return rec.Message.read(stream, len(record.content))
    return PARSERS[record.opcode].read(stream)


def read_mcap(path: Path) -> tuple[bytes, list[RawRecord]]:
    """Read a whole MCAP file and validate leading and trailing magic."""
    data = path.read_bytes()
    assert data[:8] == MAGIC, 'leading magic'
    assert data[-8:] == MAGIC, 'trailing magic'
    return data, walk_records(data)


def chunk_records(chunk: rec.Chunk) -> list[RawRecord]:
    """Decompress a chunk and split its records field."""
    raw = DECOMPRESS[chunk.compression](chunk.data, chunk.uncompressed_size)
    assert len(raw) == chunk.uncompressed_size
    if chunk.uncompressed_crc:
        assert zlib.crc32(raw) == chunk.uncompressed_crc
    return walk_records(raw, start=0, end=len(raw))


def yaml_load(text: str) -> Any:
    return YAML(typ='safe').load(text)


def yaml_dump(obj: Any) -> str:
    yaml = YAML(typ='safe')
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(obj, stream)
    return stream.getvalue().strip()


def expected_qos_string(qos: list[Qos], version: int) -> str:
    """Serialized QoS exactly as the rosbag2 Writer hands it to the storage."""
    dumped = dump_qos_v9(qos) if version >= 9 else dump_qos_v8(qos)
    return dumped if isinstance(dumped, str) else yaml_dump(dumped)


def bag_metadata(bagdir: Path) -> dict[str, Any]:
    return yaml_load((bagdir / 'metadata.yaml').read_text())['rosbag2_bagfile_information']


def to_plain(obj: Any) -> Any:
    """Convert deserialized rosbags messages to plain Python for comparison."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, numpy.ndarray):
        return obj.tolist()
    if isinstance(obj, (list, tuple)):
        return [to_plain(x) for x in obj]
    return obj


def record_size(content_len: int) -> int:
    return 9 + content_len


def string_size(text: str) -> int:
    return 4 + len(text.encode())


def schema_record_size(name: str, encoding: str, data: str) -> int:
    return record_size(2 + string_size(name) + string_size(encoding) + string_size(data))


def channel_record_size(topic: str, encoding: str, qos_string: str) -> int:
    metadata_len = string_size('offered_qos_profiles') + string_size(qos_string)
    return record_size(2 + 2 + string_size(topic) + string_size(encoding) + 4 + metadata_len)


def message_record_size(data_len: int) -> int:
    return record_size(2 + 4 + 8 + 8 + data_len)

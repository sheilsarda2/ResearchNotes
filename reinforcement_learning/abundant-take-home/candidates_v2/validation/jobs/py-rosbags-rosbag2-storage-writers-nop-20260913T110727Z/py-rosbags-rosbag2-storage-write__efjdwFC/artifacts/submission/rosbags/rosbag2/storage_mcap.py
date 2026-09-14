# Copyright 2020-2026 Ternaris
# SPDX-License-Identifier: Apache-2.0
"""Mcap storage."""

from __future__ import annotations

import heapq
import struct
from collections import defaultdict
from io import BytesIO
from struct import iter_unpack, unpack_from
from typing import TYPE_CHECKING, NamedTuple, cast

import zstandard
from lz4.frame import decompress as lz4_decompress  # type: ignore[import-untyped]

from rosbags.interfaces import (
    Connection,
    ConnectionExtRosbag2,
    MessageDefinition,
    MessageDefinitionFormat,
    Qos,
)

from .enums import CompressionMode
from .errors import ReaderError
from .metadata import ReaderMetadata, parse_qos

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable
    from pathlib import Path
    from typing import BinaryIO

    from rosbags.interfaces.typing import RPath

    Unpack = Callable[[bytes], 'tuple[int]']
    Unpack2 = Callable[[bytes], 'tuple[int, int]']
    Unpack4 = Callable[[bytes], 'tuple[int, int, int, int]']
    Unpack5 = Callable[[bytes], 'tuple[int, int, int, int, int]']


class Schema(NamedTuple):
    """Schema."""

    id: int
    name: str
    encoding: str
    data: str


class Channel(NamedTuple):
    """Channel."""

    id: int
    schema: str
    topic: str
    message_encoding: str
    metadata: bytes  # dict[str, str]


class Chunk(NamedTuple):
    """Chunk."""

    start_time: int
    end_time: int
    size: int
    crc: int
    compression: str
    records: bytes


class ChunkInfo(NamedTuple):
    """Chunk."""

    message_start_time: int
    message_end_time: int
    chunk_start_offset: int
    chunk_length: int
    message_index_offsets: dict[int, int]
    message_index_length: int
    compression: str
    compressed_size: int
    uncompressed_size: int
    channel_count: dict[int, int]


class Statistics(NamedTuple):
    """Statistics."""

    message_count: int
    schema_count: int
    channel_count: int
    attachement_count: int
    metadata_count: int
    chunk_count: int
    start_time: int
    end_time: int
    channel_message_counts: dict[int, int]


class Msg(NamedTuple):
    """Message wrapper."""

    timestamp: int
    offset: int
    connection: Connection | None
    data: bytes | None


MAXSIZE: int = 2**63 - 1

deserialize_uint16: Unpack = struct.Struct('<H').unpack
deserialize_uint32: Unpack = struct.Struct('<I').unpack
deserialize_uint64: Unpack = struct.Struct('<Q').unpack

deserialize_hq: Unpack2 = struct.Struct('<HQ').unpack
deserialize_qq: Unpack2 = struct.Struct('<QQ').unpack
deserialize_qqqi: Unpack4 = struct.Struct('<QQQI').unpack
deserialize_qqqq: Unpack4 = struct.Struct('<QQQQ').unpack
deserialize_hiqq: Unpack4 = struct.Struct('<HIQQ').unpack
deserialize_qhiqq: Unpack5 = struct.Struct('<QHIQQ').unpack


def read_sized(bio: BinaryIO) -> bytes:
    """Read one record."""
    return bio.read(deserialize_uint64(bio.read(8))[0])


def skip_sized(bio: BinaryIO) -> None:
    """Read one record."""
    _ = bio.seek(deserialize_uint64(bio.read(8))[0], 1)


def read_bytes(bio: BinaryIO) -> bytes:
    """Read string."""
    return bio.read(deserialize_uint32(bio.read(4))[0])


def read_string(bio: BinaryIO) -> str:
    """Read string."""
    return bio.read(deserialize_uint32(bio.read(4))[0]).decode()


DECOMPRESSORS: dict[str, Callable[[bytes, int], bytes]] = {
    '': lambda x, _: x,
    'lz4': lambda x, _: lz4_decompress(x),
    'zstd': zstandard.ZstdDecompressor().decompress,
}


def msgsrc(
    chunk: ChunkInfo,
    channel_map: dict[int, Connection],
    start: int,
    stop: int,
    bio: BinaryIO,
) -> Generator[Msg, None, None]:
    """Yield messages from chunk in time order."""
    yield Msg(chunk.message_start_time, 0, None, None)

    _ = bio.seek(chunk.chunk_start_offset + 9 + 40 + len(chunk.compression))
    compressed_data = bio.read(chunk.compressed_size)
    subio = BytesIO(DECOMPRESSORS[chunk.compression](compressed_data, chunk.uncompressed_size))

    messages: list[Msg] = []
    while (offset := subio.tell()) < chunk.uncompressed_size:
        op_ = ord(subio.read(1))
        if op_ == 0x05:
            recio = BytesIO(read_sized(subio))
            channel_id, _, log_time, _ = deserialize_hiqq(recio.read(22))
            if start <= log_time < stop and channel_id in channel_map:
                messages.append(
                    Msg(
                        log_time,
                        chunk.chunk_start_offset + offset,
                        channel_map[channel_id],
                        recio.read(),
                    ),
                )
        else:
            skip_sized(subio)

    yield from sorted(messages, key=lambda x: x.timestamp)


class McapReader:
    """Mcap format reader."""

    def __init__(self, path: RPath) -> None:
        """Initialize."""
        self.path = path
        self.bio: BinaryIO | None = None
        self.data_start = 0
        self.data_end = 0
        self.schemas: dict[int, Schema] = {}
        self.channels: dict[int, Channel] = {}
        self.chunks: list[ChunkInfo] = []
        self.statistics: Statistics | None = None
        self.connections: list[Connection] = []
        self.metadata = ReaderMetadata(0, 2**63 - 1, 0, 0, None, None, None, None)

    def open(self) -> None:
        """Open MCAP."""
        try:
            self.bio = self.path.open('rb')
        except OSError as err:
            msg = f'Could not open file {str(self.path)!r}: {err.strerror}.'
            raise ReaderError(msg) from err

        magic = self.bio.read(8)
        if not magic:
            msg = f'File {str(self.path)!r} seems to be empty.'
            raise ReaderError(msg)

        if magic != b'\x89MCAP0\r\n':
            msg = 'File magic is invalid.'
            raise ReaderError(msg)

        op_ = ord(self.bio.read(1))
        if op_ != 0x01:
            msg = 'Unexpected record.'
            raise ReaderError(msg)

        recio = BytesIO(read_sized(self.bio))
        profile = read_string(recio)
        if profile != 'ros2':
            msg = 'Profile is not ros2.'
            raise ReaderError(msg)
        self.data_start = self.bio.tell()

        _ = self.bio.seek(-37, 2)
        footer_start = self.bio.tell()
        data = self.bio.read()
        magic = data[-8:]
        if magic != b'\x89MCAP0\r\n':
            msg = 'File end magic is invalid.'
            raise ReaderError(msg)

        assert len(data) == 37
        assert data[0:9] == b'\x02\x14\x00\x00\x00\x00\x00\x00\x00', data[0:9]

        (summary_start,) = deserialize_uint64(data[9:17])
        if summary_start:
            self.data_end = summary_start
            self.read_index()
            if self.statistics:
                if not self.schemas:
                    self.meta_scan()
            elif self.chunks:
                message_count = sum(sum(x.channel_count.values()) for x in self.chunks)
                start_time = min(x.message_start_time for x in self.chunks)
                end_time = max(x.message_end_time for x in self.chunks)
                duration = end_time - start_time
                cstats: dict[int, int] = defaultdict(int)
                for chunk in self.chunks:
                    for cid, count in chunk.channel_count.items():
                        cstats[cid] += count
                self.statistics = Statistics(
                    message_count,
                    len(self.schemas),
                    len(self.channels),
                    0,
                    0,
                    len(self.chunks),
                    start_time,
                    end_time,
                    cstats,
                )
            else:
                self.meta_scan()
        else:
            self.data_end = footer_start
            self.meta_scan()

        def get_msgdef(name: str) -> MessageDefinition:
            """Get message definition for name."""
            fmtmap = {
                'ros2msg': MessageDefinitionFormat.MSG,
                'ros2idl': MessageDefinitionFormat.IDL,
                'omgidl': MessageDefinitionFormat.IDL,
                '': MessageDefinitionFormat.NONE,
            }
            if msgtype := next((x for x in self.schemas.values() if x.name == name), None):
                return MessageDefinition(fmtmap[msgtype.encoding], msgtype.data)
            return MessageDefinition(MessageDefinitionFormat.NONE, '')

        def get_qos(metadata: bytes) -> list[Qos]:
            bio = BytesIO(metadata)
            while bio.tell() < len(metadata):
                key = read_string(bio)
                value = read_string(bio)
                if key == 'offered_qos_profiles':
                    return parse_qos(value)
            return []

        assert self.statistics
        self.connections = [
            Connection(
                x.id,
                x.topic,
                x.schema,
                get_msgdef(x.schema),
                '',
                self.statistics.channel_message_counts.get(x.id, 0),
                ConnectionExtRosbag2(
                    x.message_encoding,
                    get_qos(x.metadata),
                ),
                self,
            )
            for x in self.channels.values()
        ]

        message_count = self.statistics.message_count
        start_time = self.statistics.start_time
        end_time = self.statistics.end_time
        duration = end_time - start_time

        self.metadata = self.metadata._replace(
            duration=duration + 1,
            start_time=start_time,
            end_time=end_time + 1,
            message_count=message_count,
        )

    def read_index(self) -> None:
        """Read index from file."""
        bio = self.bio
        assert bio

        schemas = self.schemas
        channels = self.channels
        chunks = self.chunks

        _ = bio.seek(self.data_end)
        while True:
            op_ = ord(bio.read(1))

            if op_ in {0x02, 0x0E}:
                break

            if op_ == 0x03:
                _ = bio.seek(8, 1)
                (key,) = deserialize_uint16(bio.read(2))
                schemas[key] = Schema(key, read_string(bio), read_string(bio), read_string(bio))

            elif op_ == 0x04:
                _ = bio.seek(8, 1)
                (key,) = deserialize_uint16(bio.read(2))
                schema_name = schemas.get(
                    deserialize_uint16(bio.read(2))[0],
                    Schema(0, '__schemaless__', 'cdr', ''),
                ).name
                channels[key] = Channel(
                    key,
                    schema_name,
                    read_string(bio),
                    read_string(bio),
                    read_bytes(bio),
                )

            elif op_ == 0x08:
                _ = bio.seek(8, 1)
                chunk = ChunkInfo(
                    *deserialize_qqqq(bio.read(32)),
                    {
                        x[0]: x[1]
                        for x in cast(
                            'Iterable[tuple[int, int]]',
                            iter_unpack('<HQ', bio.read(deserialize_uint32(bio.read(4))[0])),
                        )
                    },
                    *deserialize_uint64(bio.read(8)),
                    read_string(bio),
                    *deserialize_qq(bio.read(16)),
                    {},
                )
                offset_channel = sorted((v, k) for k, v in chunk.message_index_offsets.items())
                offsets = [
                    *[x[0] for x in offset_channel],
                    chunk.chunk_start_offset + chunk.chunk_length + chunk.message_index_length,
                ]
                chunk.channel_count.update(
                    {
                        x[1]: count // 16
                        for x, y, z in zip(offset_channel, offsets[1:], offsets, strict=False)
                        if (count := y - z - 15)
                    },
                )
                chunks.append(chunk)

            elif op_ == 0x0A:
                skip_sized(bio)

            elif op_ == 0x0B:
                _ = bio.seek(8, 1)
                self.statistics = Statistics(
                    *cast(
                        'tuple[int, int, int, int, int, int, int ,int]',
                        unpack_from('<QHIIIIQQ', bio.read(42), 0),
                    ),
                    dict(
                        deserialize_hq(bio.read(10))
                        for _ in range(deserialize_uint32(bio.read(4))[0] // 10)
                    ),
                )

            elif op_ == 0x0D:
                skip_sized(bio)

            else:
                skip_sized(bio)

    def close(self) -> None:
        """Close MCAP."""
        assert self.bio
        self.bio.close()
        self.bio = None

    def meta_scan(self) -> None:
        """Generate metadata by scanning through file."""
        assert self.bio
        bio = self.bio
        bio_size = self.data_end
        _ = bio.seek(self.data_start)

        msgcount = 0
        start_time = 2**63 - 1
        end_time = 0
        nchunks = 0
        cstats: dict[int, int] = defaultdict(int)

        schemas = self.schemas
        channels = self.channels

        while bio.tell() < bio_size:
            op_ = ord(bio.read(1))

            if op_ == 0x03:
                _ = bio.seek(8, 1)
                (key,) = deserialize_uint16(bio.read(2))
                schemas[key] = Schema(key, read_string(bio), read_string(bio), read_string(bio))
            elif op_ == 0x04:
                _ = bio.seek(8, 1)
                (key,) = deserialize_uint16(bio.read(2))
                schema_name = schemas.get(
                    deserialize_uint16(bio.read(2))[0],
                    Schema(0, '__schemaless__', 'cdr', ''),
                ).name
                channels[key] = Channel(
                    key,
                    schema_name,
                    read_string(bio),
                    read_string(bio),
                    read_bytes(bio),
                )
            elif op_ == 0x05:
                (size,) = deserialize_uint64(bio.read(8))
                (cid,) = deserialize_uint16(bio.read(2))
                _ = bio.seek(4, 1)
                (timestamp,) = deserialize_uint64(bio.read(8))
                msgcount += 1
                start_time = min(timestamp, start_time)
                end_time = max(timestamp, end_time)
                cstats[cid] += 1
                _ = bio.seek(size - 14, 1)
            elif op_ == 0x06:
                _ = bio.seek(8, 1)
                _, _, uncompressed_size, _ = deserialize_qqqi(bio.read(28))
                compression = read_string(bio)
                (compressed_size,) = deserialize_uint64(bio.read(8))
                bio = BytesIO(
                    DECOMPRESSORS[compression](bio.read(compressed_size), uncompressed_size),
                )
                bio_size = uncompressed_size
                nchunks += 1
            else:
                skip_sized(bio)

            if bio.tell() == bio_size and bio != self.bio:
                bio = self.bio
                bio_size = self.data_end

        self.statistics = Statistics(
            msgcount,
            len(schemas),
            len(channels),
            0,
            0,
            nchunks,
            start_time,
            end_time,
            cstats,
        )

    def messages_scan(
        self,
        connections: Iterable[Connection],
        start: int | None = None,
        stop: int | None = None,
    ) -> Generator[tuple[Connection, int, bytes], None, None]:
        """Read messages by scanning whole bag."""
        assert self.bio
        bio = self.bio
        bio_size = self.data_end
        _ = bio.seek(self.data_start)

        cmap = {x.id: x for x in connections}

        if start is None:
            start = 0
        if stop is None:
            stop = MAXSIZE

        while bio.tell() < bio_size:
            op_ = ord(bio.read(1))

            if op_ == 0x05:
                size, channel_id, _, timestamp, _ = deserialize_qhiqq(bio.read(30))
                data = bio.read(size - 22)
                if start <= timestamp < stop and channel_id in cmap:
                    yield cmap[channel_id], timestamp, data
            elif op_ == 0x06:
                (size,) = deserialize_uint64(bio.read(8))
                start_time, end_time, uncompressed_size, _ = deserialize_qqqi(bio.read(28))
                if start < end_time and start_time < stop:
                    compression = read_string(bio)
                    (compressed_size,) = deserialize_uint64(bio.read(8))
                    bio = BytesIO(
                        DECOMPRESSORS[compression](bio.read(compressed_size), uncompressed_size),
                    )
                    bio_size = uncompressed_size
                else:
                    _ = bio.seek(size - 28, 1)
            else:
                skip_sized(bio)

            if bio.tell() == bio_size and bio != self.bio:
                bio = self.bio
                bio_size = self.data_end

    def messages(
        self,
        connections: Iterable[Connection],
        start: int | None = None,
        stop: int | None = None,
    ) -> Generator[tuple[Connection, int, bytes], None, None]:
        """Read messages from bag.

        Args:
            connections: Iterable with connections to filter for.
            start: Yield only messages at or after this timestamp (ns).
            stop: Yield only messages before this timestamp (ns).

        Yields:
            tuples of connection, timestamp (ns), and rawdata.

        """
        assert self.bio

        if not self.chunks:
            yield from self.messages_scan(connections, start, stop)
            return

        channel_map = {  # pragma: no branch
            cid: conn
            for conn in connections
            if (
                cid := next(
                    (
                        cid
                        for cid, x in self.channels.items()
                        if x.schema == conn.msgtype and x.topic == conn.topic
                    ),
                    None,
                )
            )
            is not None
        }

        chunks = [
            msgsrc(
                x,
                channel_map,
                start or x.message_start_time,
                stop or x.message_end_time + 1,
                self.bio,
            )
            for x in self.chunks
            if (start is None or start < x.message_end_time)
            and (stop is None or x.message_start_time < stop)
            and (any(x.channel_count.get(cid, 0) for cid in channel_map))
        ]

        for timestamp, offset, connection, data in heapq.merge(*chunks):
            if not offset:
                continue
            assert connection
            assert data
            yield connection, timestamp, data


class McapWriter:
    """Mcap Storage Writer."""

    def __init__(self, path: Path, compression: CompressionMode) -> None:
        """Initialize mcap storage.

        Args:
            path: Bag directory, the storage file is created inside it.
            compression: Compression mode requested by the rosbag2 writer.

        """
        raise NotImplementedError

    def add_msgtype(self, connection: Connection) -> None:
        """Add a msgtype.

        Args:
            connection: Connection.

        """
        raise NotImplementedError

    def add_connection(self, connection: Connection, offered_qos_profiles: str) -> None:
        """Add a connection.

        Args:
            connection: Connection.
            offered_qos_profiles: Serialized QoS profiles.

        """
        raise NotImplementedError

    def write(self, connection: Connection, timestamp: int, data: bytes | memoryview) -> None:
        """Write message to rosbag2.

        Args:
            connection: Connection to write message to.
            timestamp: Message timestamp (ns).
            data: Serialized message data.

        """
        raise NotImplementedError

    def close(self, version: int, metadata: str) -> None:
        """Close rosbag2 after writing.

        Args:
            version: Rosbag2 metadata version.
            metadata: Serialized rosbag2 metadata.

        """
        raise NotImplementedError

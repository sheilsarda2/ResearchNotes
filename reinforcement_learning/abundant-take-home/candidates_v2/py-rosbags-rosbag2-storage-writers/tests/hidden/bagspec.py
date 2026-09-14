"""Bag builders shared by the hidden tests (public rosbags API only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcap.reader import make_reader

from rosbags.interfaces import (
    Connection,
    MessageDefinitionFormat,
    Qos,
    QosDurability,
    QosHistory,
    QosLiveliness,
    QosReliability,
    QosTime,
)
from rosbags.rosbag2 import CompressionFormat, CompressionMode, Reader, StoragePlugin, Writer
from rosbags.rosbag2.metadata import parse_qos
from rosbags.typesys.msg import get_types_from_msg
from rosbags.typesys.store import Typestore

IDL_MSGDEF = (
    '=' * 80
    + '\nIDL: hidden_msgs/msg/Blob\n'
    + 'module hidden_msgs {\n  module msg {\n    struct Blob {\n      int32 value;\n    };\n  };\n};\n'
)
IDL_DIGEST = 'RIHS01_' + 'ab' * 32
FALLBACK_DIGEST = 'RIHS01_' + '00' * 32

LATCH = Qos(
    QosHistory.KEEP_LAST,
    10,
    QosReliability.RELIABLE,
    QosDurability.TRANSIENT_LOCAL,
    QosTime(2147483647, 4294967295),
    QosTime(2147483647, 4294967295),
    QosLiveliness.AUTOMATIC,
    QosTime(2147483647, 4294967295),
    avoid_ros_namespace_conventions=False,
)


@dataclass(frozen=True)
class ConnSpec:
    topic: str
    msgtype: str
    qos: tuple[Qos, ...] = ()
    msgdef: str | None = None
    digest: str | None = None


# Five connections: two share a msgtype (schema dedup), one uses an IDL definition,
# one never receives a message.
CONNECTIONS = (
    ConnSpec('/chatter', 'std_msgs/msg/String', (LATCH,)),
    ConnSpec('/num', 'std_msgs/msg/Int32'),
    ConnSpec('/chatter2', 'std_msgs/msg/String'),
    ConnSpec('/blob', 'hidden_msgs/msg/Blob', (LATCH,), IDL_MSGDEF, IDL_DIGEST),
    ConnSpec('/silent', 'std_msgs/msg/Empty'),
)
# rosbags' own sqlite3 reader only accepts ros2msg definitions, so reader round trips
# through sqlite3 use the subset without the IDL-defined type.
CONNECTIONS_MSG_ONLY = tuple(s for s in CONNECTIONS if s.msgdef is None)


def standard_messages(typestore: Typestore) -> list[tuple[str, int, bytes]]:
    """Write order is deliberately not time order; two channels share timestamp 300."""
    string = typestore.types['std_msgs/msg/String']
    int32 = typestore.types['std_msgs/msg/Int32']

    def ser(msg: object) -> bytes:
        return bytes(typestore.serialize_cdr(msg, msg.__msgtype__))  # type: ignore[attr-defined]

    return [
        ('/chatter', 100, ser(string('hello 0'))),
        ('/num', 50, ser(int32(0))),
        ('/chatter', 200, ser(string('hello 1'))),
        ('/chatter2', 150, ser(string('other 0'))),
        ('/blob', 120, b'\x00\x01\x00\x00\x2a\x00\x00\x00'),
        ('/num', 300, ser(int32(1))),
        ('/chatter', 300, ser(string('hello 2'))),
        ('/chatter2', 300, ser(string('other 1'))),
        ('/blob', 90, b'\x00\x01\x00\x00\x07\x00\x00\x00'),
        ('/num', 310, ser(int32(2))),
        ('/chatter', 95, ser(string('hello 3'))),
        ('/chatter', 400, ser(string('x' * 3000))),
    ]


@dataclass
class BagInfo:
    path: Path
    version: int
    storage: StoragePlugin
    compression: CompressionMode | None
    connections: list[Connection]
    written: list[tuple[str, int, bytes]]
    specs: tuple[ConnSpec, ...]
    typestore: Typestore

    @property
    def name(self) -> str:
        return self.path.name

    def conn(self, topic: str) -> Connection:
        return next(x for x in self.connections if x.topic == topic)

    def cid(self, topic: str) -> int:
        return self.conn(topic).id

    def expected_definition(self, spec: ConnSpec) -> tuple[str, str, str]:
        """(encoding, definition text, digest) the storage must record for a msgtype."""
        if spec.msgdef is not None:
            assert spec.digest is not None
            return 'ros2idl', spec.msgdef, spec.digest
        msgdef, _ = self.typestore.generate_msgdef(spec.msgtype, ros_version=2)
        return 'ros2msg', msgdef, self.typestore.hash_rihs01(spec.msgtype)

    def expected_schemas(self) -> list[tuple[int, str, str, str]]:
        """Schemas in order of first use, ids from 1."""
        out: list[tuple[int, str, str, str]] = []
        seen: set[str] = set()
        for spec in self.specs:
            if spec.msgtype in seen:
                continue
            seen.add(spec.msgtype)
            encoding, msgdef, _ = self.expected_definition(spec)
            out.append((len(out) + 1, spec.msgtype, encoding, msgdef))
        return out

    def written_by_time(self) -> list[tuple[str, int, bytes]]:
        return sorted(self.written, key=lambda x: x[1])


def make_bag(
    path: Path,
    storage: StoragePlugin,
    *,
    typestore: Typestore,
    version: int = 9,
    compression: CompressionMode | None = None,
    connections: tuple[ConnSpec, ...] = CONNECTIONS,
    messages: list[tuple[str, int, bytes]] | None = None,
) -> BagInfo:
    """Write a bag through the public rosbag2 Writer."""
    writer = Writer(path, version=version, storage_plugin=storage)  # type: ignore[arg-type]
    if compression is not None:
        writer.set_compression(compression, CompressionFormat.ZSTD)
    conns: dict[str, Connection] = {}
    with writer:
        for spec in connections:
            if spec.msgdef is None:
                conns[spec.topic] = writer.add_connection(
                    spec.topic,
                    spec.msgtype,
                    typestore=typestore,
                    offered_qos_profiles=spec.qos,
                )
            else:
                conns[spec.topic] = writer.add_connection(
                    spec.topic,
                    spec.msgtype,
                    msgdef=spec.msgdef,
                    rihs01=spec.digest,
                    offered_qos_profiles=spec.qos,
                )
        if messages is None:
            topics = {s.topic for s in connections}
            msgs = [m for m in standard_messages(typestore) if m[0] in topics]
        else:
            msgs = messages
        for topic, timestamp, data in msgs:
            writer.write(conns[topic], timestamp, data)
    return BagInfo(
        path,
        version,
        storage,
        compression,
        list(conns.values()),
        list(msgs),
        connections,
        typestore,
    )


def rihs01_digest(msgtype: str, msgdef: str, fmt: MessageDefinitionFormat, digest: str) -> str:
    """Reuse a known digest, else derive RIHS01 from the .msg definition like rosbags' reader."""
    if digest:
        return digest
    if fmt == MessageDefinitionFormat.MSG and msgdef:
        store = Typestore()
        store.register(get_types_from_msg(msgdef, msgtype))
        return store.hash_rihs01(msgtype)
    return FALLBACK_DIGEST


def rewrite_bag(
    src: Path,
    dst: Path,
    storage: StoragePlugin,
    *,
    version: int = 9,
    compression: CompressionMode | None = None,
) -> tuple[list[tuple[str, int, bytes]], dict[str, str]]:
    """Copy a rosbag2 directory bag into a new bag with the given storage plugin.

    Returns the (topic, timestamp, data) sequence read from the source and a
    topic -> msgtype map.
    """
    written: list[tuple[str, int, bytes]] = []
    types: dict[str, str] = {}
    with Reader(src) as reader:
        writer = Writer(dst, version=version, storage_plugin=storage)  # type: ignore[arg-type]
        if compression is not None:
            writer.set_compression(compression, CompressionFormat.ZSTD)
        with writer:
            cmap: dict[int, Connection] = {}
            for conn in reader.connections:
                ext = conn.ext
                cmap[conn.id] = writer.add_connection(
                    conn.topic,
                    conn.msgtype,
                    msgdef=conn.msgdef.data,
                    rihs01=rihs01_digest(conn.msgtype, conn.msgdef.data, conn.msgdef.format, conn.digest),
                    serialization_format=ext.serialization_format,  # type: ignore[union-attr]
                    offered_qos_profiles=ext.offered_qos_profiles,  # type: ignore[union-attr]
                )
                types[conn.topic] = conn.msgtype
            for conn, timestamp, data in reader.messages():
                data = bytes(data)
                writer.write(cmap[conn.id], timestamp, data)
                written.append((conn.topic, timestamp, data))
    return written, types


def rewrite_mcap_with_reference_reader(
    src: Path,
    dst: Path,
    *,
    version: int = 9,
    compression: CompressionMode | None = None,
) -> tuple[list[tuple[str, int, bytes]], dict[str, str]]:
    """Copy a single .mcap file into a rosbags MCAP bag, reading the source with mcap-python.

    Used for fixtures that rosbags' reader cannot open itself (schema encodings other
    than ros2msg/ros2idl). Returns (topic, log_time, data) in file order and topic -> schema name.
    """
    written: list[tuple[str, int, bytes]] = []
    types: dict[str, str] = {}
    with src.open('rb') as fh:
        reader = make_reader(fh)
        summary = reader.get_summary()
        assert summary is not None
        writer = Writer(dst, version=version, storage_plugin=StoragePlugin.MCAP)
        if compression is not None:
            writer.set_compression(compression, CompressionFormat.ZSTD)
        with writer:
            cmap: dict[int, Connection] = {}
            for cid, channel in sorted(summary.channels.items()):
                schema = summary.schemas.get(channel.schema_id)
                name = schema.name if schema else ''
                msgdef = schema.data.decode() if schema and schema.encoding in {'ros2msg', 'ros2idl'} else ''
                qos = parse_qos(channel.metadata.get('offered_qos_profiles', ''))
                cmap[cid] = writer.add_connection(
                    channel.topic,
                    name,
                    msgdef=msgdef,
                    rihs01=channel.metadata.get('topic_type_hash') or FALLBACK_DIGEST,
                    serialization_format=channel.message_encoding,
                    offered_qos_profiles=qos,
                )
                types[channel.topic] = name
            for _, channel, message in reader.iter_messages(log_time_order=False):
                writer.write(cmap[channel.id], message.log_time, message.data)
                written.append((channel.topic, message.log_time, message.data))
    return written, types

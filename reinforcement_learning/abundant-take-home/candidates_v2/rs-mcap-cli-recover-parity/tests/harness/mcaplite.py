"""Minimal, dependency-free MCAP byte-level toolkit used by the hidden harness.

This is deliberately independent of both the Rust and Go implementations. It only
understands the record framing from the MCAP specification (opcode + u64 length),
the Chunk record header layout, and the string/length encodings needed to build
corrupted fixtures and to inspect output chunk compression.

Spec reference: website/docs/spec/index.md (Records; Chunk op=0x06; Serialization).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional

MAGIC = b"\x89MCAP0\r\n"
OPCODE_LEN_SIZE = 9

OP_HEADER = 0x01
OP_FOOTER = 0x02
OP_SCHEMA = 0x03
OP_CHANNEL = 0x04
OP_MESSAGE = 0x05
OP_CHUNK = 0x06
OP_MESSAGE_INDEX = 0x07
OP_CHUNK_INDEX = 0x08
OP_ATTACHMENT = 0x09
OP_ATTACHMENT_INDEX = 0x0A
OP_STATISTICS = 0x0B
OP_METADATA = 0x0C
OP_METADATA_INDEX = 0x0D
OP_SUMMARY_OFFSET = 0x0E
OP_DATA_END = 0x0F

OP_NAMES = {
    OP_HEADER: "header", OP_FOOTER: "footer", OP_SCHEMA: "schema", OP_CHANNEL: "channel",
    OP_MESSAGE: "message", OP_CHUNK: "chunk", OP_MESSAGE_INDEX: "message_index",
    OP_CHUNK_INDEX: "chunk_index", OP_ATTACHMENT: "attachment",
    OP_ATTACHMENT_INDEX: "attachment_index", OP_STATISTICS: "statistics",
    OP_METADATA: "metadata", OP_METADATA_INDEX: "metadata_index",
    OP_SUMMARY_OFFSET: "summary_offset", OP_DATA_END: "data_end",
}


@dataclass
class Rec:
    offset: int      # offset of the opcode byte in the file
    opcode: int
    length: int      # body length as declared
    body: bytes

    @property
    def end(self) -> int:
        return self.offset + OPCODE_LEN_SIZE + self.length

    @property
    def raw(self) -> bytes:
        return bytes([self.opcode]) + struct.pack("<Q", self.length) + self.body


@dataclass
class ChunkHeader:
    message_start_time: int
    message_end_time: int
    uncompressed_size: int
    uncompressed_crc: int
    compression: str
    compressed_size: int
    header_len: int  # bytes consumed by the header inside the chunk body

    @property
    def crc_offset_in_body(self) -> int:
        return 8 + 8 + 8


def frame(opcode: int, body: bytes) -> bytes:
    return bytes([opcode]) + struct.pack("<Q", len(body)) + body


def mcap_string(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<I", len(b)) + b


def parse_records(data: bytes, *, tolerate_truncation: bool = True) -> List[Rec]:
    """Walk top-level records. Stops at the end magic if present. With
    tolerate_truncation, a final partial record is dropped silently."""
    if data[:8] != MAGIC:
        raise ValueError("bad start magic")
    end = len(data)
    if len(data) >= 16 and data[-8:] == MAGIC:
        end = len(data) - 8
    recs: List[Rec] = []
    off = 8
    while off + OPCODE_LEN_SIZE <= end:
        op = data[off]
        ln = struct.unpack_from("<Q", data, off + 1)[0]
        if off + OPCODE_LEN_SIZE + ln > end:
            if tolerate_truncation:
                break
            raise ValueError(f"truncated record at {off}")
        recs.append(Rec(off, op, ln, bytes(data[off + OPCODE_LEN_SIZE: off + OPCODE_LEN_SIZE + ln])))
        off += OPCODE_LEN_SIZE + ln
    return recs


def parse_chunk_header(body: bytes) -> ChunkHeader:
    st, et, usz, crc, clen = struct.unpack_from("<QQQII", body, 0)
    comp = body[32:32 + clen].decode("utf-8", errors="replace")
    csz = struct.unpack_from("<Q", body, 32 + clen)[0]
    return ChunkHeader(st, et, usz, crc, comp, csz, 32 + clen + 8)


def build_chunk_body(h: ChunkHeader, payload: bytes, *, compression: Optional[str] = None,
                     uncompressed_crc: Optional[int] = None, uncompressed_size: Optional[int] = None,
                     compressed_size: Optional[int] = None) -> bytes:
    comp = h.compression if compression is None else compression
    crc = h.uncompressed_crc if uncompressed_crc is None else uncompressed_crc
    usz = h.uncompressed_size if uncompressed_size is None else uncompressed_size
    csz = len(payload) if compressed_size is None else compressed_size
    return (struct.pack("<QQQI", h.message_start_time, h.message_end_time, usz, crc)
            + mcap_string(comp) + struct.pack("<Q", csz) + payload)


def chunk_payload(rec: Rec) -> bytes:
    h = parse_chunk_header(rec.body)
    return rec.body[h.header_len: h.header_len + h.compressed_size]


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def data_section_end(recs: List[Rec]) -> Optional[Rec]:
    for r in recs:
        if r.opcode == OP_DATA_END:
            return r
    return None


def reassemble(data: bytes, recs: List[Rec], *, keep_tail: bool = True) -> bytes:
    """Rebuild a file from magic + given records + (optionally) the original bytes
    following the last original record (summary/footer/end magic are records too, so
    keep_tail only matters for trailing garbage)."""
    out = bytearray(MAGIC)
    for r in recs:
        out += r.raw
    return bytes(out)


def replace_record(data: bytes, recs: List[Rec], index: int, new_raw: bytes) -> bytes:
    r = recs[index]
    return data[: r.offset] + new_raw + data[r.end:]


def remove_record(data: bytes, recs: List[Rec], index: int) -> bytes:
    r = recs[index]
    return data[: r.offset] + data[r.end:]


def insert_before(data: bytes, recs: List[Rec], index: int, raw: bytes) -> bytes:
    r = recs[index]
    return data[: r.offset] + raw + data[r.offset:]


def chunk_compressions(data: bytes) -> List[str]:
    """Compression strings of every top-level chunk (used on Rust CLI output)."""
    out = []
    for r in parse_records(data):
        if r.opcode == OP_CHUNK:
            out.append(parse_chunk_header(r.body).compression)
    return out


def header_fields(data: bytes):
    """(profile, library) from the first Header record, or None."""
    for r in parse_records(data):
        if r.opcode == OP_HEADER:
            plen = struct.unpack_from("<I", r.body, 0)[0]
            profile = r.body[4:4 + plen].decode("utf-8", errors="replace")
            llen = struct.unpack_from("<I", r.body, 4 + plen)[0]
            library = r.body[8 + plen: 8 + plen + llen].decode("utf-8", errors="replace")
            return profile, library
        break
    return None


def iter_uncompressed_chunk_records(payload: bytes):
    off = 0
    while off + OPCODE_LEN_SIZE <= len(payload):
        op = payload[off]
        ln = struct.unpack_from("<Q", payload, off + 1)[0]
        yield op, payload[off + OPCODE_LEN_SIZE: off + OPCODE_LEN_SIZE + ln]
        off += OPCODE_LEN_SIZE + ln


def message_payload_digest(data: bytes):
    """Count messages and hash their payloads in file order, decoding only
    uncompressed chunks (the harness requests --compression none where it uses this)."""
    import hashlib
    h = hashlib.sha256()
    count = 0
    for r in parse_records(data):
        if r.opcode == OP_MESSAGE:
            h.update(r.body[22:])
            count += 1
        elif r.opcode == OP_CHUNK:
            ch = parse_chunk_header(r.body)
            if ch.compression != "":
                raise ValueError("compressed chunk in output where uncompressed was requested")
            for op, body in iter_uncompressed_chunk_records(chunk_payload(r)):
                if op == OP_MESSAGE:
                    h.update(body[22:])
                    count += 1
        elif r.opcode == OP_DATA_END:
            break
    return count, h.hexdigest()

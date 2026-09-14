#!/usr/bin/env python3
"""Generate MCAP fixtures with recorded ground truth for the hidden indexed-reader checks.

This is a self-contained MCAP record writer (no dependency on any mcap library) so that every
byte offset, chunk boundary, index entry and statistic is known at generation time. The resulting
`manifest.json` carries, per fixture, the ground truth and a list of queries with their expected
outputs, all derived analytically from what was written -- never from any MCAP reader.

Layout produced in <out_dir>:
  <name>.mcap          the fixture
  manifest.json        {"fixtures": {name: {...}}}

Requires: lz4 (lz4.frame) and zstandard.
"""
import json
import random
import struct
import sys
import zlib
from pathlib import Path

import lz4.frame
import zstandard

MAGIC = b"\x89MCAP0\r\n"
OP = {
    "Header": 0x01, "Footer": 0x02, "Schema": 0x03, "Channel": 0x04, "Message": 0x05,
    "Chunk": 0x06, "MessageIndex": 0x07, "ChunkIndex": 0x08, "Attachment": 0x09,
    "AttachmentIndex": 0x0A, "Statistics": 0x0B, "Metadata": 0x0C, "MetadataIndex": 0x0D,
    "SummaryOffset": 0x0E, "DataEnd": 0x0F,
}
FOOTER_LENGTH = 1 + 8 + 8 + 8 + 4 + len(MAGIC)  # opcode, length, footer body, trailing magic
MAX_TIME = (1 << 64) - 1
PAD = b"\x01\xff\xff"
# Records the TypeScript conformance generator pads (see typescript/core/src/McapRecordBuilder.ts).
# Kept to the extensible records whose derived lengths the reader never has to recompute.
PAD_TYPES = {"Header", "Schema", "Channel", "ChunkIndex", "Statistics", "SummaryOffset"}


def u8(v): return struct.pack("<B", v)
def u16(v): return struct.pack("<H", v)
def u32(v): return struct.pack("<I", v)
def u64(v): return struct.pack("<Q", v)


def pstr(s):
    b = s.encode("utf-8")
    return u32(len(b)) + b


def pbytes(b):
    return u32(len(b)) + b


def kvmap(d):
    body = b"".join(pstr(k) + pstr(v) for k, v in d.items())
    return u32(len(body)) + body


def record(rtype, body, pad=False):
    if pad:
        body = body + PAD
    return u8(OP[rtype]) + u64(len(body)) + body


def compress(compression, data):
    if compression == "":
        return data
    if compression == "lz4":
        return lz4.frame.compress(data, block_size=lz4.frame.BLOCKSIZE_MAX64KB,
                                  content_checksum=True, store_size=True)
    if compression == "zstd":
        return zstandard.ZstdCompressor(level=3).compress(data)
    if compression == "bz2":  # deliberately unsupported by MCAP readers
        return data
    raise ValueError(compression)


class FileBuilder:
    """Writes one MCAP file and records everything needed to predict reader behaviour."""

    def __init__(self, *, compression="", chunk_target=600, chunking=True, message_index=True,
                 chunk_index=True, summary=True, statistics=True, summary_offsets=True,
                 repeated_schemas=True, repeated_channels=True, attachment_index=True,
                 metadata_index=True, shuffle_chunk_indexes=None, pad=False, chunk_crc=True,
                 data_crc=True, summary_crc=True, footer_override=None, profile="",
                 library="fixture-writer", chunk_compression_label=None):
        self.compression = compression
        self.chunk_compression_label = (compression if chunk_compression_label is None
                                        else chunk_compression_label)
        self.chunk_target = chunk_target
        self.chunking = chunking
        self.message_index = message_index
        self.chunk_index = chunk_index
        self.summary = summary
        self.statistics = statistics
        self.summary_offsets = summary_offsets
        self.repeated_schemas = repeated_schemas
        self.repeated_channels = repeated_channels
        self.attachment_index = attachment_index
        self.metadata_index = metadata_index
        self.shuffle_chunk_indexes = shuffle_chunk_indexes  # None or random.Random
        self.pad = pad
        self.chunk_crc = chunk_crc
        self.data_crc = data_crc
        self.summary_crc = summary_crc
        self.footer_override = footer_override

        self.buf = bytearray(MAGIC)
        self.buf += record("Header", pstr(profile) + pstr(library), self._pad("Header"))
        self.data_start = len(self.buf)

        self.schemas = []      # dicts: id, name, encoding, data(bytes)
        self.channels = []     # dicts: id, schema_id, topic, message_encoding, metadata
        self.messages = []     # ground truth, in file order
        self.chunks = []       # ground truth chunk indexes, in file order
        self.attachments = []  # ground truth attachment indexes
        self.metadata = []     # ground truth metadata indexes
        self.seq = {}

        self._chunk_records = bytearray()
        self._chunk_msgs = []  # (channel_id, log_time, offset_in_chunk, truth_index)
        self._chunk_start = None
        self._chunk_end = None
        self.data_end_offset = None
        self.summary_start = None
        self.summary_offset_start = None
        self.file_size = None

    # -- helpers -------------------------------------------------------------------------
    def _pad(self, rtype):
        return self.pad and rtype in PAD_TYPES

    def _schema_record(self, sc):
        return record("Schema", u16(sc["id"]) + pstr(sc["name"]) + pstr(sc["encoding"]) +
                      pbytes(sc["data"]), self._pad("Schema"))

    def _channel_record(self, ch):
        return record("Channel", u16(ch["id"]) + u16(ch["schema_id"]) + pstr(ch["topic"]) +
                      pstr(ch["message_encoding"]) + kvmap(ch["metadata"]), self._pad("Channel"))

    @staticmethod
    def _message_record(channel_id, sequence, log_time, publish_time, data):
        return record("Message", u16(channel_id) + u32(sequence) + u64(log_time) +
                      u64(publish_time) + data)

    def _emit_into_chunk_or_file(self, rtype, rec):
        if self.chunking:
            self._chunk_records += rec
        else:
            self.buf += rec

    # -- declarations --------------------------------------------------------------------
    def add_schema(self, sid, name, encoding, data, in_chunk=True):
        sc = {"id": sid, "name": name, "encoding": encoding, "data": data}
        self.schemas.append(sc)
        rec = self._schema_record(sc)
        if self.chunking and in_chunk:
            self._chunk_records += rec
        else:
            self.flush_chunk()
            self.buf += rec

    def add_channel(self, cid, schema_id, topic, message_encoding="proto", metadata=None,
                    in_chunk=True):
        ch = {"id": cid, "schema_id": schema_id, "topic": topic,
              "message_encoding": message_encoding, "metadata": metadata or {}}
        self.channels.append(ch)
        rec = self._channel_record(ch)
        if self.chunking and in_chunk:
            self._chunk_records += rec
        else:
            self.flush_chunk()
            self.buf += rec

    def channel(self, cid):
        return next(c for c in self.channels if c["id"] == cid)

    # -- messages ------------------------------------------------------------------------
    def add_message(self, channel_id, log_time, data, publish_time=None, sequence=None,
                    unchunked=False):
        if publish_time is None:
            publish_time = log_time
        if sequence is None:
            sequence = self.seq.get(channel_id, 0)
            self.seq[channel_id] = sequence + 1
        ch = self.channel(channel_id)
        rec = self._message_record(channel_id, sequence, log_time, publish_time, data)
        truth = {
            "file_index": len(self.messages),
            "channel_id": channel_id,
            "topic": ch["topic"],
            "schema_id": ch["schema_id"],
            "sequence": sequence,
            "log_time": log_time,
            "publish_time": publish_time,
            "data_hex": data.hex(),
            "chunked": False,
            "chunk_index": None,   # index into self.chunks (filled at flush)
            "chunk_offset": None,  # file offset of the enclosing Chunk record
            "offset": None,        # offset of the Message record within the uncompressed chunk
        }
        if self.chunking and not unchunked:
            offset = len(self._chunk_records)
            self._chunk_records += rec
            truth["chunked"] = True
            truth["offset"] = offset
            self._chunk_msgs.append((channel_id, log_time, offset, len(self.messages)))
            self._chunk_start = log_time if self._chunk_start is None else min(self._chunk_start, log_time)
            self._chunk_end = log_time if self._chunk_end is None else max(self._chunk_end, log_time)
            self.messages.append(truth)
            if len(self._chunk_records) >= self.chunk_target:
                self.flush_chunk()
        else:
            self.flush_chunk()
            truth["offset"] = len(self.buf)
            self.buf += rec
            self.messages.append(truth)
        return truth

    def flush_chunk(self):
        if not self.chunking or len(self._chunk_records) == 0:
            return
        records = bytes(self._chunk_records)
        compressed = compress(self.compression, records)
        start = self._chunk_start if self._chunk_start is not None else 0
        end = self._chunk_end if self._chunk_end is not None else 0
        crc = zlib.crc32(records) & 0xFFFFFFFF if self.chunk_crc else 0
        chunk_body = (u64(start) + u64(end) + u64(len(records)) + u32(crc) +
                      pstr(self.chunk_compression_label) + u64(len(compressed)) + compressed)
        chunk_rec = record("Chunk", chunk_body)
        chunk_start_offset = len(self.buf)
        self.buf += chunk_rec
        chunk_length = len(chunk_rec)

        chunk_no = len(self.chunks)
        message_index_offsets = {}
        message_index_start = len(self.buf)
        if self.message_index:
            per_channel = {}
            for cid, lt, off, _ in self._chunk_msgs:
                per_channel.setdefault(cid, []).append((lt, off))
            for cid in sorted(per_channel):
                entries = b"".join(u64(lt) + u64(off) for lt, off in per_channel[cid])
                message_index_offsets[cid] = len(self.buf)
                self.buf += record("MessageIndex", u16(cid) + u32(len(entries)) + entries)
        message_index_length = len(self.buf) - message_index_start

        for _, _, _, truth_index in self._chunk_msgs:
            self.messages[truth_index]["chunk_index"] = chunk_no
            self.messages[truth_index]["chunk_offset"] = chunk_start_offset

        self.chunks.append({
            "message_start_time": start,
            "message_end_time": end,
            "chunk_start_offset": chunk_start_offset,
            "chunk_length": chunk_length,
            "message_index_offsets": {str(k): v for k, v in message_index_offsets.items()},
            "message_index_length": message_index_length,
            "compression": self.chunk_compression_label,
            "compressed_size": len(compressed),
            "uncompressed_size": len(records),
            "message_count": len(self._chunk_msgs),
        })
        self._chunk_records = bytearray()
        self._chunk_msgs = []
        self._chunk_start = None
        self._chunk_end = None

    # -- attachments / metadata ----------------------------------------------------------
    def add_attachment(self, name, media_type, log_time, create_time, data):
        self.flush_chunk()
        body_wo_crc = (u64(log_time) + u64(create_time) + pstr(name) + pstr(media_type) +
                       u64(len(data)) + data)
        crc = zlib.crc32(body_wo_crc) & 0xFFFFFFFF
        rec = record("Attachment", body_wo_crc + u32(crc))
        offset = len(self.buf)
        self.buf += rec
        self.attachments.append({
            "name": name, "media_type": media_type, "log_time": log_time,
            "create_time": create_time, "data_size": len(data), "offset": offset,
            "length": len(rec),
        })

    def add_metadata(self, name, metadata):
        self.flush_chunk()
        rec = record("Metadata", pstr(name) + kvmap(metadata))
        offset = len(self.buf)
        self.buf += rec
        self.metadata.append({"name": name, "offset": offset, "length": len(rec),
                              "metadata": dict(metadata)})

    # -- statistics ----------------------------------------------------------------------
    def statistics_truth(self):
        counts = {}
        for m in self.messages:
            counts[m["channel_id"]] = counts.get(m["channel_id"], 0) + 1
        times = [m["log_time"] for m in self.messages]
        return {
            "message_count": len(self.messages),
            "schema_count": len({s["id"] for s in self.schemas}),
            "channel_count": len({c["id"] for c in self.channels}),
            "attachment_count": len(self.attachments),
            "metadata_count": len(self.metadata),
            "chunk_count": len(self.chunks),
            "message_start_time": min(times) if times else 0,
            "message_end_time": max(times) if times else 0,
            "channel_message_counts": {str(k): v for k, v in sorted(counts.items())},
        }

    def _statistics_record(self):
        st = self.statistics_truth()
        cmc = b"".join(u16(int(k)) + u64(v) for k, v in st["channel_message_counts"].items())
        body = (u64(st["message_count"]) + u16(st["schema_count"]) + u32(st["channel_count"]) +
                u32(st["attachment_count"]) + u32(st["metadata_count"]) + u32(st["chunk_count"]) +
                u64(st["message_start_time"]) + u64(st["message_end_time"]) +
                u32(len(cmc)) + cmc)
        return record("Statistics", body, self._pad("Statistics"))

    def _chunk_index_record(self, ci):
        mio = b"".join(u16(int(k)) + u64(v) for k, v in ci["message_index_offsets"].items())
        body = (u64(ci["message_start_time"]) + u64(ci["message_end_time"]) +
                u64(ci["chunk_start_offset"]) + u64(ci["chunk_length"]) + u32(len(mio)) + mio +
                u64(ci["message_index_length"]) + pstr(ci["compression"]) +
                u64(ci["compressed_size"]) + u64(ci["uncompressed_size"]))
        return record("ChunkIndex", body, self._pad("ChunkIndex"))

    # -- finish --------------------------------------------------------------------------
    def finish(self):
        self.flush_chunk()
        crc = zlib.crc32(bytes(self.buf)) & 0xFFFFFFFF if self.data_crc else 0
        self.data_end_offset = len(self.buf)
        self.buf += record("DataEnd", u32(crc))
        summary_region_start = len(self.buf)

        groups = []  # (opcode name, start, length)
        summary_start = 0
        summary_offset_start = 0
        if self.summary:
            def group(name, recs):
                if not recs:
                    return
                start = len(self.buf)
                for r in recs:
                    self.buf += r
                groups.append((name, start, len(self.buf) - start))

            if self.repeated_schemas:
                group("Schema", [self._schema_record(s) for s in self.schemas])
            if self.repeated_channels:
                group("Channel", [self._channel_record(c) for c in self.channels])
            if self.statistics:
                group("Statistics", [self._statistics_record()])
            if self.chunk_index:
                order = list(self.chunks)
                if self.shuffle_chunk_indexes is not None:
                    self.shuffle_chunk_indexes.shuffle(order)
                self.summary_chunk_index_order = [self.chunks.index(c) for c in order]
                group("ChunkIndex", [self._chunk_index_record(c) for c in order])
            if self.attachment_index:
                group("AttachmentIndex", [
                    record("AttachmentIndex", u64(a["offset"]) + u64(a["length"]) +
                           u64(a["log_time"]) + u64(a["create_time"]) + u64(a["data_size"]) +
                           pstr(a["name"]) + pstr(a["media_type"]))
                    for a in self.attachments])
            if self.metadata_index:
                group("MetadataIndex", [
                    record("MetadataIndex", u64(m["offset"]) + u64(m["length"]) + pstr(m["name"]))
                    for m in self.metadata])
            if groups:
                summary_start = summary_region_start
            if self.summary_offsets and groups:
                summary_offset_start = len(self.buf)
                for name, start, length in groups:
                    self.buf += record("SummaryOffset", u8(OP[name]) + u64(start) + u64(length),
                                       self._pad("SummaryOffset"))
        self.summary_start = summary_start
        self.summary_offset_start = summary_offset_start

        f_start, f_offset_start = summary_start, summary_offset_start
        if self.footer_override is not None:
            f_start, f_offset_start = self.footer_override
        footer_prefix = u8(OP["Footer"]) + u64(8 + 8 + 4) + u64(f_start) + u64(f_offset_start)
        if self.summary_crc:
            scrc = zlib.crc32(bytes(self.buf[summary_region_start:]) + footer_prefix) & 0xFFFFFFFF
        else:
            scrc = 0
        self.buf += footer_prefix + u32(scrc)
        self.buf += MAGIC
        self.file_size = len(self.buf)
        return bytes(self.buf)

    # -- expectations --------------------------------------------------------------------
    def expected_scan_chunk_indexes(self):
        """What a Header-to-DataEnd scan can reconstruct: one index per Chunk record, in file
        order, without any message-index information."""
        return [{
            "message_start_time": c["message_start_time"],
            "message_end_time": c["message_end_time"],
            "chunk_start_offset": c["chunk_start_offset"],
            "chunk_length": c["chunk_length"],
            "message_index_offsets": {},
            "message_index_length": 0,
            "compression": c["compression"],
            "compressed_size": c["compressed_size"],
            "uncompressed_size": c["uncompressed_size"],
        } for c in self.chunks]

    def expected_summary_chunk_indexes(self):
        """Chunk indexes as recorded in the Summary section, sorted by chunk_start_offset."""
        keys = ["message_start_time", "message_end_time", "chunk_start_offset", "chunk_length",
                "message_index_offsets", "message_index_length", "compression",
                "compressed_size", "uncompressed_size"]
        return [{k: c[k] for k in keys} for c in sorted(self.chunks, key=lambda c: c["chunk_start_offset"])]

    def has_message_indexes(self):
        return any(c["message_index_length"] > 0 for c in self.chunks)

    def expected_byte_range(self, start, end, parsed_summary, chunk_indexes, data_end):
        if not parsed_summary or not chunk_indexes:
            return [self.data_start, data_end]
        lo, hi = None, None
        for c in chunk_indexes:
            if c["message_end_time"] >= start and c["message_start_time"] <= end:
                lo = c["chunk_start_offset"] if lo is None else min(lo, c["chunk_start_offset"])
                e = c["chunk_start_offset"] + c["chunk_length"]
                hi = e if hi is None else max(hi, e)
        if lo is None:
            return [0, 0]
        return [lo, hi]


def message_view(m):
    return {"channel_id": m["channel_id"], "sequence": m["sequence"], "log_time": m["log_time"],
            "publish_time": m["publish_time"], "data_hex": m["data_hex"],
            "offset": m["offset"], "chunk_offset": m["chunk_offset"]}


def expected_messages(fb, order, start, end, topics, indexed_path):
    """Analytic expectation for readMessages().

    indexed_path: True for LogTimeOrder/ReverseLogTimeOrder (only messages inside Chunk records
    that are covered by a Chunk Index are reachable). For FileOrder all messages in the visited
    byte range are candidates; the caller decides the byte range.
    """
    sel = [m for m in fb.messages if start <= m["log_time"] < end and
           (topics is None or m["topic"] in topics)]
    if indexed_path:
        sel = [m for m in sel if m["chunked"]]
    if order == "logtime":
        sel.sort(key=lambda m: (m["log_time"], m["file_index"]))
    elif order == "reverse":
        sel.sort(key=lambda m: (-m["log_time"], -m["file_index"]))
    return [message_view(m) for m in sel]


def summary_expectation(fb, method):
    """Expected outcome of McapReader::readSummary(method) for this fixture."""
    has_summary_stats = fb.summary and fb.statistics
    bad_compression = any(c["compression"] not in ("", "lz4", "zstd") for c in fb.chunks)
    if fb.footer_override is not None:
        section_status = "InvalidFooter"
    elif not has_summary_stats:
        section_status = "MissingStatistics"
    else:
        section_status = "Success"
    section_ok = section_status == "Success"

    if method == "NoFallbackScan":
        status = section_status
        # A failed section parse still leaves whatever it parsed in the accessors; an InvalidFooter
        # is detected before any Summary record is read.
        src = "section" if section_ok else ("partial_section" if fb.footer_override is None else "none")
    elif method == "AllowFallbackScan":
        src = "section" if section_ok else "scan"
        status = "Success"
    else:
        src = "scan"
        status = "Success"
    if src == "scan" and bad_compression:
        # The scan has to decode every Chunk; an unrecognized compression aborts it.
        return {"status": "UnrecognizedCompression", "loose": True, "statistics": None,
                "parsed": False, "data_end": fb.file_size - FOOTER_LENGTH,
                "chunk_indexes": None, "schemas": None, "channels": None,
                "attachment_indexes": None, "metadata_indexes": None}

    def summary_view():
        return {
            "chunk_indexes": fb.expected_summary_chunk_indexes() if (fb.summary and fb.chunk_index) else [],
            "schemas": sorted(s["id"] for s in fb.schemas) if (fb.summary and fb.repeated_schemas) else [],
            "channels": sorted(c["id"] for c in fb.channels) if (fb.summary and fb.repeated_channels) else [],
            "attachment_indexes": sorted(
                ([{k: a[k] for k in ("name", "offset", "length", "log_time", "create_time",
                                     "data_size", "media_type")} for a in fb.attachments]
                 if (fb.summary and fb.attachment_index) else []), key=lambda a: a["offset"]),
            "metadata_indexes": sorted(
                ([{k: m[k] for k in ("name", "offset", "length")} for m in fb.metadata]
                 if (fb.summary and fb.metadata_index) else []), key=lambda m: m["offset"]),
            "data_end": fb.summary_start if fb.summary_start else fb.file_size - FOOTER_LENGTH,
        }

    exp = {"status": status, "loose": False}
    if src == "section":
        exp.update(summary_view())
        exp["statistics"] = fb.statistics_truth()
        exp["parsed"] = True
    elif src == "partial_section":
        exp.update(summary_view())
        exp["statistics"] = None
        exp["parsed"] = False
    elif src == "scan":
        exp["chunk_indexes"] = fb.expected_scan_chunk_indexes()
        exp["statistics"] = fb.statistics_truth()
        # only Schema/Channel records encountered in the Data section (in or out of chunks)
        exp["schemas"] = sorted(s["id"] for s in fb.schemas)
        exp["channels"] = sorted(c["id"] for c in fb.channels)
        exp["attachment_indexes"] = sorted(
            [{k: a[k] for k in ("name", "offset", "length", "log_time", "create_time",
                                "data_size", "media_type")} for a in fb.attachments],
            key=lambda a: a["offset"])
        exp["metadata_indexes"] = sorted(
            [{k: m[k] for k in ("name", "offset", "length")} for m in fb.metadata],
            key=lambda m: m["offset"])
        exp["data_end"] = fb.data_end_offset
        exp["parsed"] = True
    else:  # none: InvalidFooter before anything was read
        exp.update(chunk_indexes=[], statistics=None, schemas=[], channels=[],
                   attachment_indexes=[], metadata_indexes=[],
                   data_end=fb.file_size - FOOTER_LENGTH, parsed=False)
    return exp


def build_fixture(name, fb, queries, byte_range_probes, sibling_check, notes):
    """Assemble the manifest entry. `queries` is a list of dicts with keys order, start, end,
    topics, summary (readSummary method to call first, or None)."""
    entry = {
        "file": f"{name}.mcap",
        "notes": notes,
        "sibling_check": sibling_check,
        "file_size": fb.file_size,
        "data_start": fb.data_start,
        "data_end_offset": fb.data_end_offset,
        "summary_start": fb.summary_start,
        "summary_offset_start": fb.summary_offset_start,
        "has_message_indexes": fb.has_message_indexes(),
        "message_count": len(fb.messages),
        "chunk_count": len(fb.chunks),
        "chunked_messages_logtime": expected_messages(fb, "logtime", 0, MAX_TIME, None, True),
        "summary": {},
        "queries": [],
    }
    for method in ("NoFallbackScan", "AllowFallbackScan", "ForceScan"):
        exp = summary_expectation(fb, method)
        exp["byte_ranges"] = []
        for (s, e) in byte_range_probes:
            exp["byte_ranges"].append({
                "start": s, "end": e,
                "range": fb.expected_byte_range(s, e, exp["parsed"], exp["chunk_indexes"] or [],
                                                exp["data_end"])})
        entry["summary"][method] = exp

    bad_compression = any(c["compression"] not in ("", "lz4", "zstd") for c in fb.chunks)
    for q in queries:
        order, start, end, topics, summary = q["order"], q["start"], q["end"], q.get("topics"), q.get("summary")
        indexed = order in ("logtime", "reverse")
        problems = []
        stats_present = None
        if indexed:
            # Which chunk indexes does the log-time reader get to work with? Whatever an explicit
            # readSummary() left behind, or otherwise the result of its own AllowFallbackScan.
            pre = summary_expectation(fb, summary) if summary else None
            if pre is None or not pre["chunk_indexes"]:
                post = summary_expectation(fb, "AllowFallbackScan")
                assert post["status"] == "Success", (name, post["status"])
                cis = post["chunk_indexes"] or []
                stats_present = post["statistics"] is not None
            else:
                cis = pre["chunk_indexes"]
                stats_present = pre["statistics"] is not None
            if not cis or all(ci["message_index_length"] == 0 for ci in cis):
                msgs = []
                problems = ["NoMessageIndexesAvailable"]
            elif bad_compression:
                msgs = []
                problems = ["UnrecognizedCompression"]
            else:
                msgs = expected_messages(fb, order, start, end, topics, True)
        else:
            exp = summary_expectation(fb, summary) if summary else None
            if exp is not None:
                stats_present = exp["statistics"] is not None
            if exp is not None and exp["parsed"] and exp["chunk_indexes"]:
                # FileOrder after a parsed summary visits only the byte range spanned by the
                # chunks overlapping the window: records outside that span are never visited.
                lo, hi = fb.expected_byte_range(start, end, True, exp["chunk_indexes"], exp["data_end"])
                cand = [m for m in fb.messages if
                        (m["chunked"] and lo <= m["chunk_offset"] < hi) or
                        (not m["chunked"] and lo <= m["offset"] < hi)]
                sel = [m for m in cand if start <= m["log_time"] < end and
                       (topics is None or m["topic"] in topics)]
                msgs = [message_view(m) for m in sel]
            else:
                msgs = expected_messages(fb, "file", start, end, topics, False)
            if bad_compression:
                problems = ["UnrecognizedCompression"]
                msgs = [m for m in msgs if m["chunk_offset"] is None]
        entry["queries"].append({
            "name": q["name"], "order": order, "start": start, "end": end, "topics": topics,
            "summary": summary, "check_offsets": indexed,
            "expected_messages": msgs, "expected_problems": problems,
            "expected_statistics_present": stats_present,
        })
    return entry


def rand_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


NS = 1_000_000_000
BASE = 1_700_000_000 * NS


def std_channels(fb):
    fb.add_schema(1, "sensor_msgs/Imu", "ros2msg", b"float64 x\nfloat64 y\n")
    fb.add_schema(2, "sensor_msgs/Image", "ros2msg", b"uint8[] data\n")
    fb.add_channel(1, 1, "/imu", "cdr", {"origin": "fixture"})
    fb.add_channel(2, 2, "/camera/image", "cdr", {})
    fb.add_channel(3, 0, "/log", "json", {"schemaless": "true"})


def write_multi(fb, rng, n=240, big_every=17, jitter_ns=25_000_000, step_ns=10_000_000):
    """Interleaved producers with small clock jitter so consecutive messages (and chunks) are
    slightly out of order and share exact timestamps now and then."""
    t = BASE
    for i in range(n):
        t += step_ns
        cid = [1, 1, 2, 3, 1, 2][i % 6]
        lt = t + rng.randint(-jitter_ns, jitter_ns)
        if i % 11 == 0 and i > 0:
            lt = fb.messages[-1]["log_time"]  # exact duplicate of the previous message
        size = 200 if (i % big_every) == 0 else rng.randint(2, 16)
        fb.add_message(cid, lt, rand_bytes(rng, size), publish_time=lt - rng.randint(0, 5_000_000))


def standard_queries(fb, rng):
    times = sorted(m["log_time"] for m in fb.messages)
    if not times:
        return []
    lo, hi = times[0], times[-1]
    mid = times[len(times) // 2]
    q = [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_reverse", "order": "reverse", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "all_file_after_summary", "order": "file", "start": 0, "end": MAX_TIME,
         "summary": "NoFallbackScan"},
        {"name": "inclusive_start_exclusive_end", "order": "logtime", "start": mid, "end": times[min(len(times) - 1, len(times) // 2 + 7)]},
        {"name": "window_reverse", "order": "reverse", "start": times[len(times) // 3], "end": times[2 * len(times) // 3]},
        {"name": "window_file", "order": "file", "start": times[len(times) // 3], "end": times[2 * len(times) // 3]},
        {"name": "empty_window_before", "order": "logtime", "start": 0, "end": lo},
        {"name": "empty_window_after", "order": "logtime", "start": hi + 1, "end": MAX_TIME},
        {"name": "point_window_zero_width", "order": "logtime", "start": mid, "end": mid},
        {"name": "single_topic_imu", "order": "logtime", "start": 0, "end": MAX_TIME, "topics": ["/imu"]},
        {"name": "two_topics_reverse", "order": "reverse", "start": 0, "end": MAX_TIME, "topics": ["/imu", "/log"]},
        {"name": "schemaless_topic_window", "order": "logtime", "start": times[len(times) // 4], "end": times[3 * len(times) // 4], "topics": ["/log"]},
        {"name": "unknown_topic", "order": "logtime", "start": 0, "end": MAX_TIME, "topics": ["/nope"]},
        {"name": "topic_file_order", "order": "file", "start": 0, "end": MAX_TIME, "topics": ["/camera/image"]},
    ]
    # a window between two chunks if one exists
    if len(fb.chunks) >= 3:
        cs = sorted(fb.chunks, key=lambda c: c["chunk_start_offset"])
        gaps = [(cs[i]["message_end_time"], cs[i + 1]["message_start_time"]) for i in range(len(cs) - 1)
                if cs[i + 1]["message_start_time"] > cs[i]["message_end_time"] + 1]
        if gaps:
            a, b = gaps[len(gaps) // 2]
            q.append({"name": "gap_between_chunks", "order": "logtime", "start": a + 1, "end": b})
        c = cs[len(cs) // 2]
        q.append({"name": "single_chunk_span", "order": "logtime", "start": c["message_start_time"], "end": c["message_end_time"] + 1})
        q.append({"name": "single_chunk_span_reverse", "order": "reverse", "start": c["message_start_time"], "end": c["message_end_time"] + 1})
    return q


def byte_range_probes(fb):
    times = sorted(m["log_time"] for m in fb.messages)
    probes = [(0, MAX_TIME), (0, 0)]
    if times:
        lo, hi = times[0], times[-1]
        mid = times[len(times) // 2]
        probes += [(lo, hi), (mid, mid), (hi + 1, MAX_TIME), (0, lo - 1 if lo > 0 else 0)]
    if len(fb.chunks) >= 2:
        cs = sorted(fb.chunks, key=lambda c: c["chunk_start_offset"])
        c = cs[len(cs) // 2]
        probes.append((c["message_start_time"], c["message_end_time"]))
        probes.append((c["message_end_time"], c["message_end_time"]))
    return probes


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"fixtures": {}}

    def emit(name, fb, queries, sibling_check, notes):
        data = fb.finish()
        (out / f"{name}.mcap").write_bytes(data)
        manifest["fixtures"][name] = build_fixture(name, fb, queries, byte_range_probes(fb),
                                                   sibling_check, notes)

    # 1. zstd, many small chunks, three topics incl. schemaless, jittered/duplicate timestamps
    rng = random.Random(1)
    fb = FileBuilder(compression="zstd", chunk_target=700)
    std_channels(fb)
    write_multi(fb, rng)
    emit("multi_zstd", fb, standard_queries(fb, rng), True,
         "zstd chunks; 3 channels (one schemaless); cross-chunk out-of-order and duplicate log times")

    # 2. lz4, chunk indexes written to the summary in shuffled order, no summary offsets
    rng = random.Random(2)
    fb = FileBuilder(compression="lz4", chunk_target=500, summary_offsets=False,
                     shuffle_chunk_indexes=random.Random(22))
    std_channels(fb)
    write_multi(fb, rng, n=180, jitter_ns=40_000_000)
    emit("multi_lz4_shuffled_index", fb, standard_queries(fb, rng), True,
         "lz4 chunks; Chunk Index records appear in the Summary in shuffled order; no Summary Offset section")

    # 3. uncompressed with padded (extended) records
    rng = random.Random(3)
    fb = FileBuilder(compression="", chunk_target=450, pad=True)
    std_channels(fb)
    write_multi(fb, rng, n=90)
    emit("uncompressed_pad", fb, standard_queries(fb, rng), True,
         "no compression; Header/Schema/Channel/ChunkIndex/Statistics/SummaryOffset records carry unknown trailing bytes")

    # 4. heavily overlapping chunk time ranges across two channels + exact cross-chunk ties
    rng = random.Random(4)
    fb = FileBuilder(compression="", chunk_target=10 ** 9)  # manual flushes only
    std_channels(fb)
    t0 = BASE
    for k in range(8):
        cid = 1 if k % 2 == 0 else 2
        for j in range(6):
            lt = t0 + (k // 2) * 100 * NS + j * 30 * NS + (0 if cid == 1 else 15 * NS)
            if j == 5:
                lt = t0 + (k // 2) * 100 * NS + 90 * NS  # identical timestamp in both chunks of a pair
            fb.add_message(cid, lt, rand_bytes(rng, 8))
        fb.flush_chunk()
    # a third-channel chunk whose time range spans everything
    for j in range(4):
        fb.add_message(3, t0 + j * 120 * NS, rand_bytes(rng, 4))
    fb.flush_chunk()
    emit("overlap_heavy", fb, standard_queries(fb, rng), True,
         "chunks alternate channels with overlapping time ranges and identical timestamps across chunks")

    # 5. chunked with message indexes but no Summary section at all
    rng = random.Random(5)
    fb = FileBuilder(compression="zstd", chunk_target=500, summary=False)
    std_channels(fb)
    write_multi(fb, rng, n=60)
    emit("no_summary", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_reverse", "order": "reverse", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "all_file_after_scan", "order": "file", "start": 0, "end": MAX_TIME, "summary": "AllowFallbackScan"},
        {"name": "window_file_after_scan", "order": "file", "start": fb.messages[10]["log_time"], "end": fb.messages[40]["log_time"], "summary": "ForceScan"},
    ], False, "chunked and message-indexed but the Footer points at no Summary section")

    # 6. chunk indexes without message-index information
    rng = random.Random(6)
    fb = FileBuilder(compression="lz4", chunk_target=500, message_index=False)
    std_channels(fb)
    write_multi(fb, rng, n=60)
    emit("no_message_index", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_reverse", "order": "reverse", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "window_file_after_summary", "order": "file", "start": fb.messages[15]["log_time"], "end": fb.messages[45]["log_time"], "summary": "NoFallbackScan"},
    ], False, "Chunk Index records have empty message_index_offsets and message_index_length 0")

    # 7. no chunks at all
    rng = random.Random(7)
    fb = FileBuilder(chunking=False)
    std_channels(fb)
    for i in range(30):
        fb.add_message([1, 2, 3][i % 3], BASE + i * 7 * NS, rand_bytes(rng, 6))
    emit("unchunked", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "window_file_after_summary", "order": "file", "start": BASE + 35 * NS, "end": BASE + 140 * NS, "summary": "NoFallbackScan"},
        {"name": "topic_file", "order": "file", "start": 0, "end": MAX_TIME, "topics": ["/log"]},
    ], False, "Schema/Channel/Message records written directly to the Data section; Summary has no Chunk Index")

    # 8. indexed chunks followed by Message records outside any chunk
    rng = random.Random(8)
    fb = FileBuilder(compression="zstd", chunk_target=400)
    std_channels(fb)
    write_multi(fb, rng, n=48)
    tail_t = fb.messages[-1]["log_time"]
    for i in range(3):
        fb.add_message(1, tail_t + (i + 1) * NS, rand_bytes(rng, 5), unchunked=True)
    emit("mixed_unchunked_tail", fb, standard_queries(fb, rng) + [
        {"name": "tail_window_file_no_summary", "order": "file", "start": tail_t + 1, "end": MAX_TIME},
        {"name": "tail_window_file_after_summary", "order": "file", "start": tail_t + 1, "end": MAX_TIME, "summary": "NoFallbackScan"},
        {"name": "tail_window_logtime", "order": "logtime", "start": tail_t + 1, "end": MAX_TIME},
    ], True, "three Message records live outside chunks after the last indexed chunk")

    # 9. boundary timestamps: 0 and 2^63
    rng = random.Random(9)
    fb = FileBuilder(compression="", chunk_target=10 ** 9)
    std_channels(fb)
    fb.add_message(1, 0, b"\x00")
    fb.add_message(2, 0, b"\x01")
    fb.add_message(1, 1, b"\x02")
    fb.flush_chunk()
    fb.add_message(3, 5, b"\x03")
    fb.add_message(1, 1 << 63, b"\x04")
    fb.add_message(2, (1 << 63) + 1, b"\x05")
    fb.flush_chunk()
    emit("boundary_times", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_reverse", "order": "reverse", "start": 0, "end": MAX_TIME},
        {"name": "zero_only", "order": "logtime", "start": 0, "end": 1},
        {"name": "zero_width_at_zero", "order": "logtime", "start": 0, "end": 0},
        {"name": "huge_only", "order": "reverse", "start": 1 << 63, "end": MAX_TIME},
        {"name": "up_to_huge_exclusive", "order": "logtime", "start": 0, "end": 1 << 63},
    ], True, "log_time 0 in the first chunk and 2^63 in the last; exercises inclusive start / exclusive end")

    # 10. footer with summary_offset_start < summary_start
    rng = random.Random(10)
    fb = FileBuilder(compression="", chunk_target=400)
    std_channels(fb)
    write_multi(fb, rng, n=40)
    fb.flush_chunk()
    # We need the real offsets to build a bad footer: finish once to learn them, then rebuild.
    probe = fb.finish()
    bad = (fb.summary_start, fb.summary_start - 10)
    rng = random.Random(10)
    fb = FileBuilder(compression="", chunk_target=400, footer_override=bad)
    std_channels(fb)
    write_multi(fb, rng, n=40)
    emit("bad_footer", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "all_file_after_fallback", "order": "file", "start": 0, "end": MAX_TIME, "summary": "AllowFallbackScan"},
    ], False, "Footer.summary_offset_start is smaller than Footer.summary_start")
    del probe

    # 11. attachments and metadata interleaved with indexed chunks
    rng = random.Random(11)
    fb = FileBuilder(compression="lz4", chunk_target=600)
    fb.add_attachment("calib.yaml", "text/yaml", BASE - NS, BASE - 2 * NS, b"fx: 1.0\nfy: 1.0\n")
    std_channels(fb)
    fb.add_metadata("recording", {"vehicle": "v1", "site": "lab"})
    write_multi(fb, rng, n=70)
    fb.add_attachment("snapshot.bin", "application/octet-stream", BASE + 10 * NS, 0, rand_bytes(rng, 300))
    fb.add_metadata("recording", {"note": "duplicate name is legal"})
    write_multi(fb, rng, n=30)
    fb.add_metadata("tags", {"a": "1", "b": "2"})
    emit("attachments_metadata", fb, standard_queries(fb, rng), True,
         "two Attachment and three Metadata records (one duplicated name) between indexed chunks")

    # 12. unknown compression string
    rng = random.Random(12)
    fb = FileBuilder(compression="bz2", chunk_target=10 ** 9)
    std_channels(fb)
    for i in range(5):
        fb.add_message(1, BASE + i * NS, rand_bytes(rng, 4))
    emit("unknown_compression", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
    ], False, "single chunk whose compression field is \"bz2\"")

    # 13. channels without messages and a chunk holding only Schema/Channel records
    rng = random.Random(13)
    fb = FileBuilder(compression="zstd", chunk_target=10 ** 9)
    std_channels(fb)
    fb.add_schema(3, "std_msgs/String", "ros2msg", b"string data\n")
    fb.add_channel(4, 3, "/silent", "cdr", {})
    fb.add_channel(5, 3, "/also_silent", "cdr", {})
    fb.flush_chunk()  # chunk with no messages
    for i in range(12):
        fb.add_message([1, 2][i % 2], BASE + i * 3 * NS, rand_bytes(rng, 5))
    fb.flush_chunk()
    fb.add_schema(4, "late/Schema", "ros2msg", b"late\n")
    fb.add_channel(6, 4, "/late", "cdr", {})
    fb.add_message(6, BASE + 2 * NS, rand_bytes(rng, 3))  # earlier than the previous chunk's end
    emit("empty_channels_and_chunk", fb, standard_queries(fb, rng) + [
        {"name": "silent_topic", "order": "logtime", "start": 0, "end": MAX_TIME, "topics": ["/silent"]},
        {"name": "late_topic_reverse", "order": "reverse", "start": 0, "end": MAX_TIME, "topics": ["/late", "/imu"]},
    ], True, "channels with zero messages; a message-less first chunk; a late Schema/Channel pair declared in the last chunk")

    # 14. Summary with Schema/Channel/ChunkIndex records but no Statistics record (the shape of the
    #     conformance corpus variants without the "st" feature)
    rng = random.Random(14)
    fb = FileBuilder(compression="zstd", chunk_target=500, statistics=False)
    std_channels(fb)
    write_multi(fb, rng, n=72)
    emit("no_statistics", fb, [
        {"name": "all_logtime", "order": "logtime", "start": 0, "end": MAX_TIME},
        {"name": "all_logtime_after_nofallback", "order": "logtime", "start": 0, "end": MAX_TIME, "summary": "NoFallbackScan"},
        {"name": "all_reverse_after_nofallback", "order": "reverse", "start": 0, "end": MAX_TIME, "summary": "NoFallbackScan"},
        {"name": "window_topic_after_nofallback", "order": "logtime", "start": fb.messages[12]["log_time"], "end": fb.messages[50]["log_time"], "topics": ["/imu", "/camera/image"], "summary": "NoFallbackScan"},
        {"name": "all_file", "order": "file", "start": 0, "end": MAX_TIME},
        {"name": "all_file_after_nofallback", "order": "file", "start": 0, "end": MAX_TIME, "summary": "NoFallbackScan"},
    ], True, "Summary section carries Schema, Channel, ChunkIndex and SummaryOffset records but no Statistics record")

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    total_q = sum(len(f["queries"]) for f in manifest["fixtures"].values())
    print(f"wrote {len(manifest['fixtures'])} fixtures, {total_q} message queries")


if __name__ == "__main__":
    main()

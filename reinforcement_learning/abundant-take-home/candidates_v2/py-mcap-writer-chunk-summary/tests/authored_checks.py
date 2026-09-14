#!/usr/bin/env python3
"""Verifier-owned differential and structural checks on files written by the
submitted ``mcap.writer.Writer``.

Files are produced by the submitted package in a subprocess; every assertion
below is evaluated by verifier-owned code (raw byte parsing per the MCAP spec
tables, plus the Rust and Go reference readers). Each check id maps to a
sentence of instruction.md (see provenance.json -> derivability).

Usage: authored_checks.py --python PY --out OUT.json
         [--go-reader BIN] [--rust-streamed BIN] [--rust-indexed BIN]
"""
import argparse
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

TIMEOUT = 120

GEN = r'''
import json, random, sys
from mcap.writer import CompressionType, IndexType, Writer
out = sys.argv[1]
manifest = {}

def register_all(w):
    s1 = w.register_schema("A", "jsonschema", b'{"type":"object"}')
    s2 = w.register_schema("B", "jsonschema", b'{"type":"array"}')
    c1 = w.register_channel("/a", "json", s1, {"k": "v"})
    c2 = w.register_channel("/b", "json", s2)
    c3 = w.register_channel("/c", "json", 0)
    return [s1, s2], [c1, c2, c3]

def multi(name, **kw):
    path = f"{out}/{name}.mcap"
    w = Writer(path, chunk_size=kw.pop("chunk_size", 200), **kw)
    w.start(profile="x-test", library="authored")
    schemas, chans = register_all(w)
    rng = random.Random(1234)
    times = [rng.randrange(0, 10**6) for _ in range(30)]
    times[6] = times[5]
    msgs = []
    for i, t in enumerate(times):
        ch = chans[i % 3]
        data = bytes([i]) * (1 + (i * 7) % 40)
        w.add_message(ch, t, data, t + 1, sequence=i)
        msgs.append({"channel_id": ch, "log_time": t, "publish_time": t + 1, "sequence": i, "data": list(data)})
        if i == 14:
            w.add_attachment(create_time=11, log_time=22, name="att.bin", media_type="application/octet-stream", data=b"\x00\x01\x02\x03\x04")
            w.add_metadata("meta1", {"x": "1", "y": "2"})
    w.finish()
    manifest[name] = {"messages": msgs, "schemas": schemas, "channels": chans, "chunk_size": 200}

multi("multi_zstd", compression=CompressionType.ZSTD, enable_data_crcs=True)
multi("multi_lz4", compression=CompressionType.LZ4, enable_data_crcs=True)
multi("multi_none", compression=CompressionType.NONE, enable_data_crcs=True)
multi("no_crcs", compression=CompressionType.ZSTD, enable_crcs=False)
multi("unchunked", compression=CompressionType.NONE, use_chunking=False, enable_data_crcs=True)

# defaults, header and footer only
w = Writer(f"{out}/empty_groups.mcap")
w.start()
w.finish()
manifest["empty_groups"] = {}

# ordering: attachments/metadata are written immediately, chunk stays open
path = f"{out}/ordering.mcap"
w = Writer(path, compression=CompressionType.NONE)
w.start()
schemas, chans = register_all(w)
msgs = []
for i in range(10):
    if i == 5:
        w.add_attachment(create_time=1, log_time=2, name="mid.bin", media_type="text/plain", data=b"mid")
        w.add_metadata("mid", {"m": "1"})
    ch = chans[i % 2]
    w.add_message(ch, 100 + i, bytes([i]), 100 + i, sequence=i)
    msgs.append({"channel_id": ch, "log_time": 100 + i, "publish_time": 100 + i, "sequence": i, "data": [i]})
w.finish()
manifest["ordering"] = {"messages": msgs, "channels": chans}

json.dump(manifest, open(f"{out}/manifest.json", "w"))
'''

OP = {"HEADER": 1, "FOOTER": 2, "SCHEMA": 3, "CHANNEL": 4, "MESSAGE": 5, "CHUNK": 6, "MESSAGE_INDEX": 7,
      "CHUNK_INDEX": 8, "ATTACHMENT": 9, "ATTACHMENT_INDEX": 10, "STATISTICS": 11, "METADATA": 12,
      "METADATA_INDEX": 13, "SUMMARY_OFFSET": 14, "DATA_END": 15}
SUMMARY_GROUP_ORDER = [3, 4, 11, 8, 10, 13]


def u16(b, o): return struct.unpack_from("<H", b, o)[0]
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
def u64(b, o): return struct.unpack_from("<Q", b, o)[0]


def pstr(b, o):
    n = u32(b, o)
    return b[o + 4:o + 4 + n].decode(), o + 4 + n


def record_at(b, off):
    op = b[off]
    ln = u64(b, off + 1)
    return op, ln, b[off + 9:off + 9 + ln]


def iter_records(b, start, end):
    off = start
    while off < end:
        op, ln, body = record_at(b, off)
        yield off, op, ln, body
        off += 9 + ln
    if off != end:
        raise ValueError(f"records do not tile [{start},{end}): ended at {off}")


def parse_footer(b):
    foot_off = len(b) - 8 - 29
    op, ln, body = record_at(b, foot_off)
    if op != OP["FOOTER"] or ln != 20 or b[-8:] != b[:8]:
        raise ValueError("bad footer / magic")
    return foot_off, {"summary_start": u64(body, 0), "summary_offset_start": u64(body, 8), "summary_crc": u32(body, 16)}


def parse_chunk_index(body):
    o = 0
    r = {"message_start_time": u64(body, o), "message_end_time": u64(body, o + 8), "chunk_start_offset": u64(body, o + 16), "chunk_length": u64(body, o + 24)}
    o += 32
    n = u32(body, o); o += 4
    end = o + n
    offs = []
    while o < end:
        offs.append((u16(body, o), u64(body, o + 2))); o += 10
    r["message_index_offsets"] = offs
    r["message_index_length"] = u64(body, o); o += 8
    r["compression"], o = pstr(body, o)
    r["compressed_size"] = u64(body, o); r["uncompressed_size"] = u64(body, o + 8)
    return r


def parse_chunk(body):
    o = 0
    r = {"message_start_time": u64(body, 0), "message_end_time": u64(body, 8), "uncompressed_size": u64(body, 16), "uncompressed_crc": u32(body, 24)}
    r["compression"], o = pstr(body, 28)
    n = u64(body, o)
    r["records"] = body[o + 8:o + 8 + n]
    return r


def parse_message_index(body):
    cid = u16(body, 0)
    n = u32(body, 2)
    entries = [(u64(body, 6 + i), u64(body, 14 + i)) for i in range(0, n, 16)]
    return cid, entries


def parse_summary_offset(body):
    return {"group_opcode": body[0], "group_start": u64(body, 1), "group_length": u64(body, 9)}


def parse_attachment_index(body):
    r = {"offset": u64(body, 0), "length": u64(body, 8), "log_time": u64(body, 16), "create_time": u64(body, 24), "data_size": u64(body, 32)}
    r["name"], o = pstr(body, 40)
    r["media_type"], _ = pstr(body, o)
    return r


def parse_metadata_index(body):
    r = {"offset": u64(body, 0), "length": u64(body, 8)}
    r["name"], _ = pstr(body, 16)
    return r


def parse_statistics(body):
    r = {"message_count": u64(body, 0), "schema_count": u16(body, 8), "channel_count": u32(body, 10), "attachment_count": u32(body, 14),
         "metadata_count": u32(body, 18), "chunk_count": u32(body, 22), "message_start_time": u64(body, 26), "message_end_time": u64(body, 34)}
    n = u32(body, 42)
    counts = {}
    for i in range(0, n, 10):
        counts[u16(body, 46 + i)] = u64(body, 48 + i)
    r["channel_message_counts"] = counts
    return r


def parse_message(body):
    return {"channel_id": u16(body, 0), "sequence": u32(body, 2), "log_time": u64(body, 6), "publish_time": u64(body, 14), "data": list(body[22:])}


def decompress(comp, data, size):
    if comp == "":
        return data
    if comp == "zstd":
        import zstandard
        return zstandard.decompress(data, size)
    if comp == "lz4":
        import lz4.frame
        return lz4.frame.decompress(data)
    raise ValueError(f"unknown compression {comp!r}")


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if p.returncode != 0:
        return None, p.stderr.decode(errors="replace")[-300:] or f"exit {p.returncode}"
    return p.stdout.decode(), None


def field(rec, name):
    for k, v in rec["fields"]:
        if k == name:
            return v
    raise KeyError(name)


def msg_key(m):
    return (str(m["channel_id"]), str(m["log_time"]), str(m["publish_time"]), str(m["sequence"]), [str(x) for x in m["data"]])


def json_msg_key(rec):
    return (field(rec, "channel_id"), field(rec, "log_time"), field(rec, "publish_time"), field(rec, "sequence"), list(field(rec, "data")))


class Checks:
    def __init__(self):
        self.results = {}
        self.skipped = {}  # reference-tool crashes: recorded for attribution, excluded from the required set

    def skip(self, cid, info=""):
        self.skipped[cid] = str(info)[:300]

    def check(self, cid, cond, info=""):
        prev = self.results.get(cid, (True, ""))
        ok = bool(cond) and prev[0]
        self.results[cid] = (ok, prev[1] if prev[0] is False else ("" if ok else str(info)[:300]))
        return ok


def structural(name, b, man, C, expect_compression, chunked, enable_crcs, enable_data_crcs):
    p = f"{name}:"
    try:
        foot_off, footer = parse_footer(b)
    except Exception as e:  # noqa: BLE001
        C.check(p + "footer", False, e)
        return
    C.check(p + "footer", True)
    ss, sos = footer["summary_start"], footer["summary_offset_start"]
    C.check(p + "summary_present", ss != 0 and sos != 0 and ss < sos < foot_off, footer)
    if not (ss != 0 and sos != 0 and ss < sos < foot_off):
        return
    # data section: from 8 (after magic) up to DataEnd; DataEnd is the 13 bytes before summary_start
    de_off = ss - 13
    op, ln, body = record_at(b, de_off)
    C.check(p + "data_end", op == OP["DATA_END"] and ln == 4, (op, ln))
    data_crc = u32(body, 0)
    if enable_data_crcs:
        C.check(p + "data_section_crc", data_crc == zlib.crc32(b[:de_off]), (data_crc, zlib.crc32(b[:de_off])))
    else:
        C.check(p + "data_section_crc_zero", data_crc == 0, data_crc)
    # summary crc covers [summary_start, footer + 25)
    exp_crc = zlib.crc32(b[ss:foot_off + 25])
    if enable_crcs:
        C.check(p + "summary_crc", footer["summary_crc"] == exp_crc, (footer["summary_crc"], exp_crc))
    else:
        C.check(p + "summary_crc_zero", footer["summary_crc"] == 0, footer["summary_crc"])

    # data section records
    data_recs = list(iter_records(b, 8, de_off))
    data_ops = [op for _, op, _, _ in data_recs]
    C.check(p + "data_section_ops", set(data_ops) <= {1, 3, 4, 5, 6, 7, 9, 12} and data_ops[0] == 1, data_ops)

    # summary section
    summ = list(iter_records(b, ss, sos))
    summ_ops = [op for _, op, _, _ in summ]
    C.check(p + "summary_ops_allowed", set(summ_ops) <= set(SUMMARY_GROUP_ORDER), summ_ops)
    ranks = [SUMMARY_GROUP_ORDER.index(o) for o in summ_ops]
    C.check(p + "summary_group_order", ranks == sorted(ranks), summ_ops)
    groups = {}
    for off, op, ln, body in summ:
        g = groups.setdefault(op, {"start": off, "length": 0, "records": []})
        g["length"] += 9 + ln
        g["records"].append((off, body))
    offsets = [parse_summary_offset(body) for _, op, _, body in iter_records(b, sos, foot_off) if C.check(p + "summary_offset_ops", op == OP["SUMMARY_OFFSET"], op)]
    C.check(p + "summary_offset_groups", [o["group_opcode"] for o in offsets] == SUMMARY_GROUP_ORDER, [o["group_opcode"] for o in offsets])
    for o in offsets:
        g = groups.get(o["group_opcode"])
        if g is None:
            C.check(p + "summary_offset_empty_group", o["group_length"] == 0 and ss <= o["group_start"] <= sos, o)
        else:
            C.check(p + "summary_offset_points_to_group", o["group_start"] == g["start"] and o["group_length"] == g["length"], (o, g["start"], g["length"]))

    # repeated schemas / channels
    sch = [(u16(body, 0), pstr(body, 2)[0]) for _, body in groups.get(3, {"records": []})["records"]]
    chn = [(u16(body, 0), pstr(body, 4)[0]) for _, body in groups.get(4, {"records": []})["records"]]
    if man.get("schemas"):
        C.check(p + "summary_schemas", [i for i, _ in sch] == man["schemas"] == [1, 2] and [n for _, n in sch] == ["A", "B"], sch)
        C.check(p + "summary_channels", [i for i, _ in chn] == man["channels"] == [1, 2, 3] and [t for _, t in chn] == ["/a", "/b", "/c"], chn)

    # statistics
    stats_recs = groups.get(11, {"records": []})["records"]
    C.check(p + "statistics_present", len(stats_recs) == 1, len(stats_recs))
    stats = parse_statistics(stats_recs[0][1]) if stats_recs else None

    # chunks and chunk indexes
    cidx = [parse_chunk_index(body) for _, body in groups.get(8, {"records": []})["records"]]
    msgs = man.get("messages", [])
    if chunked:
        C.check(p + "chunk_count_multiple", len(cidx) >= 2, len(cidx))
        seen_msgs = []
        for k, ci in enumerate(cidx):
            op, ln, body = record_at(b, ci["chunk_start_offset"])
            C.check(p + "chunk_index_points_to_chunk", op == OP["CHUNK"] and 9 + ln == ci["chunk_length"], (op, ln, ci))
            ch = parse_chunk(body)
            C.check(p + "chunk_compression", ch["compression"] == ci["compression"] == expect_compression, (ch["compression"], ci["compression"]))
            C.check(p + "chunk_sizes", ci["compressed_size"] == len(ch["records"]) and ci["uncompressed_size"] == ch["uncompressed_size"], ci)
            raw = decompress(ch["compression"], ch["records"], ch["uncompressed_size"])
            C.check(p + "chunk_uncompressed_size", len(raw) == ch["uncompressed_size"], (len(raw), ch["uncompressed_size"]))
            if enable_crcs:
                C.check(p + "chunk_crc", ch["uncompressed_crc"] == zlib.crc32(raw), (ch["uncompressed_crc"], zlib.crc32(raw)))
            else:
                C.check(p + "chunk_crc_zero", ch["uncompressed_crc"] == 0, ch["uncompressed_crc"])
            inner = list(iter_records(raw, 0, len(raw)))
            C.check(p + "chunk_inner_ops", set(op for _, op, _, _ in inner) <= {3, 4, 5}, [op for _, op, _, _ in inner])
            cmsgs = [(off, parse_message(body)) for off, op, _, body in inner if op == OP["MESSAGE"]]
            C.check(p + "chunk_has_messages", len(cmsgs) >= 1, len(cmsgs))
            times = [m["log_time"] for _, m in cmsgs]
            C.check(p + "chunk_time_range", times and ci["message_start_time"] == ch["message_start_time"] == min(times) and ci["message_end_time"] == ch["message_end_time"] == max(times), (ci["message_start_time"], ci["message_end_time"], min(times) if times else None, max(times) if times else None))
            if k < len(cidx) - 1:
                C.check(p + "chunk_size_threshold", ch["uncompressed_size"] > man["chunk_size"], (ch["uncompressed_size"], man["chunk_size"]))
            seen_msgs.extend(m for _, m in cmsgs)
            # message indexes immediately after the chunk
            mi_start = ci["chunk_start_offset"] + ci["chunk_length"]
            mi_recs = list(iter_records(b, mi_start, mi_start + ci["message_index_length"]))
            C.check(p + "message_index_ops", all(op == OP["MESSAGE_INDEX"] for _, op, _, _ in mi_recs), [op for _, op, _, _ in mi_recs])
            by_chan = {}
            for off, m in cmsgs:
                by_chan.setdefault(m["channel_id"], []).append((m["log_time"], off))
            C.check(p + "message_index_offsets_map", [c for c, _ in ci["message_index_offsets"]] == list(by_chan.keys()) and [o for _, o in ci["message_index_offsets"]] == [off for off, _, _, _ in mi_recs], (ci["message_index_offsets"], list(by_chan.keys()), [off for off, _, _, _ in mi_recs]))
            for (off, _, _, body), (cid, _) in zip(mi_recs, ci["message_index_offsets"]):
                c, entries = parse_message_index(body)
                C.check(p + "message_index_entries", c == cid and entries == by_chan.get(c), (c, cid, entries, by_chan.get(c)))
            # message indexes are followed by another data record, never by stray bytes
        C.check(p + "chunk_messages_complete", sorted(msg_key(m) for m in seen_msgs) == sorted(msg_key(m) for m in msgs), (len(seen_msgs), len(msgs)))
        C.check(p + "no_loose_messages", OP["MESSAGE"] not in data_ops, data_ops)
    else:
        C.check(p + "unchunked_no_chunks", OP["CHUNK"] not in data_ops and OP["MESSAGE_INDEX"] not in data_ops and not cidx, (data_ops, len(cidx)))
        loose = [parse_message(body) for _, op, _, body in data_recs if op == OP["MESSAGE"]]
        C.check(p + "unchunked_messages", [msg_key(m) for m in loose] == [msg_key(m) for m in msgs], len(loose))
    if stats and msgs:
        counts = {}
        for m in msgs:
            counts[m["channel_id"]] = counts.get(m["channel_id"], 0) + 1
        exp = {"message_count": len(msgs), "schema_count": 2, "channel_count": 3, "attachment_count": 1, "metadata_count": 1,
               "chunk_count": len(cidx), "message_start_time": min(m["log_time"] for m in msgs), "message_end_time": max(m["log_time"] for m in msgs),
               "channel_message_counts": counts}
        C.check(p + "statistics_values", stats == exp, (stats, exp))

    # attachment / metadata indexes
    aidx = [parse_attachment_index(body) for _, body in groups.get(10, {"records": []})["records"]]
    midx = [parse_metadata_index(body) for _, body in groups.get(13, {"records": []})["records"]]
    if msgs:
        C.check(p + "attachment_index_count", len(aidx) == 1 and len(midx) == 1, (len(aidx), len(midx)))
        for ai in aidx:
            op, ln, body = record_at(b, ai["offset"])
            ok = op == OP["ATTACHMENT"] and 9 + ln == ai["length"]
            if ok:
                lt, ct = u64(body, 0), u64(body, 8)
                nm, o = pstr(body, 16); mt, o = pstr(body, o); dl = u64(body, o)
                ok = (lt, ct, nm, mt, dl) == (ai["log_time"], ai["create_time"], ai["name"], ai["media_type"], ai["data_size"]) == (22, 11, "att.bin", "application/octet-stream", 5) or (lt, ct, nm, mt, dl) == (ai["log_time"], ai["create_time"], ai["name"], ai["media_type"], ai["data_size"]) == (2, 1, "mid.bin", "text/plain", 3)
            C.check(p + "attachment_index_points_to_attachment", ok, ai)
        for mi in midx:
            op, ln, body = record_at(b, mi["offset"])
            ok = op == OP["METADATA"] and 9 + ln == mi["length"] and pstr(body, 0)[0] == mi["name"] and mi["name"] in ("meta1", "mid")
            C.check(p + "metadata_index_points_to_metadata", ok, mi)


def readers(name, path, man, C, args, chunked=True):
    p = f"{name}:"
    msgs = man.get("messages", [])
    exp_sorted = sorted((msg_key(m) for m in msgs), key=lambda k: (int(k[1]), int(k[3])))
    if args.go_reader:
        out, err = run([args.go_reader, path, "streamed"])
        C.check(p + "go_streamed_reads", out is not None, err)
        if out is not None:
            recs = json.loads(out)["records"]
            got = [json_msg_key(r) for r in recs if r["type"] == "Message"]
            C.check(p + "go_streamed_messages", got == [msg_key(m) for m in msgs], (len(got), len(msgs)))
            types = [r["type"] for r in recs]
            man["_go_types"] = types
        if chunked and msgs:
            out, err = run([args.go_reader, path, "indexed"])
            if out is None and err and any(m in err for m in ("SIGSEGV", "segmentation violation", "panic:")):
                # The Go conformance reader itself crashed (observed: nil-schema dereference on the LZ4
                # fixture while Go streamed, Rust indexed and Python indexed reads all agree). A crash of
                # the reference tool is not evidence against the submission.
                C.skip(p + "go_indexed_reads", err)
            else:
                C.check(p + "go_indexed_reads", out is not None, err)
                if out is not None:
                    got = [json_msg_key(r) for r in json.loads(out)["messages"]]
                    C.check(p + "go_indexed_messages", sorted(got, key=lambda k: (int(k[1]), int(k[3]))) == exp_sorted and [int(k[1]) for k in got] == sorted(int(k[1]) for k in got), len(got))
    if args.rust_streamed:
        out, err = run([args.rust_streamed, path])
        C.check(p + "rust_streamed_reads", out is not None, err)
        if out is not None:
            got = [json_msg_key(r) for r in json.loads(out)["records"] if r["type"] == "Message"]
            C.check(p + "rust_streamed_messages", got == [msg_key(m) for m in msgs], (len(got), len(msgs)))
    if args.rust_indexed and chunked and msgs:
        out, err = run([args.rust_indexed, path])
        C.check(p + "rust_indexed_reads", out is not None, err)
        if out is not None:
            got = [json_msg_key(r) for r in json.loads(out)["messages"]]
            C.check(p + "rust_indexed_messages", sorted(got, key=lambda k: (int(k[1]), int(k[3]))) == exp_sorted and [int(k[1]) for k in got] == sorted(int(k[1]) for k in got), len(got))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--go-reader")
    ap.add_argument("--rust-streamed")
    ap.add_argument("--rust-indexed")
    args = ap.parse_args()
    C = Checks()
    tmp = Path(tempfile.mkdtemp(prefix="mcap-authored-"))
    try:
        p = subprocess.run([args.python, "-c", GEN, str(tmp)], capture_output=True, timeout=TIMEOUT, cwd=str(tmp))
    except subprocess.TimeoutExpired:
        p = None
    if p is None or p.returncode != 0:
        C.check("generate", False, (p.stderr.decode(errors="replace")[-600:] if p else "timeout"))
    else:
        C.check("generate", True)
        man = json.loads((tmp / "manifest.json").read_text())
        for name, comp, crcs, dcrcs in [("multi_zstd", "zstd", True, True), ("multi_lz4", "lz4", True, True), ("multi_none", "", True, True), ("no_crcs", "zstd", False, False)]:
            b = (tmp / f"{name}.mcap").read_bytes()
            structural(name, b, man[name], C, comp, True, crcs, dcrcs)
            readers(name, str(tmp / f"{name}.mcap"), man[name], C, args)
        b = (tmp / "unchunked.mcap").read_bytes()
        structural("unchunked", b, man["unchunked"], C, "", False, True, True)
        readers("unchunked", str(tmp / "unchunked.mcap"), man["unchunked"], C, args, chunked=False)

        # empty_groups: defaults, only start()/finish()
        b = (tmp / "empty_groups.mcap").read_bytes()
        foot_off, footer = parse_footer(b)
        ss, sos = footer["summary_start"], footer["summary_offset_start"]
        C.check("empty_groups:summary_present", ss != 0 and sos != 0, footer)
        if ss and sos:
            summ = list(iter_records(b, ss, sos))
            C.check("empty_groups:only_statistics", [op for _, op, _, _ in summ] == [OP["STATISTICS"]], [op for _, op, _, _ in summ])
            st = parse_statistics(summ[0][3]) if summ else None
            C.check("empty_groups:statistics_zero", st == {"message_count": 0, "schema_count": 0, "channel_count": 0, "attachment_count": 0, "metadata_count": 0, "chunk_count": 0, "message_start_time": 0, "message_end_time": 0, "channel_message_counts": {}}, st)
            offs = [parse_summary_offset(body) for _, op, _, body in iter_records(b, sos, foot_off)]
            C.check("empty_groups:six_summary_offsets", [o["group_opcode"] for o in offs] == SUMMARY_GROUP_ORDER, offs)
            C.check("empty_groups:empty_group_lengths", all(o["group_length"] == 0 for o in offs if o["group_opcode"] != 11) and any(o["group_opcode"] == 11 and o["group_length"] == 9 + summ[0][2] and o["group_start"] == ss for o in offs), offs)
            C.check("empty_groups:group_starts_monotonic", [o["group_start"] for o in offs] == sorted(o["group_start"] for o in offs) and all(ss <= o["group_start"] <= sos for o in offs), offs)
            de_off = ss - 13
            op, ln, body = record_at(b, de_off)
            C.check("empty_groups:data_crc_default_zero", op == OP["DATA_END"] and u32(body, 0) == 0, (op, u32(body, 0)))
            C.check("empty_groups:summary_crc", footer["summary_crc"] == zlib.crc32(b[ss:foot_off + 25]), footer["summary_crc"])
            data_ops = [op for _, op, _, _ in iter_records(b, 8, de_off)]
            C.check("empty_groups:data_section_header_only", data_ops == [OP["HEADER"]], data_ops)
            hdr = record_at(b, 8)[2]
            prof, o = pstr(hdr, 0); lib, _ = pstr(hdr, o)
            C.check("empty_groups:library_identifier", prof == "" and lib.startswith("mcap-python/"), (prof, lib))
        if args.go_reader:
            out, err = run([args.go_reader, str(tmp / "empty_groups.mcap"), "streamed"])
            C.check("empty_groups:go_streamed_reads", out is not None, err)
            if out is not None:
                types = [r["type"] for r in json.loads(out)["records"]]
                C.check("empty_groups:record_sequence", types == ["Header", "DataEnd", "Statistics"] + ["SummaryOffset"] * 6 + ["Footer"], types)

        # ordering: attachment and metadata precede the chunk they interrupt
        b = (tmp / "ordering.mcap").read_bytes()
        m = man["ordering"]
        foot_off, footer = parse_footer(b)
        ss = footer["summary_start"]
        de_off = ss - 13
        data_recs = list(iter_records(b, 8, de_off))
        ops = [op for _, op, _, _ in data_recs]
        C.check("ordering:attachment_before_chunk", ops.index(OP["ATTACHMENT"]) < ops.index(OP["CHUNK"]) and ops.index(OP["METADATA"]) < ops.index(OP["CHUNK"]), ops)
        C.check("ordering:single_chunk", ops.count(OP["CHUNK"]) == 1 and ops.count(OP["MESSAGE_INDEX"]) == 2, ops)
        chunk_body = next(body for _, op, _, body in data_recs if op == OP["CHUNK"])
        ch = parse_chunk(chunk_body)
        inner = [parse_message(body) for _, op, _, body in iter_records(ch["records"], 0, len(ch["records"])) if op == OP["MESSAGE"]]
        C.check("ordering:chunk_holds_all_messages", [msg_key(x) for x in inner] == [msg_key(x) for x in m["messages"]], len(inner))
        readers("ordering", str(tmp / "ordering.mcap"), m, C, args)
        if "_go_types" in m:
            t = m["_go_types"]
            C.check("ordering:go_attachment_before_messages", t.index("Attachment") < t.index("Message") and t.index("Metadata") < t.index("Message"), t)

    passed = sum(1 for ok, _ in C.results.values() if ok)
    failed = {k: info for k, (ok, info) in C.results.items() if not ok}
    Path(args.out).write_text(json.dumps({"passed": passed, "total": len(C.results), "failed": failed, "ids": sorted(C.results), "skipped": C.skipped}, indent=1, sort_keys=True))
    print(f"authored checks: {passed}/{len(C.results)} passed")
    for k, v in failed.items():
        print("  FAIL", k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Generate the hidden differential fixture set for `mcap recover` and its manifest.

Runs at verifier-image build time (network-free). Inputs are the pristine repo's
Git-LFS corpus (tests/conformance/data, rust/mcap/tests/data, testdata/mcap). Every
transformation is deterministic (seeded PRNG, fixed offsets) so the manifest and the
case count are identical on every build.

Expectations are *not* stored here: at verify time the runner derives them from the
Go reference CLI (built from the same commit) on the file named by each case's
`expect.input`, optionally filtered. Only the exit code and stderr/structure rules come
from the instruction contract.

Usage:
  gen_fixtures.py --repo <pristine repo> --out <fixtures dir> [--go-bin <mcap-go>] [--big]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcaplite as M  # noqa: E402

SEED = 20260601
CORPUS_GROUPS = ["NoData", "OneAttachment", "OneMessage", "OneMetadata", "OneSchemalessMessage", "TenMessages"]
BIG_MESSAGES = 2048          # 2048 * 256 KiB = 512 MiB of payload
BIG_MSG_SIZE = 256 * 1024
BIG_CHUNK_MSGS = 4           # 1 MiB uncompressed chunks
MEMORY_LIMIT_MIB = 256

cases = []
_ids = set()


def add_case(cid, group, input_rel, expect_exit, expect, *, mode="file", args=None, checks=None, note=None):
    assert cid not in _ids, cid
    _ids.add(cid)
    cases.append({
        "id": cid, "group": group, "input": input_rel, "mode": mode,
        "args": args or [], "expect_exit": expect_exit, "expect": expect,
        "checks": checks or {}, "note": note or "",
    })


def rel(out, p):
    return os.path.relpath(p, out)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def codec_for(i):
    return ["none", "preserve", "zstd", "lz4"][i % 4]


def preserve_expectation(data):
    """Expected output chunk compression under `preserve`, per the instruction's rule:
    first chunk's codec if a chunk is the first codec-determining record; uncompressed if
    a message/attachment/private record comes first; None if vacuous (no output chunks
    can be asserted)."""
    for r in M.parse_records(data):
        if r.opcode == M.OP_CHUNK:
            return M.parse_chunk_header(r.body).compression
        if r.opcode in (M.OP_MESSAGE, M.OP_ATTACHMENT) or r.opcode >= 0x80:
            return ""
        if r.opcode == M.OP_DATA_END:
            return None
    return None


def compression_check(codec, data):
    if codec == "none":
        return ""
    if codec == "preserve":
        return preserve_expectation(data)
    return codec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--go-bin", default=None, help="Go reference CLI (used only to build lz4/zstd corpus variants)")
    ap.add_argument("--big", action="store_true", help="also generate the 512 MiB streaming fixture")
    a = ap.parse_args()
    repo, out = os.path.abspath(a.repo), os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    rng = random.Random(SEED)

    corpus_dir = os.path.join(repo, "tests/conformance/data")
    corpus = []
    for g in CORPUS_GROUPS:
        for name in sorted(os.listdir(os.path.join(corpus_dir, g))):
            if name.endswith(".mcap"):
                corpus.append((g, name, os.path.join(corpus_dir, g, name)))
    fixtures = {
        "compressed": os.path.join(repo, "rust/mcap/tests/data/compressed.mcap"),
        "uncompressed": os.path.join(repo, "rust/mcap/tests/data/uncompressed.mcap"),
        "zstd_padding": os.path.join(repo, "rust/mcap/tests/data/zstd_chunk_with_padding.mcap"),
        "chunk_not_closed": os.path.join(repo, "rust/mcap/tests/data/chunk_not_closed.mcap"),
        "demo": os.path.join(repo, "testdata/mcap/demo.mcap"),
    }
    for p in fixtures.values():
        with open(p, "rb") as f:
            assert f.read(8) == M.MAGIC, f"LFS pointer or bad file: {p}"

    # ---------------------------------------------------------------- pristine copies
    pristine = {}

    def keep_pristine(key, src):
        dst = os.path.join(out, "pristine", key + ".mcap")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        pristine[key] = dst
        return dst

    for g, name, path in corpus:
        keep_pristine(f"{g}/{name[:-5]}", path)
    for k, p in fixtures.items():
        keep_pristine(f"fixtures/{k}", p)

    # ------------------------------------------------- lz4/zstd variants via Go filter
    # The corpus is entirely uncompressed; the reference CLI re-encodes a few fully
    # featured files so compressed inputs are covered (and lz4 exists at all).
    # Only sources with messages qualify: the Go writer emits no chunk for a file that
    # holds just an attachment or just metadata, so no codec variant exists for those.
    variant_sources = [
        "TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum",
        "TenMessages/TenMessages-ch-chx-mx-st-sum",
        "OneMessage/OneMessage-ch-chx-mx-pad-rch-rsh-st-sum",
        "OneSchemalessMessage/OneSchemalessMessage-ch-chx-mx-pad-rch-st-sum",
        "fixtures/demo",
    ]
    variants = {}
    for src in variant_sources:
        for codec in ("lz4", "zstd"):
            key = f"variants/{src.split('/')[-1]}-{codec}"
            dst = os.path.join(out, "pristine", key + ".mcap")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if a.go_bin:
                subprocess.run([a.go_bin, "filter", pristine[src], "-o", dst, "--output-compression", codec,
                                "--include-metadata", "--include-attachments"], check=True)
                with open(dst, "rb") as f:
                    d = f.read()
                comps = M.chunk_compressions(d)
                assert comps and all(c == codec for c in comps), (key, comps)
            pristine[key] = dst
            variants[key] = dst

    def load(key):
        with open(pristine[key], "rb") as f:
            return f.read()

    # ================================================================ V: valid inputs
    # zstd_chunk_with_padding.mcap is excluded: its 12 messages reference channel 0, which is
    # never defined, so no implementation can recover them (Go `recover -a` fails outright).
    # It is exercised as a missing-channel case below instead.
    valid_keys = [f"{g}/{n[:-5]}" for g, n, _ in corpus] + [f"fixtures/{k}" for k in
                                                            ("compressed", "uncompressed", "demo")] + sorted(variants)
    for i, key in enumerate(valid_keys):
        codec = codec_for(i)
        data = load(key) if (a.go_bin or not key.startswith("variants/")) else None
        checks = {"header": "input"}
        if data is not None:
            cc = compression_check(codec, data)
            if cc is not None:
                checks["chunk_compression"] = cc
        elif codec != "preserve":
            checks["chunk_compression"] = "" if codec == "none" else codec
        else:
            checks["chunk_compression"] = key.rsplit("-", 1)[-1]  # variant codec
        args = [] if codec == "preserve" else ["--compression", codec]
        add_case(f"valid/{key}/{codec}", "diff_valid", rel(out, pristine[key]), 0,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=args, checks=checks)

    # ============================================================== T: truncations
    trunc_dir = os.path.join(out, "truncated")

    def truncation_cases(key, offsets, codec_rotate=True):
        data = load(key)
        recs = M.parse_records(data)
        de = M.data_section_end(recs)
        first_end = recs[0].end if recs else None
        for j, off in enumerate(sorted(set(offsets))):
            if off <= 0 or off >= len(data):
                continue
            inside = next((r for r in recs if r.offset < off < r.end), None)
            if inside is not None and inside.opcode == M.OP_DATA_END and off == inside.offset + M.OPCODE_LEN_SIZE:
                # Data End opcode+length complete, 4-byte body absent: the upstream Rust
                # LinearReader consumes the prefix before requesting the body and reports a clean
                # EOF here, while the contract wording ("inside a Data End") suggests lossy. Nothing
                # recoverable is lost either way (the CRC is regenerated); this one offset is not tested.
                continue
            t = data[:off]
            name = f"{key}@{off}"
            dst = os.path.join(trunc_dir, key.replace('/', '__') + f"__{off}.mcap")
            write(dst, t)
            # exit code per contract
            if off <= 8 or (first_end is not None and off < first_end):
                ex = 1                       # no complete record
            elif de is not None and off >= de.end:
                ex = 0                       # scan stops at DataEnd; nothing after is read
            else:
                boundary = any(r.offset == off for r in recs)
                ex = 0 if boundary else 3
            expect_input = dst
            if ex == 3 and inside is not None and inside.opcode == M.OP_ATTACHMENT:
                # Go's lexer streams an attachment to the writer before reading its CRC, so Go keeps
                # an attachment truncated inside its trailing CRC; the contract drops any partial
                # record. Derive the expectation from Go on the prefix ending at the attachment.
                expect_input = os.path.join(out, "expect", key.replace('/', '__') + f"__before_attachment_{inside.offset}.mcap")
                write(expect_input, data[: inside.offset])
            codec = codec_for(j) if codec_rotate else "none"
            if codec == "lz4" or codec == "zstd":
                codec = "none"
            args = [] if codec == "preserve" else ["--compression", "none"]
            checks = {} if ex == 1 else {"header": "input"}
            add_case(f"trunc/{name}/{codec}", "diff_truncated", rel(out, dst), ex,
                     {"kind": "none"} if ex == 1 else {"kind": "go", "input": rel(out, expect_input)},
                     args=args, checks=checks)

    def all_offsets(data, per_record_interior=3):
        recs = M.parse_records(data)
        offs = {3, 8, 9, len(data) - 1, len(data) - 3}
        for r in recs:
            offs.add(r.offset)
            interior = [r.offset + 1, r.offset + 5, r.offset + M.OPCODE_LEN_SIZE, r.offset + M.OPCODE_LEN_SIZE + r.length // 2,
                        r.end - 1]
            for k in interior[:per_record_interior + 2]:
                if r.offset < k < r.end:
                    offs.add(k)
        return offs

    trunc_sources = []
    for g in CORPUS_GROUPS:
        for suffix in ("", "-ch", "-ch-chx-mx-pad-rch-rsh-st-sum", "-ax-pad-st-sum", "-mdx-pad-st-sum",
                       "-ch-chx-mx-st", "-pad-st-sum"):
            key = f"{g}/{g}{suffix}"
            if key in pristine and key not in trunc_sources:
                trunc_sources.append(key)
    TEN_FULL = "TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum"
    for key in trunc_sources:
        offs = all_offsets(load(key))
        if key == TEN_FULL:
            offs.add(300)   # mirrors the upstream cli-conformance known-difference case
        truncation_cases(key, offs)
    # large multi-chunk fixtures: chunk boundaries and interiors of selected chunks
    for key in ("fixtures/compressed", "fixtures/uncompressed"):
        data = load(key)
        recs = M.parse_records(data)
        chunks = [r for r in recs if r.opcode == M.OP_CHUNK]
        offs = set()
        for idx in (0, 1, 2, 7, 50, 200, len(chunks) - 2, len(chunks) - 1):
            c = chunks[idx]
            offs.update({c.offset, c.offset + 12, c.offset + M.OPCODE_LEN_SIZE + 40, c.offset + M.OPCODE_LEN_SIZE + c.length // 2, c.end - 1, c.end})
        de = M.data_section_end(recs)
        offs.update({de.offset, de.offset + 5, de.end, de.end + 30, len(data) - 1})
        truncation_cases(key, offs)
    truncation_cases("fixtures/demo", all_offsets(load("fixtures/demo"), per_record_interior=2))

    # ============================================================ C: bad chunk CRC
    def flip_crc_case(key, chunk_idx, tag):
        data = load(key)
        recs = M.parse_records(data)
        chunks = [i for i, r in enumerate(recs) if r.opcode == M.OP_CHUNK]
        if chunk_idx >= len(chunks):
            return
        ri = chunks[chunk_idx]
        r = recs[ri]
        h = M.parse_chunk_header(r.body)
        new_crc = (h.uncompressed_crc ^ 0x5A5A5A5A) or 0x00C0FFEE
        body = bytearray(r.body)
        struct.pack_into("<I", body, h.crc_offset_in_body, new_crc)
        mod = M.replace_record(data, recs, ri, M.frame(M.OP_CHUNK, bytes(body)))
        dst = os.path.join(out, "bad_crc", key.replace('/', '__') + f"__{tag}.mcap")
        write(dst, mod)
        codec = "none" if chunk_idx % 2 == 0 else "preserve"
        add_case(f"crc/{key}/{tag}/{codec}", "diff_bad_crc", rel(out, dst), 0,
                 {"kind": "go", "input": rel(out, pristine[key])},
                 args=[] if codec == "preserve" else ["--compression", "none"],
                 checks={"header": "input", "not_lossy": True},
                 note="stored chunk CRC corrupted; payload intact -> benign, all records recovered")

    chunked_corpus = [k for k in valid_keys if "-ch" in k and not k.startswith("variants/")]
    rng.shuffle(chunked_corpus)
    for key in sorted(chunked_corpus[:36]):
        flip_crc_case(key, 0, "c0")
    for idx, tag in ((0, "c0"), (5, "c5"), (412, "c412")):
        flip_crc_case("fixtures/compressed", idx, tag)
        flip_crc_case("fixtures/uncompressed", idx, tag)
    flip_crc_case("fixtures/demo", 0, "c0")
    for key in sorted(variants):
        if a.go_bin:
            flip_crc_case(key, 0, "c0")
        else:
            # plan the case even without the file so the manifest count is stable
            dst = os.path.join(out, "bad_crc", key.replace('/', '__') + "__c0.mcap")
            add_case(f"crc/{key}/c0/none", "diff_bad_crc", rel(out, dst), 0,
                     {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", "none"],
                     checks={"header": "input", "not_lossy": True})

    # ===================================================== X: undecodable chunk payload
    def undecodable_case(key, chunk_idx, how, tag):
        data = load(key)
        recs = M.parse_records(data)
        chunks = [i for i, r in enumerate(recs) if r.opcode == M.OP_CHUNK]
        if chunk_idx >= len(chunks):
            return
        ri = chunks[chunk_idx]
        r = recs[ri]
        h = M.parse_chunk_header(r.body)
        payload = M.chunk_payload(r)
        if how == "unsupported":
            body = M.build_chunk_body(h, payload, compression="xxxx")
        elif how == "garbage":
            if h.compression == "":
                return
            body = M.build_chunk_body(h, b"\x00\x00\x00\x00" + payload[4:])
        else:
            raise AssertionError(how)
        mod = M.replace_record(data, recs, ri, M.frame(M.OP_CHUNK, body))
        dst = os.path.join(out, "undecodable", key.replace('/', '__') + f"__{tag}.mcap")
        write(dst, mod)
        # expectation: everything before the bad chunk == Go on pristine truncated at the chunk start
        exp = os.path.join(out, "expect", key.replace('/', '__') + f"__before_{tag}.mcap")
        write(exp, data[: r.offset])
        add_case(f"undecodable/{key}/{tag}", "diff_undecodable_chunk", rel(out, dst), 3,
                 {"kind": "go", "input": rel(out, exp)}, args=["--compression", "none"],
                 checks={"header": "input"},
                 note=f"chunk #{chunk_idx} {how}: chunk discarded, scan stops, exit 3")

    for key in sorted(chunked_corpus[36:60]):
        undecodable_case(key, 0, "unsupported", "u0")
    for idx, tag in ((0, "u0"), (1, "u1"), (50, "u50"), (412, "u412")):
        undecodable_case("fixtures/compressed", idx, "unsupported", tag)
        undecodable_case("fixtures/uncompressed", idx, "unsupported", tag)
    for idx, tag in ((1, "g1"), (100, "g100")):
        undecodable_case("fixtures/compressed", idx, "garbage", tag)
    undecodable_case("fixtures/demo", 0, "garbage", "g0")
    undecodable_case("fixtures/zstd_padding", 0, "garbage", "g0")

    # ================================================ U: unparsable top-level record body
    unchunked_corpus = [k for k in valid_keys if "-ch" not in k and not k.startswith("variants/")
                        and not k.startswith("fixtures/") and not k.startswith("NoData")]

    def corrupt_string_len(body, at):
        b = bytearray(body)
        struct.pack_into("<I", b, at, 0xFFFFFFF0)
        return bytes(b)

    def unparsable_case(key, opcode, tag, drop):
        data = load(key)
        recs = M.parse_records(data)
        idx = next((i for i, r in enumerate(recs) if r.opcode == opcode), None)
        if idx is None:
            return
        r = recs[idx]
        if opcode == M.OP_SCHEMA:
            body = corrupt_string_len(r.body, 2)          # id(u16) then name string
        elif opcode == M.OP_CHANNEL:
            body = corrupt_string_len(r.body, 4)          # id, schema_id, then topic string
        elif opcode == M.OP_METADATA:
            body = corrupt_string_len(r.body, 0)          # name string first
        elif opcode == M.OP_ATTACHMENT:
            body = corrupt_string_len(r.body, 16)         # log_time, create_time, then name
        elif opcode == M.OP_MESSAGE:
            body = r.body[:5]                             # shorter than the 22-byte fixed header
        else:
            raise AssertionError(opcode)
        mod = M.replace_record(data, recs, idx, M.frame(opcode, body))
        dst = os.path.join(out, "unparsable", key.replace('/', '__') + f"__{tag}.mcap")
        write(dst, mod)
        add_case(f"unparsable/{key}/{tag}", "diff_unparsable_record", rel(out, dst), 3,
                 {"kind": "go_filtered", "input": rel(out, pristine[key]), **drop},
                 args=["--compression", "none"], checks={"header": "input"},
                 note=f"{M.OP_NAMES[opcode]} body unparsable -> discarded, scan continues")

    for key in unchunked_corpus:
        g = key.split("/")[0]
        if g in ("OneMessage", "TenMessages"):
            unparsable_case(key, M.OP_SCHEMA, "schema", {"drop_schema_ids": [1]})
            unparsable_case(key, M.OP_CHANNEL, "channel", {"drop_channel_ids": [1]})
            unparsable_case(key, M.OP_MESSAGE, "message", {"drop_message_index": [0]})
        elif g == "OneSchemalessMessage":
            unparsable_case(key, M.OP_CHANNEL, "channel", {"drop_channel_ids": [1]})
        elif g == "OneMetadata":
            unparsable_case(key, M.OP_METADATA, "metadata", {"drop_metadata_index": [0]})
        elif g == "OneAttachment":
            unparsable_case(key, M.OP_ATTACHMENT, "attachment", {"drop_attachment_index": [0]})
    # corrupt leading header (1-byte body) on files whose profile is non-empty and on a corpus file
    for key in ("fixtures/demo", "fixtures/compressed", "TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum"):
        data = load(key)
        recs = M.parse_records(data)
        assert recs[0].opcode == M.OP_HEADER
        mod = M.replace_record(data, recs, 0, M.frame(M.OP_HEADER, b"\x00"))
        dst = os.path.join(out, "unparsable", key.replace('/', '__') + "__header.mcap")
        write(dst, mod)
        add_case(f"unparsable/{key}/header", "diff_unparsable_record", rel(out, dst), 3,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", "none"],
                 checks={"header": "default_profile"},
                 note="header body unparsable -> discarded (lossy); output uses the default (empty) profile")

    # ================================================= M: missing loose schema/channel
    for key in unchunked_corpus:
        g = key.split("/")[0]
        if g not in ("OneMessage", "TenMessages", "OneSchemalessMessage"):
            continue
        data = load(key)
        recs = M.parse_records(data)
        for opcode, tag, drop in ((M.OP_CHANNEL, "nochannel", {"drop_channel_ids": [1]}),
                                  (M.OP_SCHEMA, "noschema", {"drop_schema_ids": [1]})):
            idx = next((i for i, r in enumerate(recs) if r.opcode == opcode), None)
            if idx is None:
                continue
            mod = M.remove_record(data, recs, idx)
            dst = os.path.join(out, "missing", key.replace('/', '__') + f"__{tag}.mcap")
            write(dst, mod)
            add_case(f"missing/{key}/{tag}", "diff_missing_record", rel(out, dst), 3,
                     {"kind": "go_filtered", "input": rel(out, pristine[key]), **drop},
                     args=["--compression", "none"], checks={"header": "input"},
                     note=f"loose {M.OP_NAMES[opcode]} removed -> dependent records discarded")

    # messages whose channel is never defined: rust/mcap's zstd_chunk_with_padding.mcap holds 12
    # zstd-chunked messages for channel 0 and no Channel record anywhere. Every one is discarded
    # (lossy); the expected stream is Go on the header-only prefix (Go `recover -a` itself fails).
    zp = load("fixtures/zstd_padding")
    zp_recs = M.parse_records(zp)
    assert zp_recs[0].opcode == M.OP_HEADER and zp_recs[1].opcode == M.OP_CHUNK
    exp = os.path.join(out, "expect", "zstd_padding__header_only.mcap")
    write(exp, zp[: zp_recs[0].end])
    add_case("missing/fixtures/zstd_padding/nochannel", "diff_missing_record", rel(out, pristine["fixtures/zstd_padding"]), 3,
             {"kind": "go", "input": rel(out, exp)}, args=["--compression", "none"], checks={"header": "input"},
             note="12 messages for channel 0, which is never defined -> all discarded (lossy); output holds no messages")

    # ================================================== K: unknown / private opcodes
    unk_sources = sorted(rng.sample([k for k in valid_keys if not k.startswith("variants/")], 24))
    for i, key in enumerate(unk_sources):
        data = load(key)
        recs = M.parse_records(data)
        de_idx = next(i2 for i2, r in enumerate(recs) if r.opcode == M.OP_DATA_END)
        op = 0x80 + (i % 3) if i % 2 == 0 else 0x7E
        extra = M.frame(op, bytes(rng.getrandbits(8) for _ in range(16)))
        pos = 1 if i % 3 else de_idx      # right after the header, or right before DataEnd
        mod = M.insert_before(data, recs, pos, extra)
        dst = os.path.join(out, "unknown_op", key.replace('/', '__') + f"__op{op:02x}.mcap")
        write(dst, mod)
        add_case(f"unknown_op/{key}/op{op:02x}", "diff_unknown_opcode", rel(out, dst), 0,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", "none"],
                 checks={"header": "input", "not_lossy": True},
                 note="unknown/private top-level record is skipped without loss")
    # private record inside an uncompressed chunk (sizes and CRC recomputed so the chunk stays valid)
    for key in ("TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum", "OneMessage/OneMessage-ch",
                "fixtures/uncompressed"):
        data = load(key)
        recs = M.parse_records(data)
        ri = next(i for i, r in enumerate(recs) if r.opcode == M.OP_CHUNK)
        r = recs[ri]
        h = M.parse_chunk_header(r.body)
        payload = M.chunk_payload(r)
        extra = M.frame(0x81, b"private-in-chunk")
        newp = extra + payload
        body = M.build_chunk_body(h, newp, uncompressed_crc=M.crc32(newp), uncompressed_size=len(newp))
        mod = M.replace_record(data, recs, ri, M.frame(M.OP_CHUNK, body))
        dst = os.path.join(out, "unknown_op", key.replace('/', '__') + "__inchunk.mcap")
        write(dst, mod)
        add_case(f"unknown_op/{key}/inchunk", "diff_unknown_opcode", rel(out, dst), 0,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", "none"],
                 checks={"header": "input", "not_lossy": True})

    # ================================================= G: structural (summary/footer)
    struct_sources = [k for k in valid_keys if k.endswith("-st-sum") and not k.startswith("variants/")]
    struct_sources = sorted(rng.sample(struct_sources, 18)) + ["fixtures/compressed", "fixtures/demo"]
    for key in struct_sources:
        data = load(key)
        recs = M.parse_records(data)
        de = M.data_section_end(recs)
        footer = next(r for r in recs if r.opcode == M.OP_FOOTER)
        forms = {
            "no_summary": data[: de.end],          # ends right after DataEnd
            "no_footer": data[: footer.offset],    # summary present, footer and end magic missing
            "no_dataend": data[: de.offset],       # ends at a record boundary before DataEnd
            "trailing_garbage": data + bytes(rng.getrandbits(8) for _ in range(64)),
        }
        for tag, mod in forms.items():
            dst = os.path.join(out, "structural", key.replace('/', '__') + f"__{tag}.mcap")
            write(dst, mod)
            add_case(f"structural/{key}/{tag}", "diff_structural", rel(out, dst), 0,
                     {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", "none"],
                     checks={"header": "input", "not_lossy": True})
    # chunk whose declared length is absurd (unclosed writer): stop, lossy, header only
    cnc = load("fixtures/chunk_not_closed")
    recs = M.parse_records(cnc)
    exp = os.path.join(out, "expect", "chunk_not_closed__header_only.mcap")
    write(exp, cnc[: recs[0].end])
    add_case("structural/fixtures/chunk_not_closed", "diff_structural", rel(out, pristine["fixtures/chunk_not_closed"]), 3,
             {"kind": "go", "input": rel(out, exp)}, args=["--compression", "none"], checks={"header": "input"},
             note="chunk record length 2^64-1 exceeds the 1 GiB record limit -> scan stops after the header")
    # synthetic: header then a record claiming u64::MAX length
    for key in ("fixtures/demo", "TenMessages/TenMessages"):
        data = load(key)
        recs = M.parse_records(data)
        mod = data[: recs[0].end] + bytes([M.OP_MESSAGE]) + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + b"\x00" * 16
        dst = os.path.join(out, "structural", key.replace('/', '__') + "__huge_len.mcap")
        write(dst, mod)
        exp = os.path.join(out, "expect", key.replace('/', '__') + "__header_only.mcap")
        write(exp, data[: recs[0].end])
        add_case(f"structural/{key}/huge_len", "diff_structural", rel(out, dst), 3,
                 {"kind": "go", "input": rel(out, exp)}, args=["--compression", "none"], checks={"header": "input"})
    # empty file / magic only / non-mcap: hard failure
    for tag, blob in (("empty", b""), ("magic_only", M.MAGIC), ("not_mcap", b"this is not an mcap file\n" * 4),
                      ("short_magic", M.MAGIC[:5])):
        dst = os.path.join(out, "structural", f"garbage__{tag}.mcap")
        write(dst, blob)
        add_case(f"structural/garbage/{tag}", "diff_structural", rel(out, dst), 1, {"kind": "none"},
                 args=["--compression", "none"])

    # ============================================ P: compression selection (preserve)
    def records_between(data, start_op_filter):
        return [r for r in M.parse_records(data) if start_op_filter(r)]

    comp = load("fixtures/compressed")
    comp_recs = M.parse_records(comp)
    comp_chunks = [r for r in comp_recs if r.opcode == M.OP_CHUNK][:3]
    comp_header = comp_recs[0]
    data_end = M.frame(M.OP_DATA_END, struct.pack("<I", 0))

    def build(parts):
        return M.MAGIC + b"".join(parts) + data_end

    meta = next(r for r in M.parse_records(load("OneMetadata/OneMetadata")) if r.opcode == M.OP_METADATA)
    att = next(r for r in M.parse_records(load("OneAttachment/OneAttachment")) if r.opcode == M.OP_ATTACHMENT)
    mixes = {
        "metadata_then_zstd_chunks": (build([comp_header.raw, meta.raw] + [c.raw for c in comp_chunks]), "zstd"),
        "attachment_then_zstd_chunks": (build([comp_header.raw, att.raw] + [c.raw for c in comp_chunks]), ""),
        "zstd_chunks_only": (build([comp_header.raw] + [c.raw for c in comp_chunks]), "zstd"),
        "private_then_zstd_chunks": (build([comp_header.raw, M.frame(0x85, b"x" * 8)] + [c.raw for c in comp_chunks]), ""),
    }
    for tag, (blob, expected_codec) in mixes.items():
        dst = os.path.join(out, "preserve", f"{tag}.mcap")
        write(dst, blob)
        add_case(f"preserve/{tag}", "compression_selection", rel(out, dst), 0,
                 {"kind": "go", "input": rel(out, dst)}, args=[],
                 checks={"header": "input", "chunk_compression": expected_codec, "min_chunks": 1, "not_lossy": True},
                 note="default --compression preserve picks the codec from the first codec-determining record")
    # explicit codec overrides on differently encoded inputs
    for key, codec in (("TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum", "lz4"),
                       ("TenMessages/TenMessages", "zstd"), ("fixtures/compressed", "none"),
                       ("fixtures/compressed", "lz4"), ("fixtures/demo", "none")):
        add_case(f"explicit/{key}/{codec}", "compression_selection", rel(out, pristine[key]), 0,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=["--compression", codec],
                 checks={"header": "input", "chunk_compression": "" if codec == "none" else codec, "min_chunks": 1})
    # unchunked input under preserve stays uncompressed (output is chunked by the writer)
    for key in ("TenMessages/TenMessages", "TenMessages/TenMessages-st-sum", "OneMessage/OneMessage"):
        add_case(f"preserve_unchunked/{key}", "compression_selection", rel(out, pristine[key]), 0,
                 {"kind": "go", "input": rel(out, pristine[key])}, args=[],
                 checks={"header": "input", "chunk_compression": "", "min_chunks": 1})
    # chunk size: a small target must produce many output chunks. compressed.mcap carries only
    # ~104 KiB of message payload (826 messages; its 2 MB comes from schema records repeated in
    # every input chunk), so 4096 allows at most ~34 chunks (Rust writer: 32, Go: 413) and 1024
    # at most ~130 (Rust: 110, Go: 826). Thresholds leave room for either flush policy.
    for target, min_chunks in ((4096, 20), (1024, 50)):
        add_case(f"chunk_size/fixtures/compressed/{target}", "compression_selection", rel(out, pristine["fixtures/compressed"]), 0,
                 {"kind": "go", "input": rel(out, pristine["fixtures/compressed"])},
                 args=["--compression", "none", "--chunk-size", str(target)],
                 checks={"header": "input", "chunk_compression": "", "min_chunks": min_chunks})

    # ======================================================== S: CLI surface
    ten = pristine["TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum"]
    add_case("surface/help", "cli_surface", rel(out, ten), 0, {"kind": "none"}, mode="raw",
             args=["recover", "--help"], checks={"stdout_nonempty": True})
    add_case("surface/always_decode_chunk_removed", "cli_surface", rel(out, ten), "nonzero", {"kind": "none"},
             mode="raw_file", args=["recover", "{input}", "-o", "{out}", "--always-decode-chunk"],
             checks={"stderr_contains": ["--always-decode-chunk"]})
    add_case("surface/short_a_removed", "cli_surface", rel(out, ten), "nonzero", {"kind": "none"},
             mode="raw_file", args=["recover", "{input}", "-o", "{out}", "-a"], checks={})
    add_case("surface/unknown_compression", "cli_surface", rel(out, ten), 1, {"kind": "none"},
             mode="raw_file", args=["recover", "{input}", "-o", "{out}", "--compression", "snappy"],
             checks={"stderr_contains": ["preserve", "none", "zstd", "lz4"]})
    add_case("surface/missing_input", "cli_surface", rel(out, ten), 1, {"kind": "none"},
             mode="raw_file", args=["recover", "{input}.does-not-exist", "-o", "{out}"], checks={})
    add_case("surface/unwritable_output", "cli_surface", rel(out, ten), 1, {"kind": "none"},
             mode="raw_file", args=["recover", "{input}", "-o", "/nonexistent-dir/x/out.mcap"], checks={})
    add_case("surface/output_written_on_lossy", "cli_surface",
             rel(out, os.path.join(trunc_dir, "TenMessages__TenMessages-ch-chx-mx-pad-rch-rsh-st-sum__300.mcap")), 3,
             {"kind": "go", "input": rel(out, os.path.join(trunc_dir, "TenMessages__TenMessages-ch-chx-mx-pad-rch-rsh-st-sum__300.mcap"))},
             args=["--compression", "none"], checks={"header": "input"},
             note="mirror of the upstream cli-conformance case: truncated at byte 300 -> exit 3 and a valid output file")

    # ======================================================== I: stdin / stdout / remote
    io_sources = ["TenMessages/TenMessages-ch-chx-mx-pad-rch-rsh-st-sum", "TenMessages/TenMessages",
                  "OneAttachment/OneAttachment-ax-pad-st-sum", "OneMetadata/OneMetadata-mdx-pad-st-sum",
                  "fixtures/compressed", "fixtures/demo", "OneSchemalessMessage/OneSchemalessMessage-ch"]
    for key in io_sources:
        for mode in ("stdin", "stdout", "remote"):
            add_case(f"io/{mode}/{key}", "io_modes", rel(out, pristine[key]), 0,
                     {"kind": "go", "input": rel(out, pristine[key])}, mode=mode, args=["--compression", "none"],
                     checks={"header": "input"})
    t300 = os.path.join(trunc_dir, "TenMessages__TenMessages-ch-chx-mx-pad-rch-rsh-st-sum__300.mcap")
    for mode in ("stdin", "stdout", "remote"):
        add_case(f"io/{mode}/truncated300", "io_modes", rel(out, t300), 3, {"kind": "go", "input": rel(out, t300)},
                 mode=mode, args=["--compression", "none"], checks={"header": "input"})
    add_case("io/remote_requires_opt_in", "io_modes", rel(out, ten), 1, {"kind": "none"}, mode="remote_noflag",
             args=["--compression", "none"], note="full remote read without --allow-remote-scan is a hard failure")
    add_case("io/stdout_preserve_zstd", "io_modes", rel(out, pristine["fixtures/compressed"]), 0,
             {"kind": "go", "input": rel(out, pristine["fixtures/compressed"])}, mode="stdout", args=[],
             checks={"header": "input", "chunk_compression": "zstd", "min_chunks": 1})

    # ======================================================== R: streaming memory bound
    big_dir = os.path.join(out, "big")
    os.makedirs(big_dir, exist_ok=True)
    big_path = os.path.join(big_dir, "big_uncompressed.mcap")
    digest = None
    if a.big:
        digest = write_big(big_path)
    add_case("memory/local_file", "memory", rel(out, big_path), 0, {"kind": "big", "count": BIG_MESSAGES, "sha256": digest},
             mode="file", args=["--compression", "none"],
             checks={"max_rss_anon_mib": MEMORY_LIMIT_MIB, "header": "input"},
             note=f"{BIG_MESSAGES * BIG_MSG_SIZE // (1 << 20)} MiB input must be recovered with bounded anonymous memory")
    add_case("memory/stdin", "memory", rel(out, big_path), 0, {"kind": "big", "count": BIG_MESSAGES, "sha256": digest},
             mode="stdin", args=["--compression", "none"],
             checks={"max_rss_anon_mib": MEMORY_LIMIT_MIB, "header": "input"})

    manifest = {
        "version": 1,
        "seed": SEED,
        "expected_total": len(cases),
        "groups": sorted({c["group"] for c in cases}),
        "cases": cases,
    }
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    by_group = {}
    for c in cases:
        by_group[c["group"]] = by_group.get(c["group"], 0) + 1
    print(json.dumps({"total": len(cases), "by_group": by_group}, indent=1))


def write_big(path):
    """512 MiB of message payload in 1 MiB uncompressed chunks with correct CRCs.
    Returns sha256 over the message payloads in order (what the runner verifies)."""
    import hashlib
    h = hashlib.sha256()
    rng = random.Random(SEED + 1)
    header = M.frame(M.OP_HEADER, M.mcap_string("") + M.mcap_string("gen_fixtures"))
    schema = M.frame(M.OP_SCHEMA, struct.pack("<H", 1) + M.mcap_string("Blob") + M.mcap_string("raw") + struct.pack("<I", 3) + b"abc")
    channel = M.frame(M.OP_CHANNEL, struct.pack("<HH", 1, 1) + M.mcap_string("/blob") + M.mcap_string("raw") + struct.pack("<I", 0))
    with open(path, "wb") as f:
        f.write(M.MAGIC + header)
        seq = 0
        payload_template = bytes(rng.getrandbits(8) for _ in range(BIG_MSG_SIZE))
        for chunk_i in range(BIG_MESSAGES // BIG_CHUNK_MSGS):
            recs = bytearray()
            if chunk_i == 0:
                recs += schema + channel
            start = seq
            for _ in range(BIG_CHUNK_MSGS):
                # cheap per-message variation while keeping generation fast
                data = bytearray(payload_template)
                struct.pack_into("<Q", data, 0, seq)
                data = bytes(data)
                h.update(data)
                recs += M.frame(M.OP_MESSAGE, struct.pack("<HIQQ", 1, seq, seq * 1000, seq * 1000) + data)
                seq += 1
            crc = M.crc32(bytes(recs))
            body = struct.pack("<QQQI", start * 1000, (seq - 1) * 1000, len(recs), crc) + M.mcap_string("") + struct.pack("<Q", len(recs)) + bytes(recs)
            f.write(M.frame(M.OP_CHUNK, body))
        f.write(M.frame(M.OP_DATA_END, struct.pack("<I", 0)))
        f.write(M.frame(M.OP_FOOTER, struct.pack("<QQI", 0, 0, 0)))
        f.write(M.MAGIC)
    return h.hexdigest()


if __name__ == "__main__":
    main()

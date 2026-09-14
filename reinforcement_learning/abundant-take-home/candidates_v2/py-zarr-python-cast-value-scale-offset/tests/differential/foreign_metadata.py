"""Interop check: arrays whose metadata was written by hand / whose chunks were written by zarrs.

The submitted zarr-python must

  * open hand-written ``zarr.json`` documents that use the ``cast_value`` codec in its
    JSON form (scalar_map as lists of 2-element arrays, optional fields absent or present),
  * decode chunks that the Rust reference (zarrs) encoded, to the same bytes zarrs decodes,
  * re-encode the same source values into a metadata-only copy and produce a byte-identical
    chunk,
  * expose the parsed codec configuration through ``metadata.to_dict()`` with the same
    meaning as the original document,
  * reject invalid documents (unknown configuration keys, wrap on a float target, an
    unsupported target data type, an unrepresentable scalar_map entry) at open time, exactly
    like the reference does.

Usage: foreign_metadata.py --oracle BIN --workdir DIR --out results.json [--expect N]
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Oracle,
    array_to_hex,
    array_metadata,
    clone_metadata_only,
    eprint,
    read_chunk,
    values_to_hex,
    write_metadata,
    write_results,
)

BYTES_LE = {"name": "bytes", "configuration": {"endian": "little"}}


def cv(config: dict[str, Any]) -> dict[str, Any]:
    return {"name": "cast_value", "configuration": config}


VALID_CASES: list[dict[str, Any]] = [
    {
        "id": "cv_minimal_f32_to_i16",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "int16"}),
        "values": [0.0, 1.0, 2.0, 3.0, -7.0, 1000.0],
    },
    {
        "id": "cv_full_json_lists_f64_to_u8",
        "data_type": "float64",
        "fill_value": "NaN",
        "codec": cv(
            {
                "data_type": "uint8",
                "rounding": "towards-zero",
                "out_of_range": "clamp",
                "scalar_map": {"encode": [["NaN", 0], ["Infinity", 255]], "decode": [[0, "NaN"]]},
            }
        ),
        "values": [0.0, 1.9, math.nan, math.inf, 300.0, -5.0, 2.5],
    },
    {
        "id": "cv_rounding_only_f32_to_i32",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "int32", "rounding": "nearest-away"}),
        "values": [0.5, 1.5, 2.5, -0.5, -1.5, 3.0],
    },
    {
        "id": "cv_explicit_defaults_i32_to_f32",
        "data_type": "int32",
        "fill_value": 0,
        "codec": cv({"data_type": "float32", "rounding": "nearest-even"}),
        "values": [0, 1, -2, 1000000, 16777216],
    },
    {
        "id": "cv_wrap_i32_to_i8",
        "data_type": "int32",
        "fill_value": 0,
        "codec": cv({"data_type": "int8", "out_of_range": "wrap"}),
        "values": [0, 127, 128, 255, 256, -129],
    },
    {
        "id": "cv_clamp_u16_to_u8",
        "data_type": "uint16",
        "fill_value": 0,
        "codec": cv({"data_type": "uint8", "out_of_range": "clamp"}),
        "values": [0, 255, 256, 65535],
    },
    {
        "id": "cv_f32_to_f16_nearest_even",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "float16"}),
        "values": [0.1, 1.0, 65504.0, -2.5, 0.0],
    },
    {
        "id": "cv_decode_map_only_i16_to_i8",
        "data_type": "int16",
        "fill_value": 0,
        "codec": cv({"data_type": "int8", "out_of_range": "clamp", "scalar_map": {"decode": [[127, -1]]}}),
        "values": [0, 5, 127, 1000, -1000],
    },
]

INVALID_CASES: list[dict[str, Any]] = [
    {
        "id": "invalid_unknown_configuration_key",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "int16", "bogus": 1}),
    },
    {
        "id": "invalid_wrap_on_float_target",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "float16", "out_of_range": "wrap"}),
    },
    {
        "id": "invalid_unsupported_target_bool",
        "data_type": "float32",
        "fill_value": 0.0,
        "codec": cv({"data_type": "bool"}),
    },
    {
        "id": "invalid_scalar_map_nan_key_for_int_source",
        "data_type": "int32",
        "fill_value": 0,
        "codec": cv({"data_type": "int8", "scalar_map": {"encode": [["NaN", 0]]}}),
    },
]


def normalize_cv(config: dict[str, Any]) -> dict[str, Any]:
    """Semantic form of a cast_value configuration (defaults filled, maps as dicts)."""
    sm = config.get("scalar_map")
    norm_sm = None
    if sm is not None:
        norm_sm = {}
        for direction in ("encode", "decode"):
            if direction in sm and sm[direction] is not None:
                entries = sm[direction]
                if isinstance(entries, dict):
                    entries = list(entries.items())
                norm_sm[direction] = {json.dumps(k): json.dumps(v) for k, v in entries}
    return {
        "data_type": config["data_type"],
        "rounding": config.get("rounding", "nearest-even"),
        "out_of_range": config.get("out_of_range"),
        "scalar_map": norm_sm,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect", type=int, default=None)
    args = ap.parse_args()

    oracle = Oracle(args.oracle)
    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    import zarr  # the submitted implementation

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    # --- valid cases: zarrs writes, zarr-python reads and re-encodes --------------------------
    ops: list[tuple[str, Path, str | None]] = []
    for case in VALID_CASES:
        d = workdir / case["id"]
        meta = array_metadata(
            shape=[len(case["values"])],
            data_type=case["data_type"],
            fill_value=case["fill_value"],
            codecs=[case["codec"], BYTES_LE],
        )
        write_metadata(d, meta)
        ops.append(("store", d, values_to_hex(case["values"], case["data_type"])))
    store_results = oracle.batch(ops, workdir)
    retrieve_ops = [("retrieve", workdir / c["id"], None) for c in VALID_CASES]
    retrieve_results = oracle.batch(retrieve_ops, workdir)

    for case, (s_status, s_payload), (r_status, r_payload) in zip(
        VALID_CASES, store_results, retrieve_results, strict=True
    ):
        d = workdir / case["id"]
        rec: dict[str, Any] = {"id": case["id"], "kind": "valid"}
        records.append(rec)
        if s_status != "ok" or r_status != "ok":
            rec["status"] = "harness_error"
            rec["detail"] = f"reference could not write/read its own case: {s_status} {s_payload} / {r_status} {r_payload}"
            failures.append(rec)
            continue
        oracle_chunk = read_chunk(d)
        rec["oracle_encoded_hex"] = oracle_chunk.hex() if oracle_chunk else None
        rec["oracle_decoded_hex"] = r_payload
        problems: list[str] = []
        try:
            arr = zarr.open_array(store=str(d), mode="r")
            decoded = arr[:]
            rec["agent_decoded_hex"] = array_to_hex(decoded, case["data_type"])
            if str(np.asarray(decoded).dtype) != case["data_type"]:
                problems.append(f"decoded dtype {np.asarray(decoded).dtype} != {case['data_type']}")
            if rec["agent_decoded_hex"] != r_payload:
                problems.append("decoded bytes differ from reference")
            # semantic metadata round trip
            codecs_json = json.loads(json.dumps(arr.metadata.to_dict()["codecs"], default=str))
            first = codecs_json[0]
            if first.get("name") != "cast_value":
                problems.append(f"first codec in to_dict is {first.get('name')!r}")
            elif normalize_cv(first.get("configuration", {})) != normalize_cv(case["codec"]["configuration"]):
                problems.append(f"to_dict configuration differs semantically: {first}")
            # re-encode into a metadata-only copy
            copy = workdir / (case["id"] + "__agent_copy")
            clone_metadata_only(d, copy)
            warr = zarr.open_array(store=str(copy), mode="r+")
            warr[:] = np.asarray(case["values"], dtype=case["data_type"])
            agent_chunk = read_chunk(copy)
            rec["agent_encoded_hex"] = agent_chunk.hex() if agent_chunk else None
            if rec["agent_encoded_hex"] != rec["oracle_encoded_hex"]:
                problems.append("re-encoded chunk differs from reference chunk")
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
            rec["traceback"] = traceback.format_exc()[-1500:]
        if problems:
            rec["status"] = "fail"
            rec["detail"] = "; ".join(problems)
            failures.append(rec)
        else:
            rec["status"] = "pass"

    # --- invalid cases: both must reject at open ---------------------------------------------
    check_ops: list[tuple[str, Path, str | None]] = []
    for case in INVALID_CASES:
        d = workdir / case["id"]
        meta = array_metadata(shape=[4], data_type=case["data_type"], fill_value=case["fill_value"], codecs=[case["codec"], BYTES_LE])
        write_metadata(d, meta)
        check_ops.append(("check", d, None))
    check_results = oracle.batch(check_ops, workdir)
    for case, (c_status, c_payload) in zip(INVALID_CASES, check_results, strict=True):
        d = workdir / case["id"]
        rec = {"id": case["id"], "kind": "invalid", "oracle_check": (c_status, c_payload)}
        records.append(rec)
        if c_status == "ok":
            rec["status"] = "harness_error"
            rec["detail"] = "reference accepted a document the harness expects to be invalid"
            failures.append(rec)
            continue
        try:
            arr = zarr.open_array(store=str(d), mode="r")
            _ = arr[:]
            rec["status"] = "fail"
            rec["detail"] = "submission accepted invalid metadata that the reference rejects"
            failures.append(rec)
        except Exception as e:  # noqa: BLE001 - rejection with any exception type is the requirement
            rec["status"] = "pass"
            rec["agent_error"] = f"{type(e).__name__}: {e}"

    total = len(records)
    passed = sum(1 for r in records if r["status"] == "pass")
    ok = passed == total
    summary: dict[str, Any] = {"total": total, "passed": passed, "failed": total - passed, "ok": ok}
    if args.expect is not None and total != args.expect:
        summary["ok"] = False
        summary["expect_error"] = f"{total} cases, expected {args.expect}"
    write_results(args.out, {"summary": summary, "failures": failures, "records": records})
    eprint(f"foreign metadata: {summary}")
    for f in failures:
        eprint("  FAIL", f["id"], f.get("detail"))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

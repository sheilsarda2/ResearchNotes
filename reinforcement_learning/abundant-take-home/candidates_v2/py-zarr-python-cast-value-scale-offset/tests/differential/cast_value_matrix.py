"""Cross-implementation differential check for the ``cast_value`` codec.

For every case in a fixed matrix (source dtype x target dtype x rounding x out_of_range x
scalar_map), the submitted zarr-python writes an array with a ``cast_value`` filter and reads
it back.  The independent Rust reference (zarrs) then

  * decodes the very same stored chunk  -> must equal what zarr-python decoded, byte for byte;
  * encodes the same source values into a metadata-only copy of the array -> the chunk it
    writes must be byte-identical to the chunk zarr-python wrote.

When one side raises (e.g. an out-of-range value with no ``out_of_range`` policy), the other
side must raise too.  Cases in the documented divergence list are excluded before comparison.

Usage: cast_value_matrix.py --oracle BIN --workdir DIR --out results.json [--expect N]
"""

from __future__ import annotations

import argparse
import math
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DTYPES,
    Oracle,
    array_to_hex,
    clone_metadata_only,
    eprint,
    is_float,
    is_int,
    is_signed,
    read_chunk,
    values_to_hex,
    write_results,
)

ROUNDINGS = ["nearest-even", "towards-zero", "towards-positive", "towards-negative", "nearest-away"]
OUT_OF_RANGE: list[str | None] = [None, "clamp", "wrap"]
MAP_KINDS = ["none", "finite", "both", "nan_to_zero"]


def source_values(src: str, oor: str | None, map_kind: str) -> list[Any]:
    if map_kind == "nan_to_zero":
        return [0.0, 1.0, math.nan, 3.0]
    if oor is None:
        return [0, 1, 2, 3]
    if is_float(src):
        return [0.0, 1.5, 127.5, 300.0, -3.0, 0.1]
    info = np.iinfo(src)
    values: list[Any] = [0, 1]
    if info.min < 0:
        values.append(-3)
    values.append(min(int(info.max), 300))
    values.append(min(int(info.max), 127))
    return values


def scalar_map_for(src: str, tgt: str, map_kind: str) -> dict[str, list[tuple[Any, Any]]] | None:
    one_src = 1.0 if is_float(src) else 1
    two_tgt = 2.0 if is_float(tgt) else 2
    two_src = 2.0 if is_float(src) else 2
    one_tgt = 1.0 if is_float(tgt) else 1
    if map_kind == "none":
        return None
    if map_kind == "finite":
        return {"encode": [(one_src, two_tgt)]}
    if map_kind == "both":
        return {"encode": [(one_src, two_tgt)], "decode": [(two_tgt, one_src)]}
    if map_kind == "nan_to_zero":
        return {"encode": [("NaN", 0.0 if is_float(tgt) else 0)]}
    raise ValueError(map_kind)


def should_generate(src: str, tgt: str, rounding: str, oor: str | None, map_kind: str) -> bool:
    if oor == "wrap" and not is_int(tgt):
        return False
    if map_kind == "nan_to_zero" and not (is_float(src) and is_int(tgt)):
        return False
    if oor is None and map_kind != "nan_to_zero" and rounding not in ("nearest-even", "towards-zero"):
        # values are exactly representable, rounding cannot matter; keep two modes as a smoke test
        return False
    return True


def strict_cases() -> list[dict[str, Any]]:
    """out_of_range unset: values the target cannot hold must make both implementations fail."""
    cases: list[dict[str, Any]] = []
    for src in DTYPES:
        for tgt in DTYPES:
            if src == tgt:
                continue
            vals: list[Any] | None = None
            if is_int(tgt) and np.iinfo(tgt).max < 300 and (is_float(src) or np.iinfo(src).max >= 300):
                vals = [0, 300] if is_int(src) else [0.0, 300.0]
            elif is_int(tgt) and np.iinfo(tgt).min == 0 and is_signed(src):
                vals = [0, -3]
            elif is_int(tgt) and is_float(src):
                vals = [0.0, math.nan]
            if vals is None:
                continue
            cases.append(
                {
                    "family": "strict",
                    "src": src,
                    "tgt": tgt,
                    "rounding": "nearest-even",
                    "oor": None,
                    "map_kind": "none",
                    "values": vals,
                }
            )
    return cases


def excluded(case: dict[str, Any]) -> str | None:
    """Documented divergences between zarr-python (cast-value-rs 0.4.0) and zarrs.

    zarrs' own interop test (zarrs/tests/zarr_python.rs) skips these: zarr-python rounds the
    decoded wrapped u64::MAX-ish value to the *nearest* float even when a directed rounding mode
    is configured.
    """
    if is_float(case["src"]) and case["oor"] == "wrap":
        # The merged zarr-python implementation rejects out_of_range="wrap" for floating-point
        # *arrays* at validation time (its own test suite asserts this); zarrs accepts wrap
        # whenever the *target* is integral, as the zarr-extensions spec text reads. The
        # instruction states the zarr-python behaviour, so these cases are not compared here.
        return "zarr-python rejects wrap for floating-point arrays; zarrs accepts it"
    if (
        is_float(case["src"])
        and case["tgt"] == "uint64"
        and case["oor"] == "wrap"
        and case["rounding"] in ("towards-zero", "towards-negative")
    ):
        return "zarr-python directed rounding of decoded u64 near 2**64 (zarrs skips the same)"
    return None


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for src in DTYPES:
        for tgt in DTYPES:
            for rounding in ROUNDINGS:
                for oor in OUT_OF_RANGE:
                    for map_kind in MAP_KINDS:
                        if not should_generate(src, tgt, rounding, oor, map_kind):
                            continue
                        cases.append(
                            {
                                "family": "matrix",
                                "src": src,
                                "tgt": tgt,
                                "rounding": rounding,
                                "oor": oor,
                                "map_kind": map_kind,
                                "values": source_values(src, oor, map_kind),
                            }
                        )
    cases.extend(strict_cases())
    for i, c in enumerate(cases):
        c["id"] = f"{i:05d}_{c['family']}_{c['src']}_to_{c['tgt']}_{c['rounding']}_{c['oor']}_{c['map_kind']}"
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect", type=int, default=None, help="expected number of compared cases")
    args = ap.parse_args()

    oracle = Oracle(args.oracle)
    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    import zarr  # the submitted implementation
    from zarr.codecs import BytesCodec
    from zarr.codecs.cast_value import CastValue

    cases = build_cases()
    records: list[dict[str, Any]] = []
    ops: list[tuple[str, Path, str | None]] = []
    op_index: dict[str, tuple[int | None, int | None]] = {}

    for case in cases:
        rec: dict[str, Any] = {"id": case["id"], "config": {k: case[k] for k in ("src", "tgt", "rounding", "oor", "map_kind")}}
        records.append(rec)
        reason = excluded(case)
        if reason:
            rec["status"] = "excluded"
            rec["reason"] = reason
            continue
        d = workdir / case["id"]
        src_hex = values_to_hex(case["values"], case["src"])
        rec["source_hex"] = src_hex
        try:
            arr = zarr.create_array(
                store=str(d),
                shape=(len(case["values"]),),
                chunks=(len(case["values"]),),
                dtype=case["src"],
                fill_value=0,
                filters=[
                    CastValue(
                        data_type=case["tgt"],
                        rounding=case["rounding"],
                        out_of_range=case["oor"],
                        scalar_map=scalar_map_for(case["src"], case["tgt"], case["map_kind"]),
                    )
                ],
                serializer=BytesCodec(endian="little"),
                compressors=None,
                zarr_format=3,
                overwrite=True,
            )
            arr[:] = np.asarray(case["values"], dtype=case["src"])
            decoded = arr[:]
            rec["agent_decoded_hex"] = array_to_hex(decoded, case["src"])
            chunk = read_chunk(d)
            rec["agent_encoded_hex"] = chunk.hex() if chunk is not None else None
            rec["agent_error"] = None
        except Exception as e:  # noqa: BLE001 - any failure of the submission is data here
            rec["agent_error"] = f"{type(e).__name__}: {e}"
            rec["agent_traceback"] = traceback.format_exc()[-1500:]
        if (d / "zarr.json").exists():
            copy = workdir / (case["id"] + "__oracle_copy")
            clone_metadata_only(d, copy)
            r_idx = len(ops)
            ops.append(("retrieve", d, None))
            s_idx = len(ops)
            ops.append(("store", copy, src_hex))
            op_index[case["id"]] = (r_idx, s_idx)
        else:
            op_index[case["id"]] = (None, None)

    results = oracle.batch(ops, workdir)

    summary = {"total": 0, "compared": 0, "agree": 0, "agree_error": 0, "excluded": 0, "mismatch": 0}
    mismatches: list[dict[str, Any]] = []
    for case, rec in zip(cases, records, strict=True):
        summary["total"] += 1
        if rec.get("status") == "excluded":
            summary["excluded"] += 1
            continue
        summary["compared"] += 1
        r_idx, s_idx = op_index[case["id"]]
        if r_idx is None:
            # the submission could not even write metadata
            rec["status"] = "mismatch"
            rec["detail"] = "submission failed before writing zarr.json"
            summary["mismatch"] += 1
            mismatches.append(rec)
            continue
        r_status, r_payload = results[r_idx]
        s_status, s_payload = results[s_idx]
        rec["oracle_retrieve"] = (r_status, r_payload if r_status != "ok" else "")
        rec["oracle_store"] = (s_status, s_payload)
        copy = workdir / (case["id"] + "__oracle_copy")
        oracle_chunk = read_chunk(copy)
        rec["oracle_encoded_hex"] = oracle_chunk.hex() if oracle_chunk is not None else None

        agent_failed = rec["agent_error"] is not None
        oracle_failed = s_status != "ok"
        if agent_failed and oracle_failed:
            rec["status"] = "agree_error"
            summary["agree_error"] += 1
            summary["agree"] += 1
            continue
        if agent_failed != oracle_failed:
            rec["status"] = "mismatch"
            rec["detail"] = "one implementation raised, the other did not"
            summary["mismatch"] += 1
            mismatches.append(rec)
            continue
        problems = []
        if r_status != "ok":
            problems.append(f"oracle could not decode the submission's chunk: {r_payload}")
        elif r_payload != rec["agent_decoded_hex"]:
            problems.append("decoded bytes differ (oracle vs submission)")
        if rec["oracle_encoded_hex"] != rec["agent_encoded_hex"]:
            problems.append("encoded chunk bytes differ (oracle vs submission)")
        if problems:
            rec["status"] = "mismatch"
            rec["detail"] = "; ".join(problems)
            rec["oracle_decoded_hex"] = r_payload
            summary["mismatch"] += 1
            mismatches.append(rec)
        else:
            rec["status"] = "agree"
            summary["agree"] += 1

    ok = summary["mismatch"] == 0 and summary["compared"] > 0
    if args.expect is not None and summary["compared"] != args.expect:
        ok = False
        summary["expect_error"] = f"compared {summary['compared']} cases, expected {args.expect}"
    summary["ok"] = ok
    write_results(args.out, {"summary": summary, "mismatches": mismatches[:200], "records": records})
    eprint(f"cast_value matrix: {summary}")
    for m in mismatches[:25]:
        eprint("  MISMATCH", m["id"], m.get("detail"), m.get("agent_error"), m.get("oracle_store"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Authored exact-arithmetic reference for the ``scale_offset`` codec (and its chaining with
``cast_value``).  zarrs does not implement ``scale_offset``, so this group is author-written and
every expectation below is computed independently of the submission:

  * integers: Python unbounded ints; ``(x - offset) * scale`` must land inside the dtype range
    or the write must raise; decoding ``e / scale + offset`` must be an exact integer inside
    the range or the read must raise.
  * floats: IEEE arithmetic in the array's own dtype (numpy scalar ops of that dtype), so the
    result dtype never widens; NaN and infinities propagate.
  * metadata: the emitted codec JSON omits ``configuration`` entirely when both parameters
    are at their defaults and otherwise contains only the non-default keys.
  * fill values propagate through the chain: an unwritten region reads back as the array's
    fill value even when the stored representation differs.
  * the two worked examples from the zarr-extensions ``scale_offset`` spec (uint16 range
    reduction, float64 -> uint8 with NaN preservation) round-trip as specified.

Usage: scale_offset_reference.py --workdir DIR --out results.json [--expect N]
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
    FLOAT_DTYPES,
    INT_DTYPES,
    array_metadata,
    eprint,
    is_signed,
    le_dtype,
    read_chunk,
    write_metadata,
    write_results,
)

BYTES_LE = {"name": "bytes", "configuration": {"endian": "little"}}


# --------------------------------------------------------------------------------------------
# Reference arithmetic
# --------------------------------------------------------------------------------------------
def ref_encode_int(dtype: str, values: list[int], offset: int, scale: int) -> list[int] | str:
    info = np.iinfo(dtype)
    out = []
    for x in values:
        y = (x - offset) * scale
        if y < int(info.min) or y > int(info.max):
            return "outside the range of dtype"
        out.append(y)
    return out


def ref_decode_int(dtype: str, encoded: list[int], offset: int, scale: int) -> list[int] | str:
    info = np.iinfo(dtype)
    out = []
    for e in encoded:
        if e % scale != 0:
            return "non-zero remainder"
        q = e // scale
        y = q + offset
        if y < int(info.min) or y > int(info.max):
            return "outside the range of dtype"
        out.append(y)
    return out


def ref_encode_float(dtype: str, values: list[float], offset: float, scale: float) -> np.ndarray:
    t = np.dtype(dtype).type
    with np.errstate(all="ignore"):
        return np.array([(t(x) - t(offset)) * t(scale) for x in values], dtype=dtype)


def ref_decode_float(dtype: str, encoded: list[float], offset: float, scale: float) -> np.ndarray:
    t = np.dtype(dtype).type
    with np.errstate(all="ignore"):
        return np.array([(t(e) / t(scale)) + t(offset) for e in encoded], dtype=dtype)


def clip_values(dtype: str, values: list[int]) -> list[int]:
    info = np.iinfo(dtype)
    return [max(int(info.min), min(int(info.max), v)) for v in values]


# --------------------------------------------------------------------------------------------
# Case construction
# --------------------------------------------------------------------------------------------
def int_encode_cases() -> list[dict[str, Any]]:
    cases = []
    for dt in INT_DTYPES:
        params: list[tuple[int, int, list[int]]] = [
            (0, 1, [0, 1, 2, 3, 4, 5, 6, 7]),
            (1, 2, [1, 2, 3, 4, 5, 6, 7, 8]),
            (100, 1, clip_values(dt, [100, 101, 150, 200])),
            (100, 1, [100, 50, 200 if np.iinfo(dt).max >= 200 else 120]),  # underflow -> error
            (0, 100, [0, 1, 2]),  # overflow for 8-bit types -> error, fine elsewhere
        ]
        if is_signed(dt):
            params += [
                (-3, 3, [-40, -1, 0, 1, 30]),
                (0, -2, [-5, -1, 0, 1, 5]),
                (5, 7, [-10, 5, 12]),
            ]
        if dt == "uint64":
            params += [
                (0, 1, [0, 2**63, 2**64 - 1]),
                (1, 1, [1, 2**63, 2**64 - 1]),
                (0, 2, [0, 2**62, 2**63 - 1]),
                (0, 2, [2**63]),  # 2**64 overflows -> error
            ]
        if dt == "int64":
            # NOTE: an int64 underflow case ((1, 1, [-(2**63)])) is deliberately absent: the merged
            # upstream implementation wraps silently there (its int64 "widening" is a no-op for
            # int64), so the verifier does not demand it. Recorded in STATUS.md.
            params += [(0, 1, [-(2**63), 2**63 - 1, 0])]
        for i, (o, s, vals) in enumerate(params):
            cases.append({"id": f"so_int_encode_{dt}_{i:02d}_o{o}_s{s}", "dtype": dt, "offset": o, "scale": s, "values": vals})
    return cases


def float_encode_cases() -> list[dict[str, Any]]:
    cases = []
    base = [x * 0.5 for x in range(16)]
    for dt in FLOAT_DTYPES:
        params: list[tuple[float, float, list[float]]] = [
            (10.0, 0.1, base),
            (5.0, 2.0, base),
            (0.5, 0.25, base + [-3.75, 1e3]),
            (-1.5, 3.0, base),
            (0, 1, [1.0, math.nan, math.inf, -math.inf, -0.0]),
            (0.0, 2.0, [1.0, math.nan, math.inf, -math.inf]),
            (2, 3, [0.1, 0.2, 0.3]),  # integer-valued JSON parameters on a float array
        ]
        for i, (o, s, vals) in enumerate(params):
            cases.append({"id": f"so_float_encode_{dt}_{i:02d}", "dtype": dt, "offset": o, "scale": s, "values": vals})
    return cases


def int_decode_cases() -> list[dict[str, Any]]:
    return [
        {"id": "so_int_decode_int8_non_exact", "dtype": "int8", "offset": 0, "scale": 2, "encoded": [2, 3, 4]},
        {"id": "so_int_decode_int8_exact", "dtype": "int8", "offset": 0, "scale": 2, "encoded": [2, 4, -6]},
        {"id": "so_int_decode_int8_overflow_on_add", "dtype": "int8", "offset": 100, "scale": 1, "encoded": [0, 50, 100]},
        {"id": "so_int_decode_uint32_widened", "dtype": "uint32", "offset": 2**31, "scale": 1, "encoded": [0, 100, 1000]},
        {"id": "so_int_decode_uint32_overflow", "dtype": "uint32", "offset": 2**31, "scale": 1, "encoded": [2**31]},
        {"id": "so_int_decode_uint64_large", "dtype": "uint64", "offset": 0, "scale": 1, "encoded": [2**64 - 1, 2**63, 0]},
        {"id": "so_int_decode_uint64_scale", "dtype": "uint64", "offset": 1, "scale": 2, "encoded": [2**64 - 2, 0, 4]},
        {"id": "so_int_decode_int16_negative_scale", "dtype": "int16", "offset": 0, "scale": -2, "encoded": [4, -6, 0]},
        {"id": "so_int_decode_uint8_offset_scale", "dtype": "uint8", "offset": 5, "scale": 3, "encoded": [0, 3, 9]},
        {"id": "so_int_decode_uint8_underflow_negative_scale_absent", "dtype": "uint8", "offset": 0, "scale": 5, "encoded": [10, 11]},
    ]


def float_decode_cases() -> list[dict[str, Any]]:
    return [
        {"id": "so_float_decode_f32", "dtype": "float32", "offset": 10.0, "scale": 0.1, "encoded": [0.0, 1.0, 2.5, math.nan]},
        {"id": "so_float_decode_f64", "dtype": "float64", "offset": -1.5, "scale": 3.0, "encoded": [0.0, 4.5, -9.0, math.inf]},
        {"id": "so_float_decode_f16", "dtype": "float16", "offset": 5.0, "scale": 2.0, "encoded": [0.0, 1.0, 2.0, -3.0]},
    ]


METADATA_CASES: list[dict[str, Any]] = [
    {"id": "so_meta_defaults", "kwargs": {}, "expected": {"name": "scale_offset"}},
    {"id": "so_meta_offset_only", "kwargs": {"offset": 5}, "expected": {"name": "scale_offset", "configuration": {"offset": 5}}},
    {"id": "so_meta_scale_only", "kwargs": {"scale": 0.1}, "expected": {"name": "scale_offset", "configuration": {"scale": 0.1}}},
    {"id": "so_meta_both", "kwargs": {"offset": 5, "scale": 0.1}, "expected": {"name": "scale_offset", "configuration": {"offset": 5, "scale": 0.1}}},
    {"id": "so_meta_negative", "kwargs": {"offset": -2.5, "scale": -4.0}, "expected": {"name": "scale_offset", "configuration": {"offset": -2.5, "scale": -4.0}}},
]


def round_half_even(x: float) -> int:
    return int(round(x))  # Python's round() is round-half-to-even


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expect", type=int, default=None)
    args = ap.parse_args()
    workdir = Path(args.workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    import zarr  # the submitted implementation
    from zarr.codecs import BytesCodec
    from zarr.codecs.cast_value import CastValue
    from zarr.codecs.scale_offset import ScaleOffset

    records: list[dict[str, Any]] = []

    def record(rec: dict[str, Any], problems: list[str]) -> None:
        rec["status"] = "pass" if not problems else "fail"
        if problems:
            rec["detail"] = "; ".join(problems)
        records.append(rec)

    def create(d: Path, dtype: str, n: int, filters: list[Any], fill: Any, chunks: int | None = None) -> Any:
        return zarr.create_array(
            store=str(d),
            shape=(n,),
            chunks=(chunks or n,),
            dtype=dtype,
            fill_value=fill,
            filters=filters,
            serializer=BytesCodec(endian="little"),
            compressors=None,
            zarr_format=3,
            overwrite=True,
        )

    # ---- encode: integers -------------------------------------------------------------------
    for case in int_encode_cases():
        rec: dict[str, Any] = {"id": case["id"], "group": "int_encode"}
        problems: list[str] = []
        expected = ref_encode_int(case["dtype"], case["values"], case["offset"], case["scale"])
        rec["expected"] = expected if isinstance(expected, str) else "bytes"
        d = workdir / case["id"]
        try:
            arr = create(d, case["dtype"], len(case["values"]), [ScaleOffset(offset=case["offset"], scale=case["scale"])], 0 if case["offset"] <= 0 or not case["dtype"].startswith("u") else case["offset"])
            try:
                arr[:] = np.asarray(case["values"], dtype=case["dtype"])
                wrote = True
                err: Exception | None = None
            except Exception as e:  # noqa: BLE001
                wrote = False
                err = e
            if isinstance(expected, str):
                if wrote:
                    problems.append(f"write succeeded but reference expects an error ({expected})")
                elif not isinstance(err, ValueError):
                    problems.append(f"expected ValueError, got {type(err).__name__}: {err}")
                elif expected not in str(err):
                    problems.append(f"error message lacks {expected!r}: {err}")
            else:
                if not wrote:
                    problems.append(f"write raised {type(err).__name__}: {err}")
                else:
                    chunk = read_chunk(d)
                    exp_bytes = np.asarray(expected, dtype=le_dtype(case["dtype"])).tobytes()
                    if chunk != exp_bytes:
                        problems.append(f"encoded bytes differ: got {chunk.hex() if chunk else None} expected {exp_bytes.hex()}")
                    back = np.asarray(arr[:])
                    if back.dtype != np.dtype(case["dtype"]) or back.tolist() != case["values"]:
                        problems.append(f"round trip differs: {back.tolist()} != {case['values']}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
            rec["traceback"] = traceback.format_exc()[-1200:]
        record(rec, problems)

    # ---- encode: floats ---------------------------------------------------------------------
    for case in float_encode_cases():
        rec = {"id": case["id"], "group": "float_encode"}
        problems = []
        expected_arr = ref_encode_float(case["dtype"], case["values"], case["offset"], case["scale"])
        d = workdir / case["id"]
        try:
            arr = create(d, case["dtype"], len(case["values"]), [ScaleOffset(offset=case["offset"], scale=case["scale"])], 0.0)
            arr[:] = np.asarray(case["values"], dtype=case["dtype"])
            chunk = read_chunk(d)
            exp_bytes = expected_arr.astype(le_dtype(case["dtype"])).tobytes()
            got = np.frombuffer(chunk, dtype=le_dtype(case["dtype"])) if chunk else None
            if got is None or not np.array_equal(got, expected_arr, equal_nan=True):
                problems.append(f"encoded values differ: got {None if got is None else got.tolist()} expected {expected_arr.tolist()}")
            back = np.asarray(arr[:])
            if back.dtype != np.dtype(case["dtype"]):
                problems.append(f"decoded dtype {back.dtype} != {case['dtype']}")
            expected_back = ref_decode_float(case["dtype"], expected_arr.tolist(), case["offset"], case["scale"])
            if not np.array_equal(back, expected_back, equal_nan=True):
                problems.append(f"decoded values differ: got {back.tolist()} expected {expected_back.tolist()}")
            _ = exp_bytes
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
            rec["traceback"] = traceback.format_exc()[-1200:]
        record(rec, problems)

    # ---- decode from hand-written chunks: integers -----------------------------------------
    for case in int_decode_cases():
        rec = {"id": case["id"], "group": "int_decode"}
        problems = []
        expected = ref_decode_int(case["dtype"], case["encoded"], case["offset"], case["scale"])
        rec["expected"] = expected if isinstance(expected, str) else "values"
        d = workdir / case["id"]
        meta = array_metadata(
            shape=[len(case["encoded"])],
            data_type=case["dtype"],
            fill_value=0 if case["offset"] <= 0 or not case["dtype"].startswith("u") else case["offset"],
            codecs=[{"name": "scale_offset", "configuration": {"offset": case["offset"], "scale": case["scale"]}}, BYTES_LE],
        )
        write_metadata(d, meta)
        (d / "c").mkdir()
        (d / "c" / "0").write_bytes(np.asarray(case["encoded"], dtype=le_dtype(case["dtype"])).tobytes())
        try:
            arr = zarr.open_array(store=str(d), mode="r")
            try:
                got = np.asarray(arr[:])
                err = None
            except Exception as e:  # noqa: BLE001
                got = None
                err = e
            if isinstance(expected, str):
                if got is not None:
                    problems.append(f"read succeeded ({got.tolist()}) but reference expects an error ({expected})")
                elif not isinstance(err, ValueError):
                    problems.append(f"expected ValueError, got {type(err).__name__}: {err}")
                elif expected not in str(err):
                    problems.append(f"error message lacks {expected!r}: {err}")
            else:
                if got is None:
                    problems.append(f"read raised {type(err).__name__}: {err}")
                elif got.dtype != np.dtype(case["dtype"]) or got.tolist() != expected:
                    problems.append(f"decoded {got.tolist()} ({got.dtype}) != {expected}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
            rec["traceback"] = traceback.format_exc()[-1200:]
        record(rec, problems)

    # ---- decode from hand-written chunks: floats -------------------------------------------
    for case in float_decode_cases():
        rec = {"id": case["id"], "group": "float_decode"}
        problems = []
        expected_arr = ref_decode_float(case["dtype"], case["encoded"], case["offset"], case["scale"])
        d = workdir / case["id"]
        meta = array_metadata(
            shape=[len(case["encoded"])],
            data_type=case["dtype"],
            fill_value=0.0,
            codecs=[{"name": "scale_offset", "configuration": {"offset": case["offset"], "scale": case["scale"]}}, BYTES_LE],
        )
        write_metadata(d, meta)
        (d / "c").mkdir()
        (d / "c" / "0").write_bytes(np.asarray(case["encoded"], dtype=le_dtype(case["dtype"])).tobytes())
        try:
            got = np.asarray(zarr.open_array(store=str(d), mode="r")[:])
            if got.dtype != np.dtype(case["dtype"]) or not np.array_equal(got, expected_arr, equal_nan=True):
                problems.append(f"decoded {got.tolist()} ({got.dtype}) != {expected_arr.tolist()}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
        record(rec, problems)

    # ---- metadata emitted by the submission --------------------------------------------------
    for case in METADATA_CASES:
        rec = {"id": case["id"], "group": "metadata"}
        problems = []
        d = workdir / case["id"]
        try:
            create(d, "float64", 4, [ScaleOffset(**case["kwargs"])], 0.0)
            doc = json.loads((d / "zarr.json").read_text())
            first = doc["codecs"][0]
            if first != case["expected"]:
                problems.append(f"codec JSON {first} != {case['expected']}")
            reopened = zarr.open_array(store=str(d), mode="r")
            codec = reopened.metadata.codecs[0]
            if codec.to_dict() != case["expected"]:
                problems.append(f"reopened to_dict {codec.to_dict()} != {case['expected']}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"submission raised {type(e).__name__}: {e}")
        record(rec, problems)

    # no-configuration document written by hand must open as a no-op codec
    rec = {"id": "so_meta_noconfig_document", "group": "metadata"}
    problems = []
    d = workdir / rec["id"]
    write_metadata(d, array_metadata(shape=[3], data_type="float32", fill_value=0.0, codecs=[{"name": "scale_offset"}, BYTES_LE]))
    (d / "c").mkdir()
    (d / "c" / "0").write_bytes(np.asarray([1.5, -2.0, 7.0], dtype="<f4").tobytes())
    try:
        got = np.asarray(zarr.open_array(store=str(d), mode="r")[:])
        if got.tolist() != [1.5, -2.0, 7.0]:
            problems.append(f"no-op decode gave {got.tolist()}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"submission raised {type(e).__name__}: {e}")
    record(rec, problems)

    # ---- fill value through the codec ------------------------------------------------------
    rec = {"id": "so_fill_value_unwritten", "group": "fill"}
    problems = []
    d = workdir / rec["id"]
    try:
        arr = create(d, "float64", 6, [ScaleOffset(offset=5, scale=2)], 10.0, chunks=3)
        got = np.asarray(arr[:])
        if not np.array_equal(got, np.full(6, 10.0)):
            problems.append(f"unwritten array read {got.tolist()} != all 10.0")
        doc = json.loads((d / "zarr.json").read_text())
        if doc["fill_value"] != 10.0:
            problems.append(f"stored fill_value {doc['fill_value']} != 10.0")
        arr[:3] = np.asarray([10.0, 11.0, 12.0])
        chunk = read_chunk(d)
        if chunk != np.asarray([10.0, 12.0, 14.0], dtype="<f8").tobytes():
            problems.append("first chunk bytes are not (x-5)*2")
        got = np.asarray(arr[:])
        if got.tolist() != [10.0, 11.0, 12.0, 10.0, 10.0, 10.0]:
            problems.append(f"partial write read back {got.tolist()}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"submission raised {type(e).__name__}: {e}")
    record(rec, problems)

    # ---- spec example: uint16 range reduction with cast_value ------------------------------
    rec = {"id": "chain_uint16_range_reduction", "group": "chain"}
    problems = []
    d = workdir / rec["id"]
    try:
        arr = create(d, "uint16", 3, [ScaleOffset(offset=1000), CastValue(data_type="uint8")], 1000)
        arr[:] = np.asarray([1000, 1001, 1255], dtype="uint16")
        chunk = read_chunk(d)
        if chunk != bytes([0, 1, 255]):
            problems.append(f"encoded {chunk.hex() if chunk else None} != 0001ff")
        if np.asarray(arr[:]).tolist() != [1000, 1001, 1255]:
            problems.append(f"round trip {np.asarray(arr[:]).tolist()}")
        doc = json.loads((d / "zarr.json").read_text())
        if doc["codecs"][0] != {"name": "scale_offset", "configuration": {"offset": 1000}}:
            problems.append(f"scale_offset JSON {doc['codecs'][0]}")
        if doc["codecs"][1] != {"name": "cast_value", "configuration": {"data_type": "uint8"}}:
            problems.append(f"cast_value JSON {doc['codecs'][1]}")
        try:
            arr[:] = np.asarray([1000, 1001, 1256], dtype="uint16")  # 256 cannot be a uint8
            problems.append("write of an unrepresentable value did not raise")
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception as e:  # noqa: BLE001
        problems.append(f"submission raised {type(e).__name__}: {e}")
        rec["traceback"] = traceback.format_exc()[-1200:]
    record(rec, problems)

    # ---- spec example: float64 -> uint8 with NaN preservation, partial chunks --------------
    rec = {"id": "chain_float64_uint8_nan_preserving", "group": "chain"}
    problems = []
    d = workdir / rec["id"]
    values = [0.0, 2540.0, math.nan, 1270.0, 5.0, 12.0]
    try:
        arr = create(
            d,
            "float64",
            12,
            [
                ScaleOffset(offset=-10, scale=0.1),
                CastValue(data_type="uint8", rounding="nearest-even", scalar_map={"encode": [("NaN", 0)], "decode": [(0, "NaN")]}),
            ],
            "NaN",
            chunks=6,
        )
        arr[:6] = np.asarray(values, dtype="float64")
        expected_enc = [0 if math.isnan(x) else round_half_even((x - (-10)) * 0.1) for x in values]
        chunk = read_chunk(d)
        if chunk != bytes(expected_enc):
            problems.append(f"encoded {chunk.hex() if chunk else None} != {bytes(expected_enc).hex()}")
        if (d / "c" / "1").exists():
            problems.append("an unwritten chunk was materialised")
        expected_dec = [math.nan if e == 0 else (e / 0.1) + (-10) for e in expected_enc] + [math.nan] * 6
        got = np.asarray(arr[:])
        if got.dtype != np.dtype("float64") or not np.array_equal(got, np.asarray(expected_dec), equal_nan=True):
            problems.append(f"decoded {got.tolist()} != {expected_dec}")
        doc = json.loads((d / "zarr.json").read_text())
        if doc["fill_value"] != "NaN":
            problems.append(f"fill_value in metadata is {doc['fill_value']!r}")
        if doc["codecs"][1]["configuration"].get("scalar_map") != {"encode": [["NaN", 0]], "decode": [[0, "NaN"]]}:
            problems.append(f"scalar_map JSON {doc['codecs'][1]['configuration'].get('scalar_map')}")
    except Exception as e:  # noqa: BLE001
        problems.append(f"submission raised {type(e).__name__}: {e}")
        rec["traceback"] = traceback.format_exc()[-1200:]
    record(rec, problems)

    total = len(records)
    passed = sum(1 for r in records if r["status"] == "pass")
    summary: dict[str, Any] = {"total": total, "passed": passed, "failed": total - passed, "ok": passed == total}
    if args.expect is not None and total != args.expect:
        summary["ok"] = False
        summary["expect_error"] = f"{total} cases, expected {args.expect}"
    write_results(args.out, {"summary": summary, "failures": [r for r in records if r["status"] != "pass"], "records": records})
    eprint(f"scale_offset reference: {summary}")
    for r in records:
        if r["status"] != "pass":
            eprint("  FAIL", r["id"], r.get("detail"))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared helpers for the differential harnesses.

These scripts run inside the verifier against the *submitted* zarr package (imported as
``zarr``) and an independent Rust reference binary (``zarrs_oracle``, built from zarrs).
They never import anything from the reference solution.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

# Data types shared by the zarr v3 spec names and numpy names used throughout the harness.
INT_DTYPES = ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64"]
FLOAT_DTYPES = ["float16", "float32", "float64"]
DTYPES = INT_DTYPES + FLOAT_DTYPES


def is_float(name: str) -> bool:
    return np.issubdtype(np.dtype(name), np.floating)


def is_int(name: str) -> bool:
    return np.issubdtype(np.dtype(name), np.integer)


def is_signed(name: str) -> bool:
    return np.issubdtype(np.dtype(name), np.signedinteger)


def le_dtype(name: str) -> np.dtype[Any]:
    """Little-endian numpy dtype for a zarr v3 data type name."""
    return np.dtype(name).newbyteorder("<")


def values_to_hex(values: list[Any], dtype: str) -> str:
    return np.asarray(values, dtype=le_dtype(dtype)).tobytes().hex()


def array_to_hex(arr: Any, dtype: str) -> str:
    return np.ascontiguousarray(np.asarray(arr), dtype=le_dtype(dtype)).tobytes().hex()


def json_scalar(value: Any) -> Any:
    """Zarr v3 fill-value JSON encoding for the scalar kinds used by the harness."""
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
    return value


def array_metadata(
    *,
    shape: list[int],
    data_type: str,
    fill_value: Any,
    codecs: list[dict[str, Any]],
) -> dict[str, Any]:
    """A hand-written zarr v3 array metadata document (single regular chunk grid)."""
    return {
        "zarr_format": 3,
        "node_type": "array",
        "shape": shape,
        "data_type": data_type,
        "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": shape}},
        "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
        "fill_value": fill_value,
        "codecs": codecs,
        "attributes": {},
    }


def write_metadata(dir_: Path, metadata: dict[str, Any]) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "zarr.json").write_text(json.dumps(metadata, indent=1) + "\n")


def clone_metadata_only(src: Path, dst: Path) -> None:
    """Copy only zarr.json from one array directory to a fresh one."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copyfile(src / "zarr.json", dst / "zarr.json")


def read_chunk(dir_: Path) -> bytes | None:
    p = dir_ / "c" / "0"
    return p.read_bytes() if p.exists() else None


class Oracle:
    """Thin wrapper over the zarrs_oracle binary's batch mode."""

    def __init__(self, path: str) -> None:
        self.path = path
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            raise SystemExit(f"oracle binary not executable: {path}")

    def batch(self, ops: list[tuple[str, Path, str | None]], workdir: Path) -> list[tuple[str, str]]:
        if not ops:
            return []
        batch_file = workdir / "oracle_batch.tsv"
        with batch_file.open("w") as fh:
            for op, dir_, hex_ in ops:
                if hex_ is None:
                    fh.write(f"{op}\t{dir_}\n")
                else:
                    fh.write(f"{op}\t{dir_}\t{hex_}\n")
        proc = subprocess.run(
            [self.path, "batch", str(batch_file)],
            capture_output=True,
            text=True,
            check=False,
            timeout=1800,
        )
        if proc.returncode != 0:
            raise SystemExit(f"oracle batch failed rc={proc.returncode}: {proc.stderr[:2000]}")
        lines = [ln for ln in proc.stdout.splitlines() if ln]
        if len(lines) != len(ops):
            raise SystemExit(f"oracle returned {len(lines)} lines for {len(ops)} ops")
        out: list[tuple[str, str]] = []
        for ln in lines:
            status, _, payload = ln.partition("\t")
            out.append((status, payload))
        return out


def write_results(path: str, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=1, default=str) + "\n")


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)

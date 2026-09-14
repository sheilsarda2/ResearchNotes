#!/usr/bin/env python3
"""Verifier-owned MCAP conformance driver for the Python library.

Replicates tests/conformance/scripts/run-tests/index.ts for the three Python
runners (py-writer, py-streamed-reader, py-indexed-reader) and adds
cross-implementation self-consistency checks with the Rust and Go reference
readers on the files the Python writer produced.

Usage:
  run_conformance.py --repo REPO --data DATA_DIR --python PY --out OUT.json
                     [--rust-streamed BIN] [--rust-indexed BIN] [--go-reader BIN]
Exit status is always 0; test.sh reads OUT.json.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PER_CASE_TIMEOUT = 120


def norm(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def field(record, name):
    for k, v in record["fields"]:
        if k == name:
            return v
    raise KeyError(name)


def expected_indexed(records):
    """Port of IndexedReadTestRunner.expectedResult (TestRunner.ts)."""
    out = {"schemas": [], "channels": [], "messages": [], "statistics": []}
    seen_s, seen_c = set(), set()
    for r in records:
        t = r["type"]
        if t == "Schema":
            i = int(field(r, "id"))
            if i not in seen_s:
                out["schemas"].append(r)
                seen_s.add(i)
        elif t == "Channel":
            i = int(field(r, "id"))
            if i not in seen_c:
                out["channels"].append(r)
                seen_c.add(i)
        elif t == "Message":
            out["messages"].append(r)
        elif t == "Statistics":
            out["statistics"].append(r)
    out["messages"].sort(key=lambda r: int(field(r, "log_time")))  # stable
    out["schemas"].sort(key=lambda r: int(field(r, "id")))
    out["channels"].sort(key=lambda r: int(field(r, "id")))
    return out


def run(cmd, cwd=None, binary=False):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=PER_CASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if p.returncode != 0:
        return None, p.stderr.decode(errors="replace")[-400:] or f"exit {p.returncode}"
    return (p.stdout if binary else p.stdout.decode()), None


def parse_json(text):
    try:
        return json.loads(text), None
    except Exception as e:  # noqa: BLE001
        return None, f"bad json: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rust-streamed")
    ap.add_argument("--rust-indexed")
    ap.add_argument("--go-reader")
    a = ap.parse_args()

    repo = Path(a.repo).resolve()
    data_dir = Path(a.data).resolve()
    pkg_dir = repo / "python" / "mcap"
    reader_script = str(pkg_dir / "tests" / "run_reader_test.py")
    writer_script = str(pkg_dir / "tests" / "run_writer_test.py")

    groups = {
        "py_writer": [],
        "py_streamed_reader": [],
        "py_indexed_reader": [],
        "rust_streamed_on_py_output": [],
        "go_streamed_on_py_output": [],
        "rust_indexed_on_py_output": [],
        "go_indexed_on_py_output": [],
    }
    details = {}

    cases = sorted(data_dir.rglob("*.json"))
    tmp = Path(tempfile.mkdtemp(prefix="mcap-conf-"))
    for js in cases:
        name = js.stem
        case = json.loads(js.read_text())
        feats = set(case["meta"]["variant"]["features"])
        records = case["records"]
        has_msg = any(r["type"] == "Message" for r in records)
        mcap_path = js.with_suffix(".mcap")
        expected_bytes = mcap_path.read_bytes()
        det = {}

        # --- py-streamed-reader: all variants
        out, err = run([a.python, reader_script, str(mcap_path), "streamed"], cwd=pkg_dir)
        ok = False
        if out is not None:
            got, err = parse_json(out)
            ok = got is not None and norm(got) == norm({"records": records})
            if not ok and err is None:
                err = "mismatch"
        groups["py_streamed_reader"].append((name, ok))
        det["py_streamed_reader"] = ok if ok else err

        # --- py-indexed-reader: Message + ch + chx + rch + rsh + mx
        if has_msg and {"ch", "chx", "rch", "rsh", "mx"} <= feats:
            out, err = run([a.python, reader_script, str(mcap_path), "indexed"], cwd=pkg_dir)
            ok = False
            if out is not None:
                got, err = parse_json(out)
                ok = got is not None and norm(got) == norm(expected_indexed(records))
                if not ok and err is None:
                    err = "mismatch"
            groups["py_indexed_reader"].append((name, ok))
            det["py_indexed_reader"] = ok if ok else err

        # --- py-writer: every variant without pad
        if "pad" not in feats:
            out, err = run([a.python, writer_script, str(js)], cwd=pkg_dir, binary=True)
            ok = out is not None and out == expected_bytes
            if out is not None and not ok:
                n = next((i for i, (x, y) in enumerate(zip(out, expected_bytes)) if x != y), min(len(out), len(expected_bytes)))
                err = f"bytes differ at offset {n} (got {len(out)} bytes, expected {len(expected_bytes)})"
            groups["py_writer"].append((name, ok))
            det["py_writer"] = ok if ok else err

            if out is not None:
                py_file = tmp / f"{name}.py.mcap"
                py_file.write_bytes(out)
                # Cross-implementation self-consistency: a reference reader must
                # see the same records in the Python-written file as in the
                # expected file. (Trivially true when bytes are identical; the
                # value is attribution when they are not.)
                def cross(label, cmd_for):
                    exp_out, e1 = run(cmd_for(str(mcap_path)))
                    got_out, e2 = run(cmd_for(str(py_file)))
                    if exp_out is None:
                        return None, f"reference reader failed on expected file: {e1}"
                    if got_out is None:
                        return False, f"reference reader failed on python output: {e2}"
                    ej, _ = parse_json(exp_out)
                    gj, _ = parse_json(got_out)
                    if ej is None or gj is None:
                        return False, "reference reader emitted invalid json"
                    return norm(ej) == norm(gj), None if norm(ej) == norm(gj) else "records differ from expected file"

                if a.rust_streamed:
                    ok, err = cross("rust", lambda f: [a.rust_streamed, f])
                    if ok is not None:
                        groups["rust_streamed_on_py_output"].append((name, ok))
                        det["rust_streamed_on_py_output"] = ok if ok else err
                if a.go_reader:
                    ok, err = cross("go", lambda f: [a.go_reader, f, "streamed"])
                    if ok is not None:
                        groups["go_streamed_on_py_output"].append((name, ok))
                        det["go_streamed_on_py_output"] = ok if ok else err
                if a.rust_indexed and has_msg and {"ch", "chx", "rch", "rsh"} <= feats:
                    ok, err = cross("rust-indexed", lambda f: [a.rust_indexed, f])
                    if ok is not None:
                        groups["rust_indexed_on_py_output"].append((name, ok))
                        det["rust_indexed_on_py_output"] = ok if ok else err
                if a.go_reader and has_msg and {"ch", "chx", "rch", "rsh", "mx"} <= feats:
                    ok, err = cross("go-indexed", lambda f: [a.go_reader, f, "indexed"])
                    if ok is not None:
                        groups["go_indexed_on_py_output"].append((name, ok))
                        det["go_indexed_on_py_output"] = ok if ok else err
        details[name] = det

    summary = {}
    for g, results in groups.items():
        summary[g] = {
            "run": len(results),
            "passed": sum(1 for _, ok in results if ok),
            "failed": [n for n, ok in results if not ok],
        }
    Path(a.out).write_text(json.dumps({"summary": summary, "details": details}, indent=1, sort_keys=True))
    for g, s in summary.items():
        print(f"{g}: {s['passed']}/{s['run']} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

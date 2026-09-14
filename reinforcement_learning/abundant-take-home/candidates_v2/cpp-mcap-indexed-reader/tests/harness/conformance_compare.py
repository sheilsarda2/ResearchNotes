#!/usr/bin/env python3
"""Corpus conformance check for the C++ indexed reader.

Re-implements, in Python, exactly what tests/conformance/scripts/run-tests/index.ts does for an
IndexedReadTestRunner:

  * expectedResult(testCase): from the variant's expected records (the .json next to the .mcap),
    keep the first Schema/Channel per id, keep every Message and Statistics record, then sort
    messages by log_time (stable, so equal log_times keep file order) and schemas/channels by id.
  * The runner's stdout JSON is compared to that expected object after json-stable-stringify
    normalisation (key order ignored, array order significant, every scalar a string).

Variant selection mirrors CppIndexedReaderTestRunner.supportsVariant: the input must contain a
Message record and the variant must carry the features ch, chx, rch, rsh and mx.

Usage: conformance_compare.py <runner-binary> <corpus-dir> <out.json>
Exit status 0 when every supported variant matches and the supported count equals EXPECTED_VARIANTS.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

EXPECTED_VARIANTS = 16
REQUIRED_FEATURES = {"ch", "chx", "rch", "rsh", "mx"}
PER_VARIANT_TIMEOUT_SEC = 60


def field(record, name):
    for k, v in record["fields"]:
        if k == name:
            return v
    raise KeyError(f"record lacks field {name}: {json.dumps(record)}")


def expected_result(test_case):
    result = {"schemas": [], "channels": [], "messages": [], "statistics": []}
    known_schemas, known_channels = set(), set()
    for record in test_case["records"]:
        t = record["type"]
        if t == "Schema":
            i = int(field(record, "id"))
            if i not in known_schemas:
                known_schemas.add(i)
                result["schemas"].append(record)
        elif t == "Channel":
            i = int(field(record, "id"))
            if i not in known_channels:
                known_channels.add(i)
                result["channels"].append(record)
        elif t == "Message":
            result["messages"].append(record)
        elif t == "Statistics":
            result["statistics"].append(record)
    # Python's sort is stable, like Array.prototype.sort in modern V8.
    result["messages"].sort(key=lambda r: int(field(r, "log_time")))
    result["schemas"].sort(key=lambda r: int(field(r, "id")))
    result["channels"].sort(key=lambda r: int(field(r, "id")))
    return result


def normalized(obj):
    # json-stable-stringify: recursively sorted object keys; arrays keep order.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def supported(variant_name, test_case):
    if not any(r["type"] == "Message" for r in test_case["records"]):
        return False
    features = set(test_case.get("meta", {}).get("variant", {}).get("features", []))
    if not features:
        # fall back to the file name: <Base>-<feat>-<feat>...
        features = set(variant_name.split("-")[1:])
    return REQUIRED_FEATURES.issubset(features)


def main():
    runner, corpus, out_path = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    results = []
    for json_path in sorted(corpus.glob("*/*.json")):
        mcap_path = json_path.with_suffix(".mcap")
        test_case = json.loads(json_path.read_text())
        name = json_path.stem
        if not supported(name, test_case):
            continue
        entry = {"variant": name, "status": "fail"}
        if not mcap_path.exists():
            entry["error"] = "missing .mcap"
            results.append(entry)
            continue
        try:
            proc = subprocess.run(
                [runner, str(mcap_path)],
                capture_output=True,
                text=True,
                timeout=PER_VARIANT_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            entry["error"] = "timeout"
            results.append(entry)
            continue
        if proc.returncode != 0:
            entry["error"] = f"exit {proc.returncode}: {proc.stderr.strip()[:500]}"
            results.append(entry)
            continue
        try:
            actual = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            entry["error"] = f"invalid JSON on stdout: {exc}"
            results.append(entry)
            continue
        expected = expected_result(test_case)
        if normalized(actual) == normalized(expected):
            entry["status"] = "pass"
        else:
            entry["error"] = "output differs from expected"
            entry["expected"] = expected
            entry["actual"] = actual
        results.append(entry)

    passed = sum(1 for r in results if r["status"] == "pass")
    summary = {
        "expected_variants": EXPECTED_VARIANTS,
        "supported_variants": len(results),
        "passed": passed,
        "ok": passed == len(results) == EXPECTED_VARIANTS,
        "variants": results,
    }
    Path(out_path).write_text(json.dumps(summary, indent=1))
    print(f"corpus: {passed}/{len(results)} variants pass (expected {EXPECTED_VARIANTS})")
    for r in results:
        if r["status"] != "pass":
            print(f"  FAIL {r['variant']}: {r.get('error')}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

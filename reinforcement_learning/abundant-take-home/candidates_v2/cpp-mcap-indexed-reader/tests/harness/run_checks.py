#!/usr/bin/env python3
"""Verifier orchestration for cpp-mcap-indexed-reader.

Runs every check group against binaries that test.sh has already built from the submission,
writes <out>/score.json with per-group attribution and <out>/reward.txt (0 or 1).

Groups (reward 1 requires every group ok):
  build        test.sh reports whether each binary compiled (passed in via --build-status)
  anti_cheat   submission contents scan
  unit_tests   pristine cpp/test/unit_tests.cpp, both compile variants, exact test-case counts
  corpus       tests/conformance corpus through the pristine indexed-reader-conformance runner
  fixtures     generated fixtures through the hidden probe versus analytic expectations
  siblings     Go and Rust indexed readers versus the C++ output on cross-checked fixtures
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import conformance_compare  # noqa: E402

# Pristine cpp/test/unit_tests.cpp at the base commit on Linux (the verifier platform): 18 Catch2
# cases with compression, 16 without. One case ("FileWriter reports filesystem write errors") is
# guarded by `#if defined(__linux__)`, so the same file lists 17/15 on macOS.
EXPECTED_UNIT_CASES = {"unit-tests": 18, "unit-tests-nocompress": 16}
PROBE_TIMEOUT = 120
MAX_TIME = (1 << 64) - 1

FORBIDDEN_PATTERNS = [
    r"/logs\b", r"reward\.txt", r"score\.json", r"\bstd::system\b", r"\bsystem\s*\(",
    r"\bpopen\s*\(", r"\bexecv", r"\bexecl", r"\bfork\s*\(", r"\bdlopen\b", r"\bgetenv\s*\(",
    r"__attribute__\s*\(\s*\(\s*constructor", r"indexed-reader-conformance",
    r"test-read-conformance", r"go-indexed-reader", r"conformance_indexed_reader", r"/opt/verify", r"/tests/",
    r"indexed_probe",
]
ALLOWED_SUFFIXES = {".hpp", ".inl", ".h", ".hh", ".hxx", ".ipp"}


def run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


# ---------------------------------------------------------------- anti-cheat
def check_submission(sub):
    findings = []
    files = []
    for p in sub.rglob("*"):
        if p.is_symlink():
            findings.append(f"symlink not allowed: {p.relative_to(sub)}")
            continue
        if p.is_dir():
            continue
        files.append(p)
    if len(files) == 0:
        findings.append("submission directory is empty")
    if len(files) > 200:
        findings.append(f"too many files: {len(files)}")
    total = 0
    for p in files:
        rel = str(p.relative_to(sub))
        if p.suffix not in ALLOWED_SUFFIXES:
            findings.append(f"unexpected file type: {rel}")
            continue
        size = p.stat().st_size
        total += size
        if size > 4 * 1024 * 1024:
            findings.append(f"file too large: {rel} ({size} bytes)")
        try:
            text = p.read_text(errors="replace")
        except OSError as exc:
            findings.append(f"unreadable: {rel}: {exc}")
            continue
        for pat in FORBIDDEN_PATTERNS:
            for m in re.finditer(pat, text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(f"forbidden pattern {pat!r} in {rel}:{line}")
                break
    if total > 20 * 1024 * 1024:
        findings.append(f"submission too large: {total} bytes")
    for required in ("reader.hpp", "reader.inl", "writer.hpp", "writer.inl", "types.hpp", "mcap.hpp"):
        if not (sub / required).exists():
            findings.append(f"missing required header: {required}")
    return {"ok": not findings, "findings": findings, "file_count": len(files)}


# ---------------------------------------------------------------- unit tests
def run_unit_tests(bin_dir):
    result = {"ok": True}
    for name, expected_cases in EXPECTED_UNIT_CASES.items():
        exe = bin_dir / name
        entry = {"expected_cases": expected_cases}
        if not exe.exists():
            entry.update(status="missing binary")
            result[name] = entry
            result["ok"] = False
            continue
        rc, out, err = run([str(exe)], timeout=900)
        entry["exit"] = rc
        m = re.search(r"All tests passed \((\d+) assertions in (\d+) test cases\)", out)
        if m:
            entry["assertions"] = int(m.group(1))
            entry["cases"] = int(m.group(2))
        else:
            m2 = re.search(r"test cases:\s*(\d+)\s*\|\s*(\d+) passed(?:\s*\|\s*(\d+) failed)?", out)
            if m2:
                entry["cases"] = int(m2.group(1))
                entry["cases_passed"] = int(m2.group(2))
                entry["cases_failed"] = int(m2.group(3) or 0)
            failing = re.findall(r"^-{79}\n([^\n]+)\n", out, flags=re.M)
            entry["failing_cases"] = failing[:20]
        entry["tail"] = (out.strip().splitlines() or [""])[-1][:300]
        ok = rc == 0 and entry.get("cases") == expected_cases and "assertions" in entry
        entry["ok"] = ok
        result[name] = entry
        result["ok"] = result["ok"] and ok
    return result


# ---------------------------------------------------------------- corpus
def run_corpus(bin_dir, corpus_dir, out_dir):
    exe = bin_dir / "indexed-reader-conformance"
    if not exe.exists():
        return {"ok": False, "error": "missing indexed-reader-conformance binary"}
    report = out_dir / "corpus.json"
    rc = subprocess.call([sys.executable, str(Path(__file__).with_name("conformance_compare.py")),
                          str(exe), str(corpus_dir), str(report)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    data = json.loads(report.read_text())
    return {"ok": rc == 0 and data["ok"], "expected_variants": data["expected_variants"],
            "supported_variants": data["supported_variants"], "passed": data["passed"],
            "failed_variants": [v["variant"] for v in data["variants"] if v["status"] != "pass"]}


# ---------------------------------------------------------------- fixtures
def normalize_msg(m, check_offsets):
    base = {"channel_id": m["channel_id"], "sequence": m["sequence"], "log_time": m["log_time"],
            "publish_time": m["publish_time"], "data_hex": m["data_hex"]}
    if check_offsets:
        base["offset"] = m["offset"]
        base["chunk_offset"] = m["chunk_offset"]
    return base


def compare_summary(expected, actual):
    """Returns a list of mismatch descriptions (empty when equal)."""
    diffs = []
    if actual.get("open_status") != "Success":
        return [f"open failed: {actual.get('open_status')}"]
    if actual.get("status") != expected["status"]:
        diffs.append(f"status {actual.get('status')} != {expected['status']}")
    if expected["status"] != "Success" and expected["status"] not in actual.get("problems", []):
        diffs.append(f"onProblem not invoked with {expected['status']} (got {actual.get('problems')})")
    if expected["status"] == "Success" and actual.get("problems"):
        diffs.append(f"unexpected problems {actual.get('problems')}")
    keys = ["statistics"] if expected.get("loose") else [
        "chunk_indexes", "statistics", "schemas", "channels", "attachment_indexes", "metadata_indexes"]
    for key in keys:
        if json.dumps(actual.get(key), sort_keys=True) != json.dumps(expected[key], sort_keys=True):
            diffs.append(f"{key} differ")
    exp_ranges = {(r["start"], r["end"]): r["range"] for r in expected["byte_ranges"]}
    act_ranges = {(r["start"], r["end"]): r["range"] for r in actual.get("byte_ranges", [])}
    for k, v in exp_ranges.items():
        if act_ranges.get(k) != v:
            diffs.append(f"byteRange{k} = {act_ranges.get(k)} != {v}")
    return diffs


def run_fixtures(probe, fixtures_dir, manifest, out_dir):
    results = {"ok": True, "checks": 0, "passed": 0, "failures": []}
    details = {}
    for name, fx in sorted(manifest["fixtures"].items()):
        path = fixtures_dir / fx["file"]
        fdetail = {"summary": {}, "queries": {}}
        # summary modes
        for method, exp in fx["summary"].items():
            args = [str(probe), "summary", str(path), method]
            for r in exp["byte_ranges"]:
                args += [str(r["start"]), str(r["end"])]
            rc, out, err = run(args, PROBE_TIMEOUT)
            results["checks"] += 1
            if rc != 0:
                diffs = [f"probe exit {rc}: {err.strip()[:300]}"]
            else:
                try:
                    actual = json.loads(out)
                    diffs = compare_summary(exp, actual)
                except json.JSONDecodeError as exc:
                    diffs = [f"bad probe JSON: {exc}"]
            fdetail["summary"][method] = diffs or "pass"
            if diffs:
                results["failures"].append(f"{name}/summary/{method}: {'; '.join(diffs)[:400]}")
            else:
                results["passed"] += 1
        # message queries
        for q in fx["queries"]:
            args = [str(probe), "messages", str(path), q["order"], str(q["start"]), str(q["end"])]
            if q.get("summary"):
                args += ["--summary", q["summary"]]
            if q.get("topics") is not None:
                args += ["--topics", ",".join(q["topics"]) or ","]
            rc, out, err = run(args, PROBE_TIMEOUT)
            results["checks"] += 1
            diffs = []
            if rc != 0:
                diffs.append(f"probe exit {rc}: {err.strip()[:300]}")
            else:
                try:
                    actual = json.loads(out)
                except json.JSONDecodeError as exc:
                    actual = None
                    diffs.append(f"bad probe JSON: {exc}")
                if actual is not None:
                    if actual.get("open_status") != "Success":
                        diffs.append(f"open failed: {actual.get('open_status')}")
                    exp_msgs = [normalize_msg(m, q["check_offsets"]) for m in q["expected_messages"]]
                    act_msgs = [normalize_msg(m, q["check_offsets"]) for m in actual.get("messages", [])]
                    if exp_msgs != act_msgs:
                        # find first divergence for attribution
                        idx = next((i for i, (a, b) in enumerate(zip(exp_msgs, act_msgs)) if a != b),
                                   min(len(exp_msgs), len(act_msgs)))
                        diffs.append(f"messages differ: expected {len(exp_msgs)} got {len(act_msgs)}; "
                                     f"first divergence at #{idx}: expected "
                                     f"{exp_msgs[idx] if idx < len(exp_msgs) else None} got "
                                     f"{act_msgs[idx] if idx < len(act_msgs) else None}")
                    if sorted(set(actual.get("problems", []))) != sorted(set(q["expected_problems"])):
                        diffs.append(f"problems {actual.get('problems')} != {q['expected_problems']}")
                    # every yielded view must carry a channel matching the message
                    for m in actual.get("messages", []):
                        if m.get("channel_ptr_id") != m["channel_id"]:
                            diffs.append("MessageView.channel does not match message.channelId")
                            break
                        if m.get("channel_schema_id") not in (None, 0) and m.get("schema_id") != m.get("channel_schema_id"):
                            diffs.append("MessageView.schema does not match channel.schemaId")
                            break
                        if m.get("channel_schema_id") == 0 and m.get("schema_id") is not None:
                            diffs.append("MessageView.schema must be null for schema_id 0")
                            break
                    exp_stats = q.get("expected_statistics_present")
                    if exp_stats is not None and bool(actual.get("statistics_present")) != exp_stats:
                        diffs.append(f"statistics().has_value() is {actual.get('statistics_present')}, expected {exp_stats}")
            fdetail["queries"][q["name"]] = diffs or "pass"
            if diffs:
                results["failures"].append(f"{name}/{q['name']}: {'; '.join(diffs)[:500]}")
            else:
                results["passed"] += 1
        details[name] = fdetail
    results["ok"] = results["passed"] == results["checks"] and results["checks"] > 0
    (out_dir / "fixtures_detail.json").write_text(json.dumps(details, indent=1))
    return results


# ---------------------------------------------------------------- siblings
def sibling_messages(obj):
    """Canonical tuples from a conformance-style IndexedReadTestResult."""
    out = []
    for rec in obj["messages"]:
        f = dict((k, v) for k, v in rec["fields"])
        out.append((int(f["channel_id"]), bytes(int(x) for x in f["data"]).hex(),
                    int(f["log_time"]), int(f["publish_time"]), int(f["sequence"])))
    return out


def probe_messages(obj):
    return [(m["channel_id"], m["data_hex"], m["log_time"], m["publish_time"], m["sequence"])
            for m in obj["messages"]]


def tie_runs(seq):
    """Split a log-time-ordered message sequence into runs of consecutive equal log_time and sort
    each run canonically.

    Two sequences with equal tie_runs() contain the same messages, in the same order across
    distinct timestamps, and the same multiset within each equal-timestamp run. Only the order
    *inside* a run is left open: the MCAP spec does not define it, and the reference readers
    disagree there -- C++ and Rust break cross-chunk ties by file offset, while Go's indexed
    iterator returns messages from the earlier-*loaded* chunk first (chunks are loaded by
    message_start_time; see go/mcap/indexed_message_iterator.go, "We stable-sort to ensure that if
    messages in different chunks have the same timestamp, the one from the earlier-loaded chunk is
    returned first"). The C++ offset tie-break itself (R3.6) is verified exactly, with offsets, by
    the fixtures group; the sibling group checks cross-implementation agreement on everything the
    spec pins down."""
    runs = []
    for m in seq:
        if runs and runs[-1][0] == m[2]:
            runs[-1][1].append(m)
        else:
            runs.append((m[2], [m]))
    return [(t, sorted(ms)) for t, ms in runs]


def run_siblings(probe, fixtures_dir, manifest, go_reader, rust_reader, out_dir):
    result = {"ok": True, "checks": 0, "passed": 0, "failures": [], "readers": {}, "details": {}}
    readers = {}
    if go_reader:
        readers["go"] = lambda p: [str(go_reader), str(p), "indexed"]
    if rust_reader:
        readers["rust"] = lambda p: [str(rust_reader), str(p)]
    result["readers"] = sorted(readers)
    for name, fx in sorted(manifest["fixtures"].items()):
        if not fx["sibling_check"]:
            continue
        path = fixtures_dir / fx["file"]
        # Same sequence as the upstream conformance runner: readSummary(NoFallbackScan), then
        # LogTimeOrder over everything.
        rc, out, err = run([str(probe), "messages", str(path), "logtime", "0", str(MAX_TIME),
                            "--summary", "NoFallbackScan"], PROBE_TIMEOUT)
        cpp = None
        if rc == 0:
            try:
                cpp = probe_messages(json.loads(out))
            except (json.JSONDecodeError, KeyError):
                cpp = None
        for rname, argv in readers.items():
            result["checks"] += 1
            rc2, out2, err2 = run(argv(path), PROBE_TIMEOUT)
            if rc2 != 0:
                result["failures"].append(f"{name}/{rname}: reference reader failed: {err2.strip()[:200]}")
                continue
            try:
                ref = sibling_messages(json.loads(out2))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                result["failures"].append(f"{name}/{rname}: unparsable reference output: {exc}")
                continue
            if cpp is None:
                result["failures"].append(f"{name}/{rname}: C++ probe failed")
                continue
            ref_runs, cpp_runs = tie_runs(ref), tie_runs(cpp)
            detail = {"messages": len(cpp), "reference_messages": len(ref), "exact": ref == cpp,
                      "tie_runs": sum(1 for _, ms in cpp_runs if len(ms) > 1)}
            result["details"][f"{name}/{rname}"] = detail
            if ref_runs != cpp_runs:
                flat_ref = [m for _, ms in ref_runs for m in ms]
                flat_cpp = [m for _, ms in cpp_runs for m in ms]
                idx = next((i for i, (a, b) in enumerate(zip(flat_ref, flat_cpp)) if a != b),
                           min(len(flat_ref), len(flat_cpp)))
                result["failures"].append(
                    f"{name}/{rname}: {len(ref)} reference messages vs {len(cpp)} from C++; "
                    f"first divergence at #{idx} (equal-log_time runs compared as multisets)")
                continue
            result["passed"] += 1
    result["ok"] = result["passed"] == result["checks"]
    return result


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", required=True, type=Path)
    ap.add_argument("--probe", required=True, type=Path)
    ap.add_argument("--corpus", required=True, type=Path)
    ap.add_argument("--fixtures", required=True, type=Path)
    ap.add_argument("--submission", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--build-status", type=Path, help="JSON written by test.sh: {name: bool}")
    ap.add_argument("--go-reader", type=Path)
    ap.add_argument("--rust-reader", type=Path)
    ap.add_argument("--skip-siblings", action="store_true")
    ap.add_argument("--expected-sibling-checks", type=int, default=None,
                    help="exact number of sibling comparisons required (fixtures x readers)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    score = {}
    build = {"ok": True}
    if args.build_status and args.build_status.exists():
        status = json.loads(args.build_status.read_text())
        build.update(status)
        build["ok"] = all(bool(v) for v in status.values()) and len(status) > 0
    score["build"] = build

    score["anti_cheat"] = check_submission(args.submission)

    if build["ok"]:
        score["unit_tests"] = run_unit_tests(args.bin_dir)
        score["corpus"] = run_corpus(args.bin_dir, args.corpus, args.out)
        manifest = json.loads((args.fixtures / "manifest.json").read_text())
        if args.probe.exists():
            score["fixtures"] = run_fixtures(args.probe, args.fixtures, manifest, args.out)
            if args.skip_siblings:
                score["siblings"] = {"ok": True, "skipped": True}
            else:
                score["siblings"] = run_siblings(args.probe, args.fixtures, manifest,
                                                 args.go_reader, args.rust_reader, args.out)
                if args.expected_sibling_checks is not None and \
                        score["siblings"]["checks"] != args.expected_sibling_checks:
                    score["siblings"]["ok"] = False
                    score["siblings"]["failures"].append(
                        f"expected {args.expected_sibling_checks} sibling checks, ran {score['siblings']['checks']}")
        else:
            score["fixtures"] = {"ok": False, "error": "probe binary missing"}
            score["siblings"] = {"ok": False, "error": "probe binary missing"}
    else:
        for g in ("unit_tests", "corpus", "fixtures", "siblings"):
            score[g] = {"ok": False, "skipped": "build failed"}

    reward = 1 if all(g.get("ok") for g in score.values()) else 0
    score["reward"] = reward
    (args.out / "score.json").write_text(json.dumps(score, indent=1))
    (args.out / "reward.txt").write_text(f"{reward}\n")
    print(json.dumps({k: v.get("ok") if isinstance(v, dict) else v for k, v in score.items()}))
    for g, v in score.items():
        if isinstance(v, dict) and not v.get("ok"):
            for f in (v.get("failures") or v.get("findings") or [])[:8]:
                print(f"  [{g}] {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

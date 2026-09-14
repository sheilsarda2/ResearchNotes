#!/usr/bin/env python3
"""Turn the verifier's raw outputs into reward.txt (0/1) and score.json (attribution)."""

from __future__ import annotations

import json
import os
import re
import sys

OUT, LOGS = sys.argv[1], sys.argv[2]

# Exact expected counts (measured on the gold patch; see STATUS.md).
EXPECTED = {
    "onnx_tests": 601,   # 597 upstream + 4 hidden PR tests (test_mod + empty lib/doc targets)
    "official": 860,     # Linux count: 830 harness rows + 4 drift/gate tests + 24 differential rows = 858 on macOS, plus 2 platform-conditional rows on Linux (measured aarch64; confirm on amd64)
    "onnx_ir": 553,      # integration test targets under crates/onnx-ir/tests/ (3+10+35+6+11+3+476+9+0)
}
HIDDEN_PR_TESTS = [
    "gru::tests::gru_runtime_weights",
    "gru::tests::gru_bidirectional_runtime_weights_match_initializers",
    "lstm::tests::lstm_runtime_weights",
    "rnn::tests::rnn_runtime_weights",
]
PROMOTED_ROWS = [
    "test_gru_batchwise", "test_gru_defaults", "test_gru_seq_length", "test_gru_with_initial_bias",
    "test_lstm_batchwise", "test_lstm_defaults", "test_lstm_with_initial_bias",
    "test_rnn_seq_length", "test_simple_rnn_batchwise", "test_simple_rnn_defaults",
    "test_simple_rnn_with_initial_bias",
]
DRIFT_TESTS = [
    "verify_expectations_match_tests", "verify_fail_compare_still_fails",
    "drift_gate_polarity", "negative_gate_actually_gates",
]

RESULT_RE = re.compile(
    r"^test result: (?P<status>\w+)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; "
    r"(?P<ignored>\d+) ignored; (?P<measured>\d+) measured; (?P<filtered>\d+) filtered out",
    re.M,
)
TEST_LINE_RE = re.compile(r"^test (?P<name>\S+) \.\.\. (?P<outcome>ok|FAILED|ignored)", re.M)


def read(name: str) -> str:
    p = os.path.join(OUT, name)
    return open(p, errors="replace").read() if os.path.exists(p) else ""


def rc_of(text: str, key: str = "rc") -> int | None:
    m = re.search(rf"^{key}=(\d+)$", text, re.M)
    return int(m.group(1)) if m else None


def parse_suite(name: str, expected: int) -> dict:
    text = read(f"{name}.txt")
    totals = {"passed": 0, "failed": 0, "ignored": 0}
    for m in RESULT_RE.finditer(text):
        for k in totals:
            totals[k] += int(m.group(k))
    outcomes = {m.group("name"): m.group("outcome") for m in TEST_LINE_RE.finditer(text)}
    failed_names = sorted(n for n, o in outcomes.items() if o == "FAILED")
    compile_error = "error: could not compile" in text or "error[E" in text
    rc = rc_of(text)
    ok = (
        rc == 0
        and not compile_error
        and totals["failed"] == 0
        and totals["ignored"] == 0
        and totals["passed"] == expected
    )
    return {
        "ok": ok,
        "rc": rc,
        "expected_passed": expected,
        **totals,
        "compile_error": compile_error,
        "failed_tests": failed_names[:80],
        "_outcomes": outcomes,
    }


score: dict = {"task": "rs-burn-onnx-rnn-runtime-weights", "groups": {}}

# anti-cheat
ac = read("anticheat.txt")
sections = {"symlinks": [], "non-source files": [], "patterns": []}
current = None
for line in ac.splitlines():
    if line.rstrip(":") in sections and line.endswith(":"):
        current = line.rstrip(":")
        continue
    if current and line.strip():
        sections[current].append(line.strip())
missing = [l for l in read("timeline.txt").splitlines() if l.startswith("MISSING")]
anticheat_ok = not any(sections.values()) and not missing
score["groups"]["anticheat"] = {
    "ok": anticheat_ok,
    "symlinks": sections["symlinks"][:20],
    "non_source_files": sections["non-source files"][:20],
    "pattern_hits": sections["patterns"][:40],
    "missing_artifacts": missing,
}

# build
bt = read("build_tests.txt")
bo = read("build_onnx2burn.txt")
build_rc, o2b_rc = rc_of(bt, "build_rc"), rc_of(bo, "onnx2burn_rc")
err_lines = [l for l in (bt + "\n" + bo).splitlines() if l.startswith("error")]
score["groups"]["build"] = {
    "ok": build_rc == 0 and o2b_rc == 0,
    "tests_rc": build_rc,
    "onnx2burn_rc": o2b_rc,
    "errors": err_lines[:40],
    "warnings": sum(1 for l in bt.splitlines() if l.startswith("warning")),
}

# suites
ot = parse_suite("onnx_tests", EXPECTED["onnx_tests"])
ot["hidden_pr_tests"] = {t: ot["_outcomes"].get(t, "missing") for t in HIDDEN_PR_TESTS}
ot["rnn_family_failed"] = [t for t in ot["failed_tests"] if t.split("::")[0] in ("gru", "lstm", "rnn")]
del ot["_outcomes"]
score["groups"]["onnx_tests"] = ot

of = parse_suite("official", EXPECTED["official"])
oc = of.pop("_outcomes")
# Bounded retry of the upstream drift gate (see test.sh): accept a retry pass, record it.
retry = read("official_drift_retry.txt")
of["drift_retries"] = retry.count("== retry")
if oc.get("verify_fail_compare_still_fails") == "FAILED" and re.search(
    r"^test verify_fail_compare_still_fails \.\.\. ok$", retry, re.M
):
    oc["verify_fail_compare_still_fails"] = "ok (retry)"
    of["failed_tests"] = [t for t in of["failed_tests"] if t != "verify_fail_compare_still_fails"]
    of["passed"], of["failed"] = of["passed"] + 1, of["failed"] - 1
    of["ok"] = (
        not of["compile_error"] and of["failed"] == 0 and of["ignored"] == 0
        and of["passed"] == EXPECTED["official"]
    )
of["promoted_rows"] = {t: oc.get(t, "missing") for t in PROMOTED_ROWS}
of["drift_tests"] = {t: oc.get(t, "missing") for t in DRIFT_TESTS}
diff_rows = {n: o for n, o in oc.items() if n.startswith("test_rnnrt_")}
of["differential"] = {
    "total": len(diff_rows),
    "passed": sum(1 for o in diff_rows.values() if o == "ok"),
    "failed": sorted(n for n, o in diff_rows.items() if o != "ok"),
}
other_failed = [n for n in of["failed_tests"] if n not in diff_rows and n not in PROMOTED_ROWS and n not in DRIFT_TESTS]
of["other_failed"] = other_failed[:40]
score["groups"]["official"] = of

oi = parse_suite("onnx_ir", EXPECTED["onnx_ir"])
del oi["_outcomes"]
score["groups"]["onnx_ir"] = oi

# rejects
rj = read("rejects.txt")
rejects = {
    m.group(1): {"rc": int(m.group(2)), "files": m.group(3).split()}
    for m in re.finditer(r"^reject (\S+) rc=(\d+) files=(.*)$", rj, re.M)
}
for name, r in rejects.items():
    r["rejected"] = r["rc"] != 0 and not any(f.endswith(".rs") for f in r["files"])
accept = re.search(r"^accept (\S+) rc=(\d+) files=(.*)$", rj, re.M)
accept_ok = bool(accept) and accept.group(2) == "0" and any(f.endswith(".rs") for f in accept.group(3).split())
rejects_ok = len(rejects) >= 3 and all(r["rejected"] for r in rejects.values()) and accept_ok
score["groups"]["rejects"] = {
    "ok": rejects_ok,
    "rejected": rejects,
    "accept_probe": {"rc": int(accept.group(2)) if accept else None, "files": accept.group(3).split() if accept else []},
}

score["timeline"] = read("timeline.txt").splitlines()
all_ok = all(g["ok"] for g in score["groups"].values())
score["reward"] = 1 if all_ok else 0
score["failed_groups"] = [k for k, g in score["groups"].items() if not g["ok"]]

with open(os.path.join(LOGS, "score.json"), "w") as f:
    json.dump(score, f, indent=1, sort_keys=True)
with open(os.path.join(LOGS, "reward.txt"), "w") as f:
    f.write("1\n" if all_ok else "0\n")
print(json.dumps({k: g["ok"] for k, g in score["groups"].items()}), "reward", score["reward"])

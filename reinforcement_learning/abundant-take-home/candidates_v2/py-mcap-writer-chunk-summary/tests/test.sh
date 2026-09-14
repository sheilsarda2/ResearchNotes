#!/usr/bin/env bash
# Clean-room verifier. Harbor uploads the [[artifacts]] entry /workspace/repo/python/mcap/mcap
# (the agent's package directory) to the same path in this container; everything else here is
# pristine and rebuilt from the image. Writes /logs/verifier/reward.txt (0|1) and score.json.
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
REPO=/workspace/repo
PRISTINE=/opt/pristine
REF=/opt/ref
export PYTHONDONTWRITEBYTECODE=1
cd "$REPO" || exit 0

# 1. Restore everything the verifier executes from the pristine copy (tests, runner scripts,
#    packaging metadata). Only python/mcap/mcap may differ from pristine.
rm -rf "$REPO/python/mcap/tests" && cp -R "$PRISTINE/python/mcap/tests" "$REPO/python/mcap/tests"
cp "$PRISTINE/python/mcap/pyproject.toml" "$PRISTINE/python/mcap/setup.cfg" "$REPO/python/mcap/"
rm -rf "$REPO/tests/conformance/data" && cp -R "$PRISTINE/tests/conformance/data" "$REPO/tests/conformance/data"
rm -rf "$REPO/testdata" && cp -R "$PRISTINE/testdata" "$REPO/testdata"
find "$REPO/python/mcap/mcap" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null

python3 - <<'PY'
import json, os, re, subprocess, sys
from pathlib import Path

REPO = Path("/workspace/repo"); PRISTINE = Path("/opt/pristine"); REF = Path("/opt/ref")
SUB = REPO / "python" / "mcap" / "mcap"
LOGS = Path("/logs/verifier")
score = {"groups": {}, "reward": 0}

def group(name, ok, **info):
    score["groups"][name] = {"ok": bool(ok), **info}
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {json.dumps(info, default=str)[:400]}")
    return bool(ok)

def finish():
    score["reward"] = int(all(g["ok"] for g in score["groups"].values()))
    LOGS.joinpath("score.json").write_text(json.dumps(score, indent=1, sort_keys=True, default=str))
    LOGS.joinpath("reward.txt").write_text(f"{score['reward']}\n")
    print("REWARD", score["reward"])
    sys.exit(0)

# 2. Submission shape and anti-cheat greps.
files = sorted(p for p in SUB.rglob("*") if p.is_file())
bad_ext = [str(p.relative_to(SUB)) for p in files if not (p.suffix == ".py" or p.name == "py.typed")]
required = ["__init__.py", "writer.py", "records.py", "reader.py", "stream_reader.py", "data_stream.py", "opcode.py", "exceptions.py"]
missing = [f for f in required if not (SUB / f).exists()]
patterns = ["_pytest", "atexit", "/logs", "conformance_reader", "test-read-conformance", "tests/conformance",
            "subprocess", "os.system", "ctypes", "/opt/pristine", "/opt/ref"]
hits = []
for p in files:
    if p.suffix != ".py":
        continue
    text = p.read_text(errors="replace")
    for pat in patterns:
        if pat in text:
            hits.append(f"{p.relative_to(SUB)}:{pat}")
comp = subprocess.run([sys.executable, "-m", "compileall", "-q", str(SUB)], capture_output=True, text=True)
ok = group("submission_shape", not bad_ext and not missing and not hits and comp.returncode == 0,
           files=len(files), non_python_files=bad_ext, missing=missing, forbidden=hits, compile_rc=comp.returncode,
           compile_err=comp.stderr[-500:])
if not ok:
    finish()

# 3. Conformance corpus: Python runners + reference readers on Python output.
conf_out = LOGS / "conformance.json"
subprocess.run([sys.executable, "/tests/run_conformance.py", "--repo", str(REPO), "--data", str(REPO / "tests/conformance/data"),
                "--python", sys.executable, "--out", str(conf_out),
                "--rust-streamed", str(REF / "conformance_reader"), "--rust-indexed", str(REF / "conformance_indexed_reader"),
                "--go-reader", str(REF / "test-read-conformance")], timeout=2400)
conf = json.loads(conf_out.read_text())["summary"] if conf_out.exists() else {}
EXPECTED = {"py_writer": 208, "py_streamed_reader": 416, "py_indexed_reader": 16,
            "rust_streamed_on_py_output": 208, "go_streamed_on_py_output": 208,
            "rust_indexed_on_py_output": 16, "go_indexed_on_py_output": 8}
for g, n in EXPECTED.items():
    s = conf.get(g, {"run": 0, "passed": 0, "failed": ["harness did not run"]})
    group(f"conformance:{g}", s["run"] == n and s["passed"] == n, expected=n, run=s["run"], passed=s["passed"],
          failed_examples=s["failed"][:12], failed_count=len(s["failed"]))

# 4. Authored differential and structural checks (verifier-owned parsing + Rust/Go readers).
auth_out = LOGS / "authored.json"
subprocess.run([sys.executable, "/tests/authored_checks.py", "--python", sys.executable, "--out", str(auth_out),
                "--go-reader", str(REF / "test-read-conformance"), "--rust-streamed", str(REF / "conformance_reader"),
                "--rust-indexed", str(REF / "conformance_indexed_reader")], timeout=900)
auth = json.loads(auth_out.read_text()) if auth_out.exists() else {"passed": 0, "total": 0, "failed": {"harness": "did not run"}}
ids = set(auth.get("ids", []))
need = {"generate", "multi_zstd:go_streamed_reads", "multi_zstd:rust_streamed_reads",
        "multi_zstd:rust_indexed_reads", "multi_lz4:chunk_crc", "empty_groups:record_sequence", "ordering:attachment_before_chunk"}
group("authored_checks", auth["total"] + 2 * len(auth.get("skipped", {})) >= 210 and auth["passed"] == auth["total"] and not auth["failed"] and need <= ids,
      passed=auth["passed"], total=auth["total"], failed=dict(list(auth["failed"].items())[:15]), missing_ids=sorted(need - ids), skipped=auth.get("skipped", {}))

# 5. Upstream unit tests (restored pristine copies), exact count, no skips; the one
#    timing-decided test is deselected.
import pytest
class Results:
    def __init__(self):
        self.collected = 0
        self.outcomes = {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0}
    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
    def pytest_runtest_logreport(self, report):
        if hasattr(report, "wasxfail"):
            self.outcomes["xfail"] += 1
        if report.skipped:
            self.outcomes["skipped"] += 1
        elif report.failed:
            self.outcomes["failed"] += 1
        elif report.when == "call":
            self.outcomes["passed"] += 1
res = Results()
os.chdir(REPO / "python" / "mcap")
rc = pytest.main(["-q", "-p", "no:cacheprovider", "--color=no", "--tb=short", "--junitxml=/logs/verifier/pytest.xml",
                  "--deselect", "tests/test_message_queue.py::test_insert_order_is_faster", "tests"], plugins=[res])
group("upstream_pytest", rc == 0 and res.collected == 36 and res.outcomes["passed"] == 36
      and not any(res.outcomes[k] for k in ("failed", "skipped", "xfail")), exit_code=int(rc), collected=res.collected, **res.outcomes)
finish()
PY
exit 0

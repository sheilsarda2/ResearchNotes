#!/bin/bash
# Produce the reference .rrd recordings with the Rust snippet binary (run at verifier image build).
#
# Usage: gen_reference.sh <repo_root> <reference_dir>
# Reads <reference_dir>/plan.json (from snippet_plan.py) and writes <reference_dir>/rrd/<subdir>/<name>.rrd for every
# entry with compare=true, using the same environment as docs/snippets/compare_snippet_output.py
# (scripts/roundtrip_utils.py::roundtrip_env).
set -euo pipefail
REPO=$1
REF=$2
PLAN=$REF/plan.json
BIN=$REF/bin/snippets_rust
mkdir -p "$REF/rrd"

python3 - "$REPO" "$REF" "$PLAN" "$BIN" <<'PY'
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

repo, ref, plan_path, binary = sys.argv[1:5]
plan = json.load(open(plan_path))
entries = [e for e in plan["entries"] if e["compare"]]

def env_for(out):
    env = os.environ.copy()
    env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONWARNINGS": "error",
        "RERUN_FLUSH_NUM_ROWS": "0",
        "RERUN_STRICT": "1",
        "RERUN_PANIC_ON_WARN": "1",
        "_RERUN_TEST_FORCE_SAVE": out,
    })
    return env

def run(entry):
    out = os.path.join(ref, "rrd", entry["subdir"], entry["name"] + ".rrd")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = [binary, entry["name"], *entry["args"]]
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env_for(out), capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return entry["key"], False, "timeout"
    ok = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
    return entry["key"], ok, (proc.stderr[-2000:] if not ok else "")

with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, entries))

failed = [(k, err) for k, ok, err in results if not ok]
for k, err in failed:
    print(f"REFERENCE FAILED: {k}\n{err}", file=sys.stderr)
print(f"reference recordings: {len(results) - len(failed)}/{len(results)} produced")
if failed or len(results) != plan["compare_count"]:
    sys.exit(1)
json.dump({"produced": [k for k, ok, _ in results if ok]}, open(os.path.join(ref, "reference_manifest.json"), "w"), indent=1)
PY

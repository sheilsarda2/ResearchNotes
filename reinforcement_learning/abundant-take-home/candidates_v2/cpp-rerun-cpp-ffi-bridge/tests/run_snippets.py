#!/usr/bin/env python3
"""Run the C++ snippets built from the submission and compare their .rrd output against the Rust reference.

Usage: run_snippets.py <repo_root> <reference_dir> <out_dir> <results_json> [--user runner] [--jobs 4]

* Each planned snippet runs as `build/debug/docs/snippets/snippets <name> [args]` with the roundtrip environment
  (RERUN_FLUSH_NUM_ROWS=0, RERUN_STRICT=1, RERUN_PANIC_ON_WARN=1, _RERUN_TEST_FORCE_SAVE=<out>) from the repo root,
  optionally as an unprivileged user (the reference directory is unreadable for that user).
* Entries with compare=true are then compared with
  `rerun rrd compare --unordered --ignore-chunks-without-components <cpp.rrd> <reference.rrd>`.
The results JSON lists per-snippet outcomes; the summary carries exact counts for score.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

RUN_TIMEOUT_SEC = 180
COMPARE_TIMEOUT_SEC = 180


def roundtrip_env(save_path: str, home: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": home,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONWARNINGS": "error",
        "RERUN_FLUSH_NUM_ROWS": "0",
        "RERUN_STRICT": "1",
        "RERUN_PANIC_ON_WARN": "1",
        "_RERUN_TEST_FORCE_SAVE": save_path,
    }
    return env


def main() -> None:
    repo, ref, out_dir, results_path = sys.argv[1:5]
    user = None
    jobs = 4
    if "--user" in sys.argv:
        user = sys.argv[sys.argv.index("--user") + 1]
    if "--jobs" in sys.argv:
        jobs = int(sys.argv[sys.argv.index("--jobs") + 1])

    plan = json.load(open(os.path.join(ref, "plan.json")))
    binary = os.path.join(repo, "build", "debug", "docs", "snippets", "snippets")
    home = os.path.join(out_dir, "home")
    os.makedirs(home, exist_ok=True)

    def run(entry: dict) -> dict:
        out = os.path.join(out_dir, "rrd", entry["subdir"], entry["name"] + ".rrd")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if user:
            subprocess.run(["chown", "-R", user, out_dir], check=False)
        cmd = [binary, entry["name"], *entry["args"]]
        if user:
            cmd = ["runuser", "-u", user, "--", *cmd]
        result = {"key": entry["key"], "compare": entry["compare"], "run_ok": False, "compare_ok": None}
        try:
            proc = subprocess.run(
                cmd, cwd=repo, env=roundtrip_env(out, home), capture_output=True, text=True,
                timeout=RUN_TIMEOUT_SEC,
            )
            result["exit_code"] = proc.returncode
            result["stderr_tail"] = proc.stderr[-1500:]
            result["run_ok"] = proc.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0
            if proc.returncode == 0 and not result["run_ok"]:
                result["stderr_tail"] += "\n[verifier] snippet exited 0 but wrote no recording"
        except subprocess.TimeoutExpired:
            result["exit_code"] = None
            result["stderr_tail"] = f"timeout after {RUN_TIMEOUT_SEC}s"
        result["output"] = out
        return result

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run, plan["entries"]))

    def compare(result: dict) -> dict:
        if not result["compare"]:
            return result
        if not result["run_ok"]:
            result["compare_ok"] = False
            result["compare_error"] = "snippet did not run successfully"
            return result
        sub, name = result["key"].rsplit("/", 1)
        reference = os.path.join(ref, "rrd", sub, name + ".rrd")
        cmd = ["rerun", "rrd", "compare", "--unordered", "--ignore-chunks-without-components",
               result["output"], reference]
        try:
            proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=COMPARE_TIMEOUT_SEC,
                                  env={**os.environ, "RERUN_STRICT": "1"})
            result["compare_ok"] = proc.returncode == 0
            if not result["compare_ok"]:
                result["compare_error"] = (proc.stdout + proc.stderr)[-3000:]
        except subprocess.TimeoutExpired:
            result["compare_ok"] = False
            result["compare_error"] = f"rrd compare timeout after {COMPARE_TIMEOUT_SEC}s"
        return result

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(compare, results))

    summary = {
        "planned_run": plan["run_count"],
        "planned_compare": plan["compare_count"],
        "run_ok": sum(1 for r in results if r["run_ok"]),
        "compare_ok": sum(1 for r in results if r["compare"] and r["compare_ok"]),
        "run_failed": sorted(r["key"] for r in results if not r["run_ok"]),
        "compare_failed": sorted(r["key"] for r in results if r["compare"] and not r["compare_ok"]),
    }
    json.dump({"summary": summary, "results": results}, open(results_path, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

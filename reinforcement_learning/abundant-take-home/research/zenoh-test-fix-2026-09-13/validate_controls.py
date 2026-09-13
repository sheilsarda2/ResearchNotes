#!/usr/bin/env python3
"""Validate v3 through normal Harbor oracle/nop, then diagnose saved solutions.

Uses shared resource admission and the existing build cache. Makes no model calls.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / "research/task-revisions/rs-zenoh-timestamp-instrumentation-v3"


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = BASE / ("harbor-validation-" + stamp)
    output.mkdir()
    jobs = output / "jobs"
    jobs.mkdir()
    control = output / "control.json"
    dump(control, {"max_active": 1, "min_total_mb": 30000, "reserve_mb": 4096,
                   "startup_reserve_mb": 1536, "startup_window_sec": 120,
                   "start_interval_sec": 5, "max_memory_pressure_pct": 1.0,
                   "pressure_cooldown_sec": 60, "network_pool_cidr": "172.31.0.0/16",
                   "paused": False, "shared_pool": str(ROOT / "jobs/candidate-campaigns-shared.control.json")})
    env = dict(os.environ, HARBOR_ADMISSION_CONTROL=str(control))
    summary = {}
    dump(BASE / "latest-controls.json", {"output": str(output), "pid": os.getpid(), "started_at": now()})
    for agent in ("oracle", "nop"):
        name = f"rs-zenoh-timestamp-instrumentation-v3-{agent}-{stamp}"
        command = [sys.executable, str(ROOT / "scripts/harbor-resource-runner.py"), "run", "-p", str(TASK),
                   "-a", agent, "-y", "-n", "1", "-o", str(jobs), "--job-name", name]
        start = time.monotonic()
        print(f"{now()} Starting Harbor {agent} with shared admission: {name}", flush=True)
        with (output / f"{agent}.log").open("w") as log:
            completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        results = list((jobs / name).glob("*/result.json"))
        assert len(results) == 1, f"Expected one {agent} trial result: {results}"
        path = results[0]
        result = json.loads(path.read_text())
        record = {"job": name, "exit_code": completed.returncode,
                  "wall_seconds": round(time.monotonic() - start, 2), "result": str(path),
                  "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
                  "exception": (result.get("exception_info") or {}).get("exception_type"),
                  "task_checksum": result.get("task_checksum"),
                  "agent_info": result.get("agent_info"),
                  "timings": {k: result.get(k) for k in ("environment_setup", "agent_execution", "verifier")},
                  "model_calls": 0}
        summary[agent] = record
        dump(output / "summary.json", summary)
        dump(BASE / "harbor-validation.json", summary)
        print(f"{now()} Harbor {agent}: reward={record['reward']} exception={record['exception']}", flush=True)
    print(f"{now()} Starting focused saved-submission diagnostics (no full regrade)", flush=True)
    with (BASE / "validation-runner.log").open("w") as log:
        subprocess.run([sys.executable, str(BASE / "validate_revision.py"), "--cases", "nBCUPNz", "sAWGR4Y"],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f"{now()} All requested validation checks completed", flush=True)


if __name__ == "__main__":
    main()

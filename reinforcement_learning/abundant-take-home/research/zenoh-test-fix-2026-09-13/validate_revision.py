#!/usr/bin/env python3
"""Diagnose the revised test against saved submissions without model calls.

Run inside keen_black with Harbor's Python. A SharedAdmission reservation keeps
these sequential checks within the live campaign's existing global resource cap.
These are focused diagnostics, not complete regrades or scored Harbor trials.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission

BASE = Path(__file__).resolve().parent
TASK = ROOT / "research/task-revisions/rs-zenoh-timestamp-instrumentation-v3"
IMAGE = "sha256:818d21d3689d8d38e0dc3a6b8bf92051545f595102c7a8658ae470a14eeb3399"
ORIGINAL_JOB = ROOT / "jobs/candidates-all14-efforts-20-20260913T183301Z"
REPLAYS = {name: ORIGINAL_JOB / f"rs-zenoh-timestamp-instrumentati__{name}"
           for name in ("nBCUPNz", "sAWGR4Y")}


def now():
    return datetime.now(timezone.utc).isoformat()


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def write_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def acquire(admission, name, config, status_path):
    last = 0
    while True:
        snapshot = memory_snapshot()
        reason = admission.try_acquire(name, snapshot, config)
        if not reason:
            print(f"{now()} admitted {name}", flush=True)
            return
        if time.monotonic() - last >= 30:
            last = time.monotonic()
            print(f"{now()} waiting {name}: {reason}", flush=True)
            write_json(status_path, {"status": "waiting", "updated_at": now(), "reason": reason})
        time.sleep(2)


def check_case(case, output, admission, config):
    destination = output / case
    destination.mkdir()
    name = f"zenoh-v3-validation-{case}-{os.getpid()}".lower()
    status_path = destination / "result.json"
    record = {"case": case, "status": "pending",
              "kind": "focused_saved_submission_diagnostic",
              "image_id": IMAGE, "task": str(TASK), "started_at": now(),
              "limits": {"cpus": 4, "memory_mb": 8192, "network_mode": "none", "timeout_sec": 3600},
              "source_trial": str(REPLAYS[case]),
              "task_file_sha256": hashes(TASK), "model_calls": 0,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    record["submission_file_sha256"] = hashes(REPLAYS[case] / "artifacts/submission")
    assert record["submission_file_sha256"], "Missing saved submission"
    write_json(status_path, record)
    acquire(admission, name, config, status_path)
    created = False
    try:
        run(["docker", "create", "--name", name, "--cpus", "4", "--memory", "8192m",
             "--memory-swap", "8192m", "--network", "none", IMAGE, "sleep", "infinity"],
            stdout=subprocess.DEVNULL)
        created = True
        run(["docker", "start", name], stdout=subprocess.DEVNULL)
        run(["docker", "exec", name, "bash", "-lc",
             "mkdir -p /workspace/repo /logs/verifier; cp -a /opt/pristine/. /workspace/repo/"])
        run(["docker", "cp", str(TASK / "tests") + "/.", name + ":/tests"])
        run(["docker", "cp", str(REPLAYS[case] / "artifacts/submission") + "/.", name + ":/workspace/repo"])
        record.update(status="running", verifier_started_at=now(), container_name=name)
        write_json(status_path, record)
        print(f"{now()} verifier started: {case}", flush=True)
        # Exercise the unchanged saved model sources against the corrected
        # integration target. This is diagnostic, not a complete regrade.
        command = ["bash", "-c", "set -euo pipefail; "
                       "for c in commons/zenoh-protocol commons/zenoh-codec zenoh zenoh-ext; do "
                       "rm -rf /workspace/build/$c/src; cp -a /workspace/repo/$c/src /workspace/build/$c/src; "
                       "find /workspace/build/$c/src -type f -exec touch {} +; done; "
                       "cp /tests/hidden/zenoh/tests/timestamp_instrumentation.rs /workspace/build/zenoh/tests/; "
                       "cd /workspace/build; "
                       "cargo test --offline -p zenoh --features zenoh/test,zenoh/unstable,zenoh/internal "
                       "--test timestamp_instrumentation interception_point_try_from_valid -- --exact --test-threads=1"]
        with (destination / "verifier.stdout.log").open("w") as log:
            completed = subprocess.run(["docker", "exec", name, "timeout", "-k", "30", "3600", *command],
                                       stdout=log, stderr=subprocess.STDOUT, timeout=3670)
        record.update(verifier_finished_at=now(), verifier_exit_code=completed.returncode)
        run(["docker", "cp", name + ":/logs/verifier", str(destination / "verifier")])
        record["reward"] = None
        log_text = (destination / "verifier.stdout.log").read_text()
        record["diagnostic_log_sha256"] = hashlib.sha256((destination / "verifier.stdout.log").read_bytes()).hexdigest()
        record["diagnostic_pass"] = completed.returncode == 0 and "test interception_point_try_from_valid ... ok" in log_text
        record["full_regrade_performed"] = False
        record["status"] = "complete"
        print(f"{now()} verifier finished: {case} reward={record['reward']} exit={completed.returncode}", flush=True)
    except Exception as exc:
        record.update(status="error", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if created:
            inspection = subprocess.run(["docker", "inspect", name], text=True, capture_output=True)
            if inspection.returncode == 0:
                details = json.loads(inspection.stdout)[0]
                record["container_evidence"] = {"image": details["Image"], "state": details["State"],
                                                "memory": details["HostConfig"]["Memory"],
                                                "nano_cpus": details["HostConfig"]["NanoCpus"],
                                                "network_mode": details["HostConfig"]["NetworkMode"]}
            run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL)
        admission.release(name)
        record["finished_at"] = now()
        write_json(status_path, record)
    return record


def reusable_results(cases):
    """Reuse only complete diagnostics with unchanged inputs and raw log hashes."""
    try:
        latest = json.loads((BASE / "latest-validation.json").read_text())
        output = Path(latest["output"])
        if not output.resolve().is_relative_to(BASE):
            return None
        records = json.loads((output / "summary.json").read_text())
        if [r["case"] for r in records] != cases:
            return None
        task_hashes = hashes(TASK)
        for record in records:
            case = record["case"]
            log = output / case / "verifier.stdout.log"
            if (record["status"] != "complete" or record["image_id"] != IMAGE or
                    record["runner_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest() or
                    record["kind"] != "focused_saved_submission_diagnostic" or
                    record["full_regrade_performed"] is not False or
                    record["task_file_sha256"] != task_hashes or
                    record["submission_file_sha256"] != hashes(REPLAYS[case] / "artifacts/submission") or
                    record["diagnostic_log_sha256"] != hashlib.sha256(log.read_bytes()).hexdigest()):
                return None
        return output
    except (OSError, ValueError, TypeError, KeyError):
        return None


def run_cases(cases):
    previous = reusable_results(cases)
    if previous:
        print(f"{now()} Reusing complete matching diagnostics: {previous}", flush=True)
        return
    output = BASE / ("validation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    control = output / "control.json"
    config = {"max_active": 1, "startup_reserve_mb": 1536, "reserve_mb": 4096,
              "shared_pool": str(ROOT / "jobs/candidate-campaigns-shared.control.json")}
    write_json(control, config)
    admission = SharedAdmission(config["shared_pool"], control)
    records = []
    write_json(BASE / "latest-validation.json", {"output": str(output), "pid": os.getpid(), "started_at": now()})
    for case in cases:
        records.append(check_case(case, output, admission, config))
        write_json(output / "summary.json", records)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", choices=list(REPLAYS), default=list(REPLAYS))
    args = parser.parse_args()
    with (BASE / "diagnostics.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run_cases(args.cases)


if __name__ == "__main__":
    main()

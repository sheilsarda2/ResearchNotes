#!/usr/bin/env python3
"""Observe the frozen 99-cell take-home scope; never control benchmark workers.

Only this observer's files under research/takehome-presentation-2026-09-14
are written. Existing campaign watchdogs remain responsible for recovery.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research/takehome-presentation-2026-09-14"
SCOPE = BASE / "coverage-scope.json"
SHARED = ROOT / "jobs/candidate-campaigns-shared.control.state.json"
COUNTED = {"scored", "timeout", "verifier_timeout"}
INTERVAL = 30
HEALTHY_WATCHDOG_STATES = {"running", "starting", "waiting_for_memory", "waiting_for_shared_resources"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now_string(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    assert path.parent.resolve() == BASE.resolve()
    temporary = path.with_name(path.name + ".%s.tmp" % os.getpid())
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def key(row):
    model = row["model"]
    if not model.startswith("anthropic/"):
        model = "anthropic/claude-" + model
    return json.dumps([row["task"], model, row["effort"]], separators=(",", ":"))


def coverage(scope, summaries):
    cells = scope["cells"]
    assert scope["count"] == len(cells) == 99
    assert all(key(cell) == name for name, cell in cells.items())
    assert set(summaries) == {cell["job"] for cell in cells.values()}
    assert len(summaries) == 4
    completed, selected_trials = {}, set()
    for campaign, summary in summaries.items():
        assert summary["job"] == campaign
        actual = Counter()
        for row in summary["trials"]:
            cell = key(row)
            if cell not in cells or cells[cell]["job"] != campaign or row["status"] not in COUNTED:
                continue
            assert row["trial"] not in selected_trials, "Duplicate counted trial"
            selected_trials.add(row["trial"])
            actual[cell] += 1
        for setting in summary["settings"]:
            cell = key(setting)
            if cell not in cells or cells[cell]["job"] != campaign:
                continue
            assert cell not in completed
            assert type(setting["completed"]) is int and setting["completed"] == actual[cell]
            assert setting["target"] == cells[cell]["target"]
            assert 0 <= setting["completed"] <= setting["target"]
            completed[cell] = actual[cell]
    assert set(completed) == set(cells), "Missing scope settings"
    missing = [name for name in cells if completed[name] == 0]
    return {"covered": 99 - len(missing), "required": 99, "ready": not missing,
            "counted_trials": sum(completed.values()), "missing_cells": missing}


def process(pid, proc_root=Path("/proc")):
    """Read identity and argv for validation only; never serialize argv."""
    try:
        directory = proc_root / str(int(pid))
        fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        identity = fields[19]
        args = (directory / "cmdline").read_bytes().decode().rstrip("\0").split("\0")
        again = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        if again[0] == "Z" or again[19] != identity:
            return None
        return {"pid": int(pid), "identity": identity, "args": args}
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def flag(args, name):
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def matches_role(info, role, name=None):
    if not info:
        return False
    args = info["args"]
    scripts = {Path(value).name for value in args if value.endswith(".py")}
    if role == "watchdog":
        return "watch-candidate-campaign.py" in scripts and flag(args, "--campaign") == name
    if role == "supervisor":
        campaign = flag(args, "--campaign")
        attempts, run_id = flag(args, "--attempts"), flag(args, "--run-id")
        return ("run-candidate-screen.py" in scripts
                and f"{campaign}-efforts-{attempts}-{run_id}" == name)
    if role == "runner":
        job = flag(args, "--job-name") or Path(flag(args, "--job-path") or "").name
        return "harbor-resource-runner.py" in scripts and job == name
    if role == "publisher":
        return (flag(args, "-m") == "benchmark_coverage_priority"
                or "benchmark_coverage_priority.py" in scripts) and "--watch" in args
    if role == "observer":
        return Path(__file__).name in scripts and "--watch" in args
    return False


def age(value, now):
    if isinstance(value, (float, int)):
        return now - value
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert stamp.tzinfo is not None
    return now - stamp.timestamp()


def live_record(pid, role, name=None, identity=None):
    info = process(pid)
    valid = matches_role(info, role, name) and (identity is None or info["identity"] == str(identity))
    return {"pid": pid, "expected_identity": identity,
            "observed_identity": info["identity"] if info else None, "valid": valid}


def observe(scope, scope_sha, watcher_sha, *, now=None):
    now = time.time() if now is None else now
    alerts, campaigns, summaries, source_hashes = [], {}, {}, {}
    if digest(SCOPE.read_bytes()) != scope_sha:
        alerts.append("scope_changed: retained original frozen 99-cell scope")
    if digest(Path(__file__).read_bytes()) != watcher_sha:
        alerts.append("observer_source_changed: running process has not reloaded")
    shared = json.loads(SHARED.read_text())
    participants = shared["participants"]
    for campaign in sorted({cell["job"] for cell in scope["cells"].values()}):
        prefix = ROOT / "jobs" / campaign
        summary_bytes = prefix.with_suffix(".summary.json").read_bytes()
        summary = summaries[campaign] = json.loads(summary_bytes)
        source_hashes[campaign] = digest(summary_bytes)
        watchdog = json.loads(prefix.with_suffix(".watchdog.json").read_text())
        assert watchdog["campaign"] == campaign
        summary_age = age(summary["updated_at"], now)
        watchdog_age = age(watchdog["updated_at"], now)
        entry = campaigns[campaign] = {"summary_age_sec": round(summary_age, 2),
                    "watchdog_age_sec": round(watchdog_age, 2), "watchdog_status": watchdog["status"],
                    "automatic_recovery": watchdog.get("automatic_recovery"),
                    "active_trials": watchdog.get("active_trials"), "workers": {}}
        if summary.get("status") == "complete":
            entry["expected_terminal"] = True
            continue
        if not -5 <= summary_age <= 180:
            alerts.append(campaign + ": summary_stale")
        if not -5 <= watchdog_age <= 90:
            alerts.append(campaign + ": watchdog_stale")
        for warning in watchdog.get("alerts", []):
            alerts.append(campaign + ": watchdog_alert: " + str(warning))
        if watchdog.get("status") not in HEALTHY_WATCHDOG_STATES or watchdog.get("automatic_recovery") is not True:
            alerts.append(campaign + ": watchdog_not_healthy_running")
        observed = live_record(watchdog["watchdog_pid"], "watchdog", campaign)
        entry["watchdog"] = observed
        if not observed["valid"]:
            alerts.append(campaign + ": watchdog_process_missing_or_wrong_identity")
        supervisors = watchdog.get("supervisors", [])
        entry["supervisors"] = [live_record(p["pid"], "supervisor", campaign, p["identity"]) for p in supervisors]
        recorded_supervisor = int(prefix.with_suffix(".pid").read_text().strip())
        if len(supervisors) != 1 or recorded_supervisor not in {p["pid"] for p in supervisors}:
            alerts.append(campaign + ": supervisor_state_disagreement")
        if not entry["supervisors"] or not all(p["valid"] for p in entry["supervisors"]):
            alerts.append(campaign + ": supervisor_exit_or_identity_mismatch")
        runners = watchdog.get("runner_pids", {})
        if campaign not in runners:
            alerts.append(campaign + ": missing_runner")
        for job, pid in runners.items():
            owners = [p for p in participants.values()
                      if Path(p["control"]).name == job + ".control.json"]
            owner = next((p for p in owners if p["pid"] == pid), None)
            check = live_record(pid, "runner", job, owner["identity"] if owner else None)
            check["shared_state_matches"] = owner is not None
            check["claims"] = len(owner["trials"]) if owner else None
            entry["workers"][job] = check
            if not check["valid"] or owner is None:
                alerts.append(job + ": worker_exit_or_state_identity_mismatch")
            for candidate in owners:
                if candidate["trials"] and not live_record(candidate["pid"], "runner", job, candidate["identity"])["valid"]:
                    alerts.append(job + ": dead_owner_has_claims")
    counted = coverage(scope, summaries)
    publisher = json.loads((ROOT / "jobs/candidate-campaigns-shared.control.coverage.json").read_text())
    publisher_live = live_record(publisher["pid"], "publisher")
    publisher_age = age(publisher["updated_at"], now)
    publisher_info = {**publisher_live, "healthy": publisher.get("healthy"),
                      "age_sec": round(publisher_age, 2)}
    if not publisher_live["valid"] or publisher.get("healthy") is not True or not -5 <= publisher_age <= 30:
        alerts.append("coverage_publisher_unhealthy_or_stale")
    return {"updated_at": now_string(now), "pid": os.getpid(),
            "identity": process(os.getpid())["identity"], "interval_seconds": INTERVAL,
            "scope_sha256": scope_sha, "watcher_sha256": watcher_sha,
            "status": "attention" if alerts else "healthy", "alerts": sorted(set(alerts)),
            "coverage": counted, "campaigns": campaigns, "summary_sha256": source_hashes,
            "coverage_publisher": publisher_info,
            "readiness_basis": "Classified campaign summaries for frozen 99 cells; final collector audit remains separate.",
            "read_only": True}


def emit(record, event):
    value = {**record, "event": event}
    with (BASE / "monitor-history.jsonl").open("a") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
    print(json.dumps(value, sort_keys=True), flush=True)


def watch():
    with (BASE / "monitor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        content = SCOPE.read_bytes()
        scope, scope_sha = json.loads(content), digest(content)
        watcher_sha = digest(Path(__file__).read_bytes())
        previous_alerts, was_ready, hourly = None, False, 0.0
        while True:
            started = time.monotonic()
            try:
                record = observe(scope, scope_sha, watcher_sha)
            except Exception as error:
                record = {"updated_at": now_string(), "pid": os.getpid(),
                          "identity": process(os.getpid())["identity"], "status": "attention",
                          "alerts": ["observer_check_failed:" + type(error).__name__], "read_only": True}
            atomic_json(BASE / "monitor.json", record)
            ready = record.get("coverage", {}).get("ready", False)
            if previous_alerts is None:
                emit(record, "started")
            elif record["alerts"] != previous_alerts:
                emit(record, "attention" if record["alerts"] else "recovered")
            if ready and not was_ready:
                emit(record, "99_cells_ready")
            if time.monotonic() - hourly >= 3600:
                emit(record, "hourly_health")
                hourly = time.monotonic()
            previous_alerts, was_ready = record["alerts"], ready
            time.sleep(max(0, INTERVAL - (time.monotonic() - started)))


def launch():
    # This lock belongs only to the new observer; no benchmark lock/control is touched.
    with (BASE / "monitor-launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = [p for directory in Path("/proc").glob("[0-9]*")
                    if (p := process(int(directory.name))) and matches_role(p, "observer")]
        assert not existing, "An existing takehome observer is already running"
        log_path = BASE / "monitor.log"
        with log_path.open("ab") as log:
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--watch"],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         start_new_session=True)
        identity = None
        for _ in range(40):
            info = process(child.pid)
            if info and matches_role(info, "observer"):
                identity = info["identity"]
                break
            if child.poll() is not None:
                raise RuntimeError("Observer exited during startup")
            time.sleep(0.05)
        assert identity is not None
        record = {"started_at": now_string(), "pid": child.pid, "identity": identity,
                  "log": str(log_path.relative_to(ROOT)), "existing_observers": [],
                  "watcher_sha256": digest(Path(__file__).read_bytes()), "scope_sha256": digest(SCOPE.read_bytes()),
                  "launch_method": "detached subprocess.Popen; existing campaign recovery untouched"}
        atomic_json(BASE / "monitor-launch.json", record)
        print(json.dumps(record), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--watch", action="store_true")
    mode.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    assert Path("/proc/self/stat").exists(), "Run under the existing Docker devcontainer"
    if args.launch:
        launch()
    elif args.watch:
        watch()
    else:
        content = SCOPE.read_bytes()
        print(json.dumps(observe(json.loads(content), digest(content), digest(Path(__file__).read_bytes())), sort_keys=True))


if __name__ == "__main__":
    main()

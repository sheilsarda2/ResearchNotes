#!/usr/bin/env python3
"""Run the effort matrix with bounded concurrency and verify every recorded trial."""
from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import yaml
from harbor.models.job.config import JobConfig

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("fable-5-1", "opus-5", "sonnet-5")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TARGET = 3


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def live(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        return "Z (zombie)" not in status
    except FileNotFoundError:
        return False


def available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def active_harbor_jobs() -> dict[str, int]:
    found = {}
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().decode().split("\0")
        except (FileNotFoundError, PermissionError, UnicodeDecodeError, ProcessLookupError):
            continue
        # Never print process arguments: Docker commands can contain API keys.
        if "run" not in args or "--job-name" not in args:
            continue
        if not any(arg.endswith(("/harbor", "/harbor-benchmark-runner.py")) or arg == "harbor" for arg in args[:3]):
            continue
        found[args[args.index("--job-name") + 1]] = int(path.parent.name)
    return found


def active_trial_count(running: dict[str, int]) -> int:
    # Live queues may have been expanded without restarting their active trials.
    # Count trials, not just Harbor processes, when deciding whether to add work.
    return sum(max(1, read_json(ROOT / "jobs" / name / "result.json").get("stats", {}).get("n_running_trials", 1))
               for name in running)


def inspect_trial(path: Path, model: str, effort: str) -> tuple[dict | None, str | None]:
    result = read_json(path / "result.json")
    if not result.get("finished_at"):
        return None, None
    if result.get("exception_info"):
        return None, result["exception_info"]["exception_type"]
    try:
        assert result["config"]["agent"]["model_name"] == f"anthropic/claude-{model}"
        assert result["agent_info"]["version"] == "2.4.6"
        trajectory = read_json(path / "agent/mini-swe-agent.trajectory.json")
        settings = trajectory["info"]["config"]["model"]["model_kwargs"]
        assert settings["output_config"]["effort"] == effort
        assert settings["thinking"] == {"type": "adaptive"}
        assert settings["max_tokens"] == 64000
        ctrf = read_json(path / "verifier/ctrf.json")["results"]
        tests = ctrf["tests"]
        assert len(tests) == 5
        assert all(test["status"] in {"passed", "failed"} for test in tests)
        passed = sum(test["status"] == "passed" for test in tests)
        reward = result["verifier_result"]["rewards"]["reward"]
        assert reward == int(passed == 5)
        # A missing/wrong workbook remains a valid model failure if all checks ran.
        agent = result["agent_result"]
        return {
            "trial": str(path.relative_to(ROOT)),
            "reward": reward,
            "tests_passed": passed,
            "input_tokens": agent.get("n_input_tokens"),
            "cache_tokens": agent.get("n_cache_tokens"),
            "output_tokens": agent.get("n_output_tokens"),
            "cost_usd": agent.get("cost_usd"),
            "task_checksum": result["task_checksum"],
        }, None
    except (AssertionError, KeyError, TypeError) as error:
        return None, f"EvidenceMismatch:{type(error).__name__}:{error}"


def inspect_setting(base: str, model: str, effort: str) -> tuple[list[dict], list[dict], list[Path]]:
    folders = sorted([ROOT / "jobs" / base, *(ROOT / "jobs").glob(f"{base}-repair-*")])
    valid, invalid = [], []
    for folder in folders:
        for result_file in sorted(folder.glob("*/result.json")):
            item, error = inspect_trial(result_file.parent, model, effort)
            if item:
                valid.append(item)
            elif error:
                invalid.append({"trial": str(result_file.parent.relative_to(ROOT)), "reason": error})
    return valid, invalid, [folder for folder in folders if folder.exists()]


def validate_configs() -> None:
    for effort in EFFORTS:
        for model in MODELS:
            config = JobConfig.model_validate(yaml.safe_load((ROOT / f"scripts/jobs/{model}-{effort}.yaml").read_text()))
            assert config.job_name == f"{model}-{effort}"
            assert config.n_attempts == TARGET and config.n_concurrent_trials == 1
            assert len(config.agents) == 1 and config.agents[0].name == "mini-swe-agent"
            assert config.agents[0].model_name == f"anthropic/claude-{model}"
            assert config.agents[0].kwargs == {"reasoning_effort": effort, "version": "2.4.6"}
            assert len(config.tasks) == 1 and config.tasks[0].path == Path("restaurant-weekly-cost-control-audit")
            assert config.timeout_multiplier == 1 and config.retry.max_retries == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--reserve-mb", type=int, default=1536)
    args = parser.parse_args()
    assert 1 <= args.workers <= 15
    os.chdir(ROOT)
    validate_configs()
    prefix = ROOT / "jobs" / f"benchmark-parallel-{args.run_id}"
    lock = prefix.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    prefix.with_suffix(".pid").write_text(f"{os.getpid()}\n")
    settings = [(model, effort, f"{model}-{effort}-{args.run_id}") for effort in EFFORTS for model in MODELS]
    processes: dict[str, subprocess.Popen] = {}
    retry_after: dict[str, float] = {}
    previous_running: dict[str, int] = {}
    previous_progress = None
    print(f"Matrix: {len(settings)} settings, {TARGET} valid trials each; up to {args.workers} concurrent jobs", flush=True)
    while True:
        for name, process in list(processes.items()):
            if process.poll() is not None:
                del processes[name]
        active = active_harbor_jobs()
        running = {name: pid for name, pid in active.items() if any(name == base or name.startswith(base + "-repair-") for _, _, base in settings)}
        rows = []
        for model, effort, base in settings:
            valid, invalid, folders = inspect_setting(base, model, effort)
            live_names = [name for name in running if name == base or name.startswith(base + "-repair-")]
            rows.append({"model": model, "effort": effort, "base": base, "valid": valid, "invalid": invalid, "running": live_names})
            if len(valid) >= TARGET or live_names:
                continue
            ended = any(name == base or name.startswith(base + "-repair-") for name in previous_running if name not in running)
            if ended:
                retry_after[base] = time.monotonic() + min(30 * len(folders), 120)
                print(f"Incomplete setting {model}/{effort}: {len(valid)}/{TARGET} valid; {len(invalid)} invalid. Scheduling replacements.", flush=True)
            if time.monotonic() < retry_after.get(base, 0):
                continue
            if active_trial_count(running) >= args.workers or available_mb() < args.reserve_mb:
                continue
            name = base if not folders else f"{base}-repair-{len(folders):02d}"
            while (ROOT / "jobs" / name).exists():
                name += "r"
            command = [sys.executable, str(ROOT / "scripts/harbor-benchmark-runner.py"),
                       "run", "-c", f"scripts/jobs/{model}-{effort}.yaml", "--job-name", name,
                       "-k", str(TARGET - len(valid)), "--artifact", "/root/audit_report.xlsx", "--quiet", "-y"]
            with (ROOT / "jobs" / f"{name}.runner.log").open("ab") as output:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            processes[name] = process
            running[name] = process.pid
            rows[-1]["running"] = [name]
            print(f"Started {name}: {TARGET - len(valid)} trials, PID {process.pid}, available memory {available_mb()} MB", flush=True)
            time.sleep(2)
        total = sum(min(len(row["valid"]), TARGET) for row in rows)
        invalid_count = sum(len(row["invalid"]) for row in rows)
        status = {"run_id": args.run_id, "updated_at": time.time(), "target": len(settings) * TARGET,
                  "valid_trials": total, "invalid_trials": invalid_count, "running_jobs": running,
                  "running_trials": active_trial_count(running),
                  "available_mb": available_mb(), "max_workers": args.workers, "settings": rows}
        temporary = prefix.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(status, indent=2) + "\n")
        temporary.replace(prefix.with_suffix(".json"))
        with prefix.with_suffix(".csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=["model", "effort", "valid_trials", "passes", "pass_rate", "invalid_trials", "cost_usd"])
            writer.writeheader()
            for row in rows:
                trials = row["valid"][:TARGET]
                passes = sum(trial["reward"] for trial in trials)
                writer.writerow({"model": row["model"], "effort": row["effort"], "valid_trials": len(trials),
                                 "passes": passes, "pass_rate": passes / len(trials) if trials else "",
                                 "invalid_trials": len(row["invalid"]), "cost_usd": sum(trial["cost_usd"] or 0 for trial in trials)})
        progress = (total, invalid_count, len(running))
        if progress != previous_progress:
            print(f"Progress: {total}/45 valid, {invalid_count} invalid, {len(running)} live jobs, memory available {available_mb()} MB", flush=True)
            previous_progress = progress
        if total == len(settings) * TARGET and not running:
            checksums = {trial["task_checksum"] for row in rows for trial in row["valid"][:TARGET]}
            assert len(checksums) == 1, f"Task changed during sweep: {checksums}"
            print("COMPLETE: all 45 verified results recorded for the same task checksum.", flush=True)
            return
        previous_running = running
        time.sleep(5)


if __name__ == "__main__":
    main()

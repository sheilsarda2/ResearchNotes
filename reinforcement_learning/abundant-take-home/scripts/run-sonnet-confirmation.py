#!/usr/bin/env python3
"""Run a fixed model effort sample in a shared Harbor queue, preserving evidence."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from dotenv import load_dotenv
from harbor.models.job.config import JobConfig
from benchmark_recovery import memory_snapshot, memory_block_reason, prepare_resume
from benchmark_evidence import attach_to_record, EvidenceError

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "restaurant-weekly-cost-control-audit"
EFFORTS = ("medium", "high", "max")
MODEL = "anthropic/claude-sonnet-5"
TASK_CHECKSUM = "6abf8f40e391cab9e8cf7a1d331d8b9ad570a85012736450f3fe81404c3f866a"
INFRA_ERRORS = {
    "EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "DockerError",
    "BadGatewayError", "RateLimitError", "APIConnectionError", "APITimeoutError",
    "InternalServerError", "ServiceUnavailableError", "VerifierTimeoutError",
    "RewardFileNotFoundError", "RewardFileEmptyError", "VerifierOutputParseError",
}


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def dump(path, data):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def task_digest():
    digest = hashlib.sha256()
    for path in sorted(TASK.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(TASK)).encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def memory_mb():
    return memory_snapshot()['available_mb']


def wilson(wins, total):
    if not total:
        return None
    z = 1.959963984540054
    p = wins / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0, center - half), min(1, center + half)]


def config_for(name, efforts, attempts, workers, model=MODEL):
    return JobConfig.model_validate({
        "job_name": name, "jobs_dir": "jobs", "n_attempts": attempts,
        "n_concurrent_trials": workers, "quiet": True,
        # Native Harbor retries delete failed trial directories. Use separate
        # repair jobs instead so every infrastructure failure remains inspectable.
        "retry": {"max_retries": 0},
        "agents": [{"name": "mini-swe-agent", "model_name": model,
                    "kwargs": {"reasoning_effort": effort, "version": "2.4.6"}}
                   for effort in efforts],
        "tasks": [{"path": str(TASK.relative_to(ROOT))}],
        "artifacts": ["/root/audit_report.xlsx"],
    }).model_dump(mode="json")


def inspect_trial(path, model=MODEL):
    path = path.resolve()
    result = read_json(path)
    if not result.get("finished_at"):
        return None
    config = result["config"]
    agent = config["agent"]
    effort = agent["kwargs"]["reasoning_effort"]
    assert effort in EFFORTS and agent["model_name"] == model
    assert agent["kwargs"]["version"] == "2.4.6"
    assert result["task_checksum"] == TASK_CHECKSUM
    assert config["timeout_multiplier"] == 1
    assert not config.get("extra_instruction_paths")
    for key in ("agent_timeout_multiplier", "verifier_timeout_multiplier",
                "agent_setup_timeout_multiplier", "environment_build_timeout_multiplier"):
        assert config.get(key) is None
    assert agent.get("override_timeout_sec") is None
    assert all(value is None for key, value in config["environment"].items()
               if key.startswith("override_"))
    exception = result.get("exception_info") or {}
    error = exception.get("exception_type")
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    record = {"trial": str(path.parent.relative_to(ROOT)), "effort": effort,
              "model": model,
              "exception": error, "raw_reward": reward, "status": "review",
              "reward": None, "tests_passed": None,
              "cost_usd": (result.get("agent_result") or {}).get("cost_usd")}
    evidence_ready = attach_to_record(record, path.parent)
    if evidence_ready is None:
        return None
    if not evidence_ready:
        return record
    if error and error != "AgentTimeoutError":
        message = exception.get("exception_message", "")
        before_agent = not (result.get("agent_execution") or {}).get("started_at")
        gateway_failure = re.search(
            r"BadGatewayError|RateLimitError|APIConnectionError|APITimeoutError|"
            r"ServiceUnavailableError|InternalServerError|502 Bad Gateway|503 Service Unavailable",
            message,
        )
        if before_agent or error in INFRA_ERRORS or gateway_failure:
            record["status"] = "infrastructure"
        return record

    trajectory = read_json(path.parent / "agent/mini-swe-agent.trajectory.json")
    settings = trajectory.get("info", {}).get("config", {}).get("model", {}).get("model_kwargs")
    if settings is not None:
        assert settings["output_config"]["effort"] == effort
        assert settings["thinking"] == {"type": "adaptive"}
        assert settings["max_tokens"] == 64000
    else:
        # A killed agent may not flush its trajectory. Keep that timeout in
        # the denominator, and record the missing request evidence explicitly.
        assert error == "AgentTimeoutError", "Missing effective model configuration"
        record["effective_config_missing_after_timeout"] = True
    ctrf = read_json(path.parent / "verifier/ctrf.json").get("results", {})
    tests = ctrf.get("tests", [])
    if tests:
        assert len(tests) == 5
        assert all(test["status"] in {"passed", "failed"} for test in tests)
        passed = sum(test["status"] == "passed" for test in tests)
        assert reward == int(passed == 5)
        record["tests_passed"] = passed
    else:
        assert error == "AgentTimeoutError", "Missing verifier evidence"
    if reward == 1:
        assert len(tests) == 5
        assert (path.parent / "artifacts/root/audit_report.xlsx").is_file()
    if error is None:
        assert result["agent_info"]["version"] == "2.4.6"
        assert reward in (0, 1)
    record.update(status="timeout" if error else "scored", reward=int(reward == 1))
    return record


def active_pid(name):
    # Inspect only enough process metadata to adopt our jobs; never log argv,
    # because unrelated Docker command lines may include credentials.
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().decode().split("\0")
        except (FileNotFoundError, ProcessLookupError, PermissionError, UnicodeDecodeError):
            continue
        if not any(arg == "harbor" or arg.endswith(("/harbor", "/harbor-resource-runner.py")) for arg in args[:3]):
            continue
        if "--job-name" in args and args[args.index("--job-name") + 1] == name:
            return int(path.parent.name)
        if "--job-path" in args and Path(args[args.index("--job-path") + 1]).name == name:
            return int(path.parent.name)
    return None


def start_job(config_path, name):
    directory = ROOT / "jobs" / name
    if (directory / "config.json").exists():
        # Override Harbor's default deletion of CancelledError results with an
        # error type that cannot match an actual result. Preserve scored history.
        command = ["jobs", "resume", "--job-path", str(directory),
                   "--filter-error-type", "__preserve_all_results__", "-y"]
    else:
        command = ["run", "-c", str(config_path), "--job-name", name, "-y"]
    command = [sys.executable, str(ROOT / "scripts/harbor-resource-runner.py"), *command]
    with (ROOT / "jobs" / f"{name}.runner.log").open("ab") as output:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"Started {name}, PID {child.pid}", flush=True)
    return child


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", choices=("sonnet-5", "fable-5-1"), default="sonnet-5")
    parser.add_argument("--attempts", type=int, default=256)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--after-summary", type=Path,
                        help="Queue this sample until the preceding harness summary is complete")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    assert re.fullmatch(r"[\w-]+", args.run_id)
    assert 1 <= args.workers <= 32 and args.attempts > 0
    os.chdir(ROOT)
    jobs_dir = ROOT / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    model = f"anthropic/claude-{args.model}"
    label = "sonnet" if args.model == "sonnet-5" else "fable-5-1"
    base = f"{label}-efforts-{args.attempts}-{args.run_id}"
    after_summary = str(args.after_summary.resolve()) if args.after_summary else None
    prefix = jobs_dir / base
    lock = prefix.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan_path = prefix.with_suffix(".plan.json")
    plan = read_json(plan_path)
    if not plan:
        initial = prefix.with_suffix(".config.json")
        dump(initial, config_for(base, EFFORTS, args.attempts, args.workers, model))
        plan = {"created_at": datetime.now(timezone.utc).isoformat(), "model": model,
                "efforts": list(EFFORTS), "attempts_per_effort": args.attempts,
                "after_summary": after_summary,
                "workers": args.workers, "task_digest": task_digest(),
                "task_checksum": TASK_CHECKSUM,
                "jobs": [{"name": base, "config": str(initial.relative_to(ROOT)),
                          "sha256": hashlib.sha256(initial.read_bytes()).hexdigest()}]}
        dump(plan_path, plan)
    assert plan["attempts_per_effort"] == args.attempts and plan["workers"] == args.workers
    assert plan["efforts"] == list(EFFORTS) and plan["model"] == model
    assert plan.get("after_summary") == after_summary
    assert task_digest() == plan["task_digest"], "Task changed since this run was planned"
    control_path = prefix.with_suffix(".control.json")
    if not control_path.exists():
        dump(control_path, {"max_active": args.workers, "reserve_mb": 1536,
                            "startup_reserve_mb": 256, "startup_window_sec": 45,
                            "start_interval_sec": 3, "max_memory_pressure_pct": 1.0,
                            "paused": False})
    if args.plan_only:
        print(f"Planned {len(EFFORTS) * args.attempts} {args.model} trials; {args.workers} shared workers: {plan_path}")
        return
    load_dotenv(ROOT / ".env")
    os.environ["ANTHROPIC_API_KEY"] = os.environ["TAKE_HOME_TOKEN"]
    os.environ["HARBOR_ADMISSION_CONTROL"] = str(control_path)
    prefix.with_suffix(".pid").write_text(str(os.getpid()) + "\n")
    cache = {}
    child = None
    previous_progress = None
    last_start = 0.0
    prepared_name = None
    while True:
        if child is not None and child.poll() is not None:
            child = None
        records = []
        for job in plan["jobs"]:
            for path in sorted((jobs_dir / job["name"]).glob("*/result.json")):
                # Verifier/trajectory evidence can arrive just after result.json.
                # Reinspect when any of those files changes, including after a
                # supervisor restart, without reparsing long trajectories on every poll.
                evidence = [path, path.parent / "verifier/ctrf.json",
                            path.parent / "agent/mini-swe-agent.trajectory.json",
                            path.parent / "agent/trajectory.json",
                            path.parent / "benchmark-runtime.json",
                            path.parent / "benchmark-evidence.json"]
                stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                              for p in evidence)
                if path not in cache or cache[path][0] != stamp or cache[path][1] is None:
                    try:
                        record = inspect_trial(path, model)
                    except (AssertionError, KeyError, TypeError, ValueError, EvidenceError, OSError) as error:
                        result = read_json(path)
                        record = {"trial": str(path.parent.relative_to(ROOT)),
                                  "effort": result.get("config", {}).get("agent", {}).get("kwargs", {}).get("reasoning_effort"),
                                  "status": "review", "exception": f"EvidenceMismatch:{type(error).__name__}",
                                  "reward": None, "cost_usd": None}
                    cache[path] = (stamp, record)
                if cache[path][1] is not None:
                    records.append(cache[path][1])
        rows = []
        for effort in EFFORTS:
            group = [record for record in records if record["effort"] == effort]
            eligible = [record for record in group if record["status"] in {"scored", "timeout"}]
            assert len(eligible) <= args.attempts, "Too many counted trials"
            wins = sum(record["reward"] for record in eligible)
            checked = [record for record in eligible if record.get("tests_passed") is not None]
            check_wins = sum(record["tests_passed"] for record in checked)
            rows.append({"model": model, "effort": effort, "target": args.attempts, "completed": len(eligible),
                         "passes": wins, "pass_rate": wins / len(eligible) if eligible else None,
                         "wilson_95pct": wilson(wins, len(eligible)),
                         "checks_passed": check_wins, "checks_total": 5 * len(checked),
                         "checks_pass_pct": 100 * check_wins / (5 * len(checked)) if checked else None,
                         "timeouts": sum(record["status"] == "timeout" for record in group),
                         "infrastructure": sum(record["status"] == "infrastructure" for record in group),
                         "review": sum(record["status"] == "review" for record in group),
                         "cost_usd": sum(record.get("cost_usd") or 0 for record in group)})
        current = plan["jobs"][-1]
        name = current["name"]
        pid = active_pid(name)
        raw_job = read_json(jobs_dir / name / "result.json")
        completed = sum(row["completed"] for row in rows)
        review = sum(record["status"] == "review" for record in records)
        done = completed == len(EFFORTS) * args.attempts and not pid
        dependency = read_json(Path(after_summary)) if after_summary else None
        dependency_ready = dependency is None or dependency.get("status") == "complete"
        control = read_json(control_path)
        snapshot = memory_snapshot()
        memory_wait = memory_block_reason(control, snapshot)
        summary = {"updated_at": datetime.now(timezone.utc).isoformat(),
                   "status": "complete" if done else "running" if dependency_ready else "queued",
                   "job": name, "model": model, "after_summary": after_summary,
                   "running_pid": pid, "running_trials": raw_job.get("stats", {}).get("n_running_trials", 0) if pid else 0,
                   "completed": completed, "target": len(EFFORTS) * args.attempts,
                   "available_mb": memory_mb(), "settings": rows, "trials": records}
        summary["resources"] = read_json(control_path.with_suffix(".resources.json"))
        if not pid:
            summary["resources"] = {**snapshot, 'active_trials': 0,
                                    'admission_wait_reason': memory_wait,
                                    'updated_at': summary['updated_at']}
        if not done and dependency_ready and not pid:
            summary['status'] = 'waiting_for_memory' if memory_wait else 'recovering'
        summary['required_memory_mb'] = control.get('min_total_mb', 0)
        summary["target_concurrency"] = args.workers
        if review:
            control = read_json(control_path)
            if not control.get("paused"):
                control["paused"] = True
                control["pause_reason"] = "Unclassified result or evidence mismatch requires review"
                dump(control_path, control)
            summary["status"] = "needs_review"
        else:
            control = read_json(control_path)
            if control.get("pause_reason") == "Unclassified result or evidence mismatch requires review":
                control["paused"] = False
                control.pop("pause_reason")
                dump(control_path, control)
        dump(prefix.with_suffix(".summary.json"), summary)
        with prefix.with_suffix(".csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        progress = (completed, review, pid, summary["running_trials"], summary["status"])
        if progress != previous_progress:
            print(f"{summary['status']}: {completed}/{summary['target']}; {summary['running_trials']} running; "
                  f"{review} need review; {memory_mb()} MB available", flush=True)
            previous_progress = progress
        if done:
            assert task_digest() == plan["task_digest"]
            print("COMPLETE: fixed sample finished, with separate effort-level estimates.", flush=True)
            return
        if dependency_ready and not pid and time.monotonic() - last_start > 60:
            directory = jobs_dir / name
            if (directory / 'config.json').exists() and prepared_name != name:
                archive = prepare_resume(directory)
                if archive:
                    print(f'Preserved interrupted work: {archive}', flush=True)
                prepared_name = name
            if memory_wait or control.get('paused'):
                time.sleep(10)
                continue
            if raw_job.get("finished_at"):
                if review:
                    summary["status"] = "needs_review"
                    dump(prefix.with_suffix(".summary.json"), summary)
                    raise RuntimeError("Unclassified results require review; all evidence retained")
                # Match the exact shortfall, interleaving efforts in a common pool.
                missing = {row["effort"]: args.attempts - row["completed"] for row in rows}
                agents = [effort for i in range(max(missing.values())) for effort in EFFORTS if i < missing[effort]]
                name = f"{base}-repair-{len(plan['jobs']):03d}"
                config_path = jobs_dir / f"{name}.config.json"
                dump(config_path, config_for(name, agents, 1, args.workers, model))
                current = {"name": name, "config": str(config_path.relative_to(ROOT)),
                           "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest()}
                plan["jobs"].append(current)
                dump(plan_path, plan)
            assert task_digest() == plan["task_digest"]
            config_path = ROOT / current["config"]
            assert hashlib.sha256(config_path.read_bytes()).hexdigest() == current["sha256"]
            if memory_block_reason(control, memory_snapshot()) is None:
                child = start_job(config_path, name)
                last_start = time.monotonic()
                prepared_name = None
        time.sleep(10)


if __name__ == "__main__":
    main()

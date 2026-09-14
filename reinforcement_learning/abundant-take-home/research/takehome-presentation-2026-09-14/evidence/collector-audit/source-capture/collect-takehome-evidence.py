#!/usr/bin/env python3
"""Read frozen presentation scope and preserve independently verifiable results.

Never writes a task, trial, campaign, scheduler, or supplied take-home file.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import re
import xml.etree.ElementTree as ET
import os

COUNTED = {"scored", "timeout", "verifier_timeout"}
ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "research/takehome-presentation-2026-09-14"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def cell_key(row):
    model = row["model"]
    if not model.startswith("anthropic/"):
        model = "anthropic/claude-" + model
    return json.dumps([row["task"], model, row["effort"]], separators=(",", ":"))


def aggregate(rows):
    steps = [r["assistant_steps"] for r in rows if r["assistant_steps"] is not None]
    success_steps = [r["assistant_steps"] for r in rows
                     if r["reward"] == 1 and r["assistant_steps"] is not None]
    return {
        "counted": len(rows), "passes": sum(r["reward"] == 1 for r in rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "steps_available": len(steps),
        "median_assistant_steps": statistics.median(steps) if steps else None,
        "over_75_steps": sum(n > 75 for n in steps),
        "successful_steps_available": len(success_steps),
        "min_successful_assistant_steps": min(success_steps) if success_steps else None,
        "median_successful_assistant_steps": statistics.median(success_steps) if success_steps else None,
        "max_successful_assistant_steps": max(success_steps) if success_steps else None,
        "successful_over_75_steps": sum(n > 75 for n in success_steps),
        "recorded_cost_usd": sum(r["cost_usd"] or 0 for r in rows),
        "cost_censored_trials": sum(r["usage_censored"] is not False for r in rows),
    }


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    return parsed.astimezone(timezone.utc)


def archive_source(path, output, content=None):
    content = path.read_bytes() if content is None else content
    checksum = hashlib.sha256(content).hexdigest()
    destination = output / "sources" / (checksum + ".json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        assert destination.read_bytes() == content
    else:
        with destination.open("xb") as stream:
            stream.write(content)
    return {"sha256": checksum, "snapshot": str(destination.relative_to(output)),
            "original": str(path.relative_to(ROOT))}


def read_checks(trial, unit):
    if unit == "verifier_groups":
        score = trial / "verifier/score.json"
        groups = json.loads(score.read_text()).get("groups", {}) if score.exists() else {}
        if not groups:
            return None, None
        def passed(group):
            for key in ("ok", "pass", "passed_group"):
                if key in group:
                    return group[key] is True
            if "status" in group:
                return group["status"] == "pass"
            return bool(group.get("total")) and group.get("passed") == group["total"]
        return sum(passed(group) for group in groups.values()), len(groups)
    xml = trial / "verifier/results.xml"
    if xml.exists():
        try:
            cases = list(ET.parse(xml).getroot().iter("testcase"))
            if cases:
                failed = sum(any(c.find(tag) is not None for tag in ("failure", "error", "skipped"))
                             for c in cases)
                return len(cases) - failed, len(cases)
        except ET.ParseError:
            pass
    cargo = trial / "verifier/cargo-tests.log"
    if cargo.exists():
        outcomes = re.findall(r"^test .+ \.\.\. (ok|FAILED|ignored)\s*$", cargo.read_text(), re.M)
        if outcomes:
            return outcomes.count("ok"), len(outcomes)
    return None, None


def read_and_hash(file):
    data = file.read_bytes()
    return json.loads(data), {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def file_metadata(file):
    return {"sha256": digest(file), "bytes": file.stat().st_size}


def _collect(output, pool):
    collector_sha = digest(Path(__file__))
    output = output.resolve()
    assert output.is_relative_to(BASE.resolve()) and output != BASE.resolve()
    scope_path = BASE / "coverage-scope.json"
    scope_bytes = scope_path.read_bytes()
    scope = json.loads(scope_bytes)
    cells = scope["cells"]
    assert len(cells) == scope["count"] == 99
    assert all(cell_key(c) == key for key, c in cells.items())
    scope_source = archive_source(scope_path, output, scope_bytes)
    rows, sources, issues = [], {}, []
    plans, adopted = {}, []
    by_cell = defaultdict(list)
    infrastructure = []
    for campaign in sorted({c["job"] for c in cells.values()}):
        path = ROOT / "jobs" / (campaign + ".summary.json")
        content = path.read_bytes()
        summary = json.loads(content)
        assert summary["job"] == campaign
        sources[str(path.relative_to(ROOT))] = archive_source(path, output, content) | {
            "updated_at": summary["updated_at"]}
        plan_path = ROOT / "jobs" / (campaign + ".plan.json")
        plan_content = plan_path.read_bytes()
        plan = json.loads(plan_content)
        plans[campaign] = archive_source(plan_path, output, plan_content)
        jobs = {job["name"]: job for job in plan["jobs"]}
        task_map = {task["id"]: task for task in plan["tasks"]}
        for job in jobs.values():
            assert digest(ROOT / job["config"]) == job["sha256"], job["name"]
        for key, definition in cells.items():
            if definition["job"] == campaign:
                assert definition["task"] in task_map
                assert definition["model"].removeprefix("anthropic/claude-") in plan["models"]
                assert definition["effort"] in plan["efforts"]
        actual = Counter(cell_key(r) for r in summary["trials"] if r["status"] in COUNTED)
        for setting in summary["settings"]:
            assert actual[cell_key(setting)] == setting["completed"], setting
        def inspect_record(record):
            key = cell_key(record)
            if key not in cells or cells[key]["job"] != campaign:
                return None
            if record["status"] not in COUNTED:
                infrastructure.append({k: record[k] for k in ("trial", "task", "model", "effort", "status")})
                return None
            trial = ROOT / record["trial"]
            assert trial.resolve().is_relative_to((ROOT / "jobs").resolve()), trial
            assert trial.parent.name in jobs, "Undeclared adopted job: " + str(trial)
            if trial.parent.name != campaign:
                declaration = jobs[trial.parent.name]
                assert declaration.get("replacement_for") and declaration.get("reason")
                adopted.append({"trial": record["trial"], "campaign": campaign,
                                "declaration": declaration})
            result_path = trial / "result.json"
            # Keep parsed JSON/hash pairs only for this trial, avoiding repeat
            # reads of large trajectories or an unbounded cross-trial cache.
            parsed, hashes = {}, {}
            json_files = [result_path, trial / "benchmark-evidence.json", trial / "agent/trajectory.json",
                          trial / "agent/mini-swe-agent.trajectory.json", trial / "benchmark-snapshot.json"]
            for file, (value, metadata) in zip(json_files, pool.map(read_and_hash, json_files)):
                parsed[file], hashes[file] = value, metadata
            def read_json(file):
                if file not in parsed:
                    parsed[file], hashes[file] = read_and_hash(file)
                return parsed[file]
            result = read_json(result_path)
            assert result.get("finished_at"), trial
            assert hashes[result_path]["sha256"] == record["result_sha256"], trial
            assert record["finished_at"] == result["finished_at"]
            assert result["task_name"] == record["task"]
            configured = result["config"]["agent"]
            assert cell_key({"task": result["task_name"], "model": configured["model_name"],
                             "effort": configured["kwargs"]["reasoning_effort"]}) == key
            expected = task_map[record["task"]]
            assert result["task_checksum"] == expected.get("harbor_task_checksum", expected.get("validated_harbor_task_checksum"))
            assert record["cost_usd"] == (result.get("agent_result") or {}).get("cost_usd")
            evidence_path = trial / "benchmark-evidence.json"
            evidence = read_json(evidence_path)
            assert evidence["identity"]["task_checksum"] == result["task_checksum"]
            assert hashes[evidence_path]["sha256"] == record["benchmark_evidence_sha256"]
            raw_reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
            assert record.get("raw_reward") == raw_reward, (trial, raw_reward)
            if record["status"] == "scored":
                assert record["reward"] == raw_reward
            elif record["status"] == "verifier_timeout":
                assert record["reward"] == 0
                assert (trial / "verifier/reward.txt").read_text().strip() == "0"
            elif record["status"] == "timeout":
                assert record["reward"] == int(raw_reward == 1)
            if raw_reward is not None:
                assert float((trial / "verifier/reward.txt").read_text()) == raw_reward
            assert read_checks(trial, record["check_unit"]) == (record["checks_passed"], record["checks_total"])
            files = [result_path, evidence_path, trial / "agent/trajectory.json",
                     trial / "agent/mini-swe-agent.trajectory.json", trial / "benchmark-snapshot.json",
                     trial / "benchmark-runtime.json", trial / "benchmark-deadline.json"]
            atif = read_json(trial / "agent/trajectory.json")
            native = read_json(trial / "agent/mini-swe-agent.trajectory.json")
            agent_steps = [step for step in atif["steps"] if step.get("source") == "agent"]
            counts = evidence["counts"]
            assert len(agent_steps) == record["assistant_steps"] == counts["atif_agent_steps"]
            assert sum(len(step.get("tool_calls") or []) for step in agent_steps) == record["tool_calls"] == counts["atif_tool_calls"]
            assert sum(message.get("role") == "assistant" for message in native["messages"]) == counts["native_assistant_turns"]
            snapshot = read_json(trial / "benchmark-snapshot.json")
            assert snapshot["sha256"] == evidence["pre_verifier_snapshot_check"]["expected_sha256"]
            assert evidence["pre_verifier_snapshot_check"]["matches"] is True
            bound = evidence["input_snapshot"]
            payload = {k: v for k, v in bound.items() if k != "sha256"}
            assert hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() == bound["sha256"]
            files += sorted(p for p in (trial / "verifier").rglob("*") if p.is_file())
            artifacts = {}
            uncached = [file for file in files if file not in hashes and file.is_file()]
            hashes.update(zip(uncached, pool.map(file_metadata, uncached)))
            for file in files:
                if file.is_file():
                    metadata = hashes[file]
                    artifacts[str(file.relative_to(ROOT))] = metadata
                    entry = bound["entries"].get(str(file.relative_to(trial)))
                    if entry and entry.get("kind") == "file":
                        assert metadata["sha256"] == entry["sha256"] and metadata["bytes"] == entry["size"], file
                elif file.name in {"result.json", "trajectory.json", "benchmark-evidence.json"}:
                    issues.append({"trial": record["trial"], "missing": str(file.relative_to(ROOT))})
            if record.get("benchmark_evidence_sha256") and evidence_path.exists():
                assert artifacts[str(evidence_path.relative_to(ROOT))]["sha256"] == record["benchmark_evidence_sha256"]
            steps = record.get("assistant_steps")
            evidence_steps = evidence.get("counts", {}).get("atif_agent_steps")
            if steps is not None and evidence_steps is not None:
                assert steps == evidence_steps, trial
            row = {k: record.get(k) for k in (
                "trial", "task", "model", "effort", "status", "finished_at", "reward", "raw_reward",
                "assistant_steps", "tool_calls", "checks_passed", "checks_total", "check_unit", "cost_usd")}
            row.update({"cell": key, "campaign": campaign, "raw_job": trial.parent.name,
                        "task_checksum": result["task_checksum"],
                        "usage_censored": evidence.get("usage", {}).get("usage_censored"),
                        "timing": evidence.get("timing", {}), "artifacts": artifacts,
                        "step_definition": evidence.get("step_definition"),
                        "agent": evidence.get("identity", {}).get("agent"),
                        "request": evidence.get("request", {}),
                        "evidence_completeness": evidence.get("completeness", {})})
            return row
        # Bound live parsed trajectories to two trials while overlapping
        # independent per-trial filesystem reads through the shared I/O pool.
        with ThreadPoolExecutor(max_workers=2) as trials_pool:
            for row in trials_pool.map(inspect_record, summary["trials"]):
                if row is not None:
                    rows.append(row)
                    by_cell[row["cell"]].append(row)
    assert len({r["trial"] for r in rows}) == len(rows)
    # A supervisor can include a specifically adopted infrastructure-repair job.
    # Preserve that provenance, but never mix different task revisions per cell.
    for key, records in by_cell.items():
        assert len({r["task_checksum"] for r in records}) == 1, key
    first = []
    for key in cells:
        if by_cell[key]:
            first.append(min(by_cell[key], key=lambda r: (stamp(r["finished_at"]), r["trial"])))
    first_names = {r["trial"] for r in first}
    for r in rows:
        r["first_counted_result"] = r["trial"] in first_names
    missing = [dict(cell=c, **definition) for c, definition in cells.items() if not by_cell[c]]
    groups = {}
    for task in sorted({c["task"] for c in cells.values()}):
        task_first = [r for r in first if r["task"] == task]
        groups[task] = {"first_results": aggregate(task_first),
                        "all_counted": aggregate([r for r in rows if r["task"] == task]),
                        "by_model": {m: aggregate([r for r in task_first if r["model"] == m])
                                     for m in sorted({r["model"] for r in rows})}}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "collector_sha256": collector_sha,
              "scope_sha256": scope_source["sha256"], "scope_source": scope_source,
              "selection": "Earliest counted finished_at (UTC), then trial path, per frozen task/model/effort cell.",
              "counted_statuses": sorted(COUNTED),
              "timeouts_are_failures": all(r["reward"] == 0 for r in rows if r["status"] in {"timeout", "verifier_timeout"}),
              "reward_policy": "Preserve authoritative campaign reward and raw verifier reward; no rescoring. Verifier timeouts count as failures.",
              "coverage": {"covered": len(first), "required": len(cells), "ready": not missing, "missing": missing},
              "first_results": aggregate(first), "all_counted": aggregate(rows), "tasks": groups,
              "excluded_outcomes": sorted(infrastructure, key=lambda r: r["trial"]), "data_quality_issues": issues,
              "sources": sources, "campaign_plans": plans, "adopted_jobs": sorted(adopted, key=lambda r: r["trial"]),
              "trials": sorted(rows, key=lambda r: (r["cell"], stamp(r["finished_at"]), r["trial"]))}
    output.mkdir(parents=True, exist_ok=True)
    assert digest(Path(__file__)) == collector_sha, "Collector source changed during collection"
    temporary = output / (".results-%s.tmp" % os.getpid())
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(output / "results.json")
    columns = ["task", "model", "effort", "first_counted_result", "status", "reward", "raw_reward", "assistant_steps", "tool_calls", "checks_passed", "checks_total", "check_unit", "cost_usd", "usage_censored", "finished_at", "task_checksum", "trial"]
    temporary = output / (".trials-%s.tmp" % os.getpid())
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["trials"])
    temporary.replace(output / "trials.csv")
    print(json.dumps({k: report[k] for k in ["generated_at", "first_results", "all_counted", "data_quality_issues"]} | {"coverage": {k: report["coverage"][k] for k in ["covered", "required", "ready"]}}))
    return report


def collect(output):
    # A small I/O pool overlaps Docker Desktop filesystem reads, without
    # retaining parsed trajectories after each trial or launching processes.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return _collect(output, pool)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BASE / "data")
    collect(parser.parse_args().output)

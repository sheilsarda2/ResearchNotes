#!/usr/bin/env python3
"""Read frozen presentation scope and preserve independently verifiable results.

Never writes a task, trial, campaign, scheduler, or supplied take-home file.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

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
    return {
        "counted": len(rows), "passes": sum(r["reward"] == 1 for r in rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "steps_available": len(steps),
        "median_assistant_steps": statistics.median(steps) if steps else None,
        "over_75_steps": sum(n > 75 for n in steps),
        "recorded_cost_usd": sum(r["cost_usd"] or 0 for r in rows),
        "cost_censored_trials": sum(r["usage_censored"] is not False for r in rows),
    }


def collect(output):
    scope_path = BASE / "coverage-scope.json"
    scope = json.loads(scope_path.read_text())
    cells = scope["cells"]
    assert len(cells) == scope["count"] == 99
    rows, sources, issues = [], {}, []
    by_cell = defaultdict(list)
    infrastructure = []
    for campaign in sorted({c["job"] for c in cells.values()}):
        path = ROOT / "jobs" / (campaign + ".summary.json")
        content = path.read_bytes()
        summary = json.loads(content)
        assert summary["job"] == campaign
        sources[str(path.relative_to(ROOT))] = {
            "sha256": hashlib.sha256(content).hexdigest(), "updated_at": summary["updated_at"]}
        actual = Counter(cell_key(r) for r in summary["trials"] if r["status"] in COUNTED)
        for setting in summary["settings"]:
            assert actual[cell_key(setting)] == setting["completed"], setting
        for record in summary["trials"]:
            key = cell_key(record)
            if key not in cells or cells[key]["job"] != campaign:
                continue
            if record["status"] not in COUNTED:
                infrastructure.append({k: record[k] for k in ("trial", "task", "model", "effort", "status")})
                continue
            trial = ROOT / record["trial"]
            assert trial.resolve().is_relative_to((ROOT / "jobs").resolve()), trial
            result_path = trial / "result.json"
            result = json.loads(result_path.read_text())
            assert result.get("finished_at"), trial
            result_hash = digest(result_path)
            if record.get("result_sha256"):
                assert result_hash == record["result_sha256"], trial
            evidence_path = trial / "benchmark-evidence.json"
            evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
            raw_reward = (result.get("verifier_result") or {}).get("rewards", {}).get("reward")
            assert record.get("raw_reward") == raw_reward, (trial, raw_reward)
            if record["status"] == "scored":
                assert record["reward"] == raw_reward
            files = [result_path, evidence_path, trial / "agent/trajectory.json",
                     trial / "agent/mini-swe-agent.trajectory.json"]
            files += sorted(p for p in (trial / "verifier").rglob("*") if p.is_file())
            artifacts = {}
            for file in files:
                if file.is_file():
                    artifacts[str(file.relative_to(ROOT))] = {"sha256": digest(file), "bytes": file.stat().st_size}
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
            rows.append(row)
            by_cell[key].append(row)
    assert len({r["trial"] for r in rows}) == len(rows)
    # A supervisor can include a specifically adopted infrastructure-repair job.
    # Preserve that provenance, but never mix different task revisions per cell.
    for key, records in by_cell.items():
        assert len({r["task_checksum"] for r in records}) == 1, key
    first = []
    for key in cells:
        if by_cell[key]:
            first.append(min(by_cell[key], key=lambda r: (r["finished_at"], r["trial"])))
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
              "scope_sha256": digest(scope_path), "selection": "Earliest counted finished_at, then trial path, per frozen task/model/effort cell.",
              "counted_statuses": sorted(COUNTED), "timeouts_are_failures": True,
              "coverage": {"covered": len(first), "required": len(cells), "ready": not missing, "missing": missing},
              "first_results": aggregate(first), "all_counted": aggregate(rows), "tasks": groups,
              "excluded_outcomes": infrastructure, "data_quality_issues": issues,
              "sources": sources, "trials": sorted(rows, key=lambda r: (r["cell"], r["finished_at"], r["trial"]))}
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    columns = ["task", "model", "effort", "first_counted_result", "status", "reward", "raw_reward", "assistant_steps", "tool_calls", "checks_passed", "checks_total", "check_unit", "cost_usd", "usage_censored", "finished_at", "task_checksum", "trial"]
    with (output / "trials.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["trials"])
    print(json.dumps({k: report[k] for k in ["generated_at", "first_results", "all_counted", "data_quality_issues"]} | {"coverage": {k: report["coverage"][k] for k in ["covered", "required", "ready"]}}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BASE / "data")
    collect(parser.parse_args().output)

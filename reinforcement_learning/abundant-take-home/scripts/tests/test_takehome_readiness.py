"""Read-only collector/observer edge cases; no workers, Docker, or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load(name, filename):
    path = Path(__file__).resolve().parents[1] / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watcher = load("takehome_watcher", "watch-takehome-readiness.py")
collector = load("takehome_collector", "collect-takehome-evidence.py")


def fixture():
    scope = {"count": 99, "cells": {}}
    summaries = {"job%s" % i: {"job": "job%s" % i, "settings": [], "trials": []} for i in range(4)}
    for task in range(11):
        for model in ["fable-5-1", "opus-5", "sonnet-5"]:
            for effort in ["medium", "high", "max"]:
                job = "job%s" % (task % 4)
                cell = {"task": "task%s" % task, "model": "anthropic/claude-" + model,
                        "effort": effort, "job": job, "target": 20}
                key = watcher.key(cell)
                scope["cells"][key] = cell
                summaries[job]["settings"].append({**cell, "completed": 1})
                summaries[job]["trials"].append({**cell, "trial": "jobs/" + job + "/" + str(len(scope["cells"])),
                                                  "status": "scored", "reward": 0})
    return scope, summaries


class CoverageTests(unittest.TestCase):
    def test_failures_and_counted_timeouts_cover_cells(self):
        scope, summaries = fixture()
        summaries["job0"]["trials"][0]["status"] = "timeout"
        summaries["job0"]["trials"][1]["status"] = "verifier_timeout"
        self.assertTrue(watcher.coverage(scope, summaries)["ready"])

    def test_infrastructure_does_not_cover_cell(self):
        scope, summaries = fixture()
        summaries["job0"]["trials"][0]["status"] = "infrastructure"
        summaries["job0"]["settings"][0]["completed"] = 0
        report = watcher.coverage(scope, summaries)
        self.assertEqual(report["covered"], 98)
        self.assertFalse(report["ready"])

    def test_count_mismatch_rejected(self):
        scope, summaries = fixture()
        summaries["job0"]["settings"][0]["completed"] = 2
        with self.assertRaises(AssertionError):
            watcher.coverage(scope, summaries)

    def test_duplicate_trial_rejected(self):
        scope, summaries = fixture()
        summaries["job0"]["trials"].append(copy.deepcopy(summaries["job0"]["trials"][0]))
        with self.assertRaises(AssertionError):
            watcher.coverage(scope, summaries)

    def test_missing_scope_setting_rejected(self):
        scope, summaries = fixture()
        summaries["job0"]["settings"].pop()
        with self.assertRaises(AssertionError):
            watcher.coverage(scope, summaries)

    def test_adopted_raw_job_still_counts(self):
        scope, summaries = fixture()
        summaries["job0"]["trials"][0]["trial"] = "jobs/job0-repair/saved-trial"
        self.assertEqual(watcher.coverage(scope, summaries)["covered"], 99)


class EvidenceAndIdentityTests(unittest.TestCase):
    def test_normal_watchdog_admission_waits_are_healthy(self):
        self.assertTrue({"starting", "running", "waiting_for_memory", "waiting_for_shared_resources"}
                        <= watcher.HEALTHY_WATCHDOG_STATES)
        self.assertNotIn("attention", watcher.HEALTHY_WATCHDOG_STATES)

    def test_success_steps_do_not_include_long_failed_trial(self):
        rows = [{"assistant_steps": n, "reward": reward, "status": "scored", "cost_usd": 0,
                 "usage_censored": False} for n, reward in [(20, 1), (90, 1), (110, 1), (1000, 0), (None, 1)]]
        result = collector.aggregate(rows)
        self.assertEqual(result["passes"], 4)
        self.assertEqual(result["successful_steps_available"], 3)
        self.assertEqual(result["median_successful_assistant_steps"], 90)
        self.assertEqual(result["successful_over_75_steps"], 2)
        self.assertEqual(result["max_successful_assistant_steps"], 110)

    def test_finished_at_is_ordered_as_aware_time(self):
        self.assertLess(collector.stamp("2026-09-14T04:00:00+01:00"),
                        collector.stamp("2026-09-14T03:30:00+00:00"))
        with self.assertRaises(AssertionError):
            collector.stamp("2026-09-14T04:00:00")

    def test_missing_group_score_keeps_unknown_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(collector.read_checks(Path(directory), "verifier_groups"), (None, None))

    def test_publisher_script_and_module_forms(self):
        for args in [["python", "scripts/benchmark_coverage_priority.py", "--watch"],
                     ["python", "-m", "benchmark_coverage_priority", "--watch"]]:
            self.assertTrue(watcher.matches_role({"args": args}, "publisher"))

    def test_wrong_runner_job_is_rejected(self):
        info = {"args": ["python", "scripts/harbor-resource-runner.py", "--job-name", "wrong"]}
        self.assertFalse(watcher.matches_role(info, "runner", "expected"))

    def test_reused_pid_identity_is_rejected(self):
        info = {"pid": 12, "identity": "new", "args": ["python", "scripts/harbor-resource-runner.py", "--job-name", "job"]}
        with patch.object(watcher, "process", return_value=info):
            self.assertFalse(watcher.live_record(12, "runner", "job", "old")["valid"])

    def test_zombie_is_not_alive(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory); target = proc / "12"; target.mkdir()
            (target / "stat").write_text("12 (name with ) parentheses) " + " ".join(["Z"] + ["0"] * 18 + ["123"]))
            (target / "cmdline").write_bytes(b"python\0")
            self.assertIsNone(watcher.process(12, proc))

    def test_staleness_uses_timestamp_age(self):
        self.assertEqual(watcher.age("2026-09-14T00:00:00+00:00", 1789344091), 91)


if __name__ == "__main__":
    unittest.main()

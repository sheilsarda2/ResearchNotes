"""Revision freezing rejects incomplete controls and mismatched diagnostics."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("freeze_zenoh_revision", Path(__file__).with_name("freeze_revision.py"))
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


class FreezeRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "research/validation"
        self.task = self.root / "research/task-revisions/synthetic-v3"
        self.output = self.root / "frozen.json"
        self.base.mkdir(parents=True)
        self.task.mkdir(parents=True)
        self.write(self.task / "instruction.md", "Synthetic fixture only.", raw=True)
        self.write(self.task / "task.toml", "version = '1.0'\n", raw=True)
        self.write(self.task / "provenance.json", {"oracle": {"groups": ["build", "hidden"]}})
        self.controls = {}
        self.results = {}
        self.sources = {}
        self.diagnostics = {}
        for agent, reward in (("oracle", 1), ("nop", 0)):
            path = self.base / "controls" / agent / "result.json"
            result = {"finished_at": "2026-09-13T23:00:00Z", "exception_info": None,
                      "task_name": self.task.name, "task_checksum": "synthetic-checksum",
                      "config": {"agent": {"name": agent}, "timeout_multiplier": 1,
                                 "environment": {}, "verifier": {}},
                      "verifier_result": {"rewards": {"reward": reward}}}
            self.results[agent] = result
            self.write(path, result)
            self.write(path.parent / "verifier/reward.txt", str(reward), raw=True)
            self.write(path.parent / "verifier/score.json", {
                "reward": reward,
                "groups": {"build": {"pass": bool(reward)},
                           "hidden": {"pass": bool(reward)}}})
            self.controls[agent] = {"result": str(path), "reward": reward,
                                    "exit_code": 0, "exception": None, "model_calls": 0}
        self.write(self.base / "harbor-validation.json", self.controls)
        diagnostic_root = self.base / "diagnostics"
        self.write(self.base / "latest-validation.json", {"output": str(diagnostic_root)})
        for case in ("nBCUPNz", "sAWGR4Y"):
            source = self.root / "jobs/historical" / ("rs-zenoh-timestamp-instrumentati__" + case)
            self.sources[case] = source
            self.write(source / "artifacts/submission/source.rs", "// " + case, raw=True)
            record = {"case": case, "kind": "focused_saved_submission_diagnostic",
                      "status": "complete", "finished_at": "2026-09-13T23:10:00Z",
                      "diagnostic_pass": True, "full_regrade_performed": False,
                      "model_calls": 0, "verifier_exit_code": 0, "reward": None,
                      "task": str(self.task), "source_trial": str(source),
                      "task_file_sha256": self.hashes(self.task),
                      "submission_file_sha256": self.hashes(source / "artifacts/submission")}
            self.diagnostics[case] = record
            self.write(diagnostic_root / case / "result.json", record)
        self.write(self.base / "origin.json", {
            "source_failure_trials": [str(source.relative_to(self.root)) for source in self.sources.values()]})
        self.patches = [patch.object(freeze, "ROOT", self.root), patch.object(freeze, "BASE", self.base),
                        patch.object(freeze, "TASK", self.task), patch.object(freeze.candidate, "ROOT", self.root),
                        patch.object(freeze, "Task", return_value=SimpleNamespace(checksum="synthetic-checksum"))]
        for active in self.patches:
            active.start()
            self.addCleanup(active.stop)

    @staticmethod
    def write(path, value, raw=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if raw else json.dumps(value))

    @staticmethod
    def hashes(path):
        return {str(item.relative_to(path)): freeze.digest(item) for item in path.rglob("*") if item.is_file()}

    def run_freeze(self):
        with patch.object(sys, "argv", ["freeze_revision.py", "--output", str(self.output)]):
            with contextlib.redirect_stdout(io.StringIO()):
                freeze.main()

    def reject(self):
        with self.assertRaises((AssertionError, ValueError, KeyError, FileNotFoundError)):
            self.run_freeze()
        self.assertFalse(self.output.exists())

    def save_diagnostic(self, case):
        self.write(self.base / "diagnostics" / case / "result.json", self.diagnostics[case])

    def test_valid_controls_and_distinct_diagnostics_freeze_once(self):
        self.run_freeze()
        saved = json.loads(self.output.read_text())
        self.assertEqual(saved["tasks"][0]["id"], self.task.name)
        self.assertEqual(set(saved["tasks"][0]["alternative_layout_diagnostics"]), {"nBCUPNz", "sAWGR4Y"})
        original = self.output.read_bytes()
        with self.assertRaises(AssertionError):
            self.run_freeze()
        self.assertEqual(self.output.read_bytes(), original)

    def test_missing_nop_control_cannot_freeze(self):
        del self.controls["nop"]
        self.write(self.base / "harbor-validation.json", self.controls)
        self.reject()

    def test_failed_oracle_control_cannot_freeze(self):
        self.results["oracle"]["verifier_result"]["rewards"]["reward"] = 0
        self.write(Path(self.controls["oracle"]["result"]), self.results["oracle"])
        self.reject()

    def test_unfinished_control_cannot_freeze(self):
        self.results["oracle"]["finished_at"] = None
        self.write(Path(self.controls["oracle"]["result"]), self.results["oracle"])
        self.reject()

    def test_different_control_task_revision_cannot_freeze(self):
        self.results["nop"]["task_checksum"] = "different-revision"
        self.write(Path(self.controls["nop"]["result"]), self.results["nop"])
        self.reject()

    def test_missing_oracle_group_cannot_freeze(self):
        self.write(Path(self.controls["oracle"]["result"]).parent / "verifier/score.json",
                   {"reward": 1, "groups": {"build": {"pass": True}}})
        self.reject()

    def test_failed_group_cannot_hide_behind_passing_reward(self):
        self.write(Path(self.controls["oracle"]["result"]).parent / "verifier/score.json",
                   {"reward": 1, "groups": {"build": {"pass": True}, "hidden": {"pass": False}}})
        self.reject()

    def test_truthy_nonboolean_group_verdict_cannot_freeze(self):
        self.write(Path(self.controls["oracle"]["result"]).parent / "verifier/score.json",
                   {"reward": 1, "groups": {"build": {"pass": True}, "hidden": {"pass": "true"}}})
        self.reject()

    def test_task_changed_after_diagnostics_cannot_freeze(self):
        self.write(self.task / "instruction.md", "Changed after validation", raw=True)
        self.reject()

    def test_saved_submission_changed_after_diagnostics_cannot_freeze(self):
        self.write(self.sources["nBCUPNz"] / "artifacts/submission/source.rs", "changed", raw=True)
        self.reject()

    def test_one_failed_diagnostic_cannot_freeze(self):
        self.diagnostics["sAWGR4Y"]["diagnostic_pass"] = False
        self.save_diagnostic("sAWGR4Y")
        self.reject()

    def test_same_submission_cannot_stand_in_for_both_layouts(self):
        self.diagnostics["sAWGR4Y"]["source_trial"] = str(self.sources["nBCUPNz"])
        self.diagnostics["sAWGR4Y"]["submission_file_sha256"] = self.hashes(
            self.sources["nBCUPNz"] / "artifacts/submission")
        self.save_diagnostic("sAWGR4Y")
        self.reject()

    def test_diagnostic_from_different_task_cannot_freeze(self):
        self.diagnostics["nBCUPNz"]["task"] = str(self.root / "different-task")
        self.save_diagnostic("nBCUPNz")
        self.reject()


if __name__ == "__main__":
    unittest.main()

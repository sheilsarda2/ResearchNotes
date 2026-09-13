"""Completed diagnostics may be reused only while their evidence stays intact."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("validation", Path(__file__).with_name("validate_revision.py"))
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class DiagnosticReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.saved = {k: getattr(validation, k) for k in ("BASE", "TASK", "REPLAYS")}
        self.addCleanup(lambda: [setattr(validation, k, v) for k, v in self.saved.items()])
        base = Path(self.temp.name)
        validation.BASE = base
        validation.TASK = base / "task"
        validation.TASK.mkdir()
        (validation.TASK / "test.rs").write_text("corrected test")
        trial = base / "trial"
        source = trial / "artifacts/submission"
        source.mkdir(parents=True)
        (source / "lib.rs").write_text("saved model source")
        validation.REPLAYS = {"saved": trial}
        self.output = base / "run"
        self.log = self.output / "saved/verifier.stdout.log"
        self.log.parent.mkdir(parents=True)
        self.log.write_text("test interception_point_try_from_valid ... ok\n")
        record = {"case": "saved", "status": "complete", "image_id": validation.IMAGE,
                  "runner_sha256": validation.hashlib.sha256(Path(validation.__file__).read_bytes()).hexdigest(),
                  "kind": "focused_saved_submission_diagnostic", "full_regrade_performed": False,
                  "task_file_sha256": validation.hashes(validation.TASK),
                  "submission_file_sha256": validation.hashes(source),
                  "diagnostic_log_sha256": validation.hashlib.sha256(self.log.read_bytes()).hexdigest()}
        (self.output / "summary.json").write_text(json.dumps([record]))
        (base / "latest-validation.json").write_text(json.dumps({"output": str(self.output)}))

    def test_complete_matching_evidence_is_reused(self):
        self.assertEqual(validation.reusable_results(["saved"]), self.output)

    def test_changed_test_requires_fresh_diagnostic(self):
        (validation.TASK / "test.rs").write_text("another revision")
        self.assertIsNone(validation.reusable_results(["saved"]))

    def test_changed_submission_requires_fresh_diagnostic(self):
        (validation.REPLAYS["saved"] / "artifacts/submission/lib.rs").write_text("different implementation")
        self.assertIsNone(validation.reusable_results(["saved"]))

    def test_altered_log_is_not_reused(self):
        self.log.write_text("changed log")
        self.assertIsNone(validation.reusable_results(["saved"]))

    def test_missing_log_is_not_reused(self):
        self.log.unlink()
        self.assertIsNone(validation.reusable_results(["saved"]))

    def test_partial_set_is_not_reused(self):
        self.assertIsNone(validation.reusable_results(["saved", "another"]))

    def test_changed_runner_requires_fresh_diagnostic(self):
        path = self.output / "summary.json"
        records = json.loads(path.read_text())
        records[0]["runner_sha256"] = "earlier diagnostic wrapper"
        path.write_text(json.dumps(records))
        self.assertIsNone(validation.reusable_results(["saved"]))


if __name__ == "__main__":
    unittest.main()

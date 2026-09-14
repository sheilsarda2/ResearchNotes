"""Deterministic evidence integrity tests; no model calls or supplied task edits."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import benchmark_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "trial"
        self.root.mkdir()
        (self.root / "agent").mkdir()
        (self.root / "artifacts").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def fixture(self):
        self.put("config.json", {"agent": {"name": "mini-swe-agent", "model_name": "anthropic/request-alias",
                                          "kwargs": {"reasoning_effort": "high"}},
                                 "environment": {"SECRET": "must-never-copy-env"}})
        self.put("result.json", {
            "task_name": "a-task", "trial_name": "a-trial", "task_checksum": "abc123",
            "exception_info": {"exception_type": "AgentTimeoutError", "exception_message": "must-never-copy-exception"},
            "agent_result": {"n_input_tokens": 30, "n_cache_tokens": 20, "n_output_tokens": 10, "cost_usd": 0.25},
            "verifier_result": {"rewards": {"reward": 1}},
            "agent_execution": {"started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:01:00Z"},
            "verifier": {"started_at": "2026-01-01T00:01:01Z", "finished_at": "2026-01-01T00:01:02Z"},
            "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:01:03Z",
        })
        self.put("agent/trajectory.json", {
            "steps": [
                {"source": "user", "message": "must-never-copy-prompt"},
                {"source": "agent", "model_name": "request-alias", "reasoning_effort": "high",
                 "timestamp": "2026-01-01T00:00:30Z", "tool_calls": [{"arguments": "must-never-copy-command"}]},
            ],
            "final_metrics": {"total_prompt_tokens": 30, "total_completion_tokens": 10,
                              "total_cached_tokens": 20, "total_cost_usd": 0.25},
        })
        self.put("agent/mini-swe-agent.trajectory.json", {
            "info": {"config": {"model": {"model_kwargs": {"output_config": {"effort": "high"},
                       "thinking": {"type": "adaptive"}, "max_tokens": 64000,
                       "api_key": "must-never-copy-key"}}},
                     "model_stats": {"instance_cost": 0.75, "api_calls": 2},
                     "submission": "must-never-copy-submission"},
            "messages": [
                {"role": "assistant", "content": "must-never-copy-response", "tool_calls": [{}, {}],
                 "extra": {"timestamp": 1767225630.0, "cost": 0.25,
                           "response": {"model": "response-alias", "usage": {"prompt_tokens": 30, "completion_tokens": 10}}}},
                {"role": "tool", "extra": {"timestamp": 1767225631.0}},
                {"role": "assistant", "tool_calls": [{}],
                 "extra": {"timestamp": 1767225665.0, "cost": 0.5,
                           "response": {"model": "response-alias", "usage": {"prompt_tokens": 40, "completion_tokens": 20}}}},
                {"role": "tool", "extra": {"timestamp": "2026-01-01T00:01:06Z"}},
            ],
        })
        (self.root / "artifacts/report.txt").write_text("answer bytes")

    def test_snapshot_binds_content_additions_and_removals(self):
        self.fixture()
        snapshot = evidence.snapshot_paths(self.root, ["artifacts", "agent", "not-yet"])
        self.assertTrue(evidence.compare_snapshot(self.root, snapshot)["matches"])
        self.assertEqual(snapshot["entries"]["not-yet"], {"kind": "missing"})
        (self.root / "artifacts/report.txt").write_text("different bytes")
        (self.root / "artifacts/new.txt").write_text("added")
        (self.root / "agent/trajectory.json").unlink()
        changed = evidence.compare_snapshot(self.root, snapshot)
        self.assertFalse(changed["matches"])
        self.assertIn("artifacts/report.txt", changed["changed_paths"])
        self.assertIn("artifacts/new.txt", changed["changed_paths"])
        self.assertIn("agent/trajectory.json", changed["changed_paths"])

    def test_missing_path_created_later_is_detected(self):
        snapshot = evidence.snapshot_paths(self.root, ["artifacts/submission"])
        (self.root / "artifacts/submission").write_text("new")
        self.assertFalse(evidence.compare_snapshot(self.root, snapshot)["matches"])

    def test_escaping_and_internal_symlinks_are_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.write_text("never read outside root")
        for target in (outside, self.root / "artifacts"):
            link = self.root / "agent/link"
            link.symlink_to(target)
            with self.assertRaises(evidence.EvidenceError):
                evidence.snapshot_paths(self.root, ["agent"])
            link.unlink()
        (self.root / "alias").symlink_to(self.root / "artifacts", target_is_directory=True)
        with self.assertRaises(evidence.EvidenceError):
            evidence.snapshot_paths(self.root, ["alias/nested"])

    def test_traversal_is_rejected(self):
        for name in ("..", "../outside", "/etc/passwd", "a/../../outside", "", "."):
            with self.subTest(name=name), self.assertRaises(evidence.EvidenceError):
                evidence.snapshot_paths(self.root, [name])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX")
    def test_fifo_is_rejected_without_reading(self):
        os.mkfifo(self.root / "agent/fifo")
        with self.assertRaises(evidence.EvidenceError):
            evidence.snapshot_paths(self.root, ["agent"])

    def test_hash_rejects_symlink(self):
        target = self.root / "artifacts/file"
        target.write_bytes(b"known content")
        self.assertEqual(evidence.hash_file(target, root=self.root), hashlib.sha256(b"known content").hexdigest())
        link = self.root / "link"
        link.symlink_to(target)
        with self.assertRaises(evidence.EvidenceError):
            evidence.hash_file(link, root=self.root)

    def test_detects_change_between_stability_reads(self):
        with patch.object(evidence, "_scan_once", side_effect=[{"a": {"kind": "missing"}}, {}]):
            with self.assertRaises(evidence.EvidenceError):
                evidence.snapshot_paths(self.root, ["a"])

    def test_tampered_expected_snapshot_is_rejected(self):
        snapshot = evidence.snapshot_paths(self.root, ["agent"])
        snapshot["entries"]["extra"] = {"kind": "missing"}
        with self.assertRaises(evidence.EvidenceError):
            evidence.compare_snapshot(self.root, snapshot)

    def test_metrics_keep_native_atif_and_budget_cost_separate(self):
        self.fixture()
        data = evidence.collect_evidence(self.root)
        self.assertEqual(data["counts"]["atif_agent_steps"], 1)
        self.assertEqual(data["counts"]["native_assistant_turns"], 2)
        self.assertFalse(data["counts"]["turn_counts_match"])
        self.assertEqual(data["counts"]["atif_tool_calls"], 1)
        self.assertEqual(data["counts"]["native_tool_calls"], 3)
        self.assertEqual(data["request"]["recorded_effort"], "high")
        self.assertTrue(data["request"]["effort_matches"])
        self.assertEqual(data["response_models"]["native"], ["response-alias"])
        self.assertEqual(data["usage"]["result"]["cost_usd"], 0.25)
        self.assertEqual(data["usage"]["native_final"]["instance_cost"], 0.75)
        self.assertTrue(data["usage"]["usage_censored"])
        self.assertTrue(data["usage"]["provider_charge_may_be_missing"])
        self.assertEqual(data["budget"]["native_after_deadline"]["assistant_turns"], 1)
        self.assertEqual(data["budget"]["native_after_deadline"]["cost_usd"], 0.5)
        self.assertEqual(data["budget"]["native_tool_returns_after_deadline"], 1)
        self.assertEqual(data["budget"]["last_assistant_seconds_after_deadline"], 5)
        self.assertEqual(data["timing"]["agent_execution"]["seconds"], 60)
        self.assertNotIn("must-never-copy", json.dumps(data))

    def test_explicit_deadline_supersedes_legacy_recorded_end(self):
        self.fixture()
        data = evidence.collect_evidence(self.root, deadline_at="2026-01-01T01:00:29+01:00")
        self.assertEqual(data["budget"]["deadline_source"], "orchestrator")
        self.assertEqual(data["budget"]["native_after_deadline"]["assistant_turns"], 2)
        with self.assertRaises(evidence.EvidenceError):
            evidence.collect_evidence(self.root, deadline_at="2026-01-01T00:00:29")

    def completed_query_fixture(self):
        self.fixture()
        result = json.loads((self.root / "result.json").read_text())
        result["exception_info"] = None
        result["agent_result"] = {"n_input_tokens": 600, "n_cache_tokens": 400,
                                  "n_output_tokens": 200, "cost_usd": 5.0}
        self.put("result.json", result)
        native = json.loads((self.root / "agent/mini-swe-agent.trajectory.json").read_text())
        response = native["messages"][0]
        response["extra"]["response"]["usage"]["cache_read_input_tokens"] = 20
        native["messages"] = [response for _ in range(20)]
        native["info"].update(exit_status="Submitted", model_stats={"instance_cost": 5.0, "api_calls": 20})
        self.put("agent/mini-swe-agent.trajectory.json", native)
        atif = json.loads((self.root / "agent/trajectory.json").read_text())
        atif["steps"] = [atif["steps"][1] for _ in range(20)]
        atif["final_metrics"] = {"total_prompt_tokens": 600, "total_cached_tokens": 400,
                                 "total_completion_tokens": 200, "total_cost_usd": 5.0}
        self.put("agent/trajectory.json", atif)
        return native, result

    def test_unanswered_native_query_censors_usage_without_losing_known_totals(self):
        native, result = self.completed_query_fixture()
        result["exception_info"] = {"exception_type": "NonZeroAgentExitCodeError"}
        self.put("result.json", result)
        native["info"]["exit_status"] = "BadGatewayError"
        self.put("agent/mini-swe-agent.trajectory.json", native)
        baseline = evidence.collect_evidence(self.root)
        # Mini's failure path increments the query count but saves no response.
        native["info"]["model_stats"]["api_calls"] = 21
        self.put("agent/mini-swe-agent.trajectory.json", native)
        before = evidence.snapshot_paths(self.root, evidence.INPUT_PATHS)
        collected = evidence.collect_evidence(self.root)
        self.assertTrue(collected["usage"]["usage_censored"])
        self.assertTrue(collected["usage"]["provider_charge_may_be_missing"])
        self.assertEqual(collected["counts"]["native_assistant_turns"], 20)
        self.assertEqual(collected["usage"]["native_final"], {"instance_cost": 5.0, "api_calls": 21})
        for key in ("result", "atif_final", "native_responses"):
            self.assertEqual(collected["usage"][key], baseline["usage"][key])
        for key in ("outcome", "counts", "budget", "completeness", "lifecycle"):
            self.assertEqual(collected[key], baseline[key])
        self.assertEqual(before, evidence.snapshot_paths(self.root, evidence.INPUT_PATHS))

    def test_completed_queries_remain_uncensored_after_recovered_retries(self):
        self.completed_query_fixture()
        (self.root / "agent/mini-swe-agent.txt").write_text("Retrying after BadGatewayError\n")
        collected = evidence.collect_evidence(self.root)
        self.assertFalse(collected["usage"]["usage_censored"])
        self.assertFalse(collected["usage"]["provider_charge_may_be_missing"])

    def test_unknown_or_invalid_query_counter_does_not_invent_an_unanswered_query(self):
        native, _ = self.completed_query_fixture()
        for value in (None, True, "21", 21.5, -1, 19):
            with self.subTest(value=value):
                native["info"]["model_stats"]["api_calls"] = value
                self.put("agent/mini-swe-agent.trajectory.json", native)
                collected = evidence.collect_evidence(self.root)
                self.assertFalse(collected["usage"]["usage_censored"])
                self.assertFalse(collected["usage"]["provider_charge_may_be_missing"])

    def test_absent_timestamps_are_unclassified_not_before_deadline(self):
        self.fixture()
        native_path = self.root / "agent/mini-swe-agent.trajectory.json"
        data = json.loads(native_path.read_text())
        del data["messages"][0]["extra"]["timestamp"]
        self.put("agent/mini-swe-agent.trajectory.json", data)
        collected = evidence.collect_evidence(self.root)
        self.assertEqual(collected["budget"]["native_unclassified"]["assistant_turns"], 1)
        self.assertEqual(collected["budget"]["native_at_or_before_deadline"]["assistant_turns"], 0)

    def test_missing_invalid_and_empty_traces_are_distinct(self):
        (self.root / "agent/trajectory.json").write_text("{broken")
        self.put("agent/mini-swe-agent.trajectory.json", {"messages": []})
        data = evidence.collect_evidence(self.root)
        self.assertEqual(data["completeness"]["atif"], "invalid_json")
        self.assertEqual(data["completeness"]["result"], "missing")
        self.assertIsNone(data["counts"]["atif_agent_steps"])
        self.assertEqual(data["counts"]["native_assistant_turns"], 0)
        self.assertIsNone(data["budget"]["post_deadline_activity"])
        self.put("agent/mini-swe-agent.trajectory.json", {"messages": "not an array"})
        self.assertEqual(evidence.collect_evidence(self.root)["completeness"]["native"], "invalid_shape")

    def test_metadata_write_is_idempotent_and_does_not_modify_raw_inputs(self):
        self.fixture()
        snapshot = evidence.snapshot_paths(self.root, ["agent", "artifacts"])
        path = evidence.write_snapshot(self.root, snapshot)
        before = evidence.snapshot_paths(self.root, evidence.INPUT_PATHS)
        collected = evidence.write_evidence(self.root, snapshot)
        evidence.write_evidence(self.root, snapshot)
        self.assertTrue(path.exists())
        self.assertTrue(collected["completeness"]["pre_verifier_inputs_unchanged"])
        self.assertEqual(before, evidence.snapshot_paths(self.root, evidence.INPUT_PATHS))
        (self.root / "artifacts/report.txt").write_text("changed after result binding")
        with self.assertRaises(evidence.EvidenceError):
            evidence.write_evidence(self.root, snapshot)

    def test_post_grading_mutation_is_recorded_as_integrity_failure(self):
        self.fixture()
        snapshot = evidence.snapshot_paths(self.root, ["agent", "artifacts"])
        (self.root / "artifacts/report.txt").write_text("late write")
        collected = evidence.write_evidence(self.root, snapshot)
        self.assertFalse(collected["completeness"]["pre_verifier_inputs_unchanged"])
        self.assertIn("artifacts/report.txt", collected["pre_verifier_snapshot_check"]["changed_paths"])

    def test_detects_evidence_change_during_collection(self):
        self.fixture()
        original = evidence.snapshot_paths
        calls = 0

        def changing(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "artifacts/report.txt").write_text("writer raced collection")
            return original(*args, **kwargs)

        with patch.object(evidence, "snapshot_paths", side_effect=changing):
            with self.assertRaises(evidence.EvidenceError):
                evidence.collect_evidence(self.root)

    def guarded_fixture(self, *, quiescent=True, deadline="2026-01-01T00:02:00Z"):
        self.fixture()
        self.put("benchmark-runtime.json", {"version": 1, "scope": "single-step-linux-docker",
                  "runtime_sha256": "a" * 64, "trial_containment_sha256": "b" * 64,
                  "process_guard_sha256": "c" * 64})
        self.put("benchmark-deadline.json", {"version": 1, "quiescent": quiescent,
                  "started_at_epoch": 1767225600.0, "cleanup_finished_at_epoch": 1767225667.0,
                  "closed_at_epoch": 1767225667.0, "executions": [{"quiescent": quiescent}]})
        snapshot = evidence.snapshot_paths(self.root, ["agent", "artifacts"])
        evidence.write_evidence(self.root, snapshot, deadline_at=deadline)

    def test_current_runtime_provenance_is_projected_without_mutating_inputs(self):
        self.fixture()
        baseline = evidence.collect_evidence(self.root)
        hashes = {"runtime_sha256": "a" * 64, "trial_containment_sha256": "b" * 64,
                  "process_guard_sha256": "c" * 64}
        self.put("benchmark-runtime.json", {"version": 1, "scope": "single-step-linux-docker", **hashes})
        before = evidence.snapshot_paths(self.root, evidence.INPUT_PATHS)
        collected = evidence.collect_evidence(self.root)
        for name, value in hashes.items():
            self.assertEqual(collected["lifecycle"][name], value)
        self.assertIsNone(collected["lifecycle"]["runtime_helper_sha256"])
        self.assertEqual(collected["lifecycle"]["runtime_state"], "present")
        for name in ("outcome", "usage", "counts", "budget", "completeness"):
            self.assertEqual(collected[name], baseline[name])
        self.assertEqual(collected["lifecycle"]["quiescent"], baseline["lifecycle"]["quiescent"])
        self.assertEqual(before, evidence.snapshot_paths(self.root, evidence.INPUT_PATHS))

    def test_legacy_runtime_helper_hash_keeps_its_own_meaning(self):
        self.fixture()
        self.put("benchmark-runtime.json", {"version": 1, "helper_sha256": "d" * 64})
        lifecycle = evidence.collect_evidence(self.root)["lifecycle"]
        self.assertEqual(lifecycle["runtime_helper_sha256"], "d" * 64)
        for name in ("runtime_sha256", "trial_containment_sha256", "process_guard_sha256"):
            self.assertIsNone(lifecycle[name])

    def test_missing_or_invalid_runtime_hashes_remain_unknown(self):
        self.fixture()
        marker = self.root / "benchmark-runtime.json"
        for value, state in ((None, "missing"), ("{broken", "invalid_json"),
                             ({"version": 1, "runtime_sha256": 123,
                               "trial_containment_sha256": {}, "process_guard_sha256": False}, "present")):
            with self.subTest(state=state):
                if value is None:
                    marker.unlink(missing_ok=True)
                elif isinstance(value, str):
                    marker.write_text(value)
                else:
                    self.put("benchmark-runtime.json", value)
                lifecycle = evidence.collect_evidence(self.root)["lifecycle"]
                self.assertEqual(lifecycle["runtime_state"], state)
                self.assertEqual(lifecycle["runtime_present"], state == "present")
                self.assertIsNone(lifecycle["quiescent"])
                for name in ("runtime_helper_sha256", "runtime_sha256",
                             "trial_containment_sha256", "process_guard_sha256"):
                    self.assertIsNone(lifecycle[name])

    def test_attach_legacy_does_not_hash_artifact_tree(self):
        self.fixture()
        record = {"status": "valid"}
        with patch.object(evidence, "snapshot_paths", side_effect=AssertionError("must stay cheap")):
            self.assertTrue(evidence.attach_to_record(record, self.root))
        self.assertFalse(record["guarded_evidence"])
        self.assertEqual(record["assistant_steps"], 1)
        self.assertEqual(record["status"], "valid")

    def test_attach_guarded_checks_binding_without_rehashing_tree(self):
        self.guarded_fixture()
        record = {"status": "valid"}
        with patch.object(evidence, "snapshot_paths", side_effect=AssertionError("must stay cheap")):
            self.assertTrue(evidence.attach_to_record(record, self.root))
        self.assertTrue(record["guarded_evidence"])
        self.assertEqual(record["status"], "valid")
        self.assertEqual(len(record["benchmark_evidence_sha256"]), 64)
        self.assertTrue(record["benchmark_evidence"]["lifecycle"]["quiescent"])

    def test_attach_waits_briefly_for_sidecar_then_requires_review(self):
        self.fixture()
        self.put("benchmark-runtime.json", {"version": 1})
        record = {"status": "valid"}
        self.assertIsNone(evidence.attach_to_record(record, self.root))
        self.assertEqual(record["evidence_issue"], "evidence_pending")
        old = time.time() - 31
        os.utime(self.root / "result.json", (old, old))
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertEqual(record["status"], "review")
        self.assertIn("evidence_missing", record["evidence_issue"])

    def test_attach_rejects_result_rewritten_after_binding(self):
        self.guarded_fixture()
        result = json.loads((self.root / "result.json").read_text())
        result["verifier_result"]["rewards"]["reward"] = 0
        self.put("result.json", result)
        record = {}
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertIn("result_hash_mismatch", record["evidence_issue"])

    def test_attach_rejects_unconfirmed_quiescence(self):
        self.guarded_fixture(quiescent=False)
        record = {}
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertIn("agent_quiescence_unconfirmed", record["evidence_issue"])

    def test_attach_rejects_post_deadline_turns(self):
        self.guarded_fixture(deadline="2026-01-01T00:01:00Z")
        record = {}
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertIn("post_deadline_activity", record["evidence_issue"])

    def test_attach_rejects_verifier_without_frozen_snapshot(self):
        self.fixture()
        self.put("benchmark-runtime.json", {"version": 1})
        self.put("benchmark-deadline.json", {"quiescent": True, "cleanup_finished_at_epoch": 1767225667.0,
                                            "executions": [{"quiescent": True}]})
        evidence.write_evidence(self.root, deadline_at="2026-01-01T00:02:00Z")
        record = {}
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertIn("verifier_inputs_unbound_or_changed", record["evidence_issue"])

    def test_attach_rejects_invalid_or_symlink_evidence(self):
        self.fixture()
        self.put("benchmark-runtime.json", {"version": 1})
        self.put(evidence.EVIDENCE_FILENAME, {"schema_version": 1})
        record = {}
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertEqual(record["evidence_issue"], "evidence_invalid")
        path = self.root / evidence.EVIDENCE_FILENAME
        path.unlink()
        path.symlink_to(self.root / "result.json")
        self.assertFalse(evidence.attach_to_record(record, self.root))
        self.assertEqual(record["evidence_issue"], "evidence_read_or_integrity_error")

    def test_completed_model_usage_is_not_flagged_censored(self):
        self.fixture()
        result = json.loads((self.root / "result.json").read_text())
        result["exception_info"] = None
        self.put("result.json", result)
        self.assertFalse(evidence.collect_evidence(self.root)["usage"]["usage_censored"])

    def test_missing_native_after_agent_started_may_omit_charge(self):
        self.fixture()
        result = json.loads((self.root / "result.json").read_text())
        result["exception_info"] = None
        self.put("result.json", result)
        (self.root / "agent/mini-swe-agent.trajectory.json").unlink()
        self.assertTrue(evidence.collect_evidence(self.root)["usage"]["provider_charge_may_be_missing"])


if __name__ == "__main__":
    unittest.main()

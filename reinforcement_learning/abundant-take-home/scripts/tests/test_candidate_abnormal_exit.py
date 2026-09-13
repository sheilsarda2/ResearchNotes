"""A stopped agent can have a valid grade; passing grades get no special retry."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from benchmark_evidence import INPUT_PATHS, snapshot_paths

spec = importlib.util.spec_from_file_location("candidate_abnormal_exit_screen", SCRIPTS / "run-candidate-screen.py")
screen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screen)


class CandidateAbnormalExitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trial = self.root / "jobs" / "synthetic-trial"
        for directory in ("agent", "verifier", "artifacts/submission"):
            (self.trial / directory).mkdir(parents=True)
        (self.trial / "artifacts/submission/source.cpp").write_text("// submitted source\n")
        self.expected = {"harbor_task_checksum": "synthetic-checksum",
                         "check_format": "verifier_groups",
                         "expected_verifier_groups": ["build", "regression"]}
        self.result = {
            "task_name": "synthetic-task", "task_checksum": "synthetic-checksum",
            "config": {
                "agent": {"name": "mini-swe-agent", "model_name": "anthropic/claude-sonnet-5",
                          "kwargs": {"version": "2.4.6", "reasoning_effort": "medium"}},
                "timeout_multiplier": 1, "environment": {}, "verifier": {},
            },
            "agent_info": {"name": "mini-swe-agent", "version": "2.4.6"},
            "agent_result": {"n_input_tokens": 100, "n_output_tokens": 20,
                             "n_cache_tokens": 0, "cost_usd": 0.01},
            "agent_execution": {"started_at": "2026-09-13T20:00:00Z",
                                "finished_at": "2026-09-13T20:01:00Z"},
            "verifier": {"started_at": "2026-09-13T20:01:02Z",
                         "finished_at": "2026-09-13T20:02:00Z"},
            "finished_at": "2026-09-13T20:02:01Z",
            "exception_info": {"exception_type": "NonZeroAgentExitCodeError",
                               "exception_message": "agent stopped with exit 124"},
            "verifier_result": {"rewards": {"reward": 1}},
        }
        self.native = {"info": {"exit_status": None,
                                "config": {"model": {"model_kwargs": {
                                    "output_config": {"effort": "medium"},
                                    "thinking": {"type": "adaptive"}, "max_tokens": 64000}}}},
                       "messages": [{"role": "assistant", "content": "saved model response"}]}
        self.evidence = {
            "counts": {"native_assistant_turns": 1, "atif_agent_steps": 1,
                       "turn_counts_match": True},
            "request": {"configured_effort": "medium", "recorded_effort": "medium",
                        "effort_matches": True, "thinking_type": "adaptive", "max_tokens": 64000},
            "usage": {"usage_censored": True, "provider_charge_may_be_missing": True},
            "timing": {
                "agent_execution": {**self.result["agent_execution"], "complete": True},
                "verifier": {**self.result["verifier"], "complete": True},
            },
            "lifecycle": {"runtime_present": True, "agent_phase_started": True,
                          "deadline_record_present": True, "execution_count": 1,
                          "quiescent": True, "all_executions_quiescent": True,
                          "cleanup_error": None,
                          "cleanup_finished_at": "2026-09-13T20:01:01Z"},
            "budget": {"post_deadline_activity": False},
            "completeness": {"native": "present", "atif": "present", "result": "present",
                             "execution_and_verifier_timing": True,
                             "pre_verifier_snapshot_present": True,
                             "pre_verifier_inputs_unchanged": True},
        }
        self.score = {"reward": 1, "groups": {"build": {"ok": True}, "regression": {"ok": True}}}
        self.marker = "1\n"
        self.guarded = True
        self.evidence_ready = True
        self.change_after_snapshot = False
        self.root_patch = patch.object(screen, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def inspect(self):
        for name, data in (("result.json", self.result),
                           ("agent/mini-swe-agent.trajectory.json", self.native),
                           ("agent/trajectory.json", {"steps": [{"source": "agent"}]}),
                           ("verifier/score.json", self.score)):
            (self.trial / name).write_text(json.dumps(data))
        (self.trial / "verifier/reward.txt").write_text(self.marker)
        saved = {"input_snapshot": snapshot_paths(self.trial, INPUT_PATHS)}
        (self.trial / "benchmark-evidence.json").write_text(json.dumps(saved))
        if self.change_after_snapshot:
            (self.trial / "artifacts/submission/source.cpp").write_text("// changed after grading\n")

        def attach(record, directory):
            self.assertEqual(directory, self.trial)
            record.update(guarded_evidence=self.guarded,
                          benchmark_evidence=copy.deepcopy(self.evidence),
                          assistant_steps=self.evidence["counts"]["atif_agent_steps"],
                          native_assistant_turns=self.evidence["counts"]["native_assistant_turns"])
            if self.evidence_ready is False:
                record["evidence_issue"] = "synthetic_integrity_failure"
            return self.evidence_ready

        with patch.object(screen, "attach_to_record", side_effect=attach):
            return screen.inspect(self.trial / "result.json", {"synthetic-task": self.expected})

    def assert_review(self):
        # inspect raises on invalid evidence; its supervisor converts that into
        # a review record. It can also return a review record directly.
        try:
            record = self.inspect()
        except (ValueError, AssertionError):
            return
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "review")
        self.assertIsNone(record["reward"])

    def test_verified_success_is_counted_with_abnormal_exit_and_censored_usage(self):
        record = self.inspect()
        self.assertEqual((record["status"], record["reward"]), ("scored", 1))
        self.assertEqual((record["checks_passed"], record["checks_total"]), (2, 2))
        self.assertEqual(record["exception"], "NonZeroAgentExitCodeError")
        self.assertTrue(record["agent_exit_abnormal"])
        self.assertEqual(record["outcome_basis"], "completed_verifier_after_agent_exit")
        self.assertTrue(record["usage_censored"])
        self.assertTrue(record["provider_charge_may_be_missing"])
        self.assertEqual(record["benchmark_evidence"], self.evidence)

    def test_verified_failure_is_counted_without_a_free_replacement(self):
        self.result["verifier_result"]["rewards"]["reward"] = 0
        self.score["reward"] = 0
        self.score["groups"]["regression"]["ok"] = False
        self.marker = "0\n"
        record = self.inspect()
        self.assertEqual((record["status"], record["reward"]), ("scored", 0))
        self.assertTrue(record["agent_exit_abnormal"])
        self.assertEqual((record["checks_passed"], record["checks_total"]), (1, 2))

    def test_legacy_unconfirmed_exit_remains_review(self):
        self.guarded = False
        self.assert_review()

    def test_failed_integrity_check_is_not_overridden_by_a_passing_grade(self):
        self.evidence_ready = False
        self.assert_review()

    def test_changed_artifact_is_detected_by_full_snapshot_recheck(self):
        self.change_after_snapshot = True
        self.assert_review()

    def test_pending_evidence_is_still_pending(self):
        self.evidence_ready = None
        self.assertIsNone(self.inspect())

    def test_unconfirmed_cleanup_and_changed_inputs_remain_review(self):
        cases = [("lifecycle", "quiescent", False),
                 ("lifecycle", "all_executions_quiescent", False),
                 ("lifecycle", "cleanup_finished_at", None),
                 ("budget", "post_deadline_activity", True),
                 ("budget", "post_deadline_activity", None),
                 ("completeness", "pre_verifier_inputs_unchanged", False)]
        original = copy.deepcopy(self.evidence)
        for section, field, value in cases:
            with self.subTest(field=field, value=value):
                self.evidence = copy.deepcopy(original)
                self.evidence[section][field] = value
                self.assert_review()

    def test_cleanup_after_grading_begins_remains_review(self):
        self.evidence["lifecycle"]["cleanup_finished_at"] = "2026-09-13T20:01:03Z"
        self.assert_review()

    def test_missing_raw_timestamp_cannot_crash_supervisor(self):
        self.result["verifier"]["started_at"] = None
        self.assert_review()

    def test_naive_raw_timestamp_remains_review(self):
        self.result["verifier"]["started_at"] = "2026-09-13T20:01:02"
        self.assert_review()

    def test_missing_or_incomplete_verifier_timing_remains_review(self):
        for key in ("started_at", "finished_at"):
            with self.subTest(key=key):
                saved = self.result["verifier"][key]
                self.result["verifier"][key] = None
                self.evidence["timing"]["verifier"][key] = None
                self.evidence["timing"]["verifier"]["complete"] = False
                self.assert_review()
                self.result["verifier"][key] = saved
                self.evidence["timing"]["verifier"][key] = saved

    def test_missing_recorded_model_work_remains_review(self):
        for field in ("native_assistant_turns", "atif_agent_steps"):
            with self.subTest(field=field):
                self.evidence["counts"][field] = 0
                self.assert_review()
                self.evidence["counts"][field] = 1

    def test_mismatched_turn_snapshots_remain_review(self):
        self.evidence["counts"].update(native_assistant_turns=2, turn_counts_match=False)
        self.assert_review()

    def test_request_mismatch_remains_review(self):
        self.native["info"]["config"]["model"]["model_kwargs"]["output_config"]["effort"] = "high"
        self.assert_review()

    def test_missing_effective_request_configuration_remains_review(self):
        del self.native["info"]["config"]
        self.assert_review()

    def test_agent_version_mismatch_remains_review(self):
        self.result["agent_info"]["version"] = "unexpected-version"
        self.assert_review()

    def test_missing_expected_group_rejected_for_success_and_failure(self):
        del self.score["groups"]["regression"]
        for reward in (0, 1):
            with self.subTest(reward=reward):
                self.result["verifier_result"]["rewards"]["reward"] = reward
                self.score["reward"] = reward
                self.marker = f"{reward}\n"
                self.assert_review()

    def test_missing_submitted_artifacts_remains_review(self):
        (self.trial / "artifacts/submission/source.cpp").unlink()
        (self.trial / "artifacts/submission").rmdir()
        (self.trial / "artifacts").rmdir()
        self.assert_review()

    def test_absent_or_nonbinary_reward_remains_review(self):
        for reward in (None, 0.5, 2, "1"):
            with self.subTest(reward=reward):
                self.result["verifier_result"]["rewards"]["reward"] = reward
                self.assert_review()

    def test_reward_disagreement_remains_review(self):
        self.marker = "0\n"
        self.assert_review()
        self.marker = "1\n"
        self.score["reward"] = 0
        self.assert_review()

    def test_gateway_failure_keeps_infrastructure_precedence(self):
        self.result["exception_info"]["exception_message"] = "APIConnectionError during model request"
        record = self.inspect()
        self.assertEqual(record["status"], "infrastructure")
        self.assertIsNone(record["reward"])

    def test_known_startup_failure_keeps_infrastructure_precedence(self):
        with patch.object(screen, "classify_startup_failure",
                          return_value={"code": "litellm_notrequired_python310"}):
            record = self.inspect()
        self.assertEqual(record["status"], "infrastructure")
        self.assertEqual(record["infrastructure_detail"], "litellm_notrequired_python310")
        self.assertIsNone(record["reward"])

    def test_other_exception_cannot_use_abnormal_exit_acceptance(self):
        self.result["exception_info"]["exception_type"] = "AgentQuiescenceError"
        self.assert_review()


if __name__ == "__main__":
    unittest.main()

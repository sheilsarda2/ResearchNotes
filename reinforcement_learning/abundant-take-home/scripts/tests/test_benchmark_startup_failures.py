"""Startup classification must never turn a task attempt into a free retry."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark_startup_failures import classify_startup_failure


# Rich's line wrapping is important: it splits installed module paths.
LOG = """This is mini-swe-agent version 2.4.6.
Building agent config from specs: ['mini']
╭──────────────── Traceback (most recent call last) ─────────────────╮
│ /root/.local/share/uv/tools/mini-swe-agent/lib/python3.10/site-packages/mini │
│ sweagent/run/mini.py:99 in main │
│ ❱ 99 │ model = get_model(config) │
│ /root/.local/share/uv/tools/mini-swe-agent/lib/python3.10/site-packages/mini │
│ sweagent/models/__init__.py:111 in get_model_class │
│ ❱ 111 │ from minisweagent.models.litellm_model import LitellmModel │
│ /root/.local/share/uv/tools/mini-swe-agent/lib/python3.10/site-packages/mini │
│ sweagent/models/litellm_model.py:9 in <module> │
│ ❱ 9 import litellm │
│ /root/.local/share/uv/tools/mini-swe-agent/lib/python3.10/site-packages/lite │
│ llm/llms/anthropic/experimental_pass_through/context_management/editors/comp │
│ act.py:17 in <module> │
│ from typing import NotRequired │
╰──────────────────────────────────────────────────────────────────╯
ImportError: cannot import name 'NotRequired' from 'typing'
(/usr/lib/python3.10/typing.py)
"""


class StartupFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "agent").mkdir()
        self.log = self.root / "agent/mini-swe-agent.txt"
        self.log.write_text(LOG)
        self.result = {
            "exception_info": {"exception_type": "NonZeroAgentExitCodeError",
                               "exception_message": "must-never-copy-prompt-or-command"},
            "agent_result": {"n_input_tokens": None, "n_cache_tokens": None,
                             "n_output_tokens": None, "cost_usd": None},
            "config": {"agent": {"name": "mini-swe-agent"}},
        }

    def tearDown(self):
        self.temp.cleanup()

    def classify(self):
        return classify_startup_failure(self.root, self.result)

    def test_confirmed_startup_import_failure_has_evidence_hash(self):
        observed = self.classify()
        self.assertEqual(observed["code"], "litellm_notrequired_python310")
        self.assertEqual(observed["evidence"], {"path": "agent/mini-swe-agent.txt",
                         "sha256": hashlib.sha256(self.log.read_bytes()).hexdigest()})
        self.assertNotIn("must-never-copy", str(observed))

    def test_ansi_styling_and_rich_wrapping_are_supported(self):
        self.log.write_text("\x1b[31m" + LOG + "\x1b[0m")
        self.assertIsNotNone(self.classify())

    def test_any_saved_native_or_atif_trace_prevents_reclassification(self):
        for name in ("mini-swe-agent.trajectory.json", "trajectory.json"):
            with self.subTest(name=name):
                path = self.root / "agent" / name
                path.write_text("")
                self.assertIsNone(self.classify())
                path.unlink()

    def test_recorded_usage_cost_or_rollout_prevents_reclassification(self):
        for name, value in (("n_input_tokens", 1), ("n_cache_tokens", 1),
                            ("n_output_tokens", 1), ("cost_usd", 0.001),
                            ("rollout_details", [{"step": 1}])):
            with self.subTest(name=name):
                self.result["agent_result"][name] = value
                self.assertIsNone(self.classify())
                self.result["agent_result"][name] = None

    def test_zero_usage_is_compatible_with_startup_failure(self):
        self.result["agent_result"] = {name: 0 for name in self.result["agent_result"]}
        self.assertIsNotNone(self.classify())

    def test_other_exception_and_other_agent_are_not_reclassified(self):
        self.result["exception_info"]["exception_type"] = "AgentTimeoutError"
        self.assertIsNone(self.classify())
        self.result["exception_info"]["exception_type"] = "NonZeroAgentExitCodeError"
        self.result["config"]["agent"]["name"] = "another-agent"
        self.assertIsNone(self.classify())

    def test_task_import_error_is_not_agent_startup_failure(self):
        self.log.write_text("Traceback (most recent call last):\n"
                            "  File '/workspace/repo/tests/test_typing.py', line 1\n"
                            "    from typing import NotRequired\n"
                            "ImportError: cannot import name 'NotRequired' from 'typing'\n"
                            "(/usr/lib/python3.10/typing.py)\n")
        self.assertIsNone(self.classify())

    def test_other_module_or_startup_stage_is_not_reclassified(self):
        for old, new in (("model = get_model(config)", "agent.run(task)"),
                         ("in get_model_class", "in run"),
                         ("context_management/editors/comp", "another_module/comp"),
                         ("python3.10", "python3.11"),
                         ("NotRequired", "AnotherSymbol")):
            with self.subTest(old=old):
                self.log.write_text(LOG.replace(old, new))
                self.assertIsNone(self.classify())

    def test_work_after_error_prevents_reclassification(self):
        self.log.write_text(LOG + "Agent: running task now\n")
        self.assertIsNone(self.classify())

    def test_missing_malformed_oversized_and_symlink_logs_fail_closed(self):
        self.log.unlink()
        self.assertIsNone(self.classify())
        self.log.write_bytes(b"\xff")
        self.assertIsNone(self.classify())
        self.log.write_text(LOG + " " * (2 * 1024 * 1024))
        self.assertIsNone(self.classify())
        self.log.unlink()
        elsewhere = self.root / "elsewhere.txt"
        elsewhere.write_text(LOG)
        self.log.symlink_to(elsewhere)
        self.assertIsNone(self.classify())

    def test_broken_trajectory_symlink_prevents_reclassification(self):
        (self.root / "agent/trajectory.json").symlink_to(self.root / "missing")
        self.assertIsNone(self.classify())

    def test_agent_directory_symlink_prevents_reclassification(self):
        (self.root / "agent").rename(self.root / "other")
        (self.root / "agent").symlink_to(self.root / "other", target_is_directory=True)
        self.assertIsNone(self.classify())


if __name__ == "__main__":
    unittest.main()

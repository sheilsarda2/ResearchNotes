"""Narrow, evidence-backed classification of known pre-model startup failures.

This only reads existing evidence. It never changes a result or its raw reward.
Unknown failures remain unclassified so the campaign can pause for review.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping


_LOG_PATH = "agent/mini-swe-agent.txt"
_TRACES = ("agent/mini-swe-agent.trajectory.json", "agent/trajectory.json")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_METRICS = ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")


def _compact_rich_traceback(text: str) -> str:
    # Rich wraps file paths and source lines inside a Unicode frame. Joining
    # whitespace alone leaves that frame in the middle of package paths.
    text = _ANSI.sub("", text)
    return "".join(char for char in text if not char.isspace() and not 0x2500 <= ord(char) <= 0x257F)


def classify_startup_failure(trial_dir: Path, result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize the specific LiteLLM / Python 3.10 NotRequired import crash.

    Absence of a trajectory alone never establishes an infrastructure failure.
    Require the installed agent's startup call chain through model construction,
    the exact failing dependency, no saved native or ATIF trace, and no recorded
    token usage or cost. Return only a stable descriptor and the raw log's hash;
    never copy prompts, commands, exception messages, or credentials.
    """
    if (result.get("exception_info") or {}).get("exception_type") != "NonZeroAgentExitCodeError":
        return None
    agent_result = result.get("agent_result") or {}
    if any(agent_result.get(key) not in (None, 0) for key in _METRICS):
        return None
    if agent_result.get("rollout_details"):
        return None
    configured_name = ((result.get("config") or {}).get("agent") or {}).get("name")
    if configured_name not in (None, "mini-swe-agent"):
        return None

    trial_dir = Path(trial_dir)
    agent_dir = trial_dir / "agent"
    log_path = trial_dir / _LOG_PATH
    try:
        # Symlinks and even empty/corrupt trajectory files are ambiguous. Keep
        # those trials in review rather than infer that no model work occurred.
        if agent_dir.is_symlink() or any((trial_dir / path).exists() or
                                         (trial_dir / path).is_symlink() for path in _TRACES):
            return None
        if log_path.is_symlink() or not log_path.is_file() or log_path.stat().st_size > 2 * 1024 * 1024:
            return None
        payload = log_path.read_bytes()
        log = payload.decode("utf-8")
    except (OSError, UnicodeError):
        return None

    compact = _compact_rich_traceback(log)
    # The first package path is allowed to wrap between "mini" and "sweagent".
    required = (
        "Thisismini-swe-agentversion",
        "Traceback(mostrecentcalllast)",
        "/tools/mini-swe-agent/lib/python3.10/site-packages/minisweagent/run/mini.py:",
        "model=get_model(",
        "inget_model_class",
        "fromminisweagent.models.litellm_modelimportLitellmModel",
        "minisweagent/models/litellm_model.py:",
        "importlitellm",
        "litellm/llms/anthropic/experimental_pass_through/context_management/editors/compact.py:",
        "ImportError:cannotimportname'NotRequired'from'typing'(/usr/lib/python3.10/typing.py)",
    )
    if not all(part in compact for part in required):
        return None
    # The import error must terminate startup, rather than appear as quoted
    # output followed by a continuing agent session.
    if not compact.endswith(required[-1]):
        return None
    return {
        "kind": "agent_dependency_import_before_model",
        "code": "litellm_notrequired_python310",
        "reason": "The installed agent failed while importing LiteLLM during initial model construction.",
        "no_model_trajectory": True,
        "evidence": {"path": _LOG_PATH, "sha256": hashlib.sha256(payload).hexdigest()},
    }

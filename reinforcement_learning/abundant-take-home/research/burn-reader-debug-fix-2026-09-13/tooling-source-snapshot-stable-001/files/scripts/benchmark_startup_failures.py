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
_TOOL_RUNTIME_PATH = "agent/benchmark-agent-tool-runtime.json"
# Reviewed process-local wrapper payloads. New versions require explicit review;
# an arbitrary sidecar flag cannot turn model work into a free infrastructure retry.
_TOOL_RUNTIME_HASHES = {
    "runtime_wrapper_sha256": "1da84e136d12fcaa9bc6ba5dca893ca98577486e4c5aa14300bb3a65152306a8",
    "bootstrap_sha256": "420412aad25eaba8ee1cab3ae6fd6d1bc33dd120333d3582d28d1561c77f439d",
    "helper_sha256": "c7d17548170e677eeeac5f3790e291f6003b1705f1c08a41cbb9aea19f3d0816",
}


def _classify_tool_preflight(trial_dir: Path, result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize only a reviewed wrapper's failure before invoking Mini's CLI."""
    from benchmark_evidence import EvidenceError, _read_json, compare_snapshot, hash_file

    exception = result.get("exception_info") or {}
    if exception.get("exception_message") != "Tool runtime preflight failed; model execution withheld":
        return None
    configured = (result.get("config") or {}).get("agent") or {}
    if (configured.get("name") != "mini-swe-agent" or
            (configured.get("kwargs") or {}).get("version") != "2.4.6"):
        return None
    agent_result = result.get("agent_result") or {}
    if (any(agent_result.get(key) not in (None, 0) for key in _METRICS) or
            agent_result.get("rollout_details")):
        return None
    trial_dir = Path(trial_dir)
    try:
        # Even an empty launch log is unexpected: preflight never invokes the
        # Mini CLI or its tee pipeline, and cannot have saved a model trajectory.
        if ((trial_dir / "agent").is_symlink() or
                any((trial_dir / path).exists() or (trial_dir / path).is_symlink()
                    for path in (*_TRACES, _LOG_PATH))):
            return None
        metadata, state = _read_json(trial_dir, _TOOL_RUNTIME_PATH)
        if (state != "present" or metadata.get("schema_version") != 1 or
                metadata.get("scope") != "agent_process_only" or
                metadata.get("phase") != "tool_runtime_preflight_before_mini_cli" or
                metadata.get("preflight_passed") is not False or
                not isinstance(metadata.get("error_type"), str) or not metadata["error_type"] or
                any(metadata.get(key) != value for key, value in _TOOL_RUNTIME_HASHES.items())):
            return None
        snapshot, snapshot_state = _read_json(trial_dir, "benchmark-snapshot.json")
        evidence, evidence_state = _read_json(trial_dir, "benchmark-evidence.json")
        saved_result, result_state = _read_json(trial_dir, "result.json")
        if (snapshot_state != "present" or evidence_state != "present" or
                result_state != "present" or saved_result != result):
            return None
        # Require the sidecar to be frozen before grading, and bind both it and
        # the result to finalization evidence. Rehash this uncommon failure path.
        entry = snapshot.get("entries", {}).get(_TOOL_RUNTIME_PATH, {})
        frozen = evidence.get("pre_verifier_snapshot_check") or {}
        final = evidence.get("input_snapshot") or {}
        if (entry.get("kind") != "file" or entry.get("sha256") != hash_file(trial_dir / _TOOL_RUNTIME_PATH, root=trial_dir) or
                final.get("entries", {}).get("result.json", {}).get("sha256") != hash_file(trial_dir / "result.json", root=trial_dir) or
                frozen.get("matches") is not True or
                frozen.get("expected_sha256") != snapshot.get("sha256") or
                frozen.get("observed_sha256") != snapshot.get("sha256") or
                not compare_snapshot(trial_dir, snapshot)["matches"] or
                not compare_snapshot(trial_dir, final)["matches"]):
            return None
    except (EvidenceError, OSError, ValueError, TypeError, AttributeError):
        return None
    return {
        "kind": "agent_runtime_preflight_before_model",
        "code": "mini_tool_runtime_preflight_failed",
        "reason": "The reviewed tool runtime failed its identity preflight before Mini or a model was launched.",
        "no_model_trajectory": True,
        "evidence": {"path": _TOOL_RUNTIME_PATH, "sha256": entry["sha256"]},
    }


def _compact_rich_traceback(text: str) -> str:
    # Rich wraps file paths and source lines inside a Unicode frame. Joining
    # whitespace alone leaves that frame in the middle of package paths.
    text = _ANSI.sub("", text)
    return "".join(char for char in text if not char.isspace() and not 0x2500 <= ord(char) <= 0x257F)


def classify_startup_failure(trial_dir: Path, result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize reviewed pre-model runtime failures and the LiteLLM import crash.

    Absence of a trajectory alone never establishes an infrastructure failure.
    Require the installed agent's startup call chain through model construction,
    the exact failing dependency, no saved native or ATIF trace, and no recorded
    token usage or cost. Return only a stable descriptor and the raw log's hash;
    never copy prompts, commands, exception messages, or credentials.
    """
    exception_type = (result.get("exception_info") or {}).get("exception_type")
    if exception_type == "BenchmarkAgentPreflightError":
        return _classify_tool_preflight(trial_dir, result)
    if exception_type != "NonZeroAgentExitCodeError":
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

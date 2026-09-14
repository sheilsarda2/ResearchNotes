"""Metadata-only, immutable evidence for benchmark trials (stdlib only).

Call ``snapshot_paths`` after stopping the agent and before grading; keep that
snapshot outside the directories it binds. After grading, ``write_evidence``
binds the result and checks that the earlier inputs stayed identical. Hashes
detect changes; they do not themselves stop processes or make files read-only.
No prompt, command, environment, response prose, or exception message is copied.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Any, Iterable


SCHEMA_VERSION = 1
EVIDENCE_FILENAME = "benchmark-evidence.json"
SNAPSHOT_FILENAME = "benchmark-snapshot.json"
INPUT_PATHS = (
    "config.json", "result.json", "agent/trajectory.json",
    "agent/mini-swe-agent.trajectory.json", "artifacts", "verifier",
    "benchmark-runtime.json", "benchmark-deadline.json",
)


class EvidenceError(RuntimeError):
    """Evidence is unsafe, inconsistent, or already bound to different bytes."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _relative(value: str | Path) -> str:
    text = str(value)
    path = PurePosixPath(text)
    if (not text or path.is_absolute() or text == "." or
            ".." in path.parts or "\\" in text or "\x00" in text):
        raise EvidenceError("Snapshot paths must be nonempty relative paths without traversal")
    return path.as_posix()


def _signature(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _open_root(root: Path) -> int:
    # The caller explicitly selects the trust root. Descendant symlinks are
    # forbidden even if they happen to point back inside that root.
    root = root.resolve(strict=True)
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _open_parent(root_fd: int, relative: str) -> tuple[int, str]:
    parts = PurePosixPath(relative).parts
    parent = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
            os.close(parent)
            parent = child
        return parent, parts[-1]
    except BaseException:
        os.close(parent)
        raise


def _read_file_at(parent_fd: int, name: str, *, content: bool = False) -> tuple[dict, bytes | None]:
    # O_NONBLOCK prevents a substituted FIFO from blocking before fstat rejects it.
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    chunks = [] if content else None
    digest = hashlib.sha256()
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise EvidenceError("Only regular files can be evidence inputs")
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(fd)
        visible = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if _signature(before) != _signature(after) or _signature(after) != _signature(visible):
            raise EvidenceError("Evidence file changed while being read")
        entry = {"kind": "file", "size": after.st_size, "sha256": digest.hexdigest()}
        return entry, b"".join(chunks) if chunks is not None else None
    finally:
        os.close(fd)


def hash_file(path: Path, *, root: Path | None = None) -> str:
    """Hash one stable regular file, rejecting symlinks beneath ``root``."""
    path = Path(path)
    root = Path(root) if root is not None else path.parent
    relative = _relative(path.absolute().relative_to(root.absolute()))
    root_fd = _open_root(root)
    parent = None
    try:
        parent, name = _open_parent(root_fd, relative)
        return _read_file_at(parent, name)[0]["sha256"]
    except OSError as error:
        raise EvidenceError("Cannot safely hash evidence file") from error
    finally:
        if parent is not None:
            os.close(parent)
        os.close(root_fd)


def _scan_once(root: Path, paths: list[str]) -> dict:
    entries: dict[str, dict] = {}
    root_fd = _open_root(root)

    def scan(parent: int, name: str, relative: str) -> None:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISREG(before.st_mode):
            entries[relative] = _read_file_at(parent, name)[0]
            return
        if not stat.S_ISDIR(before.st_mode):
            raise EvidenceError("Symlinks and special files cannot enter an evidence snapshot")
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            if _signature(before) != _signature(os.fstat(directory)):
                raise EvidenceError("Evidence directory changed while opening")
            children = sorted(os.listdir(directory))
            entries[relative] = {"kind": "directory"}
            for child in children:
                scan(directory, child, relative + "/" + child)
            if (children != sorted(os.listdir(directory)) or
                    _signature(before) != _signature(os.fstat(directory)) or
                    _signature(before) != _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))):
                raise EvidenceError("Evidence directory changed while being read")
        finally:
            os.close(directory)

    try:
        for relative in paths:
            parent = None
            try:
                try:
                    parent, name = _open_parent(root_fd, relative)
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    # Missing requested inputs are explicitly bound. A file that
                    # disappears during recursion instead fails the whole scan.
                    entries[relative] = {"kind": "missing"}
                else:
                    scan(parent, name, relative)
            finally:
                if parent is not None:
                    os.close(parent)
    except OSError as error:
        raise EvidenceError("Cannot safely enumerate evidence inputs") from error
    finally:
        os.close(root_fd)
    return entries


def snapshot_paths(trial_dir: Path, relative_paths: Iterable[str | Path]) -> dict:
    """Create a deterministic snapshot of selected paths with two stability reads.

    Missing paths are recorded, so creating them after this snapshot is detected.
    Callers must enforce process quiescence; no finite read can prevent a future
    write. Directory descriptors and O_NOFOLLOW prevent traversal via symlinks.
    """
    paths = sorted({_relative(p) for p in relative_paths})
    first = _scan_once(Path(trial_dir), paths)
    second = _scan_once(Path(trial_dir), paths)
    if first != second:
        raise EvidenceError("Evidence inputs changed between stability reads")
    payload = {"schema_version": SCHEMA_VERSION, "hash_algorithm": "sha256",
               "paths": paths, "entries": first}
    return {**payload, "sha256": hashlib.sha256(_canonical(payload)).hexdigest()}


def compare_snapshot(trial_dir: Path, expected_snapshot: dict) -> dict:
    """Compare pre-grader inputs with current bytes, preserving additions/removals."""
    required = {"schema_version", "hash_algorithm", "paths", "entries", "sha256"}
    if not isinstance(expected_snapshot, dict) or set(expected_snapshot) != required:
        raise EvidenceError("Invalid expected snapshot schema")
    payload = {key: expected_snapshot[key] for key in required if key != "sha256"}
    if (payload["schema_version"] != SCHEMA_VERSION or payload["hash_algorithm"] != "sha256" or
            hashlib.sha256(_canonical(payload)).hexdigest() != expected_snapshot["sha256"]):
        raise EvidenceError("Expected snapshot digest is invalid")
    observed = snapshot_paths(trial_dir, expected_snapshot["paths"])
    expected_entries = expected_snapshot["entries"]
    current_entries = observed["entries"]
    changed = sorted(key for key in set(expected_entries) | set(current_entries)
                     if expected_entries.get(key) != current_entries.get(key))
    return {"matches": not changed, "expected_sha256": expected_snapshot["sha256"],
            "observed_sha256": observed["sha256"], "changed_paths": changed}


def _write_once(trial_dir: Path, name: str, value: dict) -> Path:
    name = _relative(name)
    if len(PurePosixPath(name).parts) != 1:
        raise EvidenceError("Evidence metadata must be a file directly under the trial directory")
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    root_fd = _open_root(Path(trial_dir))
    try:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o444, dir_fd=root_fd)
        except FileExistsError:
            _, current = _read_file_at(root_fd, name, content=True)
            if current != encoded:
                raise EvidenceError("Refusing to overwrite existing evidence metadata")
        else:
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(encoded)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                os.unlink(name, dir_fd=root_fd)
                raise
    finally:
        os.close(root_fd)
    return Path(trial_dir) / name


def write_snapshot(trial_dir: Path, snapshot: dict, name: str = SNAPSHOT_FILENAME) -> Path:
    """Persist a snapshot without overwriting existing metadata or source files."""
    if not compare_snapshot(trial_dir, snapshot)["matches"]:
        raise EvidenceError("Inputs changed before their snapshot could be persisted")
    return _write_once(Path(trial_dir), name, snapshot)


def _read_json(root: Path, relative: str) -> tuple[dict, str]:
    root_fd = _open_root(root)
    parent = None
    try:
        parent, name = _open_parent(root_fd, relative)
        _, content = _read_file_at(parent, name, content=True)
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError):
            return {}, "invalid_json"
        return (value, "present") if isinstance(value, dict) else ({}, "invalid_shape")
    except FileNotFoundError:
        return {}, "missing"
    except OSError as error:
        raise EvidenceError("Cannot safely read evidence JSON") from error
    finally:
        if parent is not None:
            os.close(parent)
        os.close(root_fd)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _label(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/+@-]{1,256}", value) else None


def _time(value: Any) -> datetime | None:
    try:
        if _number(value) is not None:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            # Harbor sometimes emits naive ISO times. Do not invent a timezone.
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _timing(value: Any) -> dict:
    source = _dict(value)
    start, end = _time(source.get("started_at")), _time(source.get("finished_at"))
    elapsed = (end - start).total_seconds() if start and end else None
    return {"started_at": _iso(start), "finished_at": _iso(end),
            "seconds": elapsed if elapsed is not None and elapsed >= 0 else None,
            "complete": elapsed is not None and elapsed >= 0}


def _numeric_fields(value: Any, fields: Iterable[str]) -> dict:
    data = _dict(value)
    return {key: _number(data.get(key)) for key in fields}


def _native_usage(messages: list[dict]) -> dict:
    fields = ("prompt_tokens", "completion_tokens", "total_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")
    values = {field: [] for field in fields}
    costs = []
    for message in messages:
        extra = _dict(message.get("extra"))
        usage = _dict(_dict(extra.get("response")).get("usage"))
        for field in fields:
            number = _number(usage.get(field))
            if number is not None:
                values[field].append(number)
        cost = _number(extra.get("cost"))
        if cost is not None:
            costs.append(cost)
    return {"assistant_turns": len(messages),
            "usage_totals": {field: sum(items) if items else None for field, items in values.items()},
            "turns_with_usage": {field: len(items) for field, items in values.items()},
            "cost_usd": sum(costs) if costs else None, "turns_with_cost": len(costs)}


def collect_evidence(trial_dir: Path, expected_snapshot: dict | None = None,
                     deadline_at: str | float | None = None, *, hash_inputs: bool = True) -> dict:
    """Collect allowlisted metadata without modifying a trial or its raw outputs.

    ``deadline_at`` is the orchestrator's actual wall-clock cutoff when available.
    A legacy AgentTimeoutError uses recorded agent_execution.finished_at as an
    explicitly labelled approximation. Untimestamped turns remain unclassified.
    The native snapshot can contain more turns/cost than Harbor's earlier ATIF
    and result; all three are retained separately, never silently reconciled.
    """
    trial_dir = Path(trial_dir)
    initial = snapshot_paths(trial_dir, INPUT_PATHS) if hash_inputs else None
    result, result_state = _read_json(trial_dir, "result.json")
    config, config_state = _read_json(trial_dir, "config.json")
    atif, atif_state = _read_json(trial_dir, "agent/trajectory.json")
    native, native_state = _read_json(trial_dir, "agent/mini-swe-agent.trajectory.json")
    runtime, runtime_state = _read_json(trial_dir, "benchmark-runtime.json")
    lifecycle, lifecycle_state = _read_json(trial_dir, "benchmark-deadline.json")
    if atif_state == "present" and not isinstance(atif.get("steps"), list):
        atif_state = "invalid_shape"
    if native_state == "present" and not isinstance(native.get("messages"), list):
        native_state = "invalid_shape"
    agent_config = _dict(_dict(result.get("config") or config).get("agent"))
    agent_kwargs = _dict(agent_config.get("kwargs"))
    native_info = _dict(native.get("info"))
    request = _dict(_dict(_dict(native_info.get("config")).get("model")).get("model_kwargs"))
    configured_effort = _label(agent_kwargs.get("reasoning_effort"))
    request_effort = _label(_dict(request.get("output_config")).get("effort") or request.get("reasoning_effort"))
    atif_steps = [row for row in _rows(atif.get("steps")) if row.get("source") == "agent"]
    native_messages = _rows(native.get("messages"))
    assistant = [row for row in native_messages if row.get("role") == "assistant"]
    tools = [row for row in native_messages if row.get("role") == "tool"]
    exception = _label(_dict(result.get("exception_info")).get("exception_type"))
    deadline = _time(deadline_at)
    if deadline_at is not None and deadline is None:
        raise EvidenceError("deadline_at must be an aware ISO timestamp or finite epoch seconds")
    deadline_source = "orchestrator" if deadline else None
    if deadline is None and exception == "AgentTimeoutError":
        deadline = _time(_dict(result.get("agent_execution")).get("finished_at"))
        deadline_source = "recorded_agent_execution_end_on_timeout" if deadline else None
    before, after, unknown = [], [], []
    for row in assistant:
        stamp = _time(_dict(row.get("extra")).get("timestamp"))
        (unknown if not deadline or not stamp else after if stamp > deadline else before).append(row)
    timed_tools = [_time(_dict(row.get("extra")).get("timestamp")) for row in tools]
    valid_tools = [stamp for stamp in timed_tools if stamp is not None]
    after_times = [_time(_dict(row.get("extra")).get("timestamp")) for row in after]
    phases = {key: _timing(result.get(key)) for key in
              ("environment_setup", "agent_setup", "agent_execution", "verifier")}
    phases["trial"] = _timing(result)
    usage = _numeric_fields(result.get("agent_result"),
                            ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd"))
    atif_totals = _numeric_fields(atif.get("final_metrics"),
                                  ("total_prompt_tokens", "total_completion_tokens", "total_cached_tokens", "total_cost_usd"))
    native_totals = _numeric_fields(native_info.get("model_stats"), ("instance_cost", "api_calls"))
    native_turns = len(assistant) if native_state == "present" else None
    atif_turns = len(atif_steps) if atif_state == "present" else None
    frozen = compare_snapshot(trial_dir, expected_snapshot) if expected_snapshot is not None else None
    final = snapshot_paths(trial_dir, INPUT_PATHS) if hash_inputs else None
    if initial != final:
        raise EvidenceError("Trial evidence changed during collection")
    return {
        "schema_version": SCHEMA_VERSION,
        "step_definition": "ATIF source=agent steps and native role=assistant turns are counted separately; tool calls are a separate count.",
        "identity": {"trial_name": _label(result.get("trial_name") or trial_dir.name),
                     "task_name": _label(result.get("task_name")),
                     "task_checksum": _label(result.get("task_checksum")),
                     "agent": _label(agent_config.get("name")),
                     "agent_version": _label(_dict(result.get("agent_info")).get("version"))},
        "outcome": {"reward": _number(_dict(_dict(result.get("verifier_result")).get("rewards")).get("reward")),
                    "exception_type": exception, "finished_at": _iso(_time(result.get("finished_at")))},
        "request": {"configured_model": _label(agent_config.get("model_name")),
                    "configured_effort": configured_effort, "recorded_effort": request_effort,
                    "effort_matches": configured_effort == request_effort if configured_effort and request_effort else None,
                    "thinking_type": _label(_dict(request.get("thinking")).get("type")),
                    "max_tokens": _number(request.get("max_tokens"))},
        "response_models": {
            "atif": sorted({label for row in atif_steps if (label := _label(row.get("model_name")))}),
            "native": sorted({label for row in assistant if (label := _label(_dict(_dict(row.get("extra")).get("response")).get("model")))}),
            "atif_reasoning_efforts": sorted({label for row in atif_steps if (label := _label(row.get("reasoning_effort")))}),
        },
        "counts": {"atif_agent_steps": atif_turns, "native_assistant_turns": native_turns,
                   "atif_tool_calls": sum(len(_rows(row.get("tool_calls"))) for row in atif_steps) if atif_turns is not None else None,
                   "native_tool_calls": sum(len(_rows(row.get("tool_calls"))) for row in assistant) if native_turns is not None else None,
                   "native_tool_returns": len(tools) if native_turns is not None else None,
                   "turn_counts_match": atif_turns == native_turns if atif_turns is not None and native_turns is not None else None,
                   "atif_steps_with_timestamp": sum(_time(row.get("timestamp")) is not None for row in atif_steps),
                   "native_turns_with_timestamp": sum(_time(_dict(row.get("extra")).get("timestamp")) is not None for row in assistant)},
        "usage": {"result": usage, "atif_final": atif_totals, "native_final": native_totals,
                  "native_responses": _native_usage(assistant)},
        "budget": {"deadline_at": _iso(deadline), "deadline_source": deadline_source,
                   "native_at_or_before_deadline": _native_usage(before) if deadline else None,
                   "native_after_deadline": _native_usage(after) if deadline else None,
                   "native_unclassified": _native_usage(unknown),
                   "native_tool_returns_after_deadline": sum(stamp > deadline for stamp in valid_tools) if deadline else None,
                   "native_tool_returns_without_timestamp": len(timed_tools) - len(valid_tools),
                   "last_assistant_seconds_after_deadline": (max(after_times) - deadline).total_seconds() if after_times else None,
                   "post_deadline_activity": bool(after or any(stamp > deadline for stamp in valid_tools)) if deadline else None},
        "timing": phases,
        "lifecycle": {"runtime_present": runtime_state == "present", "runtime_state": runtime_state,
                      "runtime_version": _number(runtime.get("version")),
                      "runtime_helper_sha256": _label(runtime.get("helper_sha256")),
                      "deadline_record_present": lifecycle_state == "present", "deadline_record_state": lifecycle_state,
                      "agent_phase_started": phases["agent_execution"]["started_at"] is not None,
                      "agent_run_started_at": _iso(_time(lifecycle.get("started_at_epoch"))),
                      "quiescent": lifecycle.get("quiescent") if isinstance(lifecycle.get("quiescent"), bool) else None,
                      "closed_at": _iso(_time(lifecycle.get("closed_at_epoch"))),
                      "cleanup_finished_at": _iso(_time(lifecycle.get("cleanup_finished_at_epoch"))),
                      "cleanup_error": _label(lifecycle.get("cleanup_error")),
                      "execution_count": len(_rows(lifecycle.get("executions"))) if isinstance(lifecycle.get("executions"), list) else None,
                      "all_executions_quiescent": all(row.get("quiescent") is True for row in _rows(lifecycle.get("executions"))) if isinstance(lifecycle.get("executions"), list) else None},
        "completeness": {"config": config_state, "result": result_state, "atif": atif_state, "native": native_state,
                         "result_usage": all(usage[key] is not None for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens")),
                         "result_cost": usage["cost_usd"] is not None,
                         "execution_and_verifier_timing": phases["agent_execution"]["complete"] and phases["verifier"]["complete"],
                         "pre_verifier_snapshot_present": expected_snapshot is not None,
                         "pre_verifier_inputs_unchanged": frozen["matches"] if frozen else None},
        "pre_verifier_snapshot_check": frozen,
        "input_snapshot": final,
    }


def write_evidence(trial_dir: Path, expected_snapshot: dict | None = None,
                   deadline_at: str | float | None = None,
                   name: str = EVIDENCE_FILENAME) -> dict:
    """Write a metadata sidecar once, returning its contents. Never edits a result.

    A changed pre-grader snapshot is recorded as a failed integrity check; the
    orchestrator decides how to classify/abort that trial. Repeated calls are
    idempotent only when every byte of the evidence is unchanged.
    """
    evidence = collect_evidence(trial_dir, expected_snapshot, deadline_at)
    _write_once(Path(trial_dir), name, evidence)
    return evidence


def _attach_to_record(record: dict, trial_dir: Path) -> bool | None:
    """Attach cheap summary metrics and enforce evidence for guarded runs.

    True means evidence passed (or this is explicitly legacy/unguarded); False
    requires review; None means wait briefly for finalization's sidecar. This is
    a polling helper: it never recursively hashes artifacts or changes raw jobs.
    A legacy record remains historical evidence, not retroactive deadline proof.
    """
    trial_dir = Path(trial_dir)
    runtime, runtime_state = _read_json(trial_dir, "benchmark-runtime.json")
    guarded = runtime_state != "missing"
    record["guarded_evidence"] = guarded
    issues = []
    evidence_hash = None
    if guarded:
        data, state = _read_json(trial_dir, EVIDENCE_FILENAME)
        if state == "missing":
            try:
                age = time.time() - (trial_dir / "result.json").stat().st_mtime
            except FileNotFoundError:
                age = 0
            if age < 30:
                record["evidence_issue"] = "evidence_pending"
                return None
            issues.append("evidence_missing")
        elif (state != "present" or data.get("schema_version") != SCHEMA_VERSION or
              any(not isinstance(data.get(key), dict) for key in
                  ("counts", "request", "response_models", "usage", "timing", "budget", "completeness", "lifecycle", "input_snapshot"))):
            issues.append("evidence_invalid")
        if runtime_state != "present" or not runtime:
            issues.append("runtime_marker_invalid")
        if not issues:
            expected = _dict(_dict(data.get("input_snapshot")).get("entries")).get("result.json")
            expected_hash = _dict(expected).get("sha256")
            actual_hash = hash_file(trial_dir / "result.json", root=trial_dir)
            if not expected_hash or expected_hash != actual_hash:
                issues.append("result_hash_mismatch")
            lifecycle = _dict(data.get("lifecycle"))
            if lifecycle.get("runtime_present") is not True:
                issues.append("runtime_not_bound")
            if lifecycle.get("agent_phase_started") is True:
                if lifecycle.get("quiescent") is not True or not lifecycle.get("cleanup_finished_at"):
                    issues.append("agent_quiescence_unconfirmed")
                if lifecycle.get("all_executions_quiescent") is not True:
                    issues.append("execution_quiescence_unconfirmed")
            verifier_ran = bool(_dict(_dict(data.get("timing")).get("verifier")).get("started_at"))
            if verifier_ran and _dict(data.get("completeness")).get("pre_verifier_inputs_unchanged") is not True:
                issues.append("verifier_inputs_unbound_or_changed")
            if _dict(data.get("budget")).get("post_deadline_activity") is True:
                issues.append("post_deadline_activity")
            evidence_hash = hash_file(trial_dir / EVIDENCE_FILENAME, root=trial_dir)
    else:
        data = collect_evidence(trial_dir, hash_inputs=False)
    if issues:
        record.update(status="review", evidence_issue=",".join(issues))
    else:
        record.pop("evidence_issue", None)
    if data:
        counts = _dict(data.get("counts"))
        response = _dict(data.get("response_models"))
        record["benchmark_evidence"] = {key: data.get(key) for key in
                                         ("counts", "request", "response_models", "usage", "timing", "budget", "completeness", "lifecycle")}
        record.update(assistant_steps=counts.get("atif_agent_steps"),
                      tool_calls=counts.get("atif_tool_calls"),
                      native_assistant_turns=counts.get("native_assistant_turns"),
                      native_tool_calls=counts.get("native_tool_calls"),
                      observed_model_names=response.get("atif", []),
                      response_model_aliases=response.get("native", []),
                      recorded_effort=_dict(data.get("request")).get("recorded_effort"),
                      benchmark_evidence_sha256=evidence_hash)
        snapshot = _dict(_dict(data.get("input_snapshot")).get("entries"))
        record["result_sha256"] = _dict(snapshot.get("result.json")).get("sha256")
    return not issues


def attach_to_record(record: dict, trial_dir: Path) -> bool | None:
    """Summary integration: True accepted, False review, None awaiting sidecar."""
    try:
        return _attach_to_record(record, Path(trial_dir))
    except (EvidenceError, OSError, ValueError, TypeError, KeyError):
        record.update(status="review", evidence_issue="evidence_read_or_integrity_error")
        return False

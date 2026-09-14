"""Explicit durable state transitions for tasks with dynamic requirements.

Only committed state is replay protected. An application effect performed
before a transition commit can occur again after process interruption.
"""

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import math
import os
import stat
import tempfile
import uuid

from luigi.task import DynamicRequirements, Task, flatten
from luigi.task_register import load_task


class CheckpointValidationError(ValueError):
    """An application returned unsupported state or requirements."""


class CheckpointCorruptionError(ValueError):
    """The persisted journal is malformed or has an invalid digest."""


class CheckpointVersionError(ValueError):
    """The task checkpoint version differs from persisted state."""


class CheckpointIdentityError(ValueError):
    """A journal belongs to another task identity."""


class CheckpointBusyError(RuntimeError):
    """Another process currently owns advancement or reset."""


class CheckpointConflictError(RuntimeError):
    """An obsolete continuation or reset attempted to change newer state."""


class CheckpointOutputError(RuntimeError):
    """A finish has missing outputs, or finished outputs were removed."""


@dataclasses.dataclass(frozen=True)
class Transition:
    state: object
    requires: object = None


@dataclasses.dataclass(frozen=True)
class Finish:
    state: object


def _json_value(value, active=None):
    if active is None:
        active = set()
    if value is None or type(value) in (bool, str, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) not in (list, dict):
        raise CheckpointValidationError("State must contain only finite JSON values")
    if id(value) in active:
        raise CheckpointValidationError("Cyclic JSON state")
    active.add(id(value))
    try:
        if type(value) is list:
            return [_json_value(item, active) for item in value]
        if any(type(key) is not str for key in value):
            raise CheckpointValidationError("JSON object keys must be strings")
        return {key: _json_value(item, active) for key, item in value.items()}
    finally:
        active.remove(id(value))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _parameters(task, significant=False):
    return {name: parameter.serialize(getattr(task, name)) for name, parameter in task.get_params()
            if not significant or parameter.significant}


def _identity(task):
    return {"module": task.__class__.__module__, "family": task.task_family,
            "parameters": _parameters(task, significant=True)}


def _encode(value, parent, active=None):
    if active is None:
        active = set()
    if value is None:
        return {"kind": "none"}
    if isinstance(value, Task):
        if _identity(value) == _identity(parent):
            raise CheckpointValidationError("A task cannot depend on itself")
        module = value.__class__.__module__
        if module == "__main__" or "<locals>" in value.__class__.__qualname__:
            raise CheckpointValidationError("Dependency task classes must be importable")
        params = _parameters(value)
        if any(type(key) is not str or type(item) is not str for key, item in params.items()):
            raise CheckpointValidationError("Dependency parameters must serialize to strings")
        descriptor = {"kind": "task", "module": module, "family": value.task_family,
                      "parameters": params, "task_id": value.task_id}
        # Check round-trip now, before publishing a descriptor we cannot replay.
        try:
            restored = load_task(module, value.task_family, params)
        except Exception as error:
            raise CheckpointValidationError("Cannot reconstruct dependency: " + value.task_id) from error
        if type(restored) is not type(value) or _parameters(restored) != params:
            raise CheckpointValidationError("Dependency parameters do not round-trip")
        return descriptor
    if type(value) not in (list, tuple, dict):
        raise CheckpointValidationError("Requirements must contain Tasks, None, lists, tuples or string-keyed dicts")
    if id(value) in active:
        raise CheckpointValidationError("Cyclic requirements")
    active.add(id(value))
    try:
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise CheckpointValidationError("Requirement dict keys must be strings")
            return {"kind": "dict", "items": [[key, _encode(item, parent, active)] for key, item in value.items()]}
        return {"kind": "list" if type(value) is list else "tuple",
                "items": [_encode(item, parent, active) for item in value]}
    finally:
        active.remove(id(value))


def _descriptor(value, restore=False):
    """Validate persisted descriptors without importing tasks for status reads."""
    if type(value) is not dict:
        raise CheckpointCorruptionError("Invalid dependency descriptor")
    kind = value.get("kind")
    if kind == "none" and set(value) == {"kind"}:
        return None
    if kind == "task":
        if set(value) != {"kind", "module", "family", "parameters", "task_id"}:
            raise CheckpointCorruptionError("Invalid dependency task fields")
        if any(type(value[key]) is not str or not value[key] for key in ("module", "family", "task_id")):
            raise CheckpointCorruptionError("Invalid dependency identity")
        if value["module"] == "__main__":
            raise CheckpointCorruptionError("Dependency module is not importable")
        if type(value["parameters"]) is not dict or any(type(k) is not str or type(v) is not str for k, v in value["parameters"].items()):
            raise CheckpointCorruptionError("Invalid dependency parameters")
        if not restore:
            return value["task_id"]
        try:
            result = load_task(value["module"], value["family"], value["parameters"])
        except Exception as error:
            raise CheckpointCorruptionError("Cannot restore dependency: " + value["task_id"]) from error
        if result.task_id != value["task_id"] or _parameters(result) != value["parameters"]:
            raise CheckpointCorruptionError("Dependency identity changed: " + value["task_id"])
        return result
    if kind not in ("dict", "list", "tuple") or set(value) != {"kind", "items"} or type(value["items"]) is not list:
        raise CheckpointCorruptionError("Invalid requirement container")
    if kind == "dict":
        result = {}
        for pair in value["items"]:
            if type(pair) is not list or len(pair) != 2 or type(pair[0]) is not str or pair[0] in result:
                raise CheckpointCorruptionError("Invalid requirement dict entry")
            result[pair[0]] = _descriptor(pair[1], restore)
        return result
    values = [_descriptor(item, restore) for item in value["items"]]
    return tuple(values) if kind == "tuple" else values


def _outputs(value):
    if value is None:
        return None
    if isinstance(value, Task):
        return value.output()
    if isinstance(value, dict):
        return {key: _outputs(item) for key, item in value.items()}
    return type(value)(_outputs(item) for item in value)


class CheckpointRequirements(DynamicRequirements):
    """Dynamic requirements which always recheck child completion."""
    def complete(self, complete_fn=None):
        return all(task.complete() for task in self.flat_requirements)

    @property
    def paths(self):
        return _outputs(self.requirements)

    def task_specs(self):
        return [(task.__class__.__module__, task.task_family, _parameters(task)) for task in self.flat_requirements]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointCorruptionError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CheckpointTask(Task):
    """A task advanced through explicit JSON state and durable dependencies.

    Override checkpoint_path, initial_state, advance(state, inputs) and output.
    Keep the checkpoint_version string stable until changing state semantics.
    """
    checkpoint_version = "1"

    def checkpoint_path(self):
        raise NotImplementedError

    def initial_state(self):
        raise NotImplementedError

    def advance(self, state, inputs):
        raise NotImplementedError

    def _path(self):
        return os.path.abspath(os.fspath(self.checkpoint_path()))

    def _version(self):
        value = self.checkpoint_version
        if type(value) is not str or not value:
            raise CheckpointValidationError("checkpoint_version must be a nonempty string")
        return value

    @contextlib.contextmanager
    def _exclusive(self):
        path = self._path() + ".lock"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path) and not stat.S_ISREG(os.lstat(path).st_mode):
            raise CheckpointCorruptionError("Invalid checkpoint lock")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise CheckpointBusyError("Checkpoint advancement is busy") from error
            yield

    def _read(self, check_version=True):
        path = self._path()
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(mode):
            raise CheckpointCorruptionError("Checkpoint is not a regular file")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                envelope = json.load(stream, object_pairs_hook=_unique_object)
            if type(envelope) is not dict or set(envelope) != {"schema_version", "data", "sha256"}:
                raise CheckpointCorruptionError("Invalid checkpoint envelope")
            if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
                raise CheckpointCorruptionError("Unsupported checkpoint schema")
            data = _json_value(envelope["data"])
            if hashlib.sha256(_canonical(data)).hexdigest() != envelope["sha256"]:
                raise CheckpointCorruptionError("Checkpoint digest mismatch")
            keys = {"identity", "checkpoint_version", "epoch", "sequence", "phase", "state", "requires"}
            if type(data) is not dict or set(data) != keys:
                raise CheckpointCorruptionError("Invalid checkpoint fields")
            identity = data["identity"]
            if (type(identity) is not dict or set(identity) != {"module", "family", "parameters"} or
                    any(type(identity.get(key)) is not str or not identity[key] for key in ("module", "family")) or
                    type(identity.get("parameters")) is not dict or
                    any(type(key) is not str or type(value) is not str for key, value in identity["parameters"].items())):
                raise CheckpointCorruptionError("Invalid checkpoint identity")
            if type(data["sequence"]) is not int or data["sequence"] < 0:
                raise CheckpointCorruptionError("Invalid checkpoint sequence")
            if type(data["epoch"]) is not str or not data["epoch"]:
                raise CheckpointCorruptionError("Invalid checkpoint epoch")
            if type(data["checkpoint_version"]) is not str or not data["checkpoint_version"]:
                raise CheckpointCorruptionError("Invalid checkpoint version")
            if data["phase"] not in ("ready", "waiting", "finished"):
                raise CheckpointCorruptionError("Invalid checkpoint phase")
            if data["phase"] == "ready" and (data["sequence"] != 0 or data["requires"] is not None):
                raise CheckpointCorruptionError("Invalid initial checkpoint")
            if data["phase"] != "ready" and data["sequence"] == 0:
                raise CheckpointCorruptionError("Missing transition sequence")
            if data["phase"] == "waiting":
                _descriptor(data["requires"])
            elif data["requires"] is not None:
                raise CheckpointCorruptionError("Unexpected pending requirements")
        except (UnicodeError, json.JSONDecodeError, CheckpointValidationError, RecursionError, OverflowError) as error:
            raise CheckpointCorruptionError("Invalid checkpoint JSON") from error
        if data["identity"] != _identity(self):
            raise CheckpointIdentityError("Checkpoint belongs to a different task")
        if check_version and data["checkpoint_version"] != self._version():
            raise CheckpointVersionError("Checkpoint version differs; explicitly reset before rerunning")
        return data

    def _write(self, data):
        path = self._path()
        directory = os.path.dirname(path)
        value = {"schema_version": 1, "data": data, "sha256": hashlib.sha256(_canonical(data)).hexdigest()}
        descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_canonical(value))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            _fsync_directory(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def checkpoint_status(self):
        data = self._read()
        if data is None:
            return {"phase": "absent", "sequence": 0, "state": None,
                    "checkpoint_version": self._version(), "identity": _identity(self), "pending_task_ids": []}
        pending = _descriptor(data["requires"]) if data["phase"] == "waiting" else None
        return {"phase": data["phase"], "sequence": data["sequence"], "state": data["state"],
                "checkpoint_version": data["checkpoint_version"], "identity": data["identity"],
                "pending_task_ids": list(dict.fromkeys(flatten(pending)))}

    def _outputs_complete(self):
        outputs = flatten(self.output())
        return bool(outputs) and all(output.exists() for output in outputs)

    def complete(self):
        data = self._read()
        return data is not None and data["phase"] == "finished" and self._outputs_complete()

    def reset_checkpoint(self, expected_sequence):
        if type(expected_sequence) is not int or expected_sequence < 0:
            raise CheckpointValidationError("expected_sequence must be a nonnegative integer")
        with self._exclusive():
            data = self._read(check_version=False)
            sequence = 0 if data is None else data["sequence"]
            if expected_sequence != sequence:
                raise CheckpointConflictError("Checkpoint sequence changed")
            if data is not None:
                os.unlink(self._path())
                _fsync_directory(os.path.dirname(self._path()))
                return True
            return False

    def run(self):
        receipt = None
        while True:
            requirements = None
            should_yield = False
            with self._exclusive():
                data = self._read()
                if receipt is not None and (data is None or receipt != (data["epoch"], data["sequence"])):
                    raise CheckpointConflictError("Obsolete checkpoint continuation")
                if data is None:
                    state = _json_value(self.initial_state())
                    data = {"identity": _identity(self), "checkpoint_version": self._version(),
                            "epoch": uuid.uuid4().hex, "sequence": 0, "phase": "ready",
                            "state": state, "requires": None}
                    self._write(data)
                if data["phase"] == "finished":
                    if not self._outputs_complete():
                        raise CheckpointOutputError("Finished checkpoint has missing final outputs; reset explicitly")
                    return
                if data["phase"] == "waiting":
                    requirements = _descriptor(data["requires"], restore=True)
                    if any(not task.complete() for task in flatten(requirements)):
                        should_yield = True
                        receipt = (data["epoch"], data["sequence"])
                if not should_yield:
                    inputs = _outputs(requirements) if data["phase"] == "waiting" else None
                    result = self.advance(_json_value(data["state"]), inputs)
                    if not isinstance(result, (Transition, Finish)):
                        raise CheckpointValidationError("advance must return Transition or Finish")
                    next_state = _json_value(result.state)
                    encoded = _encode(result.requires, self) if isinstance(result, Transition) else None
                    if isinstance(result, Finish) and not self._outputs_complete():
                        raise CheckpointOutputError("Finish requires nonempty, complete task outputs")
                    updated = dict(data, sequence=data["sequence"] + 1, state=next_state,
                                   phase="waiting" if isinstance(result, Transition) else "finished", requires=encoded)
                    self._write(updated)
                    receipt = None
                    if isinstance(result, Finish):
                        return
            if should_yield:
                yield CheckpointRequirements(requirements)

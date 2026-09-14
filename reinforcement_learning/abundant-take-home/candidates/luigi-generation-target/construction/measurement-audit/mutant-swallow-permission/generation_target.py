"""POSIX local targets publishing immutable, verified file collections.

The root publication lock serializes pointer changes and pin acquisition.
Generation pins and writer leases are OS locks, so process death releases them.
Only CURRENT makes a generation committed; abandoned staging/final directories
are garbage, never candidates for completing a Luigi task.
"""

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import uuid

from luigi.target import Target


class GenerationIntegrityError(ValueError):
    """A committed collection, manifest, or managed path is invalid."""


class GenerationConflictError(RuntimeError):
    """Another writer published after this writer captured its base."""


_CAPTURE = object()
_ID = re.compile(r"[a-f0-9]{32}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _directory(path, create=False):
    if create:
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise GenerationIntegrityError("Not a regular directory: " + path)


def _regular(path):
    if not stat.S_ISREG(os.lstat(path).st_mode):
        raise GenerationIntegrityError("Not a regular file: " + path)


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_file(path):
    if os.path.lexists(path):
        _regular(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise GenerationIntegrityError("Invalid lock: " + path)
    return os.fdopen(fd, "r+b")


@contextlib.contextmanager
def _locked(path):
    with _lock_file(path) as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GenerationIntegrityError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _read_json(path):
    _regular(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise GenerationIntegrityError("Invalid JSON: " + path) from error


def _write_json(path, value):
    with open(path, "x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


def _member_path(base, relative, create=False):
    _directory(base)
    pieces = relative.split("/")
    parent = base
    for piece in pieces[:-1]:
        parent = os.path.join(parent, piece)
        _directory(parent, create)
    return os.path.join(parent, pieces[-1])


def _digest(path, sync=False):
    _regular(path)
    result = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            result.update(chunk)
        if sync:
            os.fsync(stream.fileno())
    return size, result.hexdigest()


def _members(mapping):
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("members must be a nonempty dict")
    result = {}
    paths = set()
    for name, relative in mapping.items():
        if not isinstance(name, str) or not name or "\x00" in name:
            raise ValueError("Member names must be nonempty strings")
        relative = os.fspath(relative)
        if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
            raise ValueError("Member paths must be portable relative strings")
        if any(piece in ("", ".", "..") for piece in relative.split("/")):
            raise ValueError("Invalid relative member path: " + relative)
        if relative in paths:
            raise ValueError("Duplicate member path: " + relative)
        paths.add(relative)
        result[name] = relative
    for path in paths:
        pieces = path.split("/")
        if any("/".join(pieces[:i]) in paths for i in range(1, len(pieces))):
            raise ValueError("A member cannot also be a directory")
    return result


class LocalGenerationTarget(Target):
    """A named file set committed by replacing the root's CURRENT pointer.

    ``members`` maps logical names to unique, canonical relative paths.
    ``write`` and ``snapshot`` return context managers; snapshots may also be
    closed explicitly. Text streams use UTF-8; binary modes preserve bytes.
    Passing a Luigi ``format`` to open delegates stream semantics to it.
    """

    def __init__(self, root, members):
        self.path = os.path.abspath(os.fspath(root))
        self._members = _members(members)

    @property
    def members(self):
        return self._members.copy()

    def _layout(self, create=False):
        if create:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        _directory(self.path, create)
        for name in ("generations", "staging"):
            _directory(os.path.join(self.path, name), create)

    def _pointer(self):
        try:
            value = _read_json(os.path.join(self.path, "CURRENT"))
        except FileNotFoundError:
            return {"version": 1, "generation": None, "history": []}
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
            raise GenerationIntegrityError("Invalid CURRENT version")
        history = value.get("history")
        if (not isinstance(history, list) or not history or
                any(not isinstance(item, str) or not _ID.fullmatch(item) for item in history) or
                len(set(history)) != len(history) or value.get("generation") != history[0]):
            raise GenerationIntegrityError("Invalid CURRENT generation/history")
        return value

    def _replace_pointer(self, value):
        temporary = os.path.join(self.path, ".CURRENT-" + uuid.uuid4().hex)
        try:
            _write_json(temporary, value)
            os.replace(temporary, os.path.join(self.path, "CURRENT"))
            _sync_dir(self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @property
    def current_generation(self):
        try:
            self._layout()
        except FileNotFoundError:
            return None
        return self._pointer()["generation"]

    def write(self, expected_generation=_CAPTURE):
        """Capture the current generation on entry unless an expected ID is supplied.

        Explicit None means the store must have no committed generation.
        """
        if expected_generation is not _CAPTURE and expected_generation is not None:
            if not isinstance(expected_generation, str) or not _ID.fullmatch(expected_generation):
                raise ValueError("expected_generation must be a generation ID or None")
        return _GenerationWriter(self, expected_generation)

    def snapshot(self):
        self._layout()
        with _locked(os.path.join(self.path, ".publish.lock")):
            generation = self._pointer()["generation"]
            if generation is None:
                raise FileNotFoundError("No committed generation")
            directory = os.path.join(self.path, "generations", generation)
            try:
                _directory(directory)
                pin = _lock_file(os.path.join(directory, ".pin"))
                fcntl.flock(pin, fcntl.LOCK_SH)
            except FileNotFoundError as error:
                raise GenerationIntegrityError("Missing generation: " + generation) from error
        result = _GenerationSnapshot(self, directory, generation, pin)
        try:
            result._validate()
        except BaseException:
            result.close()
            raise
        return result

    def exists(self):
        try:
            with self.snapshot():
                return True
        except (FileNotFoundError, GenerationIntegrityError, PermissionError):
            return False

    def prune(self, keep=1):
        """Retain newest keep generations, the current one, and active pins.

        Also reclaim abandoned staging directories. Return removed generation
        IDs in sorted order; staging cleanup is not included in that list.
        """
        if type(keep) is not int or keep < 0:
            raise ValueError("keep must be a nonnegative integer")
        try:
            self._layout()
        except FileNotFoundError:
            return []
        removed = []
        with _locked(os.path.join(self.path, ".publish.lock")):
            pointer = self._pointer()
            retained = set(pointer["history"][:max(1, keep)])
            handles = []
            deletions = []
            try:
                for collection, lockname in (("generations", ".pin"), ("staging", ".lease")):
                    base = os.path.join(self.path, collection)
                    for name in sorted(os.listdir(base)):
                        if not _ID.fullmatch(name):
                            raise GenerationIntegrityError("Unexpected managed directory: " + name)
                        directory = os.path.join(base, name)
                        _directory(directory)
                        if collection == "generations" and name in retained:
                            continue
                        handle = _lock_file(os.path.join(directory, lockname))
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            handle.close()
                            continue
                        handles.append(handle)
                        deletions.append(directory)
                        if collection == "generations":
                            removed.append(name)
                # Remove retired IDs from committed history before unlinking them.
                # A crash afterward leaves safe unreferenced garbage for next prune.
                if any(name in pointer["history"] for name in removed):
                    pointer["history"] = [name for name in pointer["history"] if name not in removed]
                    self._replace_pointer(pointer)
                for directory in deletions:
                    shutil.rmtree(directory)
                for name in ("generations", "staging"):
                    _sync_dir(os.path.join(self.path, name))
            finally:
                for handle in handles:
                    handle.close()
        return sorted(removed)


class _GenerationWriter:
    def __init__(self, target, expected):
        self.target = target
        self.expected = expected
        self.generation = None
        self._active = False
        self._entered = False
        self._streams = []
        self._opened = set()
        self._lease = None

    def __enter__(self):
        if self._entered:
            raise ValueError("A writer can only be entered once")
        self._entered = True
        self.target._layout(create=True)
        with _locked(os.path.join(self.target.path, ".publish.lock")):
            pointer = self.target._pointer()
            if self.expected is _CAPTURE:
                self.expected = pointer["generation"]
            self._id = uuid.uuid4().hex
            self._stage = os.path.join(self.target.path, "staging", self._id)
            os.mkdir(self._stage)
            self._lease = _lock_file(os.path.join(self._stage, ".lease"))
            fcntl.flock(self._lease, fcntl.LOCK_EX)
            os.mkdir(os.path.join(self._stage, "data"))
            with _lock_file(os.path.join(self._stage, ".pin")):
                pass
            self._active = True
        return self

    def path(self, name):
        if not self._active:
            raise ValueError("Writer is not active")
        relative = self.target._members[name]
        return _member_path(os.path.join(self._stage, "data"), relative, create=True)

    def open(self, name, mode="w", format=None):
        if mode not in ("w", "wb"):
            raise ValueError("Writer mode must be w or wb")
        if name in self._opened:
            raise ValueError("Member already opened: " + name)
        path = self.path(name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        raw = os.fdopen(fd, "wb")
        try:
            if format is not None:
                stream = format.pipe_writer(raw)
            elif mode == "w":
                import io
                stream = io.TextIOWrapper(raw, encoding="utf-8")
            else:
                stream = raw
        except BaseException:
            raw.close()
            raise
        self._streams.append((stream, raw))
        self._opened.add(name)
        return stream

    def _commit(self):
        if any(not raw.closed for _, raw in self._streams):
            raise GenerationIntegrityError("Writer still has an open member handle")
        data = os.path.join(self._stage, "data")
        manifest = {"version": 1, "generation": self._id, "members": {}}
        for name, relative in self.target._members.items():
            try:
                path = _member_path(data, relative)
                size, digest = _digest(path, sync=True)
            except FileNotFoundError as error:
                raise GenerationIntegrityError("Missing member: " + name) from error
            manifest["members"][name] = {"path": relative, "size": size, "sha256": digest}
        wanted = set(self.target._members.values())
        for root, directories, files in os.walk(data, followlinks=False):
            for name in directories:
                _directory(os.path.join(root, name))
            for name in files:
                relative = os.path.relpath(os.path.join(root, name), data)
                if relative not in wanted:
                    raise GenerationIntegrityError("Undeclared member: " + relative)
            _sync_dir(root)
        _write_json(os.path.join(self._stage, "manifest.json"), manifest)
        _sync_dir(self._stage)
        with _locked(os.path.join(self.target.path, ".publish.lock")):
            pointer = self.target._pointer()
            if pointer["generation"] != self.expected:
                raise GenerationConflictError("Current generation changed")
            destination = os.path.join(self.target.path, "generations", self._id)
            os.rename(self._stage, destination)
            _sync_dir(os.path.dirname(destination))
            _sync_dir(os.path.dirname(self._stage))
            # A failed pointer replacement leaves an unreferenced directory;
            # never remove it here: failure may occur just after replacement.
            self.target._replace_pointer({"version": 1, "generation": self._id,
                                          "history": [self._id] + pointer["history"]})
            self.generation = self._id

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self._commit()
        finally:
            self._active = False
            try:
                for stream, raw in self._streams:
                    try:
                        stream.close()
                    finally:
                        raw.close()
            finally:
                with _locked(os.path.join(self.target.path, ".publish.lock")):
                    try:
                        if os.path.exists(self._stage):
                            shutil.rmtree(self._stage)
                    finally:
                        if self._lease is not None:
                            self._lease.close()


class _GenerationSnapshot:
    def __init__(self, target, directory, generation, pin):
        self.target = target
        self.directory = directory
        self.generation = generation
        self._pin = pin
        self._manifest = None

    def _validate(self):
        try:
            value = _read_json(os.path.join(self.directory, "manifest.json"))
            if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1 or
                    value.get("generation") != self.generation or not isinstance(value.get("members"), dict) or
                    set(value["members"]) != set(self.target._members)):
                raise GenerationIntegrityError("Invalid generation manifest")
            for name, relative in self.target._members.items():
                entry = value["members"][name]
                if (not isinstance(entry, dict) or entry.get("path") != relative or
                        type(entry.get("size")) is not int or entry["size"] < 0 or
                        not isinstance(entry.get("sha256"), str) or not _DIGEST.fullmatch(entry["sha256"])):
                    raise GenerationIntegrityError("Invalid manifest member: " + name)
                path = _member_path(os.path.join(self.directory, "data"), relative)
                if _digest(path) != (entry["size"], entry["sha256"]):
                    raise GenerationIntegrityError("Member checksum mismatch: " + name)
        except FileNotFoundError as error:
            raise GenerationIntegrityError("Missing committed content: " + str(error)) from error
        self._manifest = value

    @property
    def manifest(self):
        return copy.deepcopy(self._manifest)

    @property
    def manifest_path(self):
        return os.path.join(self.directory, "manifest.json")

    def path(self, name):
        if self._pin is None:
            raise ValueError("Snapshot is closed")
        return _member_path(os.path.join(self.directory, "data"), self.target._members[name])

    def open(self, name, mode="r", format=None):
        if mode not in ("r", "rb"):
            raise ValueError("Snapshot mode must be r or rb")
        path = self.path(name)
        entry = self._manifest["members"][name]
        try:
            if _digest(path) != (entry["size"], entry["sha256"]):
                raise GenerationIntegrityError("Member checksum mismatch: " + name)
        except FileNotFoundError as error:
            raise GenerationIntegrityError("Missing member: " + name) from error
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        raw = os.fdopen(fd, "rb")
        try:
            if format is not None:
                return format.pipe_reader(raw)
            if mode == "r":
                import io
                return io.TextIOWrapper(raw, encoding="utf-8")
            return raw
        except BaseException:
            raw.close()
            raise

    def close(self):
        if self._pin is not None:
            self._pin.close()
            self._pin = None

    def __enter__(self):
        if self._pin is None:
            raise ValueError("Snapshot is closed")
        return self

    def __exit__(self, *args):
        self.close()

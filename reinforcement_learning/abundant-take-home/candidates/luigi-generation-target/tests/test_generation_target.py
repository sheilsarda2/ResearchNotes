"""Behavioral contract checks, deliberately independent of the storage layout."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import selectors
import subprocess
import sys
import tempfile

import luigi
import pytest


MEMBERS = {"rows": "nested/rows.bin", "metadata": "metadata.json"}


@pytest.fixture
def target(tmp_path):
    assert hasattr(luigi, "LocalGenerationTarget"), "Implement LocalGenerationTarget"
    return luigi.LocalGenerationTarget(tmp_path / "store", MEMBERS)


def publish(target, payload=b"alpha", expected="capture"):
    manager = target.write() if expected == "capture" else target.write(expected_generation=expected)
    with manager as writer:
        with writer.open("rows", "wb") as stream:
            stream.write(payload)
        with writer.open("metadata") as stream:
            stream.write(json.dumps({"sha256": hashlib.sha256(payload).hexdigest()}))
    assert writer.generation == target.current_generation
    return writer.generation


def read(target):
    with target.snapshot() as snapshot:
        with snapshot.open("rows", "rb") as stream:
            payload = stream.read()
        with snapshot.open("metadata") as stream:
            metadata = json.load(stream)
        assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()
        return snapshot.generation, payload


def child(code, root, *args):
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(root), *map(str, args)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=os.environ.copy(),
    )


def line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=15), "Child failed to reach synchronization point"
    value = process.stdout.readline().strip()
    assert value, "Child ended before synchronization: " + process.stderr.read()
    return value


def finish(process, expected=0):
    try:
        out, err = process.communicate(timeout=15)
        assert process.returncode == expected, (out, err, process.returncode)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


PRELUDE = '''
import hashlib, json, os, sys
import luigi
t = luigi.LocalGenerationTarget(sys.argv[1], {"rows": "nested/rows.bin", "metadata": "metadata.json"})
def fill(w, payload):
    with w.open("rows", "wb") as f: f.write(payload)
    with w.open("metadata") as f: json.dump({"sha256": hashlib.sha256(payload).hexdigest()}, f)
'''


def test_exports_and_absent_reads(target):
    import luigi.generation_target as module
    assert module.LocalGenerationTarget is luigi.LocalGenerationTarget
    assert issubclass(luigi.GenerationIntegrityError, ValueError)
    assert issubclass(luigi.GenerationConflictError, RuntimeError)
    assert isinstance(target, luigi.Target)
    assert os.path.isabs(target.path)
    assert target.current_generation is None
    assert not target.exists()
    assert target.prune() == []
    with pytest.raises(FileNotFoundError):
        target.snapshot()
    assert not Path(target.path).exists()


@pytest.mark.parametrize("members", [
    {}, {"": "x"}, {"a": ""}, {"a": "/etc/passwd"}, {"a": "../escape"},
    {"a": "x/../y"}, {"a": "x//y"}, {"a": "./x"}, {"a": "x\\y"},
    {"a": "x\x00y"}, {"a": "x", "b": "x"}, {"a": "x", "b": "x/y"},
])
def test_invalid_member_maps(target, tmp_path, members):
    with pytest.raises(ValueError):
        luigi.LocalGenerationTarget(tmp_path / "invalid", members)


def test_defensive_configuration_and_manifest(target, tmp_path):
    members = {"a": Path("a.txt")}
    t = luigi.LocalGenerationTarget(tmp_path / "defensive", members)
    members["a"] = "changed"
    t.members["a"] = "also-changed"
    with t.write() as writer:
        with writer.open("a") as stream:
            stream.write("caf\u00e9\n")
    with t.snapshot() as snapshot:
        original = snapshot.manifest
        assert original["version"] == 1
        assert original["generation"] == snapshot.generation
        entry = original["members"]["a"]
        assert entry == {"path": "a.txt", "size": 6, "sha256": hashlib.sha256("caf\u00e9\n".encode()).hexdigest()}
        original["members"].clear()
        assert "a" in snapshot.manifest["members"]
        assert json.loads(Path(snapshot.manifest_path).read_text()) == snapshot.manifest
        assert Path(snapshot.path("a")).read_text() == "caf\u00e9\n"


def test_many_members_and_external_writer(target, tmp_path):
    rng = random.Random(817)
    contents = {f"m{i}": rng.randbytes(i * 401) for i in range(17)}
    members = {name: f"group/{name}/data" for name in contents}
    t = luigi.LocalGenerationTarget(tmp_path / "many", members)
    with t.write() as writer:
        for name, value in contents.items():
            Path(writer.path(name)).write_bytes(value)
    assert t.exists()
    with t.snapshot() as snapshot:
        for name, value in contents.items():
            with snapshot.open(name, "rb") as stream:
                assert stream.read() == value


def test_failed_writers_preserve_previous_and_close_owned_streams(target):
    old = publish(target)
    owned = None
    with pytest.raises(RuntimeError, match="user failure"):
        with target.write() as writer:
            owned = writer.open("rows", "wb")
            owned.write(b"partial")
            path = writer.path("rows")
            raise RuntimeError("user failure")
    assert owned.closed and not Path(path).exists()
    assert read(target) == (old, b"alpha")
    with pytest.raises(luigi.GenerationIntegrityError):
        with target.write() as writer:
            with writer.open("rows", "wb") as stream:
                stream.write(b"missing metadata")
    assert read(target)[0] == old


def test_unclosed_stream_rejected_and_writer_lifecycle(target):
    with pytest.raises(luigi.GenerationIntegrityError):
        with target.write() as writer:
            stream = writer.open("rows", "wb")
            stream.write(b"owned")
            with writer.open("metadata") as metadata:
                metadata.write("{}")
    assert stream.closed
    assert writer.generation is None
    assert not target.exists()
    with pytest.raises(ValueError):
        writer.path("rows")
    with pytest.raises(ValueError):
        writer.__enter__()


def test_duplicate_unknown_and_wrong_modes(target):
    with target.write() as writer:
        for mode in ("r", "a", "w+"):
            with pytest.raises(ValueError):
                writer.open("rows", mode)
        with pytest.raises(KeyError):
            writer.open("missing")
        with writer.open("rows", "wb") as stream:
            stream.write(b"")
        with pytest.raises(ValueError):
            writer.open("rows", "wb")
        with writer.open("metadata") as stream:
            stream.write("{}")
    with target.snapshot() as snapshot:
        with pytest.raises(ValueError):
            snapshot.open("rows", "w")
        with pytest.raises(KeyError):
            snapshot.open("absent")
    snapshot.close()
    for operation in (lambda: snapshot.open("rows"), lambda: snapshot.path("rows"), snapshot.__enter__):
        with pytest.raises(ValueError):
            operation()


def test_staging_invisibility_and_undeclared_files(target):
    with pytest.raises(luigi.GenerationIntegrityError):
        with target.write() as writer:
            Path(writer.path("rows")).write_bytes(b"staging")
            Path(writer.path("metadata")).write_text("{}")
            assert not target.exists() and target.current_generation is None
            Path(writer.path("metadata")).with_name("extra").write_text("not declared")
    assert not target.exists()


def test_luigi_gzip_format(target, tmp_path):
    t = luigi.LocalGenerationTarget(tmp_path / "gzip", {"archive": "data.gz"})
    payload = bytes(range(256)) * 19
    with t.write() as writer:
        with writer.open("archive", "wb", format=luigi.format.Gzip) as stream:
            stream.write(payload)
    with t.snapshot() as snapshot:
        assert gzip.decompress(Path(snapshot.path("archive")).read_bytes()) == payload
        with snapshot.open("archive", "rb", format=luigi.format.Gzip) as stream:
            assert stream.read() == payload


def test_explicit_none_and_stale_compare_and_swap(target):
    first = publish(target, expected=None)
    with pytest.raises(luigi.GenerationConflictError):
        publish(target, b"wrong", expected=None)
    second = publish(target, b"new", expected=first)
    with pytest.raises(luigi.GenerationConflictError):
        publish(target, b"stale", expected=first)
    assert read(target) == (second, b"new")


def test_capture_happens_on_entry_not_manager_creation(target):
    pending = target.write()
    publish(target)
    with pending as writer:
        with writer.open("rows", "wb") as stream:
            stream.write(b"later")
        with writer.open("metadata") as stream:
            json.dump({"sha256": hashlib.sha256(b"later").hexdigest()}, stream)
    assert read(target)[1] == b"later"


def test_pointer_removal_does_not_promote_unreferenced_generation(target):
    publish(target)
    Path(target.path, "CURRENT").unlink()
    assert target.current_generation is None
    assert not target.exists()
    with pytest.raises(FileNotFoundError):
        target.snapshot()
    target.prune(keep=99)
    assert not target.exists()


def test_two_process_writers_capture_same_generation(target):
    publish(target)
    code = PRELUDE + '''
try:
    with t.write() as w:
        fill(w, sys.argv[2].encode())
        print("READY", flush=True)
        sys.stdin.readline()
    print("COMMITTED", flush=True)
except luigi.GenerationConflictError:
    print("CONFLICT", flush=True)
'''
    processes = [child(code, target.path, label) for label in ("winner", "loser")]
    try:
        assert [line(p) for p in processes] == ["READY", "READY"]
        processes[0].stdin.write("go\n"); processes[0].stdin.flush()
        assert line(processes[0]) == "COMMITTED"
        processes[1].stdin.write("go\n"); processes[1].stdin.flush()
        assert line(processes[1]) == "CONFLICT"
        assert read(target)[1] == b"winner"
        for p in processes:
            finish(p)
    finally:
        for p in processes:
            if p.poll() is None:
                p.kill(); p.wait(timeout=5)


def test_snapshot_pins_survive_publication_and_prune(target):
    first = publish(target, b"old")
    snapshot = target.snapshot()
    old_path = snapshot.path("rows")
    second = publish(target, b"middle")
    third = publish(target, b"latest")
    assert second in target.prune(keep=0)
    assert first not in target.prune(keep=0)
    with snapshot.open("rows", "rb") as stream:
        assert stream.read() == b"old"
    assert read(target) == (third, b"latest")
    snapshot.close()
    assert first in target.prune(keep=0)
    assert not Path(old_path).exists()
    assert target.prune(keep=0) == []


def test_retention_order_and_validation(target):
    ids = [publish(target, str(i).encode()) for i in range(5)]
    assert target.prune(keep=3) == sorted(ids[:2])
    assert target.prune(keep=1) == sorted(ids[2:4])
    assert read(target) == (ids[-1], b"4")
    for keep in (-1, 1.5, True):
        with pytest.raises(ValueError):
            target.prune(keep=keep)


def test_reader_pin_released_by_process_death(target):
    old = publish(target)
    code = PRELUDE + '''
s = t.snapshot()
print(s.generation, flush=True)
sys.stdin.readline()
'''
    p = child(code, target.path)
    try:
        assert line(p) == old
        publish(target, b"new")
        assert old not in target.prune(keep=0)
        p.kill(); p.wait(timeout=5)
        assert old in target.prune(keep=0)
    finally:
        if p.poll() is None:
            p.kill(); p.wait(timeout=5)


def test_active_and_abandoned_writer_pruning(target):
    old = publish(target)
    code = PRELUDE + '''
with t.write() as w:
    fill(w, b"staged")
    print(w.path("rows"), flush=True)
    sys.stdin.readline()
'''
    p = child(code, target.path)
    try:
        path = line(p)
        assert Path(path).exists()
        target.prune(keep=0)
        assert Path(path).exists()
        p.kill(); p.wait(timeout=5)
        target.prune(keep=0)
        assert not Path(path).exists()
        assert read(target)[0] == old
    finally:
        if p.poll() is None:
            p.kill(); p.wait(timeout=5)


@pytest.mark.parametrize("after", [False, True])
def test_process_interruption_at_pointer_publication(target, after):
    old = publish(target)
    code = PRELUDE + '''
original = os.replace
def interrupted(src, dst):
    if os.path.abspath(dst) == os.path.join(t.path, "CURRENT"):
        if sys.argv[2] == "after": original(src, dst)
        os._exit(73)
    return original(src, dst)
os.replace = interrupted
with t.write() as w: fill(w, b"new generation")
'''
    p = child(code, target.path, "after" if after else "before")
    finish(p, expected=73)
    # Fresh interpreter validates that nothing depends on surviving memory.
    check = PRELUDE + '''
with t.snapshot() as s:
    with s.open("rows", "rb") as f: payload = f.read()
    with s.open("metadata") as f: m = json.load(f)
    assert hashlib.sha256(payload).hexdigest() == m["sha256"]
    print(json.dumps([s.generation, payload.decode()]), flush=True)
'''
    reopened = child(check, target.path)
    generation, payload = json.loads(line(reopened))
    finish(reopened)
    assert payload == ("new generation" if after else "alpha")
    assert (generation == old) is (not after)
    target.prune(keep=0)
    assert read(target)[0] == generation


def test_io_failure_after_pointer_replace_keeps_committed_files(target, monkeypatch):
    old = publish(target)
    original = os.replace
    def replace_then_fail(src, dst):
        original(src, dst)
        if os.path.abspath(dst) == os.path.join(target.path, "CURRENT"):
            raise OSError("publication acknowledgement failed")
    monkeypatch.setattr(os, "replace", replace_then_fail)
    with pytest.raises(OSError):
        publish(target, b"committed despite exception")
    monkeypatch.setattr(os, "replace", original)
    assert read(target)[1] == b"committed despite exception"
    assert target.current_generation != old


@pytest.mark.parametrize("damage", ["bytes", "missing", "manifest", "bool_size", "version", "duplicate_json", "mapping"])
def test_corruption_never_completes(target, damage):
    publish(target)
    with target.snapshot() as snapshot:
        member_path = Path(snapshot.path("rows"))
        manifest_path = Path(snapshot.manifest_path)
        manifest = snapshot.manifest
    if damage == "bytes":
        member_path.write_bytes(b"omega")  # same length as alpha; size-only checks are inadequate.
    elif damage == "missing":
        member_path.unlink()
    elif damage == "manifest":
        manifest_path.write_text("{")
    elif damage == "duplicate_json":
        manifest_path.write_text('{"version":1,"version":1}')
    elif damage == "bool_size":
        manifest["members"]["rows"]["size"] = True
        manifest_path.write_text(json.dumps(manifest))
    elif damage == "version":
        manifest["version"] = 99
        manifest_path.write_text(json.dumps(manifest))
    else:
        target = luigi.LocalGenerationTarget(target.path, {"rows": "different", "metadata": "metadata.json"})
    assert not target.exists()
    with pytest.raises(luigi.GenerationIntegrityError):
        target.snapshot()


def test_snapshot_revalidates_member_after_opening(target):
    publish(target)
    with target.snapshot() as snapshot:
        Path(snapshot.path("rows")).write_bytes(b"omega")
        with pytest.raises(luigi.GenerationIntegrityError, match="rows|rows.bin"):
            snapshot.open("rows", "rb")


@pytest.mark.parametrize("pointer", ["{", '{"version":99,"generation":"bad"}', '{"version":true,"generation":"bad"}', '{"version":1,"version":1}'])
def test_invalid_pointer(target, pointer):
    publish(target)
    Path(target.path, "CURRENT").write_text(pointer)
    assert not target.exists()
    with pytest.raises(luigi.GenerationIntegrityError):
        target.snapshot()


def test_symlink_member_and_directory_rejected_without_external_damage(target, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep me")
    with pytest.raises(luigi.GenerationIntegrityError):
        with target.write() as writer:
            os.symlink(sentinel, writer.path("rows"))
            Path(writer.path("metadata")).write_text("{}")
    with pytest.raises(luigi.GenerationIntegrityError):
        with target.write() as writer:
            nested = Path(writer.path("rows")).parent
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
            Path(writer.path("metadata")).write_text("{}")
    target.prune()
    assert sentinel.read_text() == "keep me"
    assert not target.exists()


def test_committed_symlink_and_external_root(target, tmp_path):
    publish(target)
    with target.snapshot() as snapshot:
        path = Path(snapshot.path("rows"))
    external = tmp_path / "external"
    external.write_bytes(b"alpha")
    path.unlink(); path.symlink_to(external)
    assert not target.exists()
    with pytest.raises(luigi.GenerationIntegrityError):
        target.snapshot()
    alias = tmp_path / "alias"
    alias.symlink_to(target.path, target_is_directory=True)
    other = luigi.LocalGenerationTarget(alias, MEMBERS)
    assert not other.exists()
    with pytest.raises(luigi.GenerationIntegrityError):
        other.prune()
    assert external.read_bytes() == b"alpha"


def test_unrelated_io_errors_propagate(target):
    # Use an OS permission boundary, independent of the Python file-opening API.
    with tempfile.TemporaryDirectory(prefix="generation-permission-") as directory:
        protected = luigi.LocalGenerationTarget(Path(directory) / "store", MEMBERS)
        publish(protected)
        root = Path(directory)
        root.chmod(0o777)
        for path in root.rglob("*"):
            path.chmod(0o777 if path.is_dir() else 0o666)
        pointer = Path(protected.path) / "CURRENT"
        pointer.chmod(0)
        code = PRELUDE + """
try:
    t.exists()
except PermissionError:
    print("permission propagated")
else:
    raise AssertionError("unreadable pointer was treated as absence or success")
"""
        credentials = {"user": 65534, "group": 65534, "extra_groups": []} if os.geteuid() == 0 else {}
        try:
            result = subprocess.run([sys.executable, "-c", code, protected.path],
                                    capture_output=True, text=True, timeout=20,
                                    env=os.environ.copy(), **credentials)
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert result.stdout.strip() == "permission propagated"
        finally:
            pointer.chmod(0o600)


class GenerationProducer(luigi.Task):
    location = luigi.Parameter()
    def output(self):
        return luigi.LocalGenerationTarget(self.location, MEMBERS)
    def run(self):
        publish(self.output(), b"real task payload")


class GenerationConsumer(luigi.Task):
    location = luigi.Parameter()
    def requires(self):
        return GenerationProducer(self.location)
    def output(self):
        return luigi.LocalTarget(self.location + "-consumed.txt")
    def run(self):
        with self.input().snapshot() as snapshot:
            with snapshot.open("rows", "rb") as source:
                value = source.read().decode()
        with self.output().open("w") as dest:
            dest.write(value)


@pytest.mark.parametrize("workers", [1, 2])
def test_real_luigi_completion_dependencies_and_consumption(target, workers):
    consumer = GenerationConsumer(target.path)
    assert not consumer.requires().complete()
    assert luigi.build([consumer], local_scheduler=True, workers=workers)
    assert consumer.requires().complete()
    assert Path(consumer.output().path).read_text() == "real task payload"
    with target.snapshot() as snapshot:
        Path(snapshot.path("rows")).write_bytes(b"corrupt")
    assert not consumer.requires().complete()

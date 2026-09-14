import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile

import luigi
import pytest
from checkpoint_fixtures import BlockingWorkflow, CollisionWorkflow, EmptyTransitions, InvalidWorkflow, Number, PrivateWorkflow, Workflow


@pytest.fixture
def task(tmp_path):
    assert hasattr(luigi, "CheckpointTask"), "Implement the checkpoint task protocol"
    return Workflow(str(tmp_path / "workflow"))


def suspend(task):
    generator = task.run()
    requirements = next(generator)
    # Luigi accepts either a wrapper or an ordinary nested requirement structure.
    if not isinstance(requirements, luigi.DynamicRequirements):
        requirements = luigi.DynamicRequirements(requirements)
    return generator, requirements


def run_children(requirements):
    for dependency in requirements.flat_requirements:
        if not dependency.complete():
            dependency.run()


def trace(task):
    path = Path(task.root) / (task.cohort + ".trace")
    return [int(line) for line in path.read_text().splitlines()] if path.exists() else []


def child(code, task, *args):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parent) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.Popen([sys.executable, "-u", "-c", code, task.root, *map(str, args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=environment)


def line(process):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(15), "Child failed to reach a synchronization point"
    value = process.stdout.readline().strip()
    assert value, process.stderr.read()
    return value


def finish(process, expected=0):
    try:
        out, err = process.communicate(timeout=20)
        assert process.returncode == expected, (out, err, process.returncode)
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)


def test_exports_and_absent_reads(task):
    import luigi.checkpoint as module
    for name in ("CheckpointTask", "Transition", "Finish", "CheckpointValidationError", "CheckpointCorruptionError",
                 "CheckpointVersionError", "CheckpointIdentityError", "CheckpointBusyError", "CheckpointConflictError", "CheckpointOutputError"):
        assert getattr(module, name) is getattr(luigi, name)
    assert issubclass(luigi.CheckpointTask, luigi.Task)
    assert task.checkpoint_status()["phase"] == "absent"
    assert task.checkpoint_status()["sequence"] == 0
    assert task.checkpoint_status()["pending_task_ids"] == []
    assert not task.complete()
    assert not Path(task.root).exists()
    with pytest.raises((AttributeError, TypeError)):
        luigi.Transition({}).state = 1


@pytest.mark.parametrize("workers", [1, 2])
def test_real_luigi_executes_dynamic_children_and_finishes(task, workers):
    assert luigi.build([task], local_scheduler=True, workers=workers)
    assert Path(task.output().path).read_text() == "60"
    assert task.complete()
    assert task.checkpoint_status()["state"] == {"phase": 3, "sum": 60}
    assert task.checkpoint_status()["sequence"] == 3
    assert task.checkpoint_status()["phase"] == "finished"
    assert trace(task) == [0, 1, 2]
    assert list(task.run()) == []
    assert trace(task) == [0, 1, 2]


def test_committed_transition_survives_generator_and_fresh_interpreter(task):
    generator, requirements = suspend(task)
    status = task.checkpoint_status()
    assert status["phase"] == "waiting" and status["sequence"] == 1
    assert status["pending_task_ids"] == [Number(task.root, 1).task_id, Number(task.root, 2).task_id]
    assert not Number(task.root, 1).complete()  # Implementation must schedule, not execute, children.
    status["state"]["phase"] = 999
    status["pending_task_ids"].clear()
    assert task.checkpoint_status()["state"]["phase"] == 1
    generator.close()
    code = '''
import sys, luigi
from checkpoint_fixtures import Workflow
t = Workflow(sys.argv[1])
assert luigi.build([t], local_scheduler=True, workers=2)
assert t.complete()
print(t.checkpoint_status()["sequence"], flush=True)
'''
    process = child(code, task)
    assert line(process) == "3"
    finish(process)
    assert trace(task) == [0, 1, 2]


def test_waiting_state_and_sequence_stable_until_children_complete(task):
    for _ in range(3):
        generator, requirements = suspend(task)
        assert task.checkpoint_status()["sequence"] == 1
        generator.close()
    assert trace(task) == [0]
    run_children(requirements)
    generator, next_requirements = suspend(task)
    assert task.checkpoint_status()["sequence"] == 2
    assert [child.number for child in next_requirements.flat_requirements] == [3]
    generator.close()
    assert trace(task) == [0, 1]


def test_stale_suspended_generator_is_fenced(task):
    older, requirements = suspend(task)
    run_children(requirements)
    newer, _ = suspend(task)
    with pytest.raises(luigi.CheckpointConflictError):
        next(older)
    newer.close()
    assert trace(task) == [0, 1]
    assert task.checkpoint_status()["sequence"] == 2


def test_reset_cas_epoch_and_stale_continuation(task):
    older, _ = suspend(task)
    before = json.loads(Path(task.checkpoint_path()).read_text())["data"]["epoch"]
    with pytest.raises(luigi.CheckpointConflictError):
        task.reset_checkpoint(expected_sequence=0)
    assert task.reset_checkpoint(expected_sequence=1)
    assert task.checkpoint_status()["phase"] == "absent"
    with pytest.raises(luigi.CheckpointConflictError):
        next(older)
    newer, _ = suspend(task)
    after = json.loads(Path(task.checkpoint_path()).read_text())["data"]["epoch"]
    assert after != before
    newer.close()
    assert task.reset_checkpoint(expected_sequence=1)
    assert task.reset_checkpoint(expected_sequence=0) is False
    for sequence in (-1, True, 1.5):
        with pytest.raises(luigi.CheckpointValidationError):
            task.reset_checkpoint(sequence)


def test_version_upgrade_requires_explicit_reset(task, monkeypatch):
    generator, _ = suspend(task); generator.close()
    monkeypatch.setattr(Workflow, "checkpoint_version", "2")
    for call in (task.complete, task.checkpoint_status, lambda: next(task.run())):
        with pytest.raises(luigi.CheckpointVersionError):
            call()
    assert task.reset_checkpoint(expected_sequence=1)
    assert luigi.build([task], local_scheduler=True)
    assert task.checkpoint_status()["checkpoint_version"] == "2"


def test_identity_collision_and_significant_parameter_isolation(task):
    first = CollisionWorkflow(task.root, cohort="first")
    second = CollisionWorkflow(task.root, cohort="second")
    generator, _ = suspend(first); generator.close()
    for call in (second.complete, second.checkpoint_status, lambda: next(second.run()), lambda: second.reset_checkpoint(1)):
        with pytest.raises(luigi.CheckpointIdentityError):
            call()
    other = Workflow(task.root, cohort="independent")
    assert other.checkpoint_status()["phase"] == "absent"
    assert luigi.build([other], local_scheduler=True)
    assert first.checkpoint_status()["sequence"] == 1


def test_insignificant_parameter_does_not_change_identity(task):
    generator, _ = suspend(task); generator.close()
    other = Workflow(task.root, ignored="different")
    assert other.checkpoint_status()["identity"] == task.checkpoint_status()["identity"]
    assert other.checkpoint_status()["sequence"] == 1


@pytest.mark.parametrize("mode", ["bad_initial", "bad_return", "self", "req_cycle", "req_leaf", "req_key", "local_task",
                                  "nan", "infinity", "bytes", "tuple", "set", "key", "state_cycle"])
def test_invalid_application_protocol_does_not_commit(task, mode):
    invalid = InvalidWorkflow(task.root, mode=mode)
    with pytest.raises(luigi.CheckpointValidationError):
        list(invalid.run())
    status = invalid.checkpoint_status()
    assert status["sequence"] == 0
    assert status["phase"] == ("absent" if mode == "bad_initial" else "ready")


def test_hook_exception_preserves_state_and_releases_lock(task):
    invalid = InvalidWorkflow(task.root, mode="hook_error")
    with pytest.raises(LookupError, match="application failure"):
        list(invalid.run())
    assert invalid.checkpoint_status()["state"] == {"phase": 0, "sum": 0}
    assert invalid.reset_checkpoint(0)


def test_finish_needs_outputs_and_removed_outputs_need_reset(task):
    invalid = InvalidWorkflow(task.root, mode="missing_outputs")
    with pytest.raises(luigi.CheckpointOutputError):
        list(invalid.run())
    assert invalid.checkpoint_status()["phase"] == "ready"
    invalid.reset_checkpoint(0)
    assert luigi.build([task], local_scheduler=True)
    Path(task.output().path).unlink()
    assert not task.complete()
    with pytest.raises(luigi.CheckpointOutputError):
        list(task.run())
    assert trace(task) == [0, 1, 2]
    assert task.reset_checkpoint(3)


def test_existing_outputs_are_insufficient_without_journal(task):
    with task.output().open("w") as stream:
        stream.write("existing")
    assert not task.complete()
    assert task.checkpoint_status()["phase"] == "absent"


def test_empty_requirements_preserve_container_shape(task):
    empty = EmptyTransitions(task.root)
    assert luigi.build([empty], local_scheduler=True)
    assert empty.checkpoint_status()["sequence"] == 2
    assert Path(empty.output().path).read_text() == "empty ok"


def test_private_dependency_parameter_survives_native_worker_scheduling(task):
    private = PrivateWorkflow(task.root)
    generator, _ = suspend(private); generator.close()
    assert luigi.build([private], local_scheduler=True, workers=2)
    assert Path(private.output().path).read_text() == "preserved-token"


def test_failed_child_leaves_parent_waiting(task):
    Path(task.root).mkdir()
    failure = Path(task.root, "fail-1"); failure.touch()
    assert not luigi.build([task], local_scheduler=True)
    assert task.checkpoint_status()["sequence"] == 1
    assert trace(task) == [0]
    failure.unlink()
    assert luigi.build([task], local_scheduler=True)
    assert trace(task) == [0, 1, 2]


def test_deleted_child_rechecked_despite_worker_completion_cache(task):
    from luigi.worker import TaskProcess
    generator, requirements = suspend(task)
    run_children(requirements)
    generator.close()
    child_task = requirements.flat_requirements[0]
    Path(child_task.output().path).unlink()
    cache = {child_task.task_id: True}
    process = TaskProcess(task, "test-worker", None, None, task_completion_cache=cache)
    pending = process._run_get_new_deps()
    assert pending and any(params["number"] == "1" for _, _, params in pending)
    assert trace(task) == [0]
    assert luigi.build([task], local_scheduler=True)
    assert trace(task) == [0, 1, 2]


@pytest.mark.parametrize("damage", ["truncated", "duplicate", "checksum", "schema", "boolean_sequence"])
def test_corrupt_journals_fail_closed(task, damage):
    generator, _ = suspend(task); generator.close()
    path = Path(task.checkpoint_path())
    envelope = json.loads(path.read_text())
    if damage == "truncated":
        path.write_text('{"schema_version":1')
    elif damage == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1}')
    else:
        if damage == "checksum": envelope["data"]["state"]["phase"] = 800
        if damage == "schema": envelope["schema_version"] = 900
        if damage == "boolean_sequence":
            import hashlib
            envelope["data"]["sequence"] = True
            raw = json.dumps(envelope["data"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
            envelope["sha256"] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(envelope))
    before = path.read_bytes()
    for call in (task.complete, task.checkpoint_status, lambda: list(task.run()), lambda: task.reset_checkpoint(1)):
        with pytest.raises(luigi.CheckpointCorruptionError):
            call()
    assert path.read_bytes() == before and trace(task) == [0]


@pytest.mark.parametrize("after", [False, True])
def test_process_death_at_transition_commit(task, after):
    code = '''
import json, os, sys
from checkpoint_fixtures import Workflow
t = Workflow(sys.argv[1])
original = os.replace
def crash(src, dst):
    if os.path.abspath(dst) == os.path.abspath(t.checkpoint_path()):
        with open(src) as stream: sequence = json.load(stream)["data"]["sequence"]
        if sequence == 1:
            if sys.argv[2] == "after": original(src, dst)
            os._exit(72)
    return original(src, dst)
os.replace = crash
next(t.run())
'''
    process = child(code, task, "after" if after else "before")
    finish(process, expected=72)
    assert task.checkpoint_status()["sequence"] == (1 if after else 0)
    assert trace(task) == [0]
    assert luigi.build([task], local_scheduler=True)
    assert trace(task) == ([0, 1, 2] if after else [0, 0, 1, 2])


def test_exception_after_replace_keeps_committed_transition(task, monkeypatch):
    original = os.replace
    def replace_then_raise(src, dst):
        with open(src) as stream: sequence = json.load(stream)["data"]["sequence"]
        original(src, dst)
        if sequence == 1:
            raise OSError("commit acknowledgement lost")
    monkeypatch.setattr(os, "replace", replace_then_raise)
    with pytest.raises(OSError):
        next(task.run())
    monkeypatch.setattr(os, "replace", original)
    assert task.checkpoint_status()["sequence"] == 1
    assert luigi.build([task], local_scheduler=True)
    assert trace(task) == [0, 1, 2]


def test_execution_lock_busy_and_released_on_process_death(task):
    blocking = BlockingWorkflow(task.root)
    code = '''
import sys
from checkpoint_fixtures import BlockingWorkflow
next(BlockingWorkflow(sys.argv[1]).run())
'''
    process = child(code, task)
    try:
        assert line(process) == "ADVANCING"
        with pytest.raises(luigi.CheckpointBusyError):
            next(blocking.run())
        with pytest.raises(luigi.CheckpointBusyError):
            blocking.reset_checkpoint(0)
        assert blocking.checkpoint_status()["phase"] == "ready"
        process.kill(); process.wait(timeout=5)
        assert blocking.reset_checkpoint(0)
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)


def test_yield_releases_lock_for_another_process(task):
    generator, _ = suspend(task)
    code = '''
import sys
from checkpoint_fixtures import Workflow
t=Workflow(sys.argv[1])
print(t.reset_checkpoint(1), flush=True)
'''
    process = child(code, task)
    assert line(process) == "True"
    finish(process)
    with pytest.raises(luigi.CheckpointConflictError):
        next(generator)


def test_io_failures_propagate(task):
    # Real filesystem denial reaches os.open, imported aliases, builtins.open,
    # pathlib and other legitimate readers alike. Root containers drop UID in
    # the reader process so mode bits have their ordinary effect.
    with tempfile.TemporaryDirectory(prefix="checkpoint-permission-") as directory:
        protected = Workflow(str(Path(directory) / "workflow"))
        generator, _ = suspend(protected)
        generator.close()
        root = Path(directory)
        root.chmod(0o777)
        for path in root.rglob("*"):
            path.chmod(0o777 if path.is_dir() else 0o666)
        journal = Path(protected.checkpoint_path())
        journal.chmod(0)
        code = """
import sys
from checkpoint_fixtures import Workflow
task = Workflow(sys.argv[1])
try:
    task.complete()
except PermissionError:
    print("permission propagated")
else:
    raise AssertionError("unreadable journal was treated as absence or success")
"""
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).parent) + os.pathsep + environment.get("PYTHONPATH", "")
        credentials = {"user": 65534, "group": 65534, "extra_groups": []} if os.geteuid() == 0 else {}
        try:
            result = subprocess.run([sys.executable, "-c", code, protected.root],
                                    capture_output=True, text=True, timeout=20,
                                    env=environment, **credentials)
            assert result.returncode == 0, (result.stdout, result.stderr)
            assert result.stdout.strip() == "permission propagated"
        finally:
            journal.chmod(0o600)

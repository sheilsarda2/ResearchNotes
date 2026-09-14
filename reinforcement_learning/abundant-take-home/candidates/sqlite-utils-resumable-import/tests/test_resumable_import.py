"""Independent end-to-end contract tests for durable resumable imports."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from sqlite_utils import Database
from sqlite_utils.db import NoTable, TransactionError


class Paused(Exception):
    pass


def pause(_state):
    raise Paused()


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return path


def checkpoint(path, identity="job"):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT * FROM "_sqlite_utils_imports" WHERE import_id=?', [identity]
        ).fetchone()
        return dict(row) if row else None


def rows(path, table="events"):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM "{}"'.format(table))]


def logical_dump(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "sqlite_utils", *map(str, args)],
        text=True, capture_output=True, timeout=30,
    )


def child(code, *args):
    return subprocess.run(
        [sys.executable, "-c", code, *map(str, args)],
        text=True, capture_output=True, timeout=30,
    )


def assert_result(result, identity, count, offset, complete=True):
    assert result == {
        "import_id": identity, "rows_committed": count,
        "byte_offset": offset, "completed": complete,
    }
    assert isinstance(result["completed"], bool)


def test_api_persistent_identity_callback_and_noop(tmp_path):
    source = write_rows(tmp_path / "source.ndjson", [
        {"id": 10, "name": "雪", "nested": {"a": [1, 2]}},
        {"id": 20, "name": "café", "nested": None},
        {"id": 30, "name": "third", "nested": {"ok": True}},
    ])
    target = tmp_path / "data.db"
    seen = []
    with Database(target) as db:
        def report(state):
            seen.append(dict(state))
            # The notification occurs after a durable commit visible elsewhere.
            saved = checkpoint(target)
            assert saved["rows_committed"] == state["rows_committed"]
            state["rows_committed"] = -999
        result = db["events"].insert_file_resumable(
            source, import_id="job", pk="id", batch_size=2, progress=report,
        )
        assert_result(result, "job", 3, source.stat().st_size)
    assert [s["rows_committed"] for s in seen] == [2, 3]
    assert [row["id"] for row in rows(target)] == [10, 20, 30]
    assert json.loads(rows(target)[0]["nested"]) == {"a": [1, 2]}
    state = checkpoint(target)
    assert state["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert json.loads(state["options_json"]) == {"pk": ["id"], "alter": False, "upsert": False}
    before = logical_dump(target)
    moved = tmp_path / "same-bytes.ndjson"
    shutil.copyfile(source, moved)
    with Database(target) as db:
        result = db["EVENTS"].insert_file_resumable(
            moved, import_id="job", pk=["id"], batch_size=1,
            progress=lambda _: pytest.fail("completed import notified progress"),
        )
        assert_result(result, "job", 3, source.stat().st_size)
    assert logical_dump(target) == before


def test_unicode_byte_checkpoint_and_final_record_without_newline(tmp_path):
    source = tmp_path / "unicode.ndjson"
    prefix = b'\xef\xbb\xbf\r\n' + '{"id":1,"text":"雪"}\r\n'.encode() + b' \r\n'
    second = '{"id":2,"text":"é"}\r\n'.encode()
    tail = b'\n' + '{"id":3,"text":"🌱"}'.encode()
    source.write_bytes(prefix + second + tail)
    target = tmp_path / "u.db"
    with Database(target) as db:
        with pytest.raises(Paused):
            db["events"].insert_file_resumable(
                source, import_id="job", pk="id", batch_size=2, progress=pause,
            )
    state = checkpoint(target)
    assert state["byte_offset"] == len(prefix + second)
    assert state["rows_committed"] == 2
    assert state["completed"] == 0
    with Database(target) as db:
        result = db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=1)
    assert_result(result, "job", 3, len(prefix + second + tail))
    assert [r["text"] for r in rows(target)] == ["雪", "é", "🌱"]


@pytest.mark.parametrize("content", [b"", b" \r\n\n\t", b"\xef\xbb\xbf", b"\xef\xbb\xbf\r\n \n"])
def test_empty_input_completion(tmp_path, content):
    source = tmp_path / "empty.ndjson"
    source.write_bytes(content)
    target = tmp_path / "empty.db"
    with Database(target) as db:
        result = db["events"].insert_file_resumable(source, import_id="job")
    assert_result(result, "job", 0, len(content))
    state = checkpoint(target)
    assert (state["rows_committed"], state["byte_offset"], state["completed"]) == (0, len(content), 1)


def test_trailing_blank_lines_are_checkpointed(tmp_path):
    source = tmp_path / "tail.ndjson"
    source.write_bytes(b'{"id":1}\n\r\n \t\n')
    target = tmp_path / "tail.db"
    with Database(target) as db:
        with pytest.raises(Paused):
            db["events"].insert_file_resumable(source, import_id="job", batch_size=1, progress=pause)
    assert checkpoint(target)["byte_offset"] == len(b'{"id":1}\n')
    with Database(target) as db:
        result = db["events"].insert_file_resumable(source, import_id="job", batch_size=7)
    assert_result(result, "job", 1, source.stat().st_size)
    assert rows(target) == [{"id": 1}]


def test_checkpoint_write_failure_rolls_back_data_schema_and_trigger_effects(tmp_path):
    source = write_rows(tmp_path / "e.ndjson", [
        {"id": 1, "value": "a"}, {"id": 2, "value": "b"},
        {"id": 3, "value": "c", "new_column": 3},
        {"id": 4, "value": "d", "new_column": 4},
    ])
    target = tmp_path / "e.db"
    with Database(target) as db:
        with pytest.raises(Paused):
            db["events"].insert_file_resumable(source, import_id="job", pk="id", alter=True, batch_size=2, progress=pause)
        db.execute("CREATE TABLE audit (value TEXT)")
        db.execute("CREATE TRIGGER log_event AFTER INSERT ON events BEGIN INSERT INTO audit VALUES (NEW.value); END")
        db.execute("CREATE TRIGGER reject_progress BEFORE UPDATE ON _sqlite_utils_imports WHEN NEW.rows_committed > 2 BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END")
    before = logical_dump(target)
    with Database(target) as db:
        with pytest.raises(sqlite3.IntegrityError, match="checkpoint unavailable"):
            db["events"].insert_file_resumable(source, import_id="job", pk="id", alter=True, batch_size=2)
    assert logical_dump(target) == before
    with Database(target) as db:
        assert "new_column" not in db["events"].columns_dict
        db.execute("DROP TRIGGER reject_progress")
        result = db["events"].insert_file_resumable(source, import_id="job", pk="id", alter=True, batch_size=1)
    assert_result(result, "job", 4, source.stat().st_size)
    assert rows(target, "audit") == [{"value": "c"}, {"value": "d"}]
    assert [r["id"] for r in rows(target)] == [1, 2, 3, 4]


def test_row_failure_preserves_previous_batches_and_can_resume(tmp_path):
    source = write_rows(tmp_path / "rows.ndjson", [{"id": i} for i in range(1, 7)])
    target = tmp_path / "rows.db"
    with Database(target) as db:
        db["events"].create({"id": int}, pk="id")
        db.execute("CREATE TRIGGER blocked BEFORE INSERT ON events WHEN NEW.id=4 BEGIN SELECT RAISE(ABORT, 'blocked row'); END")
        with pytest.raises(sqlite3.IntegrityError, match="blocked row"):
            db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=2)
    assert rows(target) == [{"id": 1}, {"id": 2}]
    assert checkpoint(target)["rows_committed"] == 2
    with Database(target) as db:
        db.execute("DROP TRIGGER blocked")
        result = db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=3)
    assert_result(result, "job", 6, source.stat().st_size)
    assert rows(target) == [{"id": i} for i in range(1, 7)]


def test_internal_sql_subbatches_are_one_logical_batch(tmp_path):
    target = tmp_path / "wide.db"
    source = tmp_path / "wide.ndjson"
    wide = [{"id": i, **{f"c{j}": j for j in range(600)}} for i in (1, 2, 3)]
    write_rows(source, wide)
    with Database(target) as db:
        db["events"].insert({"id": 0, **{f"c{j}": j for j in range(600)}}, pk="id")
        db.execute("CREATE TRIGGER third_fails BEFORE INSERT ON events WHEN NEW.id=3 BEGIN SELECT RAISE(ABORT, 'third failed'); END")
    with sqlite3.connect(target) as conn:
        before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    with Database(target) as db:
        with pytest.raises(sqlite3.IntegrityError, match="third failed"):
            db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=3)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall() == before
    assert rows(target) == [{"id": 0, **{f"c{j}": j for j in range(600)}}]


@pytest.mark.parametrize("bad", [b'{"id":3', b'[]\n', b'null\n', b'4\n', b'{"id":NaN}\n', b'{"id":Infinity}\n', b'{"id":"\xff"}\n'])
def test_bad_late_line_does_not_consume_or_commit_its_batch(tmp_path, bad):
    source = tmp_path / "bad.ndjson"
    good = b'{"id":1}\n{"id":2}\n'
    source.write_bytes(good + b'{"id":99}\n' + bad)
    target = tmp_path / "bad.db"
    with Database(target) as db:
        with pytest.raises(ValueError):
            db["events"].insert_file_resumable(source, import_id="job", batch_size=2)
    assert rows(target) == [{"id": 1}, {"id": 2}]
    state = checkpoint(target)
    assert (state["rows_committed"], state["byte_offset"], state["completed"]) == (2, len(good), 0)
    before = logical_dump(target)
    # Repairing a partial final line changes source identity, never silently resumes.
    source.write_bytes(good + b'{"id":99}\n{"id":3}\n')
    with Database(target) as db:
        with pytest.raises(ValueError):
            db["events"].insert_file_resumable(source, import_id="job", batch_size=2)
    assert logical_dump(target) == before


def test_first_batch_failure_creates_no_schema_or_checkpoint(tmp_path):
    target = tmp_path / "initial.db"
    source = tmp_path / "initial.ndjson"
    source.write_bytes(b'{"id":1}\n{"id":2')
    with Database(target) as db:
        with pytest.raises(ValueError):
            db["events"].insert_file_resumable(source, import_id="job", batch_size=5)
        assert db.table_names() == []


def test_abrupt_exit_before_checkpoint_commit_then_reopen(tmp_path):
    source = write_rows(tmp_path / "crash.ndjson", [{"id": i} for i in range(1, 7)])
    target = tmp_path / "crash.db"
    with Database(target) as db:
        with pytest.raises(Paused):
            db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=2, progress=pause)
        db.execute("CREATE TRIGGER crash_in_checkpoint BEFORE UPDATE ON _sqlite_utils_imports WHEN NEW.rows_committed=4 BEGIN SELECT terminate_import(); END")
    proc = child('''
import os,sys
from sqlite_utils import Database
db=Database(sys.argv[1])
db.conn.create_function("terminate_import",0,lambda: os._exit(73))
db["events"].insert_file_resumable(sys.argv[2],import_id="job",pk="id",batch_size=2)
''', target, source)
    assert proc.returncode == 73, proc.stderr
    assert rows(target) == [{"id": 1}, {"id": 2}]
    assert checkpoint(target)["rows_committed"] == 2
    with Database(target) as db:
        db.execute("DROP TRIGGER crash_in_checkpoint")
    proc = cli("import-resumable", target, "events", source, "--import-id", "job", "--pk", "id", "--batch-size", 3)
    assert proc.returncode == 0, proc.stderr
    assert_result(json.loads(proc.stdout), "job", 6, source.stat().st_size)
    assert rows(target) == [{"id": i} for i in range(1, 7)]


def test_abrupt_exit_after_commit_then_cli_resume(tmp_path):
    source = write_rows(tmp_path / "after.ndjson", [{"id": i, "s": "雪"} for i in range(5)])
    target = tmp_path / "after.db"
    proc = child('''
import os,sys
from sqlite_utils import Database
db=Database(sys.argv[1])
db["events"].insert_file_resumable(sys.argv[2],import_id="job",pk="id",batch_size=2,progress=lambda _:os._exit(74))
''', target, source)
    assert proc.returncode == 74, proc.stderr
    assert checkpoint(target)["rows_committed"] == 2
    assert len(rows(target)) == 2
    proc = cli("import-resumable", target, "events", source, "--import-id", "job", "--pk", "id")
    assert proc.returncode == 0, proc.stderr
    assert_result(json.loads(proc.stdout), "job", 5, source.stat().st_size)
    assert [r["id"] for r in rows(target)] == list(range(5))


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("change", ["source", "table", "pk", "alter", "upsert"])
def test_identity_changes_rejected_before_mutation(tmp_path, complete, change):
    source = write_rows(tmp_path / "identity.ndjson", [{"id": 1}, {"id": 2}])
    target = tmp_path / "identity.db"
    with Database(target) as db:
        kwargs = dict(import_id="job", pk="id", batch_size=1)
        if complete:
            db["events"].insert_file_resumable(source, **kwargs)
        else:
            with pytest.raises(Paused):
                db["events"].insert_file_resumable(source, progress=pause, **kwargs)
    before = logical_dump(target)
    name = "events"
    if change == "source":
        source.write_bytes(source.read_bytes().replace(b'1', b'9', 1))
    elif change == "table":
        name = "other"
    elif change == "pk":
        kwargs["pk"] = None
    else:
        kwargs[change] = True
    with Database(target) as db:
        with pytest.raises(ValueError):
            db[name].insert_file_resumable(source, **kwargs)
    assert logical_dump(target) == before


def test_upsert_compound_keys_and_inferred_primary_key(tmp_path):
    target = tmp_path / "upsert.db"
    source = write_rows(tmp_path / "upsert.ndjson", [
        {"tenant": "a", "key": 1, "value": "new"},
        {"tenant": "b", "key": 1, "value": "different"},
        {"tenant": "a", "key": 1, "value": "final"},
    ])
    with Database(target) as db:
        db["events"].insert({"tenant": "a", "key": 1, "value": "old"}, pk=("tenant", "key"))
        result = db["events"].insert_file_resumable(source, import_id="job", upsert=True, batch_size=2)
    assert_result(result, "job", 3, source.stat().st_size)
    assert rows(target) == [
        {"tenant": "a", "key": 1, "value": "final"},
        {"tenant": "b", "key": 1, "value": "different"},
    ]
    source2 = write_rows(tmp_path / "new.ndjson", [{"tenant": "x", "key": 8}])
    proc = cli("import-resumable", target, "composite", source2, "--import-id", "other", "--pk", "tenant", "--pk", "key")
    assert proc.returncode == 0, proc.stderr
    with Database(target) as db:
        assert db["composite"].pks == ["tenant", "key"]
    replacement = write_rows(tmp_path / "replacement.ndjson", [{"tenant": "x", "key": 8, "value": "updated"}])
    proc = cli("import-resumable", target, "composite", replacement, "--import-id", "replacement", "--upsert", "--alter")
    assert proc.returncode == 0, proc.stderr
    assert rows(target, "composite") == [{"tenant": "x", "key": 8, "value": "updated"}]


def test_two_import_ids_same_destination_and_alter_cli(tmp_path):
    target = tmp_path / "two.db"
    a = write_rows(tmp_path / "a.ndjson", [{"id": 1, "a": "x"}])
    b = write_rows(tmp_path / "b.ndjson", [{"id": 2, "b": "y"}])
    for identity, source in [("a", a), ("b", b), ("a", a), ("b", b)]:
        proc = cli("import-resumable", target, "events", source, "--import-id", identity, "--pk", "id", "--alter")
        assert proc.returncode == 0, proc.stderr
    assert rows(target) == [{"id": 1, "a": "x", "b": None}, {"id": 2, "a": None, "b": "y"}]


@pytest.mark.parametrize("kwargs", [
    {"import_id": ""}, {"batch_size": 0}, {"batch_size": -2}, {"batch_size": True},
    {"batch_size": 1.5}, {"pk": []}, {"pk": ["id", "ID"]}, {"pk": 3},
    {"alter": "yes"}, {"upsert": 1}, {"progress": 5},
])
def test_invalid_arguments_do_not_mutate_database(tmp_path, kwargs):
    source = write_rows(tmp_path / "args.ndjson", [{"id": 1}])
    with Database(memory=True) as db:
        with pytest.raises((ValueError, TypeError)):
            db["events"].insert_file_resumable(source, **{"import_id": "job", **kwargs})
        assert db.table_names() == []


def test_open_transaction_refused_without_consuming_or_mutating(tmp_path):
    source = tmp_path / "does-not-exist.ndjson"
    target = tmp_path / "outer.db"
    with Database(target) as db:
        db["sentinel"].create({"id": int})
        db.begin()
        db["sentinel"].insert({"id": 1})
        with pytest.raises(TransactionError):
            db["events"].insert_file_resumable(source, import_id="job")
        assert db.conn.in_transaction
        assert db.table_names() == ["sentinel"]
        db.rollback()
    assert rows(target, "sentinel") == []


@pytest.mark.parametrize("kind", ["view", "virtual", "reserved", "directory", "missing"])
def test_unsupported_targets_and_sources_rejected(tmp_path, kind):
    source = write_rows(tmp_path / "source.ndjson", [{"value": "x"}])
    with Database(memory=True) as db:
        table = "events"
        if kind == "view":
            db.execute("CREATE VIEW events AS SELECT 'x' AS value")
        elif kind == "virtual":
            db.execute("CREATE VIRTUAL TABLE events USING fts5(value)")
        elif kind == "reserved":
            table = "_SQLITE_UTILS_IMPORTS"
        elif kind == "directory":
            source = tmp_path
        else:
            source = tmp_path / "missing.ndjson"
        before = list(db.conn.iterdump())
        with pytest.raises((ValueError, OSError, NoTable)):
            db.table(table).insert_file_resumable(source, import_id="job")
        assert list(db.conn.iterdump()) == before


def test_cli_errors_are_concise_and_do_not_mark_failed_file_complete(tmp_path):
    source = tmp_path / "cli-bad.ndjson"
    source.write_bytes(b'{"id":1}\n{"id":')
    target = tmp_path / "cli-bad.db"
    proc = cli("import-resumable", target, "events", source, "--import-id", "job", "--batch-size", 1)
    assert proc.returncode != 0
    assert "Error:" in proc.stderr and "Traceback" not in proc.stderr
    assert checkpoint(target)["completed"] == 0
    source.write_bytes(b'{"id":1}\n{"id":2}\n')
    before = logical_dump(target)
    proc = cli("import-resumable", target, "events", source, "--import-id", "job")
    assert proc.returncode != 0
    assert "Error:" in proc.stderr and "Traceback" not in proc.stderr
    assert logical_dump(target) == before


def test_input_is_streamed_and_file_bytes_remain_unchanged(tmp_path, monkeypatch):
    source = write_rows(tmp_path / "stream.ndjson", [{"id": i, "payload": "x" * 80} for i in range(200)])
    original = source.read_bytes()
    open_original = Path.open

    class GuardedFile:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def read(self, size=-1):
            assert size >= 0, "unbounded whole-file read"
            return self.stream.read(size)
        def readlines(self, hint=-1):
            assert hint > 0, "unbounded whole-file readlines"
            return self.stream.readlines(hint)
        def __iter__(self):
            return iter(self.stream)
        def __getattr__(self, name):
            return getattr(self.stream, name)

    def guarded_open(path, *args, **kwargs):
        stream = open_original(path, *args, **kwargs)
        return GuardedFile(stream) if path == source else stream

    with monkeypatch.context() as context:
        context.setattr(Path, "open", guarded_open)
        with Database(tmp_path / "stream.db") as db:
            result = db["events"].insert_file_resumable(source, import_id="job", pk="id", batch_size=7)
    assert_result(result, "job", 200, len(original))
    assert source.read_bytes() == original

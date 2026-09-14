"""Observable graph, snapshot, idempotency and transaction behavior."""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from sqlite_utils import Database


SCHEMA = """
CREATE TABLE teams (
  id INTEGER PRIMARY KEY,
  lead_id INTEGER REFERENCES people(id) ON UPDATE CASCADE ON DELETE SET NULL,
  name TEXT UNIQUE
);
CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE,
  manager_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
  name TEXT,
  payload BLOB
);
"""
TABLES = ["people", "teams"]


def make_graph(path, label=None, destination=False):
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        if label is not None:
            if destination:
                conn.execute("INSERT INTO teams VALUES (20,10,?)", [label + " team"])
                conn.execute("INSERT INTO people VALUES (10,20,10,?,?)", [label + " existing", b"original"])
            else:
                conn.execute("INSERT INTO teams VALUES (1,1,?)", [label + " team"])
                conn.executemany("INSERT INTO people VALUES (?,?,?,?,?)", [
                    (1, 1, 2, label + " Alice", b"\x00\xff"),
                    (2, 1, None, label + " Bob", b"\x10"),
                ])
    return path


def dump(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def query(path, sql, params=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "sqlite_utils", *map(str, args)], text=True, capture_output=True, timeout=30)


def child(code, *args):
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], text=True, capture_output=True, timeout=30)


def merge(target, sources, tables=TABLES):
    with Database(target) as db:
        db.execute("PRAGMA foreign_keys=ON")
        return db.merge_preserving_keys(sources, tables=tables)


def test_multiple_sources_cycles_maps_values_and_originals(tmp_path):
    target = make_graph(tmp_path / "dest.db", "dest", destination=True)
    north = make_graph(tmp_path / "north.db", "north 雪")
    south = make_graph(tmp_path / "south.db", "south")
    source_before = (dump(north), dump(south))
    result = merge(target, [("south", south), ("north", north)], tables=reversed(TABLES))
    assert result["rows_inserted"] == 6
    assert list(result["sources"]) == ["north", "south"]
    assert result["sources"]["north"] == {
        "skipped": False, "rows_inserted": {"people": 2, "teams": 1},
        "key_map": {"people": [[1, 11], [2, 12]], "teams": [[1, 21]]},
    }
    assert result["sources"]["south"]["key_map"] == {"people": [[1, 13], [2, 14]], "teams": [[1, 22]]}
    assert query(target, "SELECT id, team_id, manager_id, name, payload FROM people ORDER BY id") == [
        (10, 20, 10, "dest existing", b"original"),
        (11, 21, 12, "north 雪 Alice", b"\x00\xff"),
        (12, 21, None, "north 雪 Bob", b"\x10"),
        (13, 22, 14, "south Alice", b"\x00\xff"),
        (14, 22, None, "south Bob", b"\x10"),
    ]
    assert query(target, "SELECT id, lead_id FROM teams ORDER BY id") == [(20, 10), (21, 11), (22, 13)]
    assert query(target, "PRAGMA foreign_key_check") == []
    assert (dump(north), dump(south)) == source_before
    assert len(query(target, "SELECT * FROM _sqlite_utils_merge_sources")) == 2
    assert len(query(target, "SELECT * FROM _sqlite_utils_merge_keys")) == 6


def test_idempotency_reopen_moved_source_and_canonical_cli_order(tmp_path):
    source = make_graph(tmp_path / "s.db", "s")
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    original = merge(target, [("s", source)])
    relocated = tmp_path / "relocated.db"
    shutil.copyfile(source, relocated)
    before = dump(target)
    proc = run_cli("merge-preserving-keys", target, "--source", "s", relocated, "--table", "TEAMS", "--table", "PEOPLE")
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["rows_inserted"] == 0
    assert result["sources"]["s"]["skipped"] is True
    assert result["sources"]["s"]["rows_inserted"] == {"people": 0, "teams": 0}
    assert result["sources"]["s"]["key_map"] == original["sources"]["s"]["key_map"]
    assert dump(target) == before


def test_input_order_does_not_change_allocation(tmp_path):
    a = make_graph(tmp_path / "a.db", "a")
    z = make_graph(tmp_path / "z.db", "z")
    d1 = make_graph(tmp_path / "d1.db", "d", destination=True)
    d2 = make_graph(tmp_path / "d2.db", "d", destination=True)
    r1 = merge(d1, [("z", z), ("a", a)], ["teams", "people"])
    r2 = merge(d2, [("a", a), ("z", z)], ["people", "teams"])
    assert r1 == r2
    assert dump(d1) == dump(d2)


def test_new_and_previously_imported_sources_allocate_only_new_ids(tmp_path):
    a = make_graph(tmp_path / "a.db", "a")
    z = make_graph(tmp_path / "z.db", "z")
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    first = merge(target, [("a", a)])
    result = merge(target, [("z", z), ("a", a)])
    assert result["rows_inserted"] == 3
    assert result["sources"]["a"]["skipped"] is True
    assert result["sources"]["a"]["key_map"] == first["sources"]["a"]["key_map"]
    assert result["sources"]["z"]["key_map"] == {"people": [[1, 13], [2, 14]], "teams": [[1, 22]]}


def test_wal_snapshot_identity_survives_checkpoint_and_detects_wal_only_changes(tmp_path):
    source = tmp_path / "wal.db"
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.executescript(SCHEMA)
        writer.execute("INSERT INTO teams VALUES (1,1,'wal team')")
        writer.execute("INSERT INTO people VALUES (1,1,NULL,'from WAL',X'00FF')")
        writer.commit()
        target = make_graph(tmp_path / "d.db")
        result = merge(target, [("wal", source)])
        assert result["rows_inserted"] == 2
        assert query(target, "SELECT name FROM people") == [("from WAL",)]
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert merge(target, [("wal", source)])["rows_inserted"] == 0
        main_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        writer.execute("UPDATE people SET name='committed only in WAL' WHERE id=1")
        writer.commit()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == main_hash
        before = dump(target)
        with pytest.raises(ValueError):
            merge(target, [("wal", source)])
        assert dump(target) == before
    finally:
        writer.close()


@pytest.mark.parametrize("change", ["data", "index"])
def test_source_identity_failure_is_atomic_with_other_new_sources(tmp_path, change):
    source = make_graph(tmp_path / "source.db", "old")
    fresh = make_graph(tmp_path / "fresh.db", "fresh")
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    merge(target, [("z-old", source)])
    with sqlite3.connect(source) as conn:
        if change == "data":
            conn.execute("UPDATE people SET name='changed' WHERE id=2")
        else:
            conn.execute("CREATE INDEX by_name ON people(name)")
    before = dump(target)
    with pytest.raises(ValueError):
        merge(target, [("a-new", fresh), ("z-old", source)])
    assert dump(target) == before


def test_unrelated_source_changes_do_not_break_identity(tmp_path):
    source = make_graph(tmp_path / "source.db", "s")
    target = make_graph(tmp_path / "d.db")
    merge(target, [("s", source)])
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE unrelated (x BLOB)")
        conn.execute("INSERT INTO unrelated VALUES (X'1234')")
    assert merge(target, [("s", source)])["sources"]["s"]["skipped"] is True


def test_metadata_failure_rolls_back_all_sources_and_audit_effects(tmp_path):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    empty = make_graph(tmp_path / "empty.db")
    source_a = make_graph(tmp_path / "a.db", "a")
    source_z = make_graph(tmp_path / "z.db", "z")
    merge(target, [("seed", empty)])
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE audit (name TEXT)")
        conn.execute("CREATE TRIGGER audit_person AFTER INSERT ON people BEGIN INSERT INTO audit VALUES (NEW.name); END")
        conn.execute("CREATE TRIGGER fail_map BEFORE INSERT ON _sqlite_utils_merge_keys WHEN NEW.source_id='z' AND NEW.table_name='teams' BEGIN SELECT RAISE(ABORT, 'map write failed'); END")
    before = dump(target)
    with pytest.raises(sqlite3.IntegrityError, match="map write failed"):
        merge(target, [("z", source_z), ("a", source_a)])
    assert dump(target) == before
    with sqlite3.connect(target) as conn:
        conn.execute("DROP TRIGGER fail_map")
    assert merge(target, [("z", source_z), ("a", source_a)])["rows_inserted"] == 6
    assert query(target, "SELECT name FROM audit") == [("a Alice",), ("a Bob",), ("z Alice",), ("z Bob",)]


def test_destination_unique_failure_rolls_back_earlier_source(tmp_path):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    a = make_graph(tmp_path / "a.db", "a")
    z = make_graph(tmp_path / "z.db", "d")  # Teams.name conflicts late with original destination.
    before = dump(target)
    with pytest.raises(sqlite3.IntegrityError):
        merge(target, [("a", a), ("z", z)])
    assert dump(target) == before


@pytest.mark.parametrize("fk,defer", [(0, 0), (0, 1), (1, 0), (1, 1)])
@pytest.mark.parametrize("fail", [False, True])
def test_pragma_restoration_on_success_and_failure(tmp_path, fk, defer, fail):
    target = make_graph(tmp_path / "d.db")
    source = make_graph(tmp_path / "s.db", "s")
    with Database(target) as db:
        if fail:
            db.execute("CREATE TRIGGER reject_people BEFORE INSERT ON people BEGIN SELECT RAISE(ABORT, 'no people'); END")
        db.execute(f"PRAGMA foreign_keys={fk}")
        db.execute(f"PRAGMA defer_foreign_keys={defer}")
        if fail:
            with pytest.raises(sqlite3.IntegrityError):
                db.merge_preserving_keys([("s", source)], tables=TABLES)
        else:
            assert db.merge_preserving_keys([("s", source)], tables=TABLES)["rows_inserted"] == 3
        assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == fk
        assert db.conn.execute("PRAGMA defer_foreign_keys").fetchone()[0] == defer
        assert not db.conn.in_transaction


def test_caller_transaction_can_rollback_successful_merge(tmp_path):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    source = make_graph(tmp_path / "s.db", "s")
    before = dump(target)
    with Database(target) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.begin()
        assert db.merge_preserving_keys([("s", source)], tables=TABLES)["rows_inserted"] == 3
        assert db.conn.in_transaction
        assert db["people"].count == 3
        assert query(target, "SELECT COUNT(*) FROM people") == [(1,)]
        db.rollback()
    assert dump(target) == before


def test_failed_nested_merge_keeps_preceding_caller_work(tmp_path):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    source = make_graph(tmp_path / "s.db", "s")
    with Database(target) as db:
        db.execute("CREATE TABLE caller_work (value TEXT)")
        db.execute("CREATE TRIGGER reject_team BEFORE INSERT ON teams WHEN NEW.id>20 BEGIN SELECT RAISE(ABORT, 'team rejected'); END")
        db.execute("PRAGMA foreign_keys=ON")
        db.begin()
        db["caller_work"].insert({"value": "keep me"})
        with pytest.raises(sqlite3.IntegrityError, match="team rejected"):
            db.merge_preserving_keys([("s", source)], tables=TABLES)
        assert db.conn.in_transaction
        assert db["people"].count == 1
        assert list(db["caller_work"].rows) == [{"value": "keep me"}]
        assert "_sqlite_utils_merge_sources" not in db.table_names()
        db.commit()
    assert query(target, "SELECT value FROM caller_work") == [("keep me",)]


@pytest.mark.parametrize("body", ["SELECT RAISE(IGNORE);", "UPDATE people SET name='tampered' WHERE id=NEW.id;"])
def test_triggers_cannot_silently_suppress_or_change_imported_rows(tmp_path, body):
    target = make_graph(tmp_path / "d.db")
    source = make_graph(tmp_path / "s.db", "s")
    with sqlite3.connect(target) as conn:
        timing = "BEFORE" if "IGNORE" in body else "AFTER"
        conn.execute(f"CREATE TRIGGER change_rows {timing} INSERT ON people BEGIN {body} END")
    before = dump(target)
    with pytest.raises(sqlite3.IntegrityError):
        merge(target, [("s", source)])
    assert dump(target) == before


def test_process_termination_before_commit_leaves_no_partial_graph(tmp_path):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    source = make_graph(tmp_path / "s.db", "s")
    empty = make_graph(tmp_path / "empty.db")
    merge(target, [("seed", empty)])
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TRIGGER stop_before_record BEFORE INSERT ON _sqlite_utils_merge_sources WHEN NEW.source_id='new' BEGIN SELECT stop_process(); END")
    before = dump(target)
    proc = child('''
import os,sys
from sqlite_utils import Database
db=Database(sys.argv[1])
db.conn.create_function("stop_process",0,lambda:os._exit(77))
db.merge_preserving_keys([("new",sys.argv[2])],tables=["people","teams"])
''', target, source)
    assert proc.returncode == 77, proc.stderr
    assert dump(target) == before
    with sqlite3.connect(target) as conn:
        conn.execute("DROP TRIGGER stop_before_record")
    proc = run_cli("merge-preserving-keys", target, "--source", "new", source, "--table", "people", "--table", "teams")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["rows_inserted"] == 3


def test_process_termination_after_commit_is_idempotent_on_retry(tmp_path):
    target = make_graph(tmp_path / "d.db")
    source = make_graph(tmp_path / "s.db", "s")
    proc = child('''
import os,sys
from sqlite_utils import Database
db=Database(sys.argv[1])
db.merge_preserving_keys([("new",sys.argv[2])],tables=["people","teams"])
os._exit(78)
''', target, source)
    assert proc.returncode == 78, proc.stderr
    before = dump(target)
    assert merge(target, [("new", source)])["rows_inserted"] == 0
    assert dump(target) == before


def test_empty_sources_have_identity_and_maps(tmp_path):
    target = make_graph(tmp_path / "d.db")
    source = make_graph(tmp_path / "s.db")
    result = merge(target, [("empty", source)])
    assert result == {"rows_inserted": 0, "sources": {"empty": {
        "skipped": False, "rows_inserted": {"people": 0, "teams": 0},
        "key_map": {"people": [], "teams": []},
    }}}
    assert merge(target, [("empty", source)])["sources"]["empty"]["skipped"] is True


@pytest.mark.parametrize("problem", ["missing_parent_selection", "missing_destination", "source_trigger", "schema", "dangling_source", "invalid_destination", "same_file"])
def test_rejected_graphs_leave_no_artifacts(tmp_path, problem):
    target = make_graph(tmp_path / "d.db", "d", destination=True)
    source = make_graph(tmp_path / "s.db", "s")
    tables = TABLES
    if problem == "missing_parent_selection":
        tables = ["people"]
    elif problem == "missing_destination":
        tables = ["missing"]
    elif problem == "same_file":
        source = tmp_path / "alias.db"
        source.symlink_to(target)
    else:
        path = target if problem == "invalid_destination" else source
        with sqlite3.connect(path) as conn:
            if problem == "source_trigger":
                conn.execute("CREATE TRIGGER source_audit AFTER INSERT ON people BEGIN SELECT 1; END")
            elif problem == "schema":
                conn.execute("ALTER TABLE people ADD COLUMN extra TEXT")
            else:
                conn.execute("UPDATE people SET team_id=9999")
    before = dump(target)
    with pytest.raises((ValueError, sqlite3.Error)):
        merge(target, [("s", source)], tables)
    assert dump(target) == before


@pytest.mark.parametrize("schema", [
    "CREATE TABLE t (id TEXT PRIMARY KEY, parent TEXT)",
    "CREATE TABLE t (id INTEGER, second INTEGER, PRIMARY KEY(id,second))",
    "CREATE TABLE t (id INTEGER PRIMARY KEY) WITHOUT ROWID",
    "CREATE TABLE t (id INTEGER PRIMARY KEY, doubled INTEGER GENERATED ALWAYS AS (id*2))",
    "CREATE VIRTUAL TABLE t USING fts5(value)",
    "CREATE TABLE t (id INTEGER PRIMARY KEY REFERENCES t(id))",
    "CREATE TABLE t (id INTEGER PRIMARY KEY, parent INTEGER REFERENCES t(id) REFERENCES t(id))",
    "CREATE TABLE t (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER, UNIQUE(a,b), FOREIGN KEY(a,b) REFERENCES t(a,b))",
])
def test_unsupported_key_and_table_layouts(schema, tmp_path):
    source, target = tmp_path / "s.db", tmp_path / "d.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.executescript(schema)
    before = dump(target)
    with pytest.raises(ValueError):
        merge(target, [("s", source)], ["t"])
    assert dump(target) == before


def test_negative_ids_implicit_fk_quoted_identifiers_and_autoincrement(tmp_path):
    schema = 'CREATE TABLE "node space" ("key" INTEGER PRIMARY KEY AUTOINCREMENT, "parent" INTEGER REFERENCES "node space", "note" TEXT)'
    source, target = tmp_path / "s.db", tmp_path / "d.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.execute(schema)
    with sqlite3.connect(target) as conn:
        conn.execute('INSERT INTO "node space" VALUES (100,NULL,\'deleted\')')
        conn.execute('DELETE FROM "node space"')
    with sqlite3.connect(source) as conn:
        conn.executemany('INSERT INTO "node space" VALUES (?,?,?)', [(-8, 0, "negative"), (0, -8, "zero")])
    result = merge(target, [("s", source)], ["node space"])
    assert result["sources"]["s"]["key_map"] == {"node space": [[-8, 101], [0, 102]]}
    assert query(target, 'SELECT * FROM "node space" ORDER BY key') == [(101, 102, "negative"), (102, 101, "zero")]


def test_id_exhaustion_is_a_prewrite_error(tmp_path):
    source, target = tmp_path / "s.db", tmp_path / "d.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO t VALUES (?)", [9223372036854775807 if path == target else 1])
    before = dump(target)
    with pytest.raises(ValueError):
        merge(target, [("s", source)], ["t"])
    assert dump(target) == before


@pytest.mark.parametrize("variant", ["no_sources", "duplicate_source", "empty_id", "duplicate_table", "no_tables", "reserved", "missing_path"])
def test_invalid_arguments_do_not_mutate(tmp_path, variant):
    source = make_graph(tmp_path / "s.db", "s")
    target = make_graph(tmp_path / "d.db")
    sources, tables = [("s", source)], TABLES
    if variant == "no_sources": sources = []
    elif variant == "duplicate_source": sources *= 2
    elif variant == "empty_id": sources = [("", source)]
    elif variant == "duplicate_table": tables = ["people", "PEOPLE"]
    elif variant == "no_tables": tables = []
    elif variant == "reserved": tables = ["_sqlite_utils_merge_keys"]
    elif variant == "missing_path": sources = [("s", tmp_path / "missing.db")]
    before = dump(target)
    with pytest.raises((ValueError, TypeError, OSError)):
        merge(target, sources, tables)
    assert dump(target) == before


def test_cli_failure_is_concise_and_atomic(tmp_path):
    source = make_graph(tmp_path / "s.db", "s")
    target = make_graph(tmp_path / "d.db")
    before = dump(target)
    proc = run_cli("merge-preserving-keys", target, "--source", "s", source, "--table", "people")
    assert proc.returncode != 0
    assert "Error:" in proc.stderr and "Traceback" not in proc.stderr
    assert dump(target) == before


def test_changed_valid_table_selection_rejected_for_existing_id(tmp_path):
    source, target = tmp_path / "s.db", tmp_path / "d.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY); CREATE TABLE u (id INTEGER PRIMARY KEY)")
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO t VALUES (1)")
        conn.execute("INSERT INTO u VALUES (1)")
    merge(target, [("s", source)], ["t"])
    before = dump(target)
    with pytest.raises(ValueError):
        merge(target, [("s", source)], ["u", "t"])
    assert dump(target) == before


def test_sqlite_numeric_storage_values_are_preserved(tmp_path):
    source, target = tmp_path / "s.db", tmp_path / "d.db"
    for path in (source, target):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, measurement REAL, optional REAL, data BLOB)")
    with sqlite3.connect(source) as conn:
        conn.executemany("INSERT INTO t VALUES (?,?,?,?)", [(1, 1.25, None, b"\x00\xff"), (2, float("inf"), -8.5, b"")])
    merge(target, [("s", source)], ["t"])
    assert query(target, "SELECT measurement,optional,data FROM t ORDER BY id") == query(source, "SELECT measurement,optional,data FROM t ORDER BY id")

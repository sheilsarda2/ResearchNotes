"""Import independent SQLite ID spaces without changing their relationships."""

import hashlib
import json
import os
from pathlib import Path

from .db import fold_identifier_case, quote_identifier, resolve_casing
from .utils import sqlite3


SOURCES = "_sqlite_utils_merge_sources"
KEYS = "_sqlite_utils_merge_keys"
MAX_ID = 9223372036854775807


def _names(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def _inspect(conn, name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", [name]
    ).fetchone()
    if not row or not row[0] or row[0].upper().startswith("CREATE VIRTUAL TABLE"):
        raise ValueError(f"{name!r} must be an ordinary existing table")
    info = conn.execute(f"PRAGMA table_xinfo({quote_identifier(name)})").fetchall()
    if any(col[6] for col in info):
        raise ValueError(f"Generated/hidden columns are unsupported in {name!r}")
    pks = [i for i, col in enumerate(info) if col[5]]
    indexes = conn.execute(f"PRAGMA index_list({quote_identifier(name)})").fetchall()
    if len(pks) != 1 or info[pks[0]][2].upper() != "INTEGER" or any(i[3] == "pk" for i in indexes):
        raise ValueError(f"{name!r} must have one INTEGER PRIMARY KEY that aliases rowid")
    fk_rows = conn.execute(f"PRAGMA foreign_key_list({quote_identifier(name)})").fetchall()
    if len({r[0] for r in fk_rows}) != len(fk_rows):
        raise ValueError(f"Compound foreign keys are unsupported in {name!r}")
    return {
        "name": name, "sql": row[0], "columns": [c[1] for c in info],
        "signature": [tuple(c[1:6]) for c in info], "pk": pks[0], "raw_fks": fk_rows,
    }


def _resolve_fks(specs):
    for name, spec in specs.items():
        fks = []
        seen = set()
        for fk in spec["raw_fks"]:
            parent = resolve_casing(fk[2], specs)
            if parent not in specs:
                raise ValueError(f"Foreign key from {name!r} leaves the selected table set")
            parent_spec = specs[parent]
            child = resolve_casing(fk[3], spec["columns"])
            target = resolve_casing(fk[4], parent_spec["columns"]) if fk[4] else parent_spec["columns"][parent_spec["pk"]]
            if child not in spec["columns"] or target != parent_spec["columns"][parent_spec["pk"]]:
                raise ValueError(f"Foreign keys in {name!r} must reference selected INTEGER primary keys")
            position = spec["columns"].index(child)
            if position == spec["pk"] or position in seen:
                raise ValueError(f"Shared-primary-key or multiply constrained FK columns are unsupported in {name!r}")
            seen.add(position)
            fks.append((position, parent, fk[5], fk[6], fk[7]))
        spec["fks"] = sorted(fks)


def _typed(value):
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    return ["text", value]


def _source_snapshot(path, selected, target_specs):
    path = Path(path)
    if not path.is_file():
        raise ValueError("Each source must be a regular existing SQLite file")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        source_names = _names(conn)
        specs = {}
        for name in selected:
            actual = resolve_casing(name, source_names)
            spec = _inspect(conn, actual)
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", [actual]
            ).fetchone():
                raise ValueError(f"Source table {actual!r} has unsupported triggers")
            specs[name] = spec
        # Referenced names are resolved to destination canonical names.
        _resolve_fks(specs)
        payload = []
        for name, spec in specs.items():
            dest = target_specs[name]
            if spec["signature"] != dest["signature"] or spec["fks"] != dest["fks"]:
                raise ValueError(f"Source schema for {name!r} does not match the destination")
            if conn.execute(f"PRAGMA foreign_key_check({quote_identifier(spec['name'])})").fetchone():
                raise ValueError(f"Source table {name!r} contains a broken foreign key")
            columns = ", ".join(quote_identifier(c) for c in spec["columns"])
            spec["rows"] = conn.execute(
                f"SELECT {columns} FROM {quote_identifier(spec['name'])} "
                f"ORDER BY {quote_identifier(spec['columns'][spec['pk']])}"
            ).fetchall()
            for row in spec["rows"]:
                for position, _, *_ in spec["fks"]:
                    if row[position] is not None and not isinstance(row[position], int):
                        raise ValueError(f"Foreign key values in {name!r} must be integers or null")
            index_sql = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
                [spec["name"]],
            ).fetchall()
            payload.append([name, spec["sql"], index_sql, [[_typed(v) for v in row] for row in spec["rows"]]])
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return specs, digest
    finally:
        conn.close()


def _prior(conn, source_id):
    if SOURCES not in _names(conn):
        return None
    return conn.execute(
        "SELECT fingerprint, tables_json FROM _sqlite_utils_merge_sources WHERE source_id=?", [source_id]
    ).fetchone()


def _saved_maps(conn, source_id, selected):
    result = {name: [] for name in selected}
    for name, old, new in conn.execute(
        "SELECT table_name, old_id, new_id FROM _sqlite_utils_merge_keys WHERE source_id=? ORDER BY table_name, old_id",
        [source_id],
    ):
        result[name].append([old, new])
    return result


def merge_preserving_keys(db, sources, *, tables):
    # Even a completed read transaction can clear defer_foreign_keys outside
    # an explicit transaction, so capture it before schema introspection.
    foreign_keys = bool(db.conn.execute("PRAGMA foreign_keys").fetchone()[0])
    deferred = bool(db.conn.execute("PRAGMA defer_foreign_keys").fetchone()[0])
    try:
        return _merge_preserving_keys(db, sources, tables=tables)
    finally:
        db.conn.execute(f"PRAGMA foreign_keys={int(foreign_keys)}")
        db.conn.execute(f"PRAGMA defer_foreign_keys={int(deferred)}")


def _merge_preserving_keys(db, sources, *, tables):
    """Plan source snapshots, then commit imported graphs and provenance together."""
    if isinstance(tables, str):
        raise ValueError("tables must be a non-empty iterable of table names")
    selected = list(tables)
    if not selected or any(not isinstance(t, str) or not t for t in selected):
        raise ValueError("tables must contain non-empty names")
    if len({fold_identifier_case(t) for t in selected}) != len(selected):
        raise ValueError("tables cannot contain duplicate identifiers")
    if any(fold_identifier_case(t) in (SOURCES, KEYS) or fold_identifier_case(t).startswith("sqlite_") for t in selected):
        raise ValueError("Internal metadata tables cannot be merged")
    existing = _names(db.conn)
    selected = sorted(resolve_casing(t, existing) for t in selected)
    target_specs = {name: _inspect(db.conn, name) for name in selected}
    _resolve_fks(target_specs)
    if db.conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Destination already contains a foreign key violation")
    entries = list(sources)
    if not entries:
        raise ValueError("At least one source is required")
    normalized = {}
    main_path = next((r[2] for r in db.conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError("Each source must be a (source_id, path) pair")
        source_id, path = entry
        if not isinstance(source_id, str) or not source_id or source_id in normalized:
            raise ValueError("Source IDs must be non-empty and distinct")
        path = Path(path)
        if main_path and path.exists() and os.path.samefile(path, main_path):
            raise ValueError("The destination cannot also be a source")
        normalized[source_id] = path
    tables_json = json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    snapshots = {}
    for source_id, path in sorted(normalized.items()):
        spec, fingerprint = _source_snapshot(path, selected, target_specs)
        snapshots[source_id] = (spec, fingerprint)

    # All writes, including those for several sources, share this transaction.
    defer_was_on = bool(db.conn.execute("PRAGMA defer_foreign_keys").fetchone()[0])
    result = {"rows_inserted": 0, "sources": {}}
    try:
        with db.atomic():
            db.conn.execute("PRAGMA defer_foreign_keys=ON")
            plans = {}
            next_ids = {}
            for name, spec in target_specs.items():
                maximum = db.conn.execute(
                    f"SELECT MAX({quote_identifier(spec['columns'][spec['pk']])}) FROM {quote_identifier(name)}"
                ).fetchone()[0]
                if "sqlite_sequence" in existing:
                    sequence = db.conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", [name]).fetchone()
                    if sequence:
                        maximum = max(maximum or 0, sequence[0])
                next_ids[name] = max(0, maximum or 0) + 1
            for source_id, (specs, fingerprint) in snapshots.items():
                prior = _prior(db.conn, source_id)
                if prior is not None:
                    if tuple(prior) != (fingerprint, tables_json):
                        raise ValueError(f"Source identity mismatch for {source_id!r}")
                    result["sources"][source_id] = {
                        "skipped": True, "rows_inserted": {n: 0 for n in selected},
                        "key_map": _saved_maps(db.conn, source_id, selected),
                    }
                    continue
                maps = {}
                for name, spec in specs.items():
                    mapping = {}
                    for row in spec["rows"]:
                        if next_ids[name] > MAX_ID:
                            raise ValueError(f"No fresh INTEGER primary keys remain for {name!r}")
                        mapping[row[spec["pk"]]] = next_ids[name]
                        next_ids[name] += 1
                    maps[name] = mapping
                rewritten = {}
                for name, spec in specs.items():
                    new_rows = []
                    for row in spec["rows"]:
                        new = list(row)
                        new[spec["pk"]] = maps[name][row[spec["pk"]]]
                        for position, parent, *_ in spec["fks"]:
                            if row[position] is not None:
                                new[position] = maps[parent][row[position]]
                        new_rows.append(tuple(new))
                    rewritten[name] = new_rows
                plans[source_id] = (fingerprint, maps, rewritten)
                counts = {name: len(values) for name, values in rewritten.items()}
                result["sources"][source_id] = {
                    "skipped": False, "rows_inserted": counts,
                    "key_map": {name: [[old, new] for old, new in mapping.items()] for name, mapping in maps.items()},
                }
                result["rows_inserted"] += sum(counts.values())
            if plans:
                db.execute("CREATE TABLE IF NOT EXISTS _sqlite_utils_merge_sources (source_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, tables_json TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS _sqlite_utils_merge_keys (source_id TEXT NOT NULL REFERENCES _sqlite_utils_merge_sources(source_id) ON DELETE CASCADE, table_name TEXT NOT NULL, old_id INTEGER NOT NULL, new_id INTEGER NOT NULL, PRIMARY KEY(source_id, table_name, old_id))")
            for source_id, (fingerprint, maps, rewritten) in plans.items():
                for name, values in rewritten.items():
                    spec = target_specs[name]
                    columns = ", ".join(quote_identifier(c) for c in spec["columns"])
                    placeholders = ", ".join("?" for _ in spec["columns"])
                    for row in values:
                        db.execute(f"INSERT INTO {quote_identifier(name)} ({columns}) VALUES ({placeholders})", row)
                db.execute(
                    "INSERT INTO _sqlite_utils_merge_sources (source_id, fingerprint, tables_json) VALUES (?, ?, ?)",
                    [source_id, fingerprint, tables_json],
                )
                for name, mapping in maps.items():
                    for old, new in mapping.items():
                        db.execute(
                            "INSERT INTO _sqlite_utils_merge_keys (source_id, table_name, old_id, new_id) VALUES (?, ?, ?, ?)",
                            [source_id, name, old, new],
                        )
            if db.conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("Merged database contains a foreign key violation")
            # A destination trigger must not silently suppress or rewrite imported rows.
            for _, _, rewritten in plans.values():
                for name, values in rewritten.items():
                    spec = target_specs[name]
                    columns = ", ".join(quote_identifier(c) for c in spec["columns"])
                    for expected in values:
                        actual = db.conn.execute(
                            f"SELECT {columns} FROM {quote_identifier(name)} WHERE {quote_identifier(spec['columns'][spec['pk']])}=?",
                            [expected[spec["pk"]]],
                        ).fetchone()
                        if actual is None or [_typed(v) for v in actual] != [_typed(v) for v in expected]:
                            raise sqlite3.IntegrityError("A trigger changed or suppressed an imported row")
    finally:
        db.conn.execute(f"PRAGMA defer_foreign_keys={int(defer_was_on)}")
    return result

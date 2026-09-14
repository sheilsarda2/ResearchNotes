"""Durable, bounded-memory imports of immutable local NDJSON files."""

import hashlib
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from .db import fold_identifier_case

if TYPE_CHECKING:
    from .db import Table


IMPORTS_TABLE = "_sqlite_utils_imports"


def _state(conn, import_id):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [IMPORTS_TABLE]
    ).fetchone()
    if not exists:
        return None
    row = conn.execute(
        "SELECT table_name, source_sha256, options_json, byte_offset, "
        "rows_committed, completed FROM _sqlite_utils_imports WHERE import_id=?",
        [import_id],
    ).fetchone()
    return tuple(row) if row is not None else None


def _result(import_id, offset, count, completed):
    return {
        "import_id": import_id,
        "byte_offset": offset,
        "rows_committed": count,
        "completed": bool(completed),
    }


def _reject_constant(value):
    raise ValueError(f"Non-JSON number {value}")


def _read_batch(stream: BinaryIO, size: int, batch_size: int):
    rows = []
    while len(rows) < batch_size:
        start = stream.tell()
        line = stream.readline()
        if not line:
            break
        try:
            text = line.decode("utf-8-sig" if start == 0 else "utf-8")
            if not text.strip():
                continue
            record = json.loads(text, parse_constant=_reject_constant)
            if not isinstance(record, dict):
                raise ValueError("expected a JSON object")
        except (UnicodeError, ValueError) as ex:
            raise ValueError(f"Invalid NDJSON at byte {start}: {ex}") from ex
        rows.append(record)
    offset = stream.tell()
    return rows, offset, offset == size


def insert_file_resumable(
    table: "Table",
    path: str | os.PathLike,
    *,
    import_id: str,
    batch_size: int = 100,
    pk: str | list[str] | tuple[str, ...] | None = None,
    alter: bool = False,
    upsert: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    from .db import TransactionError

    if not isinstance(import_id, str) or not import_id:
        raise ValueError("import_id must be a non-empty string")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if not isinstance(alter, bool) or not isinstance(upsert, bool):
        raise ValueError("alter and upsert must be booleans")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be callable")
    if pk is not None:
        if isinstance(pk, str):
            pk = [pk]
        elif isinstance(pk, (list, tuple)):
            pk = list(pk)
        else:
            raise ValueError("pk must be a column name or sequence of column names")
        if not pk or any(not isinstance(c, str) or not c for c in pk):
            raise ValueError("pk must contain non-empty column names")
        if len({fold_identifier_case(c) for c in pk}) != len(pk):
            raise ValueError("pk cannot contain duplicate columns")
    if fold_identifier_case(table.name) == IMPORTS_TABLE:
        raise ValueError(f"{IMPORTS_TABLE} is reserved for import checkpoints")
    db = table.db
    if db.conn.in_transaction:
        raise TransactionError("Resumable imports cannot run inside a transaction")
    if fold_identifier_case(table.name) in {
        fold_identifier_case(name) for name in db.view_names()
    } or (table.exists() and table.virtual_table_using):
        raise ValueError("Resumable imports require an ordinary table")
    path = Path(path)
    if not path.is_file():
        raise ValueError("Resumable imports require a regular local file")
    options = json.dumps(
        {"pk": pk, "alter": alter, "upsert": upsert},
        sort_keys=True,
        separators=(",", ":"),
    )
    identity_table = fold_identifier_case(table.name)
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Resumable imports require a regular local file")
        size = os.fstat(stream.fileno()).st_size
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        fingerprint = digest.hexdigest()
        prior = _state(db.conn, import_id)
        identity = (identity_table, fingerprint, options)
        if prior is not None and prior[:3] != identity:
            raise ValueError("Import identity mismatch: table, source bytes or options changed")
        offset, count, completed = prior[3:] if prior else (0, 0, False)
        if offset < 0 or offset > size or count < 0:
            raise ValueError("Invalid stored import checkpoint")
        if completed:
            return _result(import_id, offset, count, True)
        stream.seek(offset)
        while True:
            rows, next_offset, completed = _read_batch(stream, size, batch_size)
            next_count = count + len(rows)
            with db.atomic():
                # Another importer must not be able to advance a stale checkpoint.
                if _state(db.conn, import_id) != prior:
                    raise ValueError("Import progress changed; retry from its new checkpoint")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS _sqlite_utils_imports ("
                    "import_id TEXT PRIMARY KEY, table_name TEXT NOT NULL, "
                    "source_sha256 TEXT NOT NULL, options_json TEXT NOT NULL, "
                    "byte_offset INTEGER NOT NULL, rows_committed INTEGER NOT NULL, "
                    "completed INTEGER NOT NULL CHECK(completed IN (0, 1)))"
                )
                if rows:
                    table.insert_all(
                        rows,
                        pk=pk,
                        alter=alter,
                        upsert=upsert,
                        batch_size=batch_size,
                    )
                db.execute(
                    "INSERT INTO _sqlite_utils_imports "
                    "(import_id, table_name, source_sha256, options_json, "
                    "byte_offset, rows_committed, completed) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(import_id) DO UPDATE SET "
                    "byte_offset=excluded.byte_offset, rows_committed=excluded.rows_committed, "
                    "completed=excluded.completed",
                    [import_id, *identity, next_offset, next_count, int(completed)],
                )
            offset, count = next_offset, next_count
            prior = (*identity, offset, count, int(completed))
            result = _result(import_id, offset, count, completed)
            if progress is not None:
                progress(dict(result))
            if completed:
                return result

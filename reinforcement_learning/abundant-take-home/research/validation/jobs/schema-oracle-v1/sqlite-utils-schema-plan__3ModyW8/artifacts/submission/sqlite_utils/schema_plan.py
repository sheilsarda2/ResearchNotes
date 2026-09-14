"""Coordinated schema evolution with native dependency rewriting.

Column definitions are edited using the repository's existing lossless lexer.
Views, triggers, indexes, expressions and foreign keys are never text-rewritten.
"""

import math
from contextlib import contextmanager
from collections.abc import Mapping
import re
from decimal import Decimal, InvalidOperation

from .create_table_parser import (
    _lex,
    _meaningful,
    _matching_paren,
    _split_spans,
    _table_body,
    _unquote,
)
from .db import TransformError, fold_identifier_case as fold, quote_identifier as q
from .utils import sqlite3

_TYPES = {int: "INTEGER", float: "REAL", str: "TEXT", bytes: "BLOB"}
_NUMERIC = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_OPTIONS = {"rename", "drop", "types", "not_null", "defaults", "column_order", "strict"}
_CONSTRAINTS = {
    "CONSTRAINT",
    "PRIMARY",
    "NOT",
    "NULL",
    "UNIQUE",
    "CHECK",
    "DEFAULT",
    "COLLATE",
    "REFERENCES",
    "GENERATED",
    "AS",
}


def _resolve(name, names):
    if not isinstance(name, str):
        raise ValueError("Identifiers must be strings")
    matches = [n for n in names if fold(n) == fold(name)]
    if not matches:
        raise ValueError("Unknown identifier: {!r}".format(name))
    return matches[0]


def _references(values, names, mapping=False):
    if not isinstance(values, Mapping if mapping else list):
        raise ValueError("Expected {}".format("a mapping" if mapping else "a list"))
    resolved = [_resolve(n, names) for n in values]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate column references")
    return dict(zip(resolved, values.values())) if mapping else resolved


def _validate(db, plan):
    if not isinstance(plan, Mapping) or not plan:
        raise ValueError("plan must be a nonempty mapping")
    tables = db.table_names()
    output = {}
    for given, op in plan.items():
        name = _resolve(given, tables)
        if name in output:
            raise ValueError("Duplicate table reference: " + name)
        if not isinstance(op, Mapping) or set(op) - _OPTIONS:
            raise ValueError("Invalid operations for " + name)
        table = db[name]
        info = list(db.execute("PRAGMA main.table_xinfo({})".format(q(name))))
        kind = next(r for r in db.execute("PRAGMA main.table_list") if r[1] == name)
        if kind[2] != "table" or kind[4] or any(r[6] for r in info):
            raise ValueError(
                "Only ordinary rowid tables without generated columns are supported: "
                + name
            )
        names = [r[1] for r in info]
        if not any(
            fold(a) not in {fold(n) for n in names} for a in ("rowid", "_rowid_", "oid")
        ):
            raise ValueError("All hidden rowid aliases are shadowed: " + name)
        normalized = {}
        for key, value in op.items():
            if key == "strict":
                if type(value) is not bool:
                    raise ValueError("strict must be boolean")
                normalized[key] = value
            else:
                normalized[key] = _references(
                    value, names, key not in ("drop", "column_order")
                )
        rename = normalized.get("rename", {})
        for value in rename.values():
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("Invalid final column name")
        dropped = set(normalized.get("drop", []))
        for key in ("rename", "types", "not_null", "defaults", "column_order"):
            if dropped.intersection(normalized.get(key, {})):
                raise ValueError("Dropped column also referenced by " + key)
        final = [rename.get(n, n) for n in names if n not in dropped]
        if not final or len({fold(n) for n in final}) != len(final):
            raise ValueError("Final column names must be nonempty and unique")
        if not any(
            fold(a) not in {fold(n) for n in final} for a in ("rowid", "_rowid_", "oid")
        ):
            raise ValueError("Final schema shadows all hidden rowid aliases")
        pks = {r[1] for r in info if r[5]}
        if pks.intersection(normalized.get("types", {})) or pks.intersection(
            normalized.get("not_null", {})
        ):
            raise ValueError("Cannot change primary key type or nullability")
        for col, typ in normalized.get("types", {}).items():
            typ = _TYPES.get(typ, typ) if isinstance(typ, (str, type)) else None
            if not isinstance(typ, str) or typ.upper() not in _TYPES.values():
                raise ValueError("Unsupported target type for " + col)
            normalized["types"][col] = typ.upper()
        if any(type(v) is not bool for v in normalized.get("not_null", {}).values()):
            raise ValueError("not_null values must be boolean")
        for value in normalized.get("defaults", {}).values():
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError("Defaults must be literal JSON scalar values")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Default numbers must be finite")
            if type(value) is int and not -(2**63) <= value < 2**63:
                raise ValueError("Default integer outside signed 64-bit range")
        output[name] = normalized
    return output


def _convert(db, value, target):
    if value is None:
        return None
    if target in ("INTEGER", "REAL"):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if not _NUMERIC.fullmatch(value):
                raise ValueError("Invalid decimal numeric string")
            value = Decimal(value)
        if not isinstance(value, (int, float, Decimal)):
            raise ValueError("Numeric conversion does not accept blobs")
        if target == "REAL":
            result = float(value)
            if not math.isfinite(result):
                raise ValueError("Non-finite REAL")
            return result
        if (
            not Decimal(value).is_finite()
            or not -(2**63) <= value < 2**63
            or value != int(value)
        ):
            raise ValueError("INTEGER requires an integral signed 64-bit value")
        return int(value)
    if target == "TEXT":
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if isinstance(value, str):
            return value
        return db.execute("SELECT CAST(? AS TEXT)", [value]).fetchone()[0]
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        return value
    raise ValueError("BLOB conversion requires text or blob")


def _column_sql(db, item, name, op):
    tokens = _meaningful(_lex(item))
    edits = []
    if name in op.get("types", {}):
        boundary = next(
            (
                i
                for i in range(1, len(tokens))
                if tokens[i].kind == "word" and tokens[i].text.upper() in _CONSTRAINTS
            ),
            len(tokens),
        )
        end = tokens[boundary].start if boundary < len(tokens) else len(item)
        edits.append((tokens[0].end, end, " " + op["types"][name] + " "))
    found_not_null = False
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.text == "(":
            i = _matching_paren(tokens, i) + 1
            continue
        default = token.is_keyword("DEFAULT")
        not_null = (
            token.is_keyword("NOT")
            and i + 1 < len(tokens)
            and tokens[i + 1].is_keyword("NULL")
        )
        if not default and not not_null:
            i += 1
            continue
        start = (
            tokens[i - 2].start
            if i >= 2 and tokens[i - 2].is_keyword("CONSTRAINT")
            else token.start
        )
        if not_null:
            found_not_null = True
            end_index = i + 2
            if (
                end_index + 2 < len(tokens)
                and tokens[end_index].is_keyword("ON")
                and tokens[end_index + 1].is_keyword("CONFLICT")
            ):
                end_index += 3
            if op.get("not_null", {}).get(name) is False:
                edits.append((start, tokens[end_index - 1].end, ""))
        else:
            end_index = i + 1
            if tokens[end_index].text == "(":
                end_index = _matching_paren(tokens, end_index) + 1
            else:
                if tokens[end_index].text in ("+", "-"):
                    end_index += 1
                if (
                    tokens[end_index].is_keyword("X")
                    and end_index + 1 < len(tokens)
                    and tokens[end_index + 1].kind == "string"
                ):
                    end_index += 1
                end_index += 1
            if name in op.get("defaults", {}):
                edits.append((start, tokens[end_index - 1].end, ""))
        i = end_index
    for start, end, replacement in sorted(edits, reverse=True):
        item = item[:start] + replacement + item[end:]
    # Newlines ensure a trailing SQL line comment cannot swallow new constraints.
    if op.get("not_null", {}).get(name) is True and not found_not_null:
        item += "\nNOT NULL"
    if name in op.get("defaults", {}) and op["defaults"][name] is not None:
        value = op["defaults"][name]
        literal = db.quote(value)
        if isinstance(value, str) and "\x00" in value:
            literal = "(CAST(X'{}' AS TEXT))".format(value.encode("utf-8").hex())
        item += "\nDEFAULT " + literal
    return item


def _create_sql(db, table, temporary, op):
    sql = db[table].schema
    body, start = _table_body(sql)
    names = [c.name for c in db[table].columns]
    columns, constraints = {}, []
    for item, _, _ in _split_spans(body, _lex(body)):
        tokens = _meaningful(_lex(item))
        first = _unquote(tokens[0].text)
        table_constraint = tokens[0].kind == "word" and tokens[0].text.upper() in {
            "CONSTRAINT",
            "PRIMARY",
            "UNIQUE",
            "CHECK",
            "FOREIGN",
        }
        if first in names and not table_constraint:
            columns[first] = _column_sql(db, item, first, op)
        else:
            constraints.append(item)
    order = op.get("column_order", [])
    ordered = order + [n for n in names if n not in order]
    strict = op.get("strict", db[table].strict)
    return "CREATE TABLE {} (\n{}\n){}".format(
        q(temporary),
        "\n,\n".join([columns[n] for n in ordered] + constraints),
        " STRICT" if strict else "",
    )


def _typed(rows):
    return [[(type(v), v) for v in row] for row in rows]


@contextmanager
def _write_transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _execute(db, plan):
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("PRAGMA legacy_alter_table=OFF")
    with _write_transaction(db):
        plan = _validate(db, plan)
        if list(db.execute("PRAGMA foreign_key_check")):
            raise ValueError("Input database has invalid foreign keys")
        for table, op in sorted(plan.items()):
            names = [c.name for c in db[table].columns]
            used = {fold(n) for n in names + list(op.get("rename", {}).values())}
            renames = []
            for index, (old, new) in enumerate(op.get("rename", {}).items()):
                temp = "__schema_plan_column_{}".format(index)
                while fold(temp) in used:
                    temp += "_"
                used.add(fold(temp))
                db.execute(
                    "ALTER TABLE {} RENAME COLUMN {} TO {}".format(
                        q(table), q(old), q(temp)
                    )
                )
                renames.append((temp, new))
            for old, new in renames:
                db.execute(
                    "ALTER TABLE {} RENAME COLUMN {} TO {}".format(
                        q(table), q(old), q(new)
                    )
                )
        for table, op in sorted(plan.items()):
            for column in op.get("drop", []):
                db.execute("ALTER TABLE {} DROP COLUMN {}".format(q(table), q(column)))
        objects = list(
            db.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND sql IS NOT NULL ORDER BY type, name"
            )
        )
        restore = [r for r in objects if r[2] in plan]
        sequence = (
            dict(db.execute("SELECT name,seq FROM sqlite_sequence"))
            if "sqlite_sequence" in db.table_names()
            else {}
        )
        db.execute("PRAGMA legacy_alter_table=ON")
        for table, raw_op in sorted(plan.items()):
            rename = raw_op.get("rename", {})
            op = {
                k: (
                    {rename.get(c, c): v for c, v in val.items()}
                    if isinstance(val, dict)
                    else (
                        [rename.get(c, c) for c in val]
                        if isinstance(val, list)
                        else val
                    )
                )
                for k, val in raw_op.items()
                if k not in ("rename", "drop")
            }
            names = [c.name for c in db[table].columns]
            alias = next(
                a
                for a in ("rowid", "_rowid_", "oid")
                if fold(a) not in {fold(n) for n in names}
            )
            fields = ",".join(q(n) for n in [alias] + names)
            rows = list(
                db.execute(
                    "SELECT {} FROM {} ORDER BY {}".format(fields, q(table), q(alias))
                )
            )
            converted = []
            for row in rows:
                converted.append(
                    tuple(
                        [row[0]]
                        + [
                            (
                                _convert(db, v, op["types"][c])
                                if c in op.get("types", {})
                                else v
                            )
                            for c, v in zip(names, row[1:])
                        ]
                    )
                )
            temporary = "__schema_plan_rebuild"
            while db.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? COLLATE NOCASE", [temporary]
            ).fetchone():
                temporary += "_"
            db.execute(_create_sql(db, table, temporary, op))
            db.conn.executemany(
                "INSERT INTO {} ({}) VALUES ({})".format(
                    q(temporary), fields, ",".join("?" for _ in range(len(names) + 1))
                ),
                converted,
            )
            actual = list(
                db.execute(
                    "SELECT {} FROM {} ORDER BY {}".format(
                        fields, q(temporary), q(alias)
                    )
                )
            )
            if _typed(actual) != _typed(converted):
                raise ValueError("Rebuild changed or discarded rows in " + table)
            db.execute("DROP TABLE " + q(table))
            db.execute("ALTER TABLE {} RENAME TO {}".format(q(temporary), q(table)))
        for _, _, _, sql in restore:
            db.execute(sql)
        for name, seq in sequence.items():
            if name in plan:
                db.execute(
                    "UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?",
                    [seq, name],
                )
                db.execute(
                    "INSERT INTO sqlite_sequence(name,seq) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name=?)",
                    [name, seq, name],
                )
        if list(db.execute("PRAGMA foreign_key_check")):
            raise ValueError("Plan would leave invalid foreign keys")
        if [r[0] for r in db.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ValueError("Plan would leave invalid database constraints")
        for name in db.view_names():
            db.execute("SELECT * FROM {} LIMIT 0".format(q(name))).fetchall()
        return {
            "tables": [
                {
                    "name": t,
                    "rows": db[t].count,
                    "columns": [c.name for c in db[t].columns],
                }
                for t in sorted(plan)
            ],
            "schema": [
                dict(zip(("type", "name", "table", "sql"), r))
                for r in db.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND substr(name,1,7) != 'sqlite_' ORDER BY type,name"
                )
            ],
        }


def transform_schema(db, plan, *, dry_run=False):
    if db.conn.in_transaction:
        raise TransformError(
            "transform_schema requires a connection outside a transaction"
        )
    # Read these before schema inspection: read transactions can reset defer_foreign_keys.
    settings = {
        key: db.execute("PRAGMA " + key).fetchone()[0]
        for key in ("defer_foreign_keys", "foreign_keys", "legacy_alter_table")
    }
    clone = None
    try:
        if type(dry_run) is not bool:
            raise ValueError("dry_run must be boolean")
        if dry_run:
            from .db import Database

            clone = sqlite3.connect(":memory:")
            db.conn.backup(clone)
            return _execute(Database(clone), plan)
        return _execute(db, plan)
    except (
        ValueError,
        TypeError,
        OverflowError,
        InvalidOperation,
        sqlite3.Error,
    ) as ex:
        raise TransformError(str(ex)) from ex
    finally:
        if clone is not None:
            clone.close()
        for key in ("foreign_keys", "legacy_alter_table", "defer_foreign_keys"):
            db.execute("PRAGMA {}={}".format(key, settings[key]))

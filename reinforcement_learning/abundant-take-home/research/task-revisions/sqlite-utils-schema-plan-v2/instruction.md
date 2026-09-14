# Apply a schema plan without breaking a live SQLite database

The checkout is sqlite-utils 4.2.1 at the supplied commit. Its single-table `transform()` can rebuild columns, but a combined change involving views, triggers, expression indexes and incoming foreign keys needs a coordinated operation. Add `Database.transform_schema(plan, *, dry_run=False)` and the command `sqlite-utils transform-schema DATABASE PLAN.json [--dry-run]`.

`plan` is a nonempty mapping from existing main-database table names to operation dictionaries. Support these operations, in any combination:

- `rename`: mapping of original column names to final names, including simultaneous swaps.
- `drop`: list of original column names. A drop is legal only if SQLite's native `ALTER TABLE ... DROP COLUMN` accepts it with dependencies present; do not silently remove dependent objects or constraints.
- `types`: mapping of original column names to `INTEGER`, `REAL`, `TEXT`, or `BLOB` (case insensitive). The Python API also accepts `int`, `float`, `str`, and `bytes`.
- `not_null`: mapping of original column names to booleans, adding or removing `NOT NULL`.
- `defaults`: mapping of original column names to literal strings, signed-64-bit integers, finite floats, booleans or null. Null removes the default. All strings are literal values, including `CURRENT_TIMESTAMP`, `NULL`, quotes and parentheses. Defaults affect future inserts; do not fill existing nulls. Unmentioned defaults, including SQL expressions, retain their meaning.
- `column_order`: a full or partial list of original column names. Put those surviving columns first, followed by the remaining columns in their previous relative order.
- `strict`: boolean, preserving the original setting when omitted.

Resolve table and column identifiers using SQLite's ASCII case-insensitive matching. All references in an operation are to the original schema, even when a column is renamed. Reject unknown/duplicate references, unknown operation names, conflicting drops and changes, invalid option types, final-name collisions, and changes to the type or nullability of any primary-key column. Renaming primary-key columns is supported. Do not add, remove or change primary keys. Empty operation dictionaries are allowed.

Convert values explicitly for columns in `types`: null stays null; an empty or whitespace-only string becomes null for numeric targets. INTEGER accepts signed-64-bit integers, finite integral floats and finite integral decimal numeric strings (including exponent notation), rejecting fractions and overflow. REAL accepts numbers and decimal numeric strings that produce finite doubles. Neither numeric target accepts blobs. TEXT preserves strings, strictly UTF-8 decodes blobs and uses SQLite `CAST(... AS TEXT)` for numbers. BLOB preserves blobs and UTF-8 encodes strings; reject numeric values. Reject invalid conversions before committing any change. Columns without a type operation preserve their values. Constraints still apply to converted rows: conversion must not silently discard or replace rows when a constraint has an `ON CONFLICT` policy.

Apply the entire plan in one transaction. Preserve all rows, hidden rowids, primary keys, AUTOINCREMENT high-water marks, CHECK/UNIQUE constraints, column collations, conflict policies, FK actions and deferrability. Preserve user indexes (including expression, partial and collated indexes), triggers, and views, including dependent views. Native SQLite column renaming is available and should handle identifier references without changing string literals, comments or explicit view output aliases. Renames must propagate into incoming foreign keys, trigger bodies and dependent views, even on tables outside the plan. Rebuilds must not fire business triggers or cascade-delete child rows. Existing trigger/index names remain stable; ordinary writes afterward must demonstrate that they still work. Finish with valid foreign keys and usable views. If the input database already has invalid foreign keys, reject it. No temporary rebuild objects may remain on either success or failure.

This API operates on main-database ordinary rowid tables using built-in SQLite functions/collations. Reject selected virtual tables, generated-column tables, WITHOUT ROWID tables, and tables that shadow all three hidden rowid aliases (`rowid`, `_rowid_`, `oid`). Other tables and objects may exist. Reject calls made inside an existing transaction (including dry runs); do not commit or roll back the caller's work. Dependencies that SQLite itself cannot rename/drop unambiguously may be rejected. TEMP objects and attached databases are outside the contract. Do not require a handwritten parser for view/trigger SQL. Reuse native ALTER behavior and existing repository facilities where useful.

Restore the connection's `foreign_keys`, `defer_foreign_keys` and `legacy_alter_table` settings to their entry values on success and failure. Execution acquires a write transaction before inspecting/changing schema; a concurrent writer may cause an ordinary SQLite busy error, with no partial change. Dry run performs the same data and dependency validation against an isolated snapshot, leaving the original database's schema, data, sequence values and file bytes unchanged. A dry run must work for a read-only database connection. Return the same JSON-compatible report for dry run and execution on unchanged input:

```json
{"tables": [{"name": "table", "rows": 2, "columns": ["id", "new_name"]}],
 "schema": [{"type": "table", "name": "table", "table": "table", "sql": "CREATE TABLE ..."}]}
```

`tables` lists planned tables sorted by actual name; columns are in final order. `schema` lists all main-schema objects having non-null SQL except names starting `sqlite_`, sorted by `(type, name)`, with final SQLite-stored SQL. Do not include temporary names or random data in the report. JSON object key order and SQL whitespace are not prescribed. Reapplying a plan uses the then-current schema; this is not a migration ledger.

Raise `sqlite_utils.db.TransformError` for invalid plans, unsupported schemas, conversion, constraint and dependency failures. On a failed real execution, every table, dependency and sequence must match its prior state. CLI success prints only the report as JSON; failure has a nonzero exit and a useful error on stderr, without a traceback. Existing APIs and commands remain compatible. Document the new API and command in the existing documentation.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/sqlite_utils/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.

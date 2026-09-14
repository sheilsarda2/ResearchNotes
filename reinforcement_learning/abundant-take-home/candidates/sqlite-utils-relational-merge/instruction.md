# Merge independent SQLite ID spaces without breaking relationships

This is sqlite-utils at commit `85b1be10c81d9dd3567e36faf8dd411e4a8789bd`. Offline copies of a database can reuse the same integer IDs for unrelated records. Add a real library API and CLI command that import selected related tables, allocate destination IDs, rewrite foreign keys, and atomically record enough provenance to make retries idempotent.

## Interfaces

Add `Database.merge_preserving_keys(sources, *, tables)`, where `sources` is a nonempty iterable of `(source_id, path)` pairs and `tables` is a nonempty iterable of table names. Each `source_id` is a distinct, nonempty, case-sensitive string. Paths name existing regular SQLite database files. The destination cannot also be a source, including through a symlink. Repeating a table using SQLite-equivalent identifier casing is invalid. Resolve table names using SQLite's identifier casing rules.

Add:

```text
sqlite-utils merge-preserving-keys DEST
    --source SOURCE_ID SOURCE_PATH [--source SOURCE_ID SOURCE_PATH ...]
    --table TABLE [--table TABLE ...]
```

The CLI must call the package API and emit its result as JSON. Invalid inputs, identity conflicts and database errors produce a nonzero exit and concise `Error:` message without a traceback. Document both interfaces. Existing APIs and commands must remain compatible.

The result is a dictionary of this form:

```json
{
  "rows_inserted": 2,
  "sources": {
    "north": {
      "skipped": false,
      "rows_inserted": {"people": 2},
      "key_map": {"people": [[1, 11], [2, 12]]}
    }
  }
}
```

The outer count is for this invocation. Each source includes every selected table, counts for this invocation, and `[old_id, new_id]` pairs sorted by old ID. Previously imported sources return `skipped: true`, zero counts, and their original maps. Process source IDs and canonical destination table names in ascending lexical order, regardless of argument order. Serialize sources and tables in that order.

## Supported graph

Destination tables already exist. Source and destination tables must have matching column names/order, declared types, nullability, defaults, primary-key position, and declared foreign-key relationships/actions. Other destination constraints may be stricter and are enforced normally. Do not create or alter these application schemas.

Every selected table must have exactly one `INTEGER PRIMARY KEY` that aliases rowid. Support zero/negative source IDs, self-references, cycles spanning tables, multiple FK fields and nullable relationships. Each FK is single-column, uses a non-PK child column with integer or null values, and references the selected parent table's integer PK, explicitly or implicitly. The selected graph must be closed over outgoing FKs. Unselected destination tables may reference existing selected rows; those existing rows and relationships remain unchanged.

Reject virtual tables, generated/hidden columns, WITHOUT ROWID tables, composite primary/foreign keys, shared-primary-key relationships, one child column constrained by multiple FKs, and source triggers attached to selected tables. These require other allocation or execution semantics. Reject internal SQLite tables and the metadata tables described below as selections. Unrelated source tables/views are ignored. Broken source FKs and preexisting destination FK violations are errors before importing rows.

## Allocation and values

Before any insertion, reserve maps for all new sources and tables. For each table start above the maximum of zero, its existing PKs, and its AUTOINCREMENT sequence high-water mark if present. For each new source in canonical order, assign consecutive fresh IDs to its rows in ascending old-PK order. Reject integer exhaustion rather than overflowing or reusing IDs. Previously imported sources consume no new IDs.

Rewrite every nonnull source FK through that same source's parent map, including cycles and self-links. Copy other values faithfully, including blobs, Unicode, nulls and SQLite numeric values. Do not replace or ignore conflicting destination rows. Existing uniqueness/check constraints remain effective.

Destination triggers execute normally during insertion, in canonical source/table/old-ID order. Their audit side effects belong to the merge transaction. A trigger that rejects, suppresses or changes an imported row must not produce a successful partial/wrong merge: reject and roll back if final imported rows differ from the planned rows. FK constraints must be valid when the operation returns successfully, whether `PRAGMA foreign_keys` was initially on or off.

## Snapshots and durable identity

Read each source using a consistent, read-only SQLite snapshot, including committed content in its WAL. Do not use the main file's byte hash as source identity. Source logical contents must remain unchanged. Concurrent writes to the destination and cross-file simultaneous snapshots are outside scope; each individual source must be internally consistent.

Create library-managed `_sqlite_utils_merge_sources` with queryable columns `source_id`, `fingerprint`, `tables_json`, and `_sqlite_utils_merge_keys` with `source_id`, `table_name`, `old_id`, `new_id`. Additional bookkeeping columns are allowed. Record each source's completed import and maps in the same transaction as its rows.

Fingerprint the selected source table definitions, explicit index definitions and typed rows in deterministic order, together with the selected table identities. Equivalent SQLite storage layouts and WAL checkpointing must not change this fingerprint. Changing selected definitions, rows or the table selection under an existing ID must raise `ValueError` without importing anything new; unrelated source table changes do not affect identity. The fingerprint algorithm is not otherwise prescribed. Moving an unchanged source to another path is allowed. An unchanged completed source ID is a no-op, returning its persisted original maps across processes.

## Transactions and recovery

All new sources in one call, their rows, trigger side effects, provenance and maps are one atomic operation. A late constraint error, metadata failure or process exit before commit must leave no new source partially imported. A retry after successful commit must insert nothing again.

Respect caller transaction ownership: use a savepoint when a transaction is already open, leave it open, and allow the caller to roll the merge back. On an ordinary failure, undo merge changes without undoing preceding caller work. SQLite operations that explicitly roll back the entire enclosing transaction retain their normal SQLite semantics. Restore both foreign-key and deferred-FK pragma settings on success and failure. Never disable validation merely to make cyclic insertion succeed.

An empty selected source graph is valid and still receives a durable identity record. Invalid argument/unsupported-schema checks must not leave metadata or data artifacts. The existing repository tests are available at `/workspace/repo/tests`; the final verifier also runs protected copies of selected upstream regressions. No network service, schema reconciliation, generalized key types, or new concurrency lease system is required.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/sqlite_utils/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.

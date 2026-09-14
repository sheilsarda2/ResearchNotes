# Durable resumable NDJSON imports

This repository is sqlite-utils at commit `85b1be10c81d9dd3567e36faf8dd411e4a8789bd`. Its ordinary insertion commands can commit batches, but do not persist the source position needed to resume an interrupted import. Add an integrated Python API and CLI command that keep imported data and recovery state consistent.

## Public interface

Add this method to `sqlite_utils.db.Table`:

```python
table.insert_file_resumable(
    path,
    *,
    import_id,
    batch_size=100,
    pk=None,
    alter=False,
    upsert=False,
    progress=None,
)
```

`path` is a string or path-like name of a regular local file. `import_id` is a nonempty string identifying this import across processes and invocations. `batch_size` is a positive integer counting JSON objects. `pk` is `None`, a nonempty column name, or a nonempty list/tuple of distinct column names, in primary-key order. `alter` and `upsert` are booleans with the existing `insert_all()` meanings, including primary-key inference for upserts into existing tables. Ordinary table creation and value serialization should use the package's insertion behavior. Existing insertion APIs and CLI commands must continue working.

Return a dictionary with exactly these keys: `import_id`, `rows_committed`, `byte_offset`, `completed`. The two numbers are cumulative objects committed by this import and consumed source bytes; upserting the same key twice still counts two source objects. `completed` is a boolean. If supplied, `progress` is callable and receives a separate dictionary of this shape after each committed checkpoint update. Mutating that dictionary must not affect import state. Its exception propagates after the commit; another invocation can resume. A final checkpoint for blank input/trailing whitespace may contain no new objects. A completed no-op does not notify progress.

Add the command:

```text
sqlite-utils import-resumable DATABASE TABLE FILE --import-id ID
    [--batch-size N] [--pk COLUMN ...] [--alter] [--upsert]
```

Repeat `--pk` for compound keys. Successful CLI output is the JSON result. Expected input, identity, filesystem and database failures should produce a nonzero exit with a concise `Error:` message, without a Python traceback. Document both interfaces in the existing documentation.

## Input and identity

Support immutable UTF-8 NDJSON files containing one JSON object per nonblank physical line, LF or CRLF, optional leading UTF-8 BOM, Unicode, blank lines, and a valid final object without a newline. Reject malformed/incomplete JSON, invalid UTF-8, JSON arrays/scalars, and nonstandard NaN/Infinity numbers. A malformed final line is an error; it must never be marked consumed. Empty input is a valid completed import and need not create a destination table. Stream the input with memory proportional to one insertion batch plus the largest input line, not the complete file. Computing a full-file hash before processing is acceptable.

Only regular local files and ordinary destination tables are in scope. Reject views, virtual tables, stdin/non-files, invalid arguments, and `_sqlite_utils_imports` as the destination. Input is immutable for the duration of one invocation; concurrent modification of the source or external changes to destination rows/metadata are outside this contract. Concurrent import workers and leases are not required.

Persist metadata in `_sqlite_utils_imports`, keyed by `import_id`, with these queryable columns:

- `table_name`: destination identity, matched using SQLite identifier casing rules.
- `source_sha256`: SHA-256 hex digest of the complete original bytes.
- `options_json`: deterministic JSON of the insertion options `pk`, `alter`, `upsert`, with PK represented as a list or null.
- `byte_offset`, `rows_committed`, `completed`: committed progress; `completed` is stored as 0/1.

Other bookkeeping columns are allowed. The metadata is library-managed. Reusing an ID must validate table, source bytes and insertion options **before database mutation**, even if it is already complete. A mismatch raises `ValueError`. Identical bytes at a different path are allowed. Batch size may change on resume. A scalar PK and one-element PK sequence are equivalent. A matching completed import is a no-op. Two IDs may independently import into the same destination. Treat repaired or appended source bytes as a different source: an incomplete record cannot be repaired and resumed under the same ID silently.

## Atomicity and recovery

Each logical batch's rows, destination creation or added columns, and checkpoint advancement must commit in one transaction. If parsing, inserting, or recording a checkpoint fails, none of that batch may persist; earlier completed batches remain. This applies even when the insertion library splits a logical batch into smaller SQL batches. Do not advance past an uncommitted object. At EOF, commit a completion checkpoint with the file's full byte length, including trailing whitespace. A first-batch failure must not leave target schema or progress artifacts created by that failed batch.

Resume from the last committed byte offset without replaying committed rows. The same semantics must hold after process termination both before and after commit, with a new process reopening the database. SQLite trigger side effects on the destination belong to the same batch transaction. Reject an already-open caller transaction with `sqlite_utils.db.TransactionError` before changing data or consuming input: this API promises durable per-batch commits.

No network service, CSV/compression support, arbitrary row-conversion language, new concurrency protocol, or full SQL parser is required. Implement this in the real package; an unrelated script or command that bypasses the public method is insufficient.

The repository and dependencies are installed at `/workspace/repo`. Existing upstream tests remain available. The final verifier exercises public API/CLI state transitions and selected unchanged upstream regressions.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/sqlite_utils/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.

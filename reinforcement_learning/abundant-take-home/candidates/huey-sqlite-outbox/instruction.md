# Add a transactional SQLite outbox for Huey task graphs

This is current Huey at commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, inspected September 13, 2026. An application updates business rows in SQLite, then enqueues background work. An in-memory post-commit callback still loses that work if the process disappears after business commit and before enqueue. Build durable staging in the **same on-disk SQLite database** as Huey's queue, including its existing task graphs.

Implement this in the existing package and document the new API. Existing upstream tests are available. Keep ordinary Huey behavior compatible; this feature is independent of worker acknowledgement or at-least-once execution.

## Public API

Add `huey.contrib.sqlite_outbox.SqliteOutbox(huey)`, `Submission`, and `OutboxConflict` (a `ValueError` subclass). The outbox accepts a `SqliteHuey` configured with an on-disk database. Reject incompatible backends and `:memory:` clearly. Do not silently swap the application's Huey instance or execute tasks while preparing/publishing an outbox graph.

`SqliteOutbox` exposes:

- `initialize_schema(connection)`: create the outbox's schema, idempotently, using the supplied SQLite connection. It must not commit or roll back a transaction owned by the caller. Construction alone must not create outbox tables.
- `stage(connection, task_or_group_or_chord, key)`: require an active caller transaction on the same physical **main** SQLite database file as the queue. The caller owns its eventual commit/rollback. A different database, including one that merely attaches the correct database under another schema, is invalid. A path alias to the same file is acceptable. Stage must participate in nested SQLite savepoints and must not use an unrelated connection for its writes.
- `get(key)`: read a committed submission by key, or return `None`. The read is non-destructive and can be performed after reconstructing both Huey and the outbox in another process.
- `pending_count()`: count committed submissions that have not been published for this queue.
- `publish(limit=100)`: publish at most this many pending submissions, oldest first. Return a list of their `Submission` receipts. `limit` must be a positive integer (not a boolean).

A `Submission` exposes a stable string `id`, its string `key`, `task_ids` (all declared task IDs in deterministic graph traversal order), `published` (boolean snapshot), and `result`. For `results=True`, `result` supports the ordinary result-reading shape: a Task yields `Result`; a pipeline/group yields `ResultGroup`; a chord yields `ChordResult` including member results and callback pipeline results. Its `get()` must have Huey's normal shape, including the chord callback's value rather than silently returning the last pipeline stage. When results are disabled, `result` is `None`; task identities and durable staging still work. Receipt result handles need not support rescheduling from stored argument data; normal consumers execute the stored messages.

Also add the transaction-participating storage primitive `SqliteStorage.enqueue_many(messages, connection)`. `messages` is an iterable of `(serialized_bytes, priority)` pairs. Validate the same-file connection and require an active caller transaction. Insert the whole batch or none of it without ending the caller transaction; an inner insertion failure must roll back the batch while preserving earlier business writes. Use this storage primitive for each outbox graph publication so custom SQLite storage integrations retain one bulk enqueue boundary.

## Transaction and identity guarantees

Staging must never enqueue, emit task execution, or commit the caller's business changes. If the outer transaction rolls back, neither business rows nor staged work survive. If an inner savepoint rolls back, earlier outer work survives while the inner staged work disappears. A producer killed immediately after committing leaves a submission that a new publisher can discover and publish.

Keys are non-empty strings scoped to the Huey queue. A key identifies a serialized invocation graph, **including its task IDs**, arguments, task names, graph structure and options. Repeating an unchanged request with the same key returns its existing receipt. A different request for that key raises `OutboxConflict` without modifying the previous submission or the caller's other writes. Callers reconstructing requests after restart should use explicit stable task IDs. Broker-generated chord coordination IDs, relative-expiry resolution, compression timestamps and signatures must not make the same request conflict with itself. A published key remains reserved even after its messages have been consumed; retrying stage/publish cannot enqueue it again.

Freeze the graph during staging: later mutation of the caller's tasks, arguments, callback links, or member list must not change the stored work. Task IDs, nested chord coordination and result identities must survive reopening; publication retries must not regenerate a new graph. Preserve the configured serializer/compression for actual queue messages. Idempotency may be defined in terms of serialized request content; arbitrary custom objects with unstable serialization and alternate dict insertion orders need not compare semantically equal.

For each submission, adding **all initial queue messages for its graph** and marking it published must commit in one SQLite transaction. Competing publishers must not publish the same submission twice. If insertion fails or the publisher dies after inserting only part of a graph, reopening must show the entire submission still pending and no partial graph in the queue. Retrying can then publish it once. Each submission is its own unit of atomicity: earlier submissions in a multi-submission `publish` call may already be committed if a later one fails.

Publishing queues work; it never executes task bodies, even when the original Huey instance is configured for immediate mode. With immediate mode's default memory debug storage, use the configured SQLite broker for outbox work; a normal SQLite consumer handles it. Publication signals, if emitted, must be after commit and are best-effort external effects, not part of the durable receipt protocol.

## Graph behavior and scope

Support ordinary Tasks, success/error callback pipelines, groups of Tasks/pipelines/groups/chords, chords whose members are Tasks/pipelines or nested chords, and callback pipelines on nested chords. Preserve member result ordering and the current `Error`/`SKIPPED` behavior on failed/revoked/expired members. Store only the initial runnable messages; successors and chord callbacks still run through Huey's normal executor.

An empty group publishes no messages and returns an empty result group. An empty chord publishes its callback once with an empty result list, including when nested inside another chord. Preserve priorities, ETA, retries/backoff, timeout, args/kwargs and result IDs. Resolve a relative expiry for each initial queued task once at staging; don't extend it on every publish retry. Downstream task expiry otherwise follows ordinary Huey enqueue behavior.

Supported input graphs are finite trees using lists/tuples for group/chord members. Reject cycles, shared Task/graph nodes, duplicate task IDs within a graph, invalid/unregistered tasks, groups used directly as chord members, and Task fragments already carrying a chord configuration. Fail validation/serialization without a partial submission or changes to the original graph. These boundaries avoid ambiguous reuse of one result identity inside a graph. They do not alter what ordinary `huey.enqueue()` accepts outside the outbox.

Retain old queued/scheduled messages, results, unrelated business tables, queue-name isolation, and explicit schema creation behavior (`create_tables=False` and `initialize_schema()`). A malformed/failed submission must not erase other committed submissions. This task does not require cross-database atomicity, Redis support, a general distributed outbox, or changes to Huey's existing execution-delivery guarantees. Implement real package integration, not a standalone queue demonstration, and do not alter the verifier.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/huey/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.

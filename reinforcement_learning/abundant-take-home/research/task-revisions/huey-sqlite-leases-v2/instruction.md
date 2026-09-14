# Add recoverable SQLite task delivery to Huey

The repository is Huey at commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759` (current upstream on September 13, 2026). Its SQLite consumer deletes a queued message before executing it. We need an opt-in implementation that can recover valid queued work after a worker process disappears, while preserving Huey's task and scheduling behavior.

Implement this feature in the existing package. Keep the ordinary `SqliteHuey` and other backends compatible; existing tests are available in `huey/tests/`. Add documentation and appropriate tests of your own.

## Public interface

Export `LeasedSqliteHuey` from `huey` and `huey.api`, and `LeasedSqliteStorage` from `huey.storage`. The new Huey class uses the new storage by default and accepts the usual SQLite/Huey arguments plus:

- `lease_seconds=60`: a positive finite duration in seconds. Reject zero, negative, non-finite, and boolean durations with `ValueError`.
- `clock=None`: an optional callable returning UTC Unix seconds, defaulting to wall-clock time. This clock controls reservations; Huey's existing datetime/`timestamp` APIs continue to control task ETA and retry scheduling. The caller is responsible for using a consistent clock across processes.

The storage provides:

- `reserve(now=None)`: return `None` if nothing is available, otherwise an object with `token` (a string), `data` (the original serialized bytes), and `expires_at` (Unix seconds).
- `ack(token, now=None)`, `release(token, now=None)`, `renew(token, now=None)`, and `owns(token, now=None)`: return booleans. `ack` removes the matching work; `release` makes it available immediately; `renew` extends its expiry to at least `now + lease_seconds` without shortening an existing lease; `owns` reports current ownership. All reject unknown, expired, or superseded tokens by returning `False`.
- `queue_size(now=None)`: count available messages, including expired reservations, excluding active reservations. `inflight_count(now=None)` counts active reservations. Ordinary Huey `pending()`, `pending_count()`, and storage `enqueued_items(limit)` likewise expose available messages only, in priority order.

For each optional `now`, `None` uses the configured clock. Time must be finite. A reservation expires at `expires_at <= now`. Re-reserving a message always creates a new token; an old token can never regain authority, even after rows are removed and identifiers reused. Queue names scope all these operations. `flush_queue()` removes both available and reserved work only for its queue.

## Delivery and task lifecycle

The existing consumer's thread and process worker paths must use leases automatically with `LeasedSqliteHuey`; a separate example worker is insufficient. Calling `dequeue(now=None)` on the new Huey returns the usual Task with its reservation attached. `Task.lease_token` exposes that token (or `None` for an unreserved task), and `Task.renew_lease(now=None)` renews it, returning a boolean. A `context=True` task can therefore renew while it runs. Automatic heartbeats are not required.

A new process and new Huey instance must recover expired reservations without an interruption signal handler. Concurrent reservers must never own the same unexpired reservation. Task code must run without holding a SQLite write transaction, allowing producers and unrelated workers to continue while a task runs.

A normal return, a handled final error, revocation, expiration, or cancellation acknowledges the reservation. An interrupted attempt (`KeyboardInterrupt`, `SystemExit`, or process death) keeps its work recoverable. Define `huey.exceptions.LeaseLost` for attempts to execute a reserved Task whose token has expired or been superseded. Check ownership before starting such a task and before committing its outcome; an expired worker must not acknowledge newer work or publish its own result/retry/callback as that newer owner.

Preserve task arguments, IDs, priorities, normal result/error handling (including `results=False` and `store_intermediate_errors=False`), callbacks/pipelines, retries, retry backoff, `RetryTask`, ETA, timeout handling, revocations, and cancellation behavior. A delayed task or retry must not be acknowledged before its replacement is durable. The result writes, queue/schedule writes that Huey itself performs when finishing one leased attempt, and acknowledgement must commit or roll back together. An error after an insertion but before completion must leave the original attempt recoverable and no partial replacement or result. The user task's own external side effects and arbitrary signal/hook side effects are outside this atomicity guarantee.

Scheduled work must also survive its transfer back to the queue. Add `enqueue_due(timestamp=None)` to the Huey API and make the normal scheduler use it. For leased SQLite, removing due schedule entries and adding their queue messages must be one atomic operation. An insertion error or process death partway through transfer leaves the whole due batch retryable, with future tasks and other queues intact. Other backends retain their ordinary behavior.

## Compatibility and boundaries

Open existing current-Huey SQLite databases without losing queued/scheduled tasks, results, or unrelated application tables. Preserve `strict_fifo`, queue isolation, and explicit schema creation: `create_tables=False` must not create tables; `initialize_schema()` must remain available and repeatable.

Immediate mode and manually executing a Task that was not dequeued retain ordinary Huey behavior. The delivery guarantee applies to valid, registered tasks consumed with the new class; undecodable/unregistered messages should be discarded and reported as before, so one bad message does not block healthy work. Do not claim recovery for a queue shared with ordinary destructive consumers. Existing group/chord behavior must remain compatible, but a new exactly-once orchestration protocol is not required.

This is at-least-once delivery: a task can run again after lease expiry or a crash. It does not guarantee exactly-once external side effects or power-loss behavior beyond SQLite's own guarantees. Implement the recovery within Huey's real storage, execution, and scheduling paths; do not replace the application with a standalone demonstration or alter the verifier.

Evaluation uses a fresh offline container with the pinned dependencies. Only `/workspace/repo/huey/` is transferred as the submitted implementation. Keep runtime feature code inside that package; upstream tests and documentation may also be edited for development but do not replace the independent verifier.

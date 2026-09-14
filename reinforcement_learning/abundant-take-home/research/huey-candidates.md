# Huey candidates: durable state evolution

Historical design notes from 2026-09-13, before construction. These are proposed feature tasks on an existing repository; the maintainer does not necessarily consider existing behavior defective. The completed implementations and accepted Harbor checks are recorded in [shortlist.json](shortlist.json). Model headroom and the >75-agent-step criterion remain unmeasured. Product demand is a separate gate: see the later [Huey demand review](huey-demand-review.md) before treating the priorities below as current recommendations.

## Current baseline and evidence

- Repository: <https://github.com/coleifer/huey>, MIT licensed.
- Fresh default-branch HEAD: `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, author date `2026-09-07T20:25:03-05:00` (2026-09-08 UTC).
- Latest release: 3.4.0, commit `6e8acd022346eeb1b09f46c7fa2612f760ee194b`; author date 2026-09-03 local, released 2026-09-04 UTC.
- Source clone: `research/cache/huey`. Exact source SHA-256 hashes, repository metadata, latest release/PR metadata, and relevant issue bodies/comments are in `research/huey-sources.json`.
- Read-only inspection covered `huey/api.py`, `storage.py`, `consumer.py`, registry and serializer, Django transaction integration, stats recorder, relevant API/storage tests, docs, and current changelog. All candidates should start from the fresh HEAD above.

The three candidates exercise different durable boundaries: **worker ownership**, **business transaction to queued work**, and **terminal failure to a deliberate new attempt**. They are independent checkouts, not three stages of a shared solution. The narrow research question is whether an agent preserves ownership, transaction, identity, and recovery invariants while extending an existing Python queue implementation.

## Candidate H1 — recoverable SQLite delivery with fenced leases

**Priority:** first choice. **Suggested slug:** `huey-sqlite-leased-delivery`.

### Gap confirmed at the current commit

`SqliteStorage.dequeue()` selects then deletes a task in one transaction. The worker subsequently executes the returned task. `_tasks_in_flight` is an in-process set; `notify_interrupted_tasks()` emits signals, not a durable reservation. The [current consumer documentation](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/docs/consumer.rst#L288-L293) explicitly disclaims acknowledgements and at-least-once delivery. This feature would extend those guarantees for an opt-in SQLite implementation.

Prior requests [#418](https://github.com/coleifer/huey/issues/418), [#598](https://github.com/coleifer/huey/issues/598), and [#796](https://github.com/coleifer/huey/issues/796) confirm the use case. #796 contains a public `SIGNAL_INTERRUPTED` handler that re-enqueues the task; this is **not** a solution to uncatchable process termination, durable leases, or stale-worker fencing. Task wording must require a real process disappearance, not merely SIGTERM handled by Python.

### Proposed public contract

Add an exported `LeasedSqliteHuey` implementation and `LeasedSqliteStorage`, leaving the existing `SqliteHuey` default behavior intact. Constructor accepts a positive `lease_seconds`. Provide these named storage operations so the evaluator can exercise state transitions without guessing internals:

- `reserve(now=None)` returns `None` or a reservation object with `token`, serialized `data`, and `expires_at`.
- `ack(token, now=None)` removes only the unexpired matching reservation, returning a boolean.
- `renew(token, now=None)` extends only the current unexpired reservation by `lease_seconds`, returning a boolean.
- `release(token, now=None)` makes only the matching unexpired reservation available again, returning a boolean.
- Time values are UTC Unix seconds; `now` is an explicit testable clock input. Expiration is defined as `expires_at <= now`. Tokens are unique per reservation generation, including when a task or SQLite row identifier is reused.
- Reservations and their payloads survive a new process and new storage instance. Active reservations are hidden from other reservers; expired reservations are eligible automatically. Queue-name isolation and priority ordering apply to both new and recovered tasks.
- Integrate reservation/acknowledgement into the existing consumer path. A successful attempt and a handled terminal failure are acknowledged. A Python process killed before completion leaves recoverable work. Renewal is available to long-running task code via the task context; automatic heartbeat is optional unless separately made explicit in the task prompt.
- If the ordinary executor chooses a future schedule or retry, ownership transfer to that durable destination and removal of the reservation are atomic. An exception while creating the destination leaves the reservation recoverable. Avoid treating `RetryTask` or a delayed task as completed work.
- Existing results, callbacks, signals, revocations, timeout behavior, and error propagation continue operating. The new guarantee is at-least-once task delivery; it does not promise exactly-once task side effects or forbid re-execution after lease expiry. A stale task's effects outside the queue cannot be retracted.
- Schema initialization works when opening an existing current-Huey SQLite database. With `create_tables=False`, construction does not create tables and explicit schema initialization is supported.

For the first implementation, explicitly limit the new delivery guarantee to ordinary tasks and pipelines; existing chord APIs should retain baseline behavior, but no new exactly-once chord guarantee should be implied. This avoids an ambiguous requirement to make all orchestration atomic.

### Behavioral verifier

1. Reserve, close, reopen, and verify invisibility before expiry and recovery at expiry.
2. Verify stale `ack`, `renew`, and `release` fail after another worker reserves the task. Include repeated row-ID reuse and queue-name collisions.
3. Competing processes reserve a known generated task set; each active generation has a single owner.
4. Run the actual consumer in a subprocess whose task signals a test barrier; kill it with SIGKILL, create another consumer, and verify eventual execution after expiry.
5. Show successful and terminal-failed tasks do not repeatedly reappear. Check `results=False` and error results separately.
6. Force failure during retry/schedule insertion; reopen and ensure there is still one recoverable logical attempt. Exercise a future ETA, delayed retry, `RetryTask`, revocation, and task expiration.
7. Run focused upstream API/consumer/storage regressions and legacy-schema initialization checks.

Use explicit clock inputs and barriers rather than fragile sleeps. Polling timeouts bound verifier duration; task work itself should be fast. Long horizon comes from coordinating lifecycle paths, not waiting for leases.

### Expected failure and risk

Likely model failure: a locally correct reserve/delete implementation misses a second lifecycle path, stale token, or retry handoff. Highest implementation risk is integrating acknowledgement without accidentally changing legacy callback/retry behavior. This is the strongest candidate for sustained multi-file work, but only trial trajectories can establish its actual horizon and headroom.

## Candidate H2 — transactional SQLite outbox for task graphs

**Priority:** second choice. **Suggested slug:** `huey-sqlite-transactional-outbox`.

### Gap confirmed at the current commit

Huey has [Django `on_commit_task()`](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/contrib/djhuey/__init__.py#L167-L198). It registers an in-memory callback to enqueue after a successful database commit. A post-commit callback is not durable staging: there is no persistent envelope bridging process death between business commit and enqueue. The current source tree has no outbox API/table. `enqueue(group(...))` submits members separately; chord setup also enqueues members incrementally.

The task is an original SQLite-only contrib feature. It is not a claim that `on_commit_task()` fails its documented contract. GitHub issue search for `repo:coleifer/huey outbox` returned zero results on the check date; that is bounded search evidence, not proof no external implementation exists.

### Proposed public contract

Add `huey.contrib.sqlite_outbox.SqliteOutbox(huey)` for an existing `SqliteHuey` whose task tables and business tables share one on-disk database.

- `initialize_schema(connection)` initializes outbox storage without committing the caller's transaction; repeated calls are safe.
- `stage(connection, task_or_group_or_chord, key)` requires an explicit active transaction on that same database. It serializes a durable submission envelope, assigns stable task/graph IDs, and returns a `Submission` with `id` and task/result identity information. It must not enqueue or execute immediately.
- All business writes and staged submissions obey caller commit, rollback, and nested savepoint rollback. Stage must neither commit the caller nor silently use Huey's unrelated connection. A connection to a different database and autocommit staging are rejected clearly.
- An idempotency `key` is scoped to the Huey queue. Repeating the same key and same submission payload returns the prior submission, including after publication; reusing the key for different payload raises a conflict. For unambiguous initial tests, callers provide explicit task IDs or repeat the same task instance; the contract must define whether task IDs participate in payload equality before implementation.
- `publish(limit=100)` publishes up to that many staged submissions in insertion order. Within each submission, creating all required initial queue messages and marking the submission published is one SQLite transaction. Two publisher processes cannot publish the same submission twice. A transaction failure leaves the whole submission staged for a later publisher.
- Groups and ordinary pipelines must work. Chords must preserve Huey's stable member indices, callback graph, nested chord structure, and result-handle identities when staged and later reopened; the publisher must not regenerate a different graph on each retry.
- Invalid graph members or serialization failures leave no partial outbox record. Empty groups and empty chords have explicitly documented, consistent behavior; use a successful empty-group result and an empty-list callback for an empty chord.
- Preserve priority, ETA, expiration, retries/backoff, compression/custom serializer, and queue isolation. `results=False` does not disable durable staging. The outbox always stages asynchronously even if the Huey instance is in immediate mode; this must be explicit in task instructions.
- A published submission receipt is durable, so re-staging its key cannot republish after its worker messages have already been consumed.

The same-database limit is intentional: exactly-once publication to a different Redis or PostgreSQL database would require a different protocol and should not be smuggled into the verifier.

### Behavioral verifier

1. Commit and rollback business rows plus staged tasks; verify the queue remains empty until publication.
2. Roll back an inner savepoint while retaining an outer staged submission; verify caller transaction remains open after stage.
3. Kill the producer after business commit and before publication; reopen from a new process and publish the expected work.
4. Use two simultaneous publishers; verify one publication per submission and stable IDs after reopen.
5. Inject an SQLite failure on a later member insertion of a group, reopen, and prove there was no partial publication. Retry successfully.
6. Exercise an ordinary pipeline, a group, a chord, a nested chord, late member completion, and empty cases, verifying actual task outputs through Huey's result APIs.
7. Reuse a published key with matching and conflicting payloads; check queue isolation and source task mutation does not change the staged envelope.
8. Run upstream orchestration/storage regressions.

### Expected failure and risk

Likely model failure: an `on_commit` wrapper seems correct but loses work after process death; alternatively, the outbox publisher uses several independent `enqueue()` transactions or regenerates graph IDs. The substantial work is preserving transaction ownership while refactoring graph preparation and publication. The graph scope makes this more than a small SQL helper, but needs a carefully specified identity contract to avoid an unfair verifier.

## Candidate H3 — durable terminal failure archive and atomic redrive

**Priority:** third choice. **Suggested slug:** `huey-sqlite-failure-redrive`.

### Gap confirmed at the current commit

Huey stores an error result containing an error string, remaining retries, traceback, and task ID. That is not the full serialized task, and storage is conditional on `results`. Result reads may consume it. [Current `Result.reschedule()`](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/api.py#L1332-L1361) requires a retained in-memory task object, revokes it, then enqueues a new task; it does not expose a durable failed-work inventory or atomically claim a failure for redrive.

Public issue [#815](https://github.com/coleifer/huey/issues/815) asks about a dead-letter queue for deserialization failures. The actual upstream fix in 2.5.2 prevents one bad scheduled task from dropping the whole batch; it **does not** implement this terminal-execution-failure archive. Avoid recycling #815's already-fixed batch bug or treating malformed serialization as equivalent to a task that executed and exhausted retries.

### Proposed public contract

Add opt-in failure archiving to `SqliteHuey`, enabled by `failure_archive=True`, plus a durable API available after constructing a new Huey instance:

- `failures(limit=100, after=None)` lists stable failure records ordered by an opaque monotonic failure cursor; `failure(failure_id)` returns one record without consuming it. Records expose `failure_id`, `task_id`, registered task name, error/traceback, UTC failure time, redrive status/new task ID, and the stored task invocation needed for replay.
- Capture a record only when execution terminates in an error with no retry remaining. Intermediate automatic retries do not create terminal records. Preserve the invocation state at the beginning of that failed attempt, before task code can mutate arguments. `results=False`, result reads, and `flush_results()` do not remove archive records.
- Define terminal exceptions precisely in the prompt: ordinary task exceptions, exhausted timeout/lock errors are archived; revocation, expiration, pre-execution cancellation, and deliberate `CancelExecution(retry=False)` are non-error terminal outcomes and not archived. An always-retrying `RetryTask` is not terminal. Do not classify by signal name alone.
- `redrive(failure_id, retries=0, delay=None, priority=None)` claims an archived failure once and queues a **new** task with a new task ID. It returns the same durable redrive receipt if repeated, including from another process. Creating the replacement message and marking the archive record redriven are atomic in the same database.
- The replacement receives original args/kwargs, timeout, retry delay/backoff, and a fresh explicit retry budget; supplied delay/priority override the archived values. ETA and relative expiry must be recomputed for the new attempt, with behavior explicitly stated; expired absolute deadlines must not accidentally carry over. Archive metadata records the original failure and new task ID without rewriting original error results.
- Ordinary success/error callback pipelines are copied with fresh task IDs so redrive cannot overwrite old result handles. An archived task that is part of a chord is replayed as a **detached** invocation with `chord_config=None`; it must never increment an old chord's completed member count or run that old chord callback again. This boundary is necessary because an earlier chord may already have published a result.
- A failed replacement creates its own separate archive record, linked to its parent failure. A successful replacement leaves the original archive as an audit record with its redrive receipt. This provides failure lineage rather than an ever-mutating single failed-task row.
- `delete_failure(failure_id)` removes one archive record deliberately, not implicitly. Queue isolation, existing-schema initialization, `create_tables=False`, and default opt-out behavior must remain correct.

This task does not make running task delivery survive an arbitrary process kill: that is H1. Its durable promises begin once terminal failure recording succeeds and cover the archive-to-replacement handoff. Keeping those boundaries separate prevents three versions of the same lease task.

### Behavioral verifier

1. Fail a task and reconstruct Huey in a new process; inspect the full replayable failure. Consume/flush ordinary results and show the archive is unaffected.
2. Test automatic retry success, retry exhaustion, results disabled, timeout/lock exhaustion, cancellation, revocation, and `RetryTask` against the exact terminal-outcome policy.
3. Mutate a task argument before raising; redrive must receive the original invocation argument value.
4. Concurrent redrivers obtain the same replacement receipt and create one new message. Failure during insertion rolls back the claim.
5. Redrive after reopen with fresh budget/delay/priority; run the actual replacement and inspect its result.
6. Preserve ordinary callback pipelines with fresh IDs; redrive a failed chord member after the old chord completed and prove the old callback is not invoked again.
7. Repeated replacement failures produce distinct lineage records without changing older error results.
8. Check cursor pagination, queue isolation, schema creation policy, and opt-out regressions.

### Expected failure and risk

Likely model failure: log `SIGNAL_ERROR` events and accidentally record intermediate retries; serialize already-mutated arguments; redrive from the result store; or let concurrent redrives submit duplicate messages. Callback identity and chord detachment are the expensive integration details. This is the easiest of the three to oversimplify into a small archive wrapper, so preserve the real lifecycle/replay requirements but do not add arbitrary UI work to inflate horizon.

## Already shipped or publicly addressed: exclude from these task prompts

- Retry backoff shipped in 3.3.0; terminal-error-only storage was added in 3.2.0. Neither is a new task.
- Groups/chords and nested orchestration already exist. 3.3.0 handles a pipelined chord member failing before its tail; 3.4.0 introduces structured `Error`/`SKIPPED` member values and error propagation to skipped downstream results. These must be preserved, not presented as missing features.
- 3.4.0 already includes graceful-shutdown timeout/signal selection, memory/Redis lock TTLs, several SQLite scheduling/queue-index improvements, and stats fixes. Lock TTL is different from a durable task reservation.
- [Issue #915](https://github.com/coleifer/huey/issues/915) publicly describes the SQLite due-schedule enqueue-loss problem and links a reproducer. HEAD `dbd1aef` adds per-enqueue exception handling in response. The patch is not a broad atomic-transfer guarantee, but using the stale 3.4.0 bug as a new discovery would be misleading.
- [PR #917](https://github.com/coleifer/huey/pull/917), open 2026-09-13, handles asyncio group polling cleanup; exclude that public implementation as a candidate. [PR #916](https://github.com/coleifer/huey/pull/916) addresses explicit pickle protocol zero; also out of scope.
- `on_commit_task`, `Result.reschedule`, signal hooks, and current stats/event recording are adjacent existing capabilities, not complete implementations of H2/H3.

## Evaluation freshness and task-quality implications

The September 8, 2026 [SWE-Bench Pro Verified paper](https://arxiv.org/abs/2609.08149) identifies evaluation leakage and flawed task specifications in an earlier challenging SWE benchmark. It is a reason to audit packaging and verifier scope, not evidence that today's models fail these Huey tasks. Do not recycle the original SWE-Bench Pro headline pass rate as a current model claim.

The August 2026 [SWE-bench-Live update](https://swe-bench-live.github.io/) now requires trajectories to verify that hidden evaluator information did not leak. For our packs, the environment must contain the pinned source, public task specification, dependencies, and public tests; no golden patch, hidden verifier source, or solution-bearing git history.

The August 10, 2026 [SWE-Bench ProMax paper](https://arxiv.org/abs/2608.09802) evaluates large coordinated refactors and reviews tests for scope and specificity. Our inference is that extending an existing lifecycle across several modules is a more defensible sustained-work hypothesis than adding a new isolated queue function. Its reported numbers do not establish any Huey-specific result.

Each Harbor verifier should check outcomes, not require a particular table name or code layout. Mutation checks against believable wrong implementations should demonstrate that a happy-path-only or shortcut solution fails. Oracle and no-op runs are necessary for task validation but do not establish model headroom. The user will run actual model trials and those trajectories determine whether these make the final three.

## Scope limits and recommendation

Build H1 first, then H2, then H3 if capacity permits. They fit the same research family, share a simple offline SQLite environment, and cover independent transition boundaries. Every pack should use an independent clean snapshot of the exact current commit and preserve existing public behavior by focused upstream regression checks.

GitHub issue searches and the full current source tree were checked; this is not an exhaustive search of every fork or code index. Two final GitHub search queries (`redrive`, `failed tasks`) hit the unauthenticated search rate limit; source and web-search fallbacks were inspected. No exact complete implementation was found in the checked material. The source manifest records this limitation instead of implying absolute originality.

The supplied take-home files were treated as read-only. Only this research note, its source manifest, and the permitted source cache were written.

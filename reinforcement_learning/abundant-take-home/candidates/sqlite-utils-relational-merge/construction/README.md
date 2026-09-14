# Construction evidence

Built 2026-09-13 from sqlite-utils commit `85b1be10c81d9dd3567e36faf8dd411e4a8789bd`. The Docker image `abundant-candidate-sqlite-relational-merge:85b1be1` uses Python 3.12.11 and SQLite 3.40.1 on ARM64. Dependencies are pinned and installed before runtime.

Fresh-container validation used networking disabled, 2 CPUs and 4096 MB:

- Golden patch: **149 passed, reward 1**, no skips/xfails. See `container-oracle.log` and `container-oracle/{diagnostics.json,results.xml}`.
- Untouched baseline: **51 failed, 98 passed, reward 0**, no skips/xfails. See `container-noop.log` and `container-noop/{diagnostics.json,results.xml}`. The existing CLI already rejects an unknown command, so the concise-failure case alone also passes the baseline.
- **52 new contract cases** plus **97 protected upstream regressions**. The upstream files and fixture in `tests/upstream/` are verbatim copies from the pinned commit. Submitted repository-test edits cannot weaken this regression gate.
- Local Python 3.12.13 validation also passed 149 cases; the container evidence is authoritative for the target environment.

The oracle integrates the public Database method and Click command, adds one graph/snapshot helper module, and documents both interfaces. Its patch contains only intended package/documentation changes. The environment archive is the untouched upstream tree with license/tests and contains no reference solution or hidden verifier.

The graph cases include two sources with colliding IDs, nullable/self/cyclic relationships, deterministic allocation independent of argument order, blob/Unicode/numeric values and AUTOINCREMENT high-water marks. WAL checks import committed content before checkpointing, confirm checkpointing does not change identity, and detect updates stored only in the WAL while the main-file hash stays unchanged.

Failure cases verify that a late metadata trigger or uniqueness error rolls back earlier source rows and audit side effects. Separate subprocesses terminate inside a provenance trigger before commit and immediately after successful return. Recovery reopens the actual database and exercises the real CLI. Caller transactions retain their work after ordinary savepoint failure and can roll back successful merges. Both FK pragma settings are checked on success and failure; the oracle captures deferred-FK state before schema reads because SQLite read-transaction completion can reset it.

Public prior art is disclosed in `provenance.json`: issue491 and open PR724 propose generic merging. The inspected PR does not implement this task's remapped relational graphs, durable key maps, typed snapshot identity or whole-call transaction contract.

No model trials were run. Oracle/no-op results validate executability and verifier discrimination; they do not establish model failure rates, a >75-step horizon or training benefit. Root owns Harbor packaging/validation, networking and independent adversarial review.

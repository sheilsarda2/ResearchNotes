Current verifier audit completed 2026-09-13: 45 oracle checks passed, untouched baseline reward 0, and all representative valid implementations passed their complete suites. See [measurement audit](measurement-audit/report.md) for the current results and artifact hashes. The initial construction record below is historical; its test counts and method-specific injections are superseded where the audit explains a change.

---

Built and verified 2026-09-13. No paid model runs.

The source archive is a clean git archive of python-diskcache
`ebfa37cd99d7ef716ec452ad8af4b4276a8e2233` (5.6.3), including license and original
tests. It contains neither the new implementation nor the hidden verifier. The
reference patch adds only the opt-in class/exports and documentation; existing
FanoutCache code is untouched. The patch matches the isolated worktree diff.

Fresh Docker verification used Python 3.12.11, network disabled, 2 CPUs and 4 GiB:

| Variant | Reward | Result |
| --- | --- | --- |
| Untouched upstream | 0 | 38 missing-feature errors; 11 compatibility checks pass |
| Golden patch | 1 | 49 pass; zero failures/errors/skips |

The 38 new behavioral cases use public APIs with independent expected values,
three seeded state models, process barriers and termination. They cover persisted
configuration, invalid budgets/counts, epoch/token fencing, pause/reopen/abort,
online update/delete/reinsert/increment/add/pop after copying, bulk clear, tag
eviction, culling, absolute TTL and touches under a controlled clock, Disk and
JSONDisk serialization, streamed file payloads, transactions/rollback, decorator
and existing-instance routing, pickling, named stores, adoption, retired cleanup,
metadata corruption and missing payload refusal.

Ten before/after publication interruption cases cover preparation, baseline copy,
mutation reconciliation, cutover, and abort. A separate process is killed during
an actual partial file copy; recovery updates that source value and verifies
complete output and absence of payload orphans. A held transaction excludes a
competing process without dropping its write; two processes increment a common
key while a third client migrates, with explicit contention retries. Processes
coordinate using pipes and bounded joins rather than timed sleeps.

Ten selected original fanout tests cover CRUD, increments/decrements, binary
reads, pop, expiry, tag eviction, clear, pickle and memoization. One independent
compatibility check verifies existing FanoutCache transactions and rollback.
Their upstream fixture behavior is unchanged.

Three source mutants were tested in separate fresh network-disabled containers:

| Mutant | Selected cases | Failures |
| --- | --- | --- |
| Suppress transactional dirty records | 2 | 2 |
| Refresh absolute deadlines during copying | 1 | 1 |
| Ignore stale migration tokens | 1 | 1 |

Each fails an observable semantic assertion, with no collection errors/skips.
Mutant sources and logs are outside the agent environment. Detailed XML/stdout
and machine-readable validation results are adjacent to this file.

The task explicitly retains native FanoutCache transact and its per-database
crash-atomicity limits. Managed clients alone participate in online coordination;
Disk and JSONDisk are supported; named stores are independent. max_items bounds
entry work rather than payload bytes or elapsed time. Contention can raise Busy
before acting, and continuous writes can delay completion. Process-crash
recovery does not imply storage-device or power-loss guarantees.

Harbor integration and model benchmarking are owned by the parent. A trajectory
of at least 75 steps and model headroom remain unmeasured hypotheses. All original
take-home paths still match commit c1ae968.

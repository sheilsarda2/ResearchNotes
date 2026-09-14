Current verifier audit completed 2026-09-13: 50 oracle checks passed, untouched baseline reward 0, and all representative valid implementations passed their complete suites. See [measurement audit](measurement-audit/report.md) for the current results and artifact hashes. The initial construction record below is historical; its test counts and method-specific injections are superseded where the audit explains a change.

---

Built and verified 2026-09-13. No paid model runs.

This task starts from clean Luigi commit
715f65c4a56a908ef0a1df4df6fc33b8420e2e6c, independently of the generation-target
task. The archive includes upstream license/tests, and no oracle or hidden
tests. The golden patch exports the protocol, adds its implementation and
documentation, and integrates explicit checkpoint requirements into the native
worker. It does not change ordinary DynamicRequirements behavior.

Fresh Docker verification used Python 3.12.11, no network, 2 CPUs and 4 GiB RAM:

| Variant | Reward | Result |
| --- | --- | --- |
| Untouched upstream | 0 | 42 missing-feature errors; 8 upstream checks passed |
| Golden patch | 1 | 50 passed; no failures/errors/skips |

The 42 behavioral cases cover true Luigi one/multiple-worker execution,
fresh-interpreter continuation, structure/order/None and repeated requirements,
private and insignificant parameter retention, unchanged waiting cursors,
stale continuations, reset/epoch fencing, version upgrade, identity collision,
invalid/cyclic state and requirements, hook exceptions, Finish output gates,
deleted outputs, empty requirements, failed/deleted children, stale worker
completion caches, corrupt/truncated/digest-invalid journals, process death
immediately before/after replacement, commit acknowledgement failure, competing
advance/reset, process-death lock release, unlocked dependency waiting and I/O
error propagation.

Eight selected upstream tests cover parameter serialization, target structure,
flattening, ordinary completion caching, dynamic requirements, cross-module
dependency loading and custom DynamicRequirements completion. Their existing
small synthetic task delays are unchanged; the new concurrency tests use
explicit pipes and bounded timeouts rather than sleep-based races.

Three temporary mutants were rejected, and source was restored:

| Mutant | Selected tests | Failures |
| --- | --- | --- |
| Remove stale-continuation fence | 2 | 2 |
| Ignore journal checksum | 5 | 1 |
| Permit Finish without outputs | 1 | 1 |

Detailed JUnit and stdout are in adjacent `docker-baseline/`, `docker-oracle/`
and `mutant-*` files. The source patch was checked byte-for-byte against the
isolated restored worktree diff, and the vendored worker file against upstream.

Harbor orchestration is delegated to the parent. A 75-step trajectory and model
headroom remain hypotheses pending user benchmarking. External effects before
a durable transition commit may replay; no exactly-once guarantee is claimed.
Supplied take-home files still match original commit c1ae968.

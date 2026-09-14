# Huey selection review: user impact, current behavior, and active fixes

Checked September 13, 2026, after the user requested evidence of impact and an unresolved problem, rather than benchmark suitability alone.

**Revised recommendation: none of the three exact Huey contracts is fully qualified by the available impact evidence.** Worker recovery is the strongest lead because multiple users describe concrete consequences. Its SQLite-only scope still lacks a matched user report. Remove outbox and failure redrive from the impact-prioritized recommendations pending direct demand. The artifacts remain runnable exploratory benchmarks; this review does not invalidate their Harbor checks or alter the frozen task files/configurations.

The original selection over-weighted source tractability, deterministic verification, and the possibility of sustained integration work. We inspected source and public discussions, but the additional API, backend, graph and recovery requirements were authored by us. Failing tests for those new APIs did not establish current user harm. This audit separately reproduced existing behavior through unmodified upstream APIs with positive controls.

| Exact candidate | Evidence of impact | Current technical gap | Public fix status | Revised disposition |
|---|---|---|---|---|
| SQLite leases | Reports of lost running jobs, long video jobs during deployment, and stale application status. Strongest direct recovery request uses Redis; the status reporter asks for accurate status, not specifically replay. No matched SQLite report found. | Actual consumer SIGKILL loses the task on current source, even with retries and an interruption handler; restarted consumer handles new work. | ACK/visibility-timeout omission is intentional. Graceful shutdown and signal mitigations already exist; no matching active upstream recovery implementation found. | Best further investigation lead; downgrade exact SQLite contract as impact-qualified. |
| SQLite outbox | Related database visibility reports exist, including Postgres+Redis. No identified producer-crash incident or request matching same-file SQLite with Huey graph preservation. | Normal commit produces one order/one task; rollback zero/zero; forced death after commit produces one order/zero tasks. | Current post-commit callback promises ordering, not crash-safe staging. No matching upstream active fix found; other systems already provide transactional queue insertion. | Remove from impact-prioritized recommendations pending direct demand. |
| Failure archive/redrive | An exhausted-retry/email question exists, with accepted signal-hook guidance; backend and production loss magnitude are unspecified. Malformed-task issue #815 is a different, already-addressed problem. | Current results are optional/consumable and lack a persistent replay inventory. Existing error hooks can capture full tasks and retained result handles can reschedule. | Exact archive/atomic replay API absent; no matching active upstream implementation found. Existing mechanisms address simpler reported needs. | Remove from impact-prioritized recommendations pending an operator workflow needing this exact stronger behavior. |

Detailed primary sources, search limits, and observations:

- [Worker recovery audit](demand-audit-huey-leases.md), [reproducer](validation/demand-audit/huey-leases/reproduce_loss.py), [raw result](validation/demand-audit/huey-leases/reproduction.json).
- [Outbox audit](demand-audit-huey-outbox.md), [reproducer](validation/demand-audit/huey-outbox-reproducer.py), [raw result](validation/demand-audit/huey-outbox-reproducer.json).
- [Failure archive/redrive audit](demand-audit-huey-redrive.md).

The strongest demand examples are dated: the Redis recovery request is from 2021, the video/status reports from 2023. They establish that people have encountered the problem; they do not establish current prevalence, revenue impact, failure frequency, or demand for every requirement in our contract. A controlled crash reproducer establishes a mechanism, not the incidence of customer outages.

The current upstream check resolves to commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, latest release 3.4.0 (September 4), and one open PR #917 concerning async result polling. Audits also inspected source, relevant release changes, old issue replies and past proposals. Some later GitHub API requests were rate-limited. “No matching public upstream fix found in the checked scope” is supported; “nobody is working on it anywhere” is not.

For further selection, require evidence of a concrete affected workflow that matches the proposed backend and behavior, a reproduction of the unmet need on current upstream, and inspection of shipped solutions, workarounds and active implementations. Rank the remaining opportunities by observed consequences and breadth of affected workflows. An intentional limitation can still be worth addressing, but missing code and an old closed issue are insufficient evidence of value. Model difficulty and testability are additional gates, not substitutes for demand.

The other seven candidates have not yet undergone this exact demand-matching audit. Their construction and freshness checks must not be represented as impact qualification.

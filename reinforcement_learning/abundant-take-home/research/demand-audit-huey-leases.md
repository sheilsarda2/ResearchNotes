# Huey leases: demand and current-status audit

Checked September 13, 2026. Recommendation: **downgrade as an impact-qualified candidate; retain as an engineering benchmark hypothesis.** Current loss is demonstrable and real users report related pain. The missing evidence is demand for this **SQLite-only** implementation, and maintainer acceptance: Huey's delivery policy is deliberate.

## Evidence that people care

| Evidence | Actual workload or consequence | What it establishes—and does not |
|---|---|---|
| [#598, May 13, 2021](https://github.com/coleifer/huey/issues/598) | A user reports running tasks disappearing during maintenance, version upgrades, and other consumer restarts. They explicitly ask about **Redis**. | Direct report of lost work, not a quantified production incident or SQLite demand. |
| [#598 follow-up, Nov 27, 2023](https://github.com/coleifer/huey/issues/598#issuecomment-1828436579) | A different user cites deployment restarts and long video-processing work: “sometimes tasks are just too long”. | A concrete workload; does not say which backend or report measured loss. |
| [#733, Apr 27, 2023](https://github.com/coleifer/huey/issues/733#issuecomment-1525960861) | Backend/infrastructure owner: “My end users are currently unhappy as they see `In Progress` task in the list.” Their later [clarification](https://github.com/coleifer/huey/issues/733#issuecomment-1542527445) explains that **their application's** status remains stale after a worker dies; Huey does not emit a final signal for that task. | Direct user-facing consequence. This is not a built-in Huey status bug. The user needs accurate terminal status/documentation, not specifically automatic replay; no SQLite backend is specified. |
| [#743, May 27, 2023](https://github.com/coleifer/huey/issues/743#issuecomment-1565594055) and [June 1 follow-up](https://github.com/coleifer/huey/issues/743#issuecomment-1572226466) | Roughly five-minute jobs initiated by an HTTP request from a UI; cloud provider imposes a 30-second shutdown deadline. | A concrete mismatch between job duration and shutdown budget. Backend unspecified. Graceful-shutdown and signal-handler mitigations exist. |

These are separate public user accounts, not an adoption survey. I found no report in the checked discussions explicitly requesting SQLite crash recovery. No loss rate, revenue impact, customer count, or present-day demand magnitude is established. The strongest direct recovery request (#598) targets Redis, so the existing SQLite candidate would **not directly fix that user's configuration**. The #733 observation does not establish that replay is the desired remedy: the maintainer argues that retrying an OOM-producing task can repeat the same failure, and the user emphasizes accurate final status in their own UI.

## What happens on the current code

Fresh upstream HEAD remains [`dbd1aef45adf57d2169e80bdc9f18ea1cd704759`](https://github.com/coleifer/huey/commit/dbd1aef45adf57d2169e80bdc9f18ea1cd704759). [`SqliteStorage.dequeue()`](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/storage.py#L952-L966) deletes the message before returning it to the executor.

I ran a **whole-package reproduction**, not the benchmark's missing-API tests. An ordinary `SqliteHuey` task has three retries, an interruption handler that re-enqueues work, and the current consumer's graceful-TERM configuration. A barrier confirms actual execution before SIGKILL. A new consumer and a new database connection are then created.

| Observation | Result |
|---|---:|
| Task pending before worker starts | 1 |
| Task pending once execution starts | 0 |
| Killed consumer exit code | -9 |
| Task pending after reopening database | 0 |
| Original task completion/result after restart | absent |
| Interruption handler ran | no |
| New probe task on restarted consumer | completed |

A normal control task also completed. Source hashes for `api.py`, `consumer.py`, and `storage.py` match the current unmodified checkout. The task has been removed from queue and schedule and has no outcome; a healthy restarted consumer cannot recover it. This establishes the particular process-death behavior, not its real-world frequency or power-loss durability.

Runnable [reproduction](validation/demand-audit/huey-leases/reproduce_loss.py) and [raw result](validation/demand-audit/huey-leases/reproduction.json). From `abundant-take-home/`, using the baseline image built for this pack:

```bash
docker run --rm --network none \
  -v "$PWD/research/validation/demand-audit/huey-leases:/audit:ro" \
  abundant-huey-sqlite-leases:baseline python /audit/reproduce_loss.py
```

## Is it being fixed?

**The missing ACK protocol is an intentional feature boundary.** On [April 27, 2023](https://github.com/coleifer/huey/issues/733#issuecomment-1525932810), the maintainer describes visibility timeouts and ACKs, then says they exclude that design “out of a desire for simplicity”. The [July 2024 project comparison](https://github.com/coleifer/huey/issues/64#issuecomment-2200513055) repeats that tradeoff. Thus closed issues here do not mean delivery recovery shipped, but they also do not mean an accepted bug awaits repair.

**Several older complaints have already been addressed partially.** [#796's author](https://github.com/coleifer/huey/issues/796#issuecomment-2111982008) reports the interruption-handler snippet solved their SIGTERM problem. [Huey 3.4.0](https://github.com/coleifer/huey/releases/tag/3.4.0), released September 4, 2026, adds configurable graceful-shutdown signals and a shutdown timeout. These reduce deployment pain and must count against the candidate's incremental impact. Neither mechanism runs after SIGKILL, as the reproduction shows. The separate schedule-enqueue-exception report [#915](https://github.com/coleifer/huey/issues/915) has a current upstream mitigation; it is not evidence that the unmodified issue remains open.

Fresh official API inspection found **one open PR**, [#917](https://github.com/coleifer/huey/pull/917), concerning async result polling. It does not add recovery. Live Git remote inspection found 18 branch heads, none named for leases/ACKs; the consumer-refactor and faster-shutdown branch tips are from 2015 and 2024. Checked current source, release notes, relevant issue comments, all open PR bodies, and an all-state upstream PR keyword search did not reveal a durable recovery implementation in progress. This is bounded public-upstream evidence; it cannot establish absence in private work, all forks, or unadvertised branches. Later issue-timeline API requests hit rate limiting, recorded in the source ledger.

The general problem is also **already solved in other systems**. Current [Celery documentation](https://docs.celeryq.dev/en/stable/userguide/tasks.html) documents late acknowledgements and the additional `task_reject_on_worker_lost` setting for killed worker children; late ACK alone is insufficient in that case. That does not implement Huey compatibility, but it prevents presenting this as a novel queueing capability with no existing alternative.

## Decision against the user's criteria

- **People care:** supported for Huey delivery/restart behavior, with concrete workloads and user-facing consequences; backend-specific and current demand remain weak.
- **Absent today:** strongly supported for SQLite SIGKILL recovery by current source and a controlled real-consumer run.
- **No active fix found:** supported within the checked public upstream scope, alongside explicit maintainer reluctance and recently shipped mitigations.
- **Good impact-weighted issue:** not yet established for this exact contract. The initial SQLite choice improved self-contained evaluation and atomicity testing; that is a benchmark-construction reason, not evidence of user demand. Do not promote the task above candidates with exact-backend reports and a maintainer-endorsed unresolved requirement solely because its crash tests are elaborate.

No candidate files, frozen benchmark configurations, or original take-home materials were changed by this audit. No model benchmarking or external communication was performed.

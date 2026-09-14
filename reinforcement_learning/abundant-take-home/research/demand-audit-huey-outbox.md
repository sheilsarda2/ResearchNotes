# Huey outbox: demand and current-gap audit

Checked 2026-09-13. **Recommendation: remove this candidate from an impact-qualified shortlist until a concrete user or workload needs the proposed Huey integration.** The current behavior can lose producer work at the commit/enqueue boundary; that is now reproduced on unchanged upstream. The evidence does not establish demand for our exact same-file SQLite graph API. Building a difficult verifier did not establish that people want the feature.

The candidate remains a runnable proposed engineering extension. This audit does not alter its frozen task, baseline, oracle, verifier, or comparison lock.

| Selection question | What the evidence actually shows | Assessment |
|---|---|---|
| Do Huey users encounter related transaction problems? | Yes: public reports of a queued task reading a row before the application transaction commits. | Real adjacent demand, already addressed by `on_commit_task`. |
| Have we found a Huey user reporting producer death after commit but before enqueue? | No matching incident or request was found in the bounded upstream and web search. | Direct demand unestablished. |
| Does the producer-loss boundary exist in current Huey? | Yes: an independent subprocess reproduction left one committed order and zero queued tasks after SIGKILL at that boundary. | Current missing durability guarantee demonstrated. |
| Does the evidence support our exact SQLite + same database + pipelines/groups/nested chords scope? | No identified report required that combination. The concrete database-race reporter used PostgreSQL and Redis. | Scope was selected for benchmark tractability and integration complexity, not demonstrated user need. |
| Is an upstream outbox fix currently visible? | Current HEAD has no durable staging; the sole open PR changes async result polling only. | No matching public upstream fix found as of the check, not proof about private work or all forks. |
| Is the general problem unsolved elsewhere? | No. Existing transactionally staged job systems and a current SQLite alternative already address that boundary. | Cannot pitch a generally unsolved SQLite outbox problem. |

## People and workloads: evidence that counts, and what it does not prove

1. **`mirzadelic`, March 18, 2023:** [Huey issue #725](https://github.com/coleifer/huey/issues/725) includes runnable-shaped application code: a Django `Post` save signal queues `create_or_update_related_objects`, then the worker gets `Post.DoesNotExist`; adding a three-second delay masks it. The specified stack is **PostgreSQL 14, Redis 7.0.5, Django 4, Docker**. This is a real user report about task/database ordering, but it is a **pre-commit visibility race**, not a producer-crash incident. Huey's current documentation gives `on_commit_task()` for this case. The issue is closed. We could not retrieve its comments in this audit due to a transient API failure; we do not infer an unseen maintainer resolution.
2. **`spapas`, May 1, 2023:** [issue #735](https://github.com/coleifer/huey/issues/735) asks why Huey's decorator is different from Django's callback. The maintainer [explains](https://github.com/coleifer/huey/issues/735#issuecomment-1530261639) that it wraps argument capture and returns a result handle. This corroborates use of transactional dispatch, but not a loss incident or demand for durable staging.
3. **`simkimsia`, June 19, 2024:** [issue #805](https://github.com/coleifer/huey/issues/805) asks how to use the decorator. Most of the issue body is an explicitly ChatGPT-generated example, so it must **not** be counted as evidence of a deployed notification workload. The [maintainer response](https://github.com/coleifer/huey/issues/805#issuecomment-2178661923) again describes preventing execution before commit, not guaranteeing enqueue after producer death.
4. **`vaibhavmano`, July 29, 2020:** [issue #542](https://github.com/coleifer/huey/issues/542) supplies task logs and application paths involving batch/file extraction; queries see no rows through a Redis-backed Huey worker. We have the issue body, but its comments hit API rate limiting, and the web page did not expose the discussion. It is adjacent ordering evidence only; no stronger causal or production-impact claim is supported.

No issue above demonstrates demand for preserving a nested Huey chord graph in the same SQLite database as business data. No supported revenue, incident-frequency, customer-count, or lost-job-volume estimate was found. Repository stars and generic queue usage are not used to fill those gaps.

## Show the current loss boundary

[Reproducer](validation/demand-audit/huey-outbox-reproducer.py) and [raw observed output](validation/demand-audit/huey-outbox-reproducer.json) were created independently of the candidate verifier and oracle. They run the real current `on_commit_task()` decorator and real `SqliteHuey` queue against the **same physical SQLite database** as the business table.

Environment: Python 3.12.13, Django 6.0, upstream Huey commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`. The local upstream checkout reported that exact HEAD and a clean git status. Django 6.0 is an explicit test pin, not a claim that it is the newest patch release.

| Scenario | Producer exit | Committed orders after reopen | Queued tasks after reopen |
|---|---:|---:|---:|
| Commit normally | 0 | 1 | 1 |
| Roll back transaction | 0 | 0 | 0 |
| SIGKILL after SQLite commit, before post-commit enqueue | -9 | **1** | **0** |

The crash case wraps Django's low-level `_commit` solely to inject a fault: call the real SQLite commit, then send SIGKILL to the producer before returning to Django's post-commit callback runner. It does not change Huey code, enqueue, serialization, queue tables, the decorator, or its callback. A fresh process/connection reads the resulting database. The normal and rollback controls show that the ordinary integration works and that the fault is placed at the specific durable boundary.

Re-run from `reinforcement_learning/`:

```bash
uv run --python 3.12 --with django==6.0 python \
  abundant-take-home/research/validation/demand-audit/huey-outbox-reproducer.py \
  abundant-take-home/research/cache/huey
```

This is a controlled process-crash experiment, not an observed customer outage, probability estimate, filesystem power-loss test, or full durability stress suite.

The mechanism is visible in [pinned upstream source, lines 191–198](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/contrib/djhuey/__init__.py#L191-L198): the task exists in a closure registered with Django; the later callback calls `enqueue`. [Django documents](https://docs.djangoproject.com/en/6.0/topics/db/transactions/#timing-of-execution) that these callbacks run after successful commit and are not part of that transaction. Current [Huey docs](https://huey.readthedocs.io/en/latest/contrib.html#enqueueing-after-commit) promise ordering, not durable producer delivery. Therefore **this is an absent stronger guarantee, not a demonstrated violation of Huey's documented contract**.

## Current implementation and active-fix checks

Fresh API evidence is archived under [sources/huey-outbox-audit](sources/huey-outbox-audit/):

- Current default-branch HEAD: `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`; upstream last pushed September 8 UTC. The runtime source tree has no outbox implementation. Both the ordinary decorator and current `django.tasks` backend's `ENQUEUE_ON_COMMIT` path use Django callbacks.
- Latest release: [3.4.0, published September 4, 2026](https://github.com/coleifer/huey/releases/tag/3.4.0). Its release body contains SQLite queue/schedule changes but no transactional producer outbox.
- The **full open-PR response contained one PR**, [#917](https://github.com/coleifer/huey/pull/917). We read its body and [actual diff](sources/huey-outbox-audit/917.diff): two asyncio helper/test files, no enqueue or database durability change. This is stronger than a title-only check.
- The 100 most recent open/closed PR bodies were searched for outbox, on-commit, transaction, and atomic changes. Matching adjacent work includes [#848](https://github.com/coleifer/huey/pull/848), merged June 12, 2025, which adds `call_local` for tests, and [#829](https://github.com/coleifer/huey/pull/829), merged December 29, 2024, a decorator-naming documentation clarification. Both are present in inspected current source and do not close the crash boundary.
- GitHub issue search for `repo:coleifer/huey outbox` returned zero; transaction returned seven; on_commit returned one. The enqueue query returned 125, of which its first 100 were fetched; this audit does **not** claim that those 100 exhaust all enqueue history. Web queries for Huey outbox and commit/crash combinations found no direct report of this exact boundary. Later comments requests hit the unauthenticated API rate limit; this limitation is retained.

The defensible freshness claim is “no matching public upstream implementation or active PR found in the checked material on September 13,” not “nobody is fixing it.”

## Alternatives and public prior art narrow the opportunity

**Honker:** The [project's own current README](https://github.com/russellromney/honker/blob/c18c263af2d94ca56ff0f9a7174bf7d1c1fc9b52/README.md) provides `with db.transaction() as tx: ... queue.enqueue(..., tx=tx)` for atomic SQLite business writes and queue insertion. It also describes retry/visibility-timeout/dead-letter behavior. The repository was created April 18, 2026, and the inspected HEAD was `c18c263af2d94ca56ff0f9a7174bf7d1c1fc9b52` with a September 7 push. The README labels it alpha and explicitly excludes workflow DAGs/chains/groups/chords. These are upstream claims/source inspection, not an independent Honker crash qualification in this audit. It is **an alternative for the basic SQLite use case**, not a drop-in solution for the candidate's entire Huey graph contract.

**Hexastack Events:** [0.4.0 was published September 10, 2026](https://pypi.org/project/hexastack-events/0.4.0/) and ships a `HueyOutboxRelay` plus SQLAlchemy outbox storage. We downloaded the source distribution, checked its published SHA-256, and read the actual relay/storage modules. The relay publishes CloudEvent envelopes to an event bus, then marks records published; it does not serialize or enqueue Huey Task/group/chord graphs. Its optional Huey instance is stored but not used by the inspected class, and start/stop only set a boolean. Thus the package is relevant public prior art but its name and marketing description are not evidence that our exact graph integration is already shipped or production-proven.

**Established pattern:** Brandur Leach's [2017 staged-job article](https://brandur.org/job-drain) specifically explains that enqueue-after-commit loses work when a process dies in between and supplies a transactionally staged relay. It establishes that the reliability pattern is longstanding. It does not establish present Huey-specific demand or validate our exact benchmark scope.

## Decision

Under the user's criteria, **current technical gap = demonstrated; public upstream fix absence = bounded evidence; meaningful demand for the exact proposed task = insufficient**. Keep the artifact if it is useful as an exploratory integration benchmark, but do not count it among impact-qualified tasks yet. A concrete Huey deployment requiring durable producer submission, together with evidence that same-file SQLite and graph preservation are material to that deployment, would be the missing selection evidence. No maintainer or user was contacted in this audit.

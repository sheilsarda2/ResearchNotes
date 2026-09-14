# Huey terminal-failure archive/redrive: demand audit

Checked **2026-09-13 UTC**, against current upstream `dbd1aef45adf57d2169e80bdc9f18ea1cd704759` and the frozen candidate. No candidate files, supplied take-home files or benchmark configurations were changed. No paid model calls or external messages were made.

**Verdict: downgrade to an exploratory feature proposal; exclude it from a final selection that requires demonstrated current user pain.** There is a real difference between Huey's shipped behavior and this candidate's durable, one-time replay protocol. There is also a relevant historical user question. The evidence does **not** establish an affected SQLite deployment, recurring operational burden, a production incident, or demand for the candidate's full callback/lineage/atomic-redrive contract. “Not implemented” is supported much more strongly than “important missing work users need now.”

## The closest direct user report

On **2023-03-05**, GitHub user **spapas** asked what happens when a send-email task exhausts five retries. In follow-up, the user described a Django request/response application where retaining the original result handle was inconvenient. This is specific workflow evidence: a web request creates asynchronous email work, and the developer wants to handle a terminal failure later. It is a question about recovery architecture, not a reported quantified loss incident. Neither the issue nor its comments identifies SQLite as the backend. [Issue #718](https://github.com/coleifer/huey/issues/718).

The maintainer pointed to result handles, signals and logging, then explained that an error signal receives the task and can inspect `task.retries`. The reporter accepted that explanation the same day. Thus the issue supports interest in handling exhausted failures, but cannot be presented as an unresolved upstream bug or as evidence that all existing recovery mechanisms are nonworking. [Maintainer response and discussion](https://github.com/coleifer/huey/issues/718#issuecomment-1455114342).

The plausible beneficiary is a developer or operator whose application sends background email and needs to inspect/replay exhausted work after the originating request or process has ended. A failed email remains unsent without some recovery action. **That consequence is an inference**, not a documented production outcome in this source. No volume, failure frequency, customer loss or remediation-time estimate was found. A persistence/replay layer might reduce application-specific recovery work, but the source does not establish that this belongs in Huey's SQLite backend rather than the application's business database.

## Issue #815 is a different problem, and its reported bug shipped a fix

**2024-09-24 issue #815** concerns scheduled messages that cannot be decoded or resolved to a registered task, such as after task code is removed. The reporter initially thought the bad task remained queued; the maintainer corrected that premise. A later exchange identified a real bug: one bad deserialization could discard a due batch. The maintainer fixed individual-message handling, and **2.5.2 was released on 2024-09-25**. [Issue #815](https://github.com/coleifer/huey/issues/815), [fix confirmation](https://github.com/coleifer/huey/issues/815#issuecomment-2372021939), [release confirmation](https://github.com/coleifer/huey/issues/815#issuecomment-2374814541).

The frozen redrive candidate explicitly excludes malformed-message quarantine and archives only eligible tasks that actually executed and failed terminally. It would not solve the issue's original unregistered-task case. **Do not use #815 as direct demand evidence for this candidate.**

## What already works and what remains additional

| Shipped feature or alternative | Existing benefit | Remaining difference from the candidate |
|---|---|---|
| Native retries/backoff, `RetryTask`, error results and `store_intermediate_errors=False` | Automatically retries configured failures; supports deferring ordinary error/result handling until exhaustion. | Does not create an independent retained inventory of complete failed invocations. [Current guide](https://huey.readthedocs.io/en/latest/guide.html#error-handling). |
| `SIGNAL_ERROR`, task context, hooks and logging | Exposes the failing task and exception to application code; the simple no-retries case can be identified and serialized. | The application must choose persistence, retention and replay semantics. This is a working extension point, not the candidate's built-in atomic protocol. [Current signals documentation](https://huey.readthedocs.io/en/latest/signals.html). |
| `Result.reschedule()` | A retained result handle can enqueue a replacement using its original task data. | Requires that task object; repeated calls create distinct replacements. It does not claim a failure once across independent operators/processes. [Pinned API source](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/api.py#L1332). |
| Built-in stats plus Django/Flask-Peewee admin | Persistent signal history, failure visibility, searching and queue controls already exist. | Optional argument capture is a 400-character `repr`, not a replayable invocation; history is pruned by retention/row limits. Admin “restore” removes revocation, not replay of an exhausted failure. [Current extensions documentation](https://huey.readthedocs.io/en/latest/contrib.html#django-admin), [stats source](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/contrib/stats.py). |
| Built-in Django `tasks` backend | Stores `task_path`, `args`, `kwargs`, status and error details; reconstructs Django result/task objects across requests. | This already overlaps the “retain invocation context” claim for that interface. It has no failure-list/atomic one-time-redrive/lineage API equivalent to the candidate. Do not generalize the limitations of native error metadata to these richer Django records. [Backend source](https://github.com/coleifer/huey/blob/dbd1aef45adf57d2169e80bdc9f18ea1cd704759/huey/contrib/djhuey/tasks_backend.py). |
| `boxine/django-huey-monitor` | A separate implementation records task signals and displays task/progress/history in Django admin. | Inspected current `main` is a monitor, not a complete replay archive: no reschedule/requeue/serialized-invocation implementation was found in its runtime package. [Repository](https://github.com/boxine/django-huey-monitor). |

Another historical question, **#490 (2020-02-21)**, asks about last-retry error handling and tracebacks; it does not request atomic manual replay. Current source also contains the real-traceback repair from **PR #905, merged 2026-07-30**. A selection rationale based on absent tracebacks or universally premature final-error notification would therefore be stale. [Issue #490](https://github.com/coleifer/huey/issues/490), [PR #905](https://github.com/coleifer/huey/pull/905).

## Reproduction on unmodified current source, including positive controls

The latest upstream SHA retrieved during this audit matches the frozen source pin. A disposable offline container used the unmodified `abundant-huey-deadletter-redrive:baseline` package. A synthetic email task always failed, with one allowed retry; no real email or network service was called. After exhaustion, a new `SqliteHuey` instance reopened the same file. [Executable reproduction and observed results](sources/demand-audit-huey-redrive-reproduction.json).

- Both modes executed **two attempts**, then had **zero pending and zero scheduled tasks**.
- With `results=True`, one error result remained with only `error`, `retries`, `task_id` and `traceback`; reading it reduced the result count to zero. With `results=False`, no error result remained.
- Neither instance had native `failures()` or `redrive()` methods.
- **Positive signal control:** the existing `SIGNAL_ERROR` hook identified the final attempt and obtained a serialized task whose arguments round-tripped correctly, in both result modes. The test retained these bytes in memory; it did not pretend that this alone creates a durable archive.
- **Positive replay control:** after repairing the synthetic task, a retained handle's `reschedule()` worked. Calling it twice queued two distinct replacements and caused two successful deliveries. This illustrates the candidate's additional one-time claim semantics; it is not a violation of `reschedule()`'s existing contract.

This proves an absent built-in terminal archive and the limits of native result metadata. It also proves that failure observability, configured retries and deliberate rescheduling are functional. It does not prove a production outage or that a general signal-based application recovery approach is inadequate.

## Current implementations and in-progress work

As of the checked date, upstream `master` remains the candidate pin; the latest release is **3.4.0, published 2026-09-04**. The complete current open-PR response contained **#917**, about canceling sibling result polling after an async group fails, rather than a terminal archive/redrive implementation. The recent 50-commit window includes stats, admin, traceback and retry improvements, but no implementation of the candidate API. This supports “not currently shipped or in an open upstream PR” within the checked repository, not a claim that nobody is developing it elsewhere. [Current release](https://github.com/coleifer/huey/releases/tag/3.4.0), [PR #917](https://github.com/coleifer/huey/pull/917).

For `django-huey-monitor`, current `main` was `56f340cddcb87628b0835c847642e1724f33c87d`, dated **2025-06-02**. Its five open PRs concern Django compatibility, signal/model restructuring, hierarchy display and progress tracking; none is the candidate replay protocol. These are meaningful partial solutions to visibility, not evidence of a complete duplicate. No complete third-party equivalent was established by this audit.

Search scope and raw responses are saved in [source evidence](sources/demand-audit-huey-redrive.json). Upstream issue searches returned 30 `failed` matches, five `reschedule` matches, one `dead letter` match and 17 retry-title matches. Repository searches for Huey plus deadletter/dead-letter/failure-archive/reschedule found no repositories; this weak negative evidence is not an exhaustive fork/code search. Initial unauthenticated API calls hit rate limits; the decisive #718 comments and monitor metadata were then obtained with read-only authenticated `gh api` calls. No messages were sent.

## Selection decision

| Required selection evidence | Finding |
|---|---|
| Concrete people/workload impact | A dated Django email-recovery question exists, but no SQLite-specific operator, observed incident magnitude or recurring burden was established. **Partial.** |
| Missing or nonworking today | Exact durable inventory plus atomic one-time detached replay is absent. Existing retries, signals, monitoring and retained-handle replay work. This is primarily a new feature contract, not a reproduced defect in promised behavior. **Narrowly satisfied as missing functionality.** |
| Not already shipped or being implemented | No exact implementation in current upstream, its one open PR, inspected monitor source or monitor PRs. Several material pieces already ship. Global absence is unproven. **Supported within the stated search scope.** |

Keep the frozen task as research construction if useful, but do not spend a final demand-qualified slot on it yet. The full contract's fresh callback identities, chord detachment, retained lineage and one-time cross-process claims are engineering choices added by the candidate author; the inspected users did not ask for that entire combination. Retain it only after evidence from an actual SQLite operator establishes the recovery workflow and why existing application persistence, Django records, monitoring and ordinary replay do not suffice. No task padding or model-step estimate repairs that missing demand evidence.

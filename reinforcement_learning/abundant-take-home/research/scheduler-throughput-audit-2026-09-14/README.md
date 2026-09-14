# Sweep throughput and non-disruptive scheduling changes

The immediate throughput limit was occupied trial slots during long agent runs.
Across 26 recent completed trials, median agent execution was 50 minutes, startup
was under one minute, and grading was 1.7 minutes. Response-wait intervals account
for 78.7% of paired native agent timestamps; those intervals include request,
network, provider computation and retry time. They are not a direct measurement
of model computation. The previous hour averaged 11.84 occupied slots and spent
85.8% of its time at the 12-slot cap.

The machine exposes 18 CPUs and 32,293 MiB of memory. The short baseline sample
showed 47–61% CPU utilization and roughly 20–22 GiB available memory. Memory
pressure still fluctuated, so the existing reserve, pressure threshold, cooldown,
startup staggering and all per-trial limits were retained.

## Changes applied

At 04:08:47 UTC, the shared concurrency cap increased from 12 to 14. Campaign
worker plans and per-trial budgets did not change. The pool reached 14 active
claims at 04:08:58. During the following three minutes, available memory stayed
above 17,863 MiB and the pool continued completing and admitting work. This is a
capacity observation, not proof of a 17% throughput gain.

At 04:11:14 UTC, all four existing trial workers acknowledged the held-cell
scheduler update with their active sets unchanged. Only the four watchdog
observer processes were restarted. No trial or supervisor was signalled.

The scheduler balances primary starts across task/model/effort combinations.
Previously, a held cell with zero starts would pin the minimum and prevent
healthy tasks from entering later rounds. The new explicit `held_cells` overlay
keeps that cell's target and history intact while excluding it from the ready
round minimum. Held tasks remain ineligible. Removing a valid hold lets its cell
catch up. The watchdog uses the same rule.

Eighteen cells belonging to Huey and SQLite are held pending verifier repairs.
All 117 cells, 2,340 target trials, dispatch counts and receipts were preserved.
The existing task-specific readiness holds remain a separate admission check.
Release an overlay hold only when the corresponding task readiness or validated
revision promotion is also resolved.

## Validation and provenance

- 57 unit tests and three selection-refresh tests passed.
- A disposable worker running the previous scheduler passed 16 live-update
  assertions with zero model calls, including active-work preservation,
  held-task rejection, healthy-task advancement and catch-up after release.
- All four production updates acknowledged identical active sets before/after.
- All 36 original take-home files still match commit `c1ae968`.

`measurement.json` records the baseline methodology and caveats. `held-cells/`
contains the code/test manifest, disposable smoke and production acknowledgements.
`capacity-14/` contains exact control snapshots and short follow-up observations.
The two `all_owners_live=false` samples include every participant, even empty
ones: the Huey no-model diagnostic finished and released its claim at 04:10:51,
leaving an empty participant until the next lock pruned it. The production
activation separately verified all four benchmark runner identities remained
alive; subsequent shared-state inspection found no orphaned active claims.

To roll back capacity, change only the shared control's `max_active` and user
plan's `shared_concurrency` to 12 under the shared lock. Active trials finish
naturally; the lower cap restricts future admissions.

# Single-use admission reservation for the existing quality reviewer

Prepared only; no live controls have been changed. The actual Harbor
check-only run passed at 2026-09-14 08:30:43 UTC, with reviewer PID83205/start
5750249 alive and waiting. All six observed control files remained unchanged.
The host and Harbor runs each passed 22 offline tests. Root owns any activation.

The proposed command, run from the repository in the Harbor host, is:

```sh
/home/vscode/.local/share/uv/tools/harbor/bin/python -B research/zenoh-coverage-followup-2026-09-14/admission-reservation-001/reserve_once.py --apply
```

The default invocation and `--check-only` perform read-only preflight. They
verify the exact live reviewer and canonical quality job, its local cap of one,
the shared cap of fourteen, the four live campaign registrations, fresh healthy
supervisor/watchdog observations, and four currently unpaused campaign controls
with local caps of twelve. Unknown participants or a prior execution journal
prevent a new application. No trial is created or relaunched by this helper.

Application arms an independent fallback process before changing controls. It
sets only `paused=true` and an identifying `pause_reason` in the four named
campaign control files. Existing trials continue. Shared state, shared/local
limits, task budgets, plans, frozen helpers, and reviewer configuration remain
unchanged. Watchdogs may report the intentional pause, but respect it for idle
runner recovery. Supervisors continue collecting results and updating health.

The owner and fallback inspect the exact reviewer every 250ms. Either restores
the controls after its first claim, exit/identity change, completion, competing
participant or bound control change, observation failure, or the fixed
110-second deadline. The remaining cleanup window ends at approximately118
seconds. Both processes use the same bounded journal lock before the shared
lock, preventing application from interleaving with restoration. Restoration
attempts are separate artifacts; a transient lock/write conflict remains
retryable and never becomes a successful terminal receipt. `restoration.json`
is written only after every owned marker is confirmed absent.

Restoration merges only the two owned fields into each current document,
preserving unrelated changes. A foreign pause reason is retained. A newly
observed health problem becomes the ordinary health pause rather than being
cleared; that independent pause may legitimately remain after the reservation
ends. If another actor unpaused a control but left this helper's reason, only
the stale reason is removed. Every write records before/after values and
hashes, and an admission trigger records the exact shared-state observation.

There are two limits to this approach. A worker may already have read
`paused=false` before entering shared admission, so a narrow initial race can
still admit another sweep trial. This helper does not promise exclusive
acquisition or manufacture capacity; memory/cooldown checks remain enforced.
Also, existing control writers do not all use a common file lock. Fresh
field-level merging and a final digest check detect ordinary concurrent edits,
but cannot eliminate the final uncooperative writer race. Root must coordinate
other control writes during this short operation.

The deadline and fallback cover normal termination and transient failures;
filesystem/host failure or loss of both processes cannot be given a hard
restoration guarantee. An unresolved failure writes `cleanup-incomplete-*`,
leaves no successful terminal receipt, and permits explicit idempotent recovery:

```sh
/home/vscode/.local/share/uv/tools/harbor/bin/python -B research/zenoh-coverage-followup-2026-09-14/admission-reservation-001/reserve_once.py --restore
```

The single-use journal is `execution-001/`, currently absent. No signals to
trials, model calls, Docker operations, or scheduler code edits are performed.
The tests use temporary controls only and cover partial application, foreign
and health pauses, unrelated edits, PID reuse, changed participants, fixed
expiry, bounded lock waits, transient shared-lock/CAS failures, persistent
cleanup failure, and default check-only behavior.

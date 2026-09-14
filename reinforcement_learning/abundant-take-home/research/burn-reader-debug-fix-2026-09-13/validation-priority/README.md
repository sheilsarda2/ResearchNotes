# Temporary priority for Burn v4 validation

The immutable controls queued at 00:31:48 UTC on September 14, 2026 while
all twelve shared slots were occupied by paid trials. At 01:00:39 a Zenoh
trial acquired a slot released by Huey. The initial reservation preflight
refused the changed inventory before writing a journal or any controls.

The revised reservation applied at 01:01:13 with eleven main-campaign
trials and one Zenoh trial still active. It lowered the main local admission
cap from twelve to ten and paused new starts for Zenoh and Diskcache. The
existing twelve trials continued. The shared cap, validation control,
captured tooling, task files and task budgets did not change. The immutable
oracle acquired the next available slot at 01:04:18 after the normal memory
pressure check allowed admission.

`intent.json` preserves the original and temporary control bytes and hashes.
`activation-check.json` verifies all twelve claims survived, both bound
controls remained unchanged, and the restoration watcher was alive.
`watcher.json` binds that process to its Linux start identity.

The watcher restores each control only if it still matches the temporary
bytes. A newer edit is preserved and reported as a restoration conflict.
Restoration triggers when the identity-checked nop runner acquires a claim,
the controls finish, the controller exits, either bound control changes,
or the ninety-minute reservation expires. No trial workers are signaled.
`restoration.json` is the authoritative result when that transition occurs.

Promotion requires a successful restoration record and all three live
controls matching their original hashes. This check runs before preparation
writes, before the promotion journal is created, and under the shared lock
before publication. Six restoration-gate tests and three policy tests are
recorded in `../promotion-tests.json`. Final v4 unpausing separately compares
its own control with the prepared paused-control hash under the shared lock.

An interrupted promotion must be reconciled narrowly against fresh state.
Never replace the live shared-state file with an old whole-file snapshot:
other cohorts may have recorded new results since that snapshot.

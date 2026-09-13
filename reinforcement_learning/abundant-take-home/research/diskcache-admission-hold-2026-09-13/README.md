# DiskCache revision admission hold

The original DiskCache lease wording permits waiting under contention, while its
process-barrier test requires an immediate `ReshardBusyError`. A separate
instruction revision is being validated. This hold prevents more trials from
starting on the ambiguous revision and leaves every historical result intact.

DiskCache was already absent from the main campaign's private readiness ledger,
but it was not in the control's `image_readiness_tasks`, so that absence alone
did not block it. After recording the intent, the exact activation watcher
63850/start2662180 was stopped. Both trial runners continued. Under the global
activation lock, the hold appended only `diskcache-online-reshard` to the main
control's readiness-task list and to both stored pause/restoration values. The
ledger's ready tasks stayed unchanged; only its hold note and timestamp changed.

The archived before/after files and `rebind-review.json` record unchanged
runtime/workload hashes, runner identities, phases, budgets and shared capacity.
The main control retained its existing owned pause. The review is now bound by
the activation state, and its original remaining wait cutoff is explicit.

At 23:35:15 UTC, the same activation resumed with watcher PID 81611, start identity
2699645. No trial received a signal and no model call was made by this operation.
This is a task-specific hold, not promotion or permanent exclusion. Current live
progress remains in `jobs/runtime-activation-mini-tool-20260913T225800Z/activation.json`.

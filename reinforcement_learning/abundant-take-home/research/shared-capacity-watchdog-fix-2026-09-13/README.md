# Preserve runners waiting for shared capacity

The corrected Zenoh cohort was alive but had no admitted trials while the main
cohort occupied all 12 slots. The previous watchdog counted that legitimate
wait toward its five-minute empty-runner recovery timer. The next slot opened
naturally and admitted Zenoh before recovery fired, but later waits could
produce unnecessary runner restarts.

The watchdog now recognizes shared-capacity waits only when a fresh resource
report, the exact participant PID/start identity, and current shared allocation
agree. It checks again under the shared lock immediately before signaling.
Stale, unknown, and no-longer-blocked idle runners retain normal recovery.

All 15 watchdog tests passed, including shared-capacity and concurrent-timestamp
cases. Freshness time is sampled after reading admission status so a newly
updated timestamp cannot be mistaken for a future or stale observation. The
activation record verifies watchdog-only replacement while preserving runner
and supervisor identities and admission controls. Validation made no model
calls. Active benchmark trials continued throughout.

`activation-initial.json` preserves the first monitor-only rollout;
`activation.json` records the final rollout with the timestamp race fixed.
Neither rollout changed a runner, supervisor, trial, admission setting, or
task specification.

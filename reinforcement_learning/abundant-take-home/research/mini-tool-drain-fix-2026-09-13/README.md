# Mini tool timeout output-drain correction

The Huey SQLite leases attempt `huey-sqlite-leases__LAUZTSQ` stopped advancing
after its model launched detached consumer processes in a reproduction script.
Those processes kept the tool's output pipe open after its shell was killed.
Mini 2.4.6 enforces a 30-second tool timeout, but its exception handler then calls
`communicate()` without a timeout. The agent remained blocked collecting output.
This is a runtime cleanup defect; it does not justify excluding the Huey task.

`capture-huey-observation.py` reads the exact container's process identities,
output-pipe owners, CPU ticks, and saved turn count without reading arguments or
environment variables. The observation distinguishes container PIDs from Docker
daemon host PIDs. It does not signal any process or modify trial evidence.

The separate cleanup helper preserves the configured tool timeout and upstream
process-group kill. It limits the subsequent output drain to one second, retains
partial output without duplicating it, and re-raises the original tool timeout.
Detached processes remain subject to the existing outer execution guard.
Successful commands, nonzero commands, and ordinary background services retain
their original behavior.

The final isolated Docker reproduction passed nine tests on the actual pinned
Python 3.12.11 and Mini 2.4.6 environment, without model calls. The original
function hung until the outer guard stopped it. The correction returned the
original timeout in 1.423 seconds with a 0.4-second fixture timeout and one-second
drain. Production's 30-second default remains unchanged. See
`reproduction-final/summary.json` and its raw case records. Earlier fixture runs
remain preserved in their own directories.

The new runtime wrapper and private bootstrap apply the helper only inside new
agent processes. They retain exact task-file transport and existing CLI model
and effort settings. A separate `benchmark-agent-tool-runtime.json` records
preflight results and source identities. The existing base agent runtime remains
unchanged so already running workers keep accurate provenance.

Rollout uses one campaign at a time: pause its new admissions, wait for all its
active trials and container claims to finish, restart only the empty runner, and
restore admissions. The other campaign continues using shared capacity. Original
job configurations, locks, cell counts, task limits, and raw results are retained.
Activation status and any deliberate intervention are recorded separately; this
document alone is not evidence that activation or an intervention has completed.

The identified Huey guard phase was closed at approximately 22:51 UTC. The guard
confirmed cancellation with exit 124 and successful descendant cleanup. Harbor
then ran the verifier, which passed 193 tests. That raw grade is diagnostic: the
deliberately interrupted attempt is excluded from the success-rate denominator.
Its exact registration and final evidence bindings live under
`../benchmark-interventions/2026-09-13-huey-mini-pipe-drain/`.

Harbor labeled the nonzero exit `ApiRateLimitError` after a regular-expression
match against the captured transcript. This label is separately audited against
Harbor's classifier and the exact guard-close evidence. The original pre-action
registration and raw exception remain preserved; the audit does not establish an
actual provider rate-limit incident.

The audited final record is infrastructure, with raw reward 1 retained and
counted reward absent. The combined regression suite passed 104 tests; the final
immediate-pause activation change separately passed all 17 activation tests.
The offline Mini CLI and its source-bound integration proof also passed.

Rolling activation began at 22:57 UTC in
`jobs/runtime-activation-mini-tool-20260913T225800Z`. It first drains the main
campaign, confirms the replacement runtime, then drains the corrected Zenoh
campaign. The shared cap remains 12; the existing fair-share policy can restrict
the unpaused cohort to six while the other cohort drains. Consult that directory's
`activation.json` and `heartbeat.json` for current status. `activation-initial.json`
is a fixed initial observation, not a completion claim.

`activation-source/` preserves the pre-existing local lock-cache and activation
helper code used by the live environment. Those working files were left alone;
the evidence snapshot makes their activation hashes reproducible.

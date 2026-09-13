# Benchmark timeout cleanup and evidence binding

The installed Harbor 0.15.0 agent timeout cancelled its Docker client without
necessarily stopping the agent's processes inside the container. The restaurant
audit found later native responses in 10 of 20 timeout trials, including a
workbook rebuild after verification. Those historical results remain unchanged.

## Implementation

Our launchers install `scripts/benchmark_deadline.py` in their own Python process.
They do not edit installed Harbor or supplied task files. The guard supports the
single-step Linux Docker tasks used by these campaigns and requires Python in the
task container. Unsupported environments fail before running the agent.

`benchmark_process_guard.py` runs each agent execution under a Linux subreaper.
It owns descendants that create new sessions or double-fork, enforces the
existing agent deadline inside the container, and kills/reaps remaining children
on completion or cancellation. An independent close command confirms quiescence
before Harbor reads logs, collects artifacts, or starts verification. Failed
cleanup prevents collection and grading. The Docker buffered-output cancellation
path also terminates and waits for its client process.

The watchdog starts inside Harbor's existing agent `wait_for`, after setup and
network preparation. Task instructions, budgets, verifier limits, CPU, memory,
and scoring remain unchanged. Cleanup and evidence capture add orchestration
overhead; they do not give the agent more execution time. This process owner is
for lifecycle correctness, not containment against deliberate attacks on the
supervisor. Background work within mini-swe-agent's one main execution remains
available until that execution ends.

New trials receive three additional records:

- `benchmark-runtime.json` identifies the runtime and process guard sources.
- `benchmark-deadline.json` records the deadline, owned executions and cleanup.
- `benchmark-snapshot.json` hashes the synchronized agent logs and collected
  artifacts before grading. `benchmark-evidence.json` binds the final result,
  verifies that snapshot, and records steps, tool calls, effort, response model
  aliases, recorded cost/tokens, timing and completeness.

Snapshots reject symlinks and special files and detect concurrent modification.
The campaign supervisors keep unsafe or unbound guarded results in review, which
pauses expansion. A short grace period allows evidence finalization after Harbor
writes `result.json`. Historical trials are labeled unguarded; their metrics can
be read without pretending they have a pre-verifier snapshot. Timeouts remain in
the denominator when their evidence is valid. Recorded costs can be censored if
an interrupted provider call was charged but never saved a response.

## Validation and activation

`scripts/tests/test_benchmark_deadline.py` exercises real Harbor trials and real
disposable Docker containers without any model calls. The unguarded baseline
reproduces writes during verification for both timeout and normal-return cases.
The guarded suite covers timeout, normal return with background writers, the
buffered client path, cancellation during startup, and an injected cleanup-proof
failure. It checks ordinary children, new sessions and double-fork daemons,
stable artifacts/trajectory files, cleanup ordering, absent Docker exec clients,
and withheld grading when cleanup cannot be confirmed.

Final result: `guarded-final-02/summary.json` passes all five cases and every
assertion, with zero model calls and unchanged hashes for the four tested source
files. The metadata unit suite passes 25 tests; the activation unit suite passes
eight. `unguarded-03/summary.json` and
`unguarded-buffered-01/buffered-timeout/summary.json` retain the negative controls.
These tests establish quiescence before evidence capture and grading; they do
not claim nanosecond deadline precision. Recorded timestamps expose the cleanup
interval.

The source-bound passing suite is the prerequisite for
`scripts/activate-benchmark-deadline.py`. That tool first restarts the two named
supervisors to load evidence checks while admission stays paused. It lets the
old runner's admitted trials finish, checks both admission counts and actual
trial containers, then stops only that drained runner and restores the prior
candidate pause settings. Changed controls, launchers, plans, configurations or
tested sources prevent automatic restoration. Restaurant backlog pauses remain
intact. `activation-watcher.json` is the live activation status, when started.

During implementation a V2 campaign was started by parallel work before the
guard was installed. Its admission was paused with zero admitted trials and
zero trial containers; `v2-activation/activation.json` records its separate
activation. The original campaign's newer concurrency limit and shared network
pool settings are preserved in `activation-reconciled.json`.

`deployment.json` records the first V2 trial carrying the tested runtime hashes.
V2 activation completed; the old campaign remains draining. The current watchers
are under `activation-retry-01/` and `v2-activation-retry-01/`. The initial
watchers correctly stopped when parallel work added a job-lock hash cache; that
addition was reviewed before restarting activation, with both pauses preserved.

Already running agents retain their original runtime until they finish. This
change does not retroactively repair their evidence or the historical timeout
results. The queued campaign and subsequent runner processes load the guard.

`preservation.json` verifies all 36 supplied files against `c1ae968`, all 15
candidate task hashes against the shortlist, and all 80 previously hashed source
files for the restaurant timeout audit. `summary-integration-check.json` records
a read-only pass through completed restaurant and candidate results with the new
summary code.

Run the regression inside the existing devcontainer with Harbor's Python and
`PYTHONPATH=scripts:scripts/tests`:

```sh
python scripts/tests/test_benchmark_deadline.py --docker --output research/new-deadline-regression
python -m unittest discover -s scripts/tests -p 'test_benchmark_evidence.py'
python -m unittest discover -s scripts/tests -p 'test_activate_benchmark_deadline.py'
```

The Docker test requires a new output directory and retains its generated task,
raw trial outputs, summary and source hashes for inspection.

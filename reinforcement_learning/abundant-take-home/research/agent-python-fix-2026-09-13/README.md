# Agent Python compatibility fix

The Foxglove / Opus / medium attempt `LzsSTEy` failed before making a model
request. Harbor installed mini-swe-agent 2.4.6 with the image's Python 3.10.12;
LiteLLM 1.100.1 imported `typing.NotRequired`, which that interpreter does not
provide. The installer's `mini-swe-agent --help` check passed because it did not
load the model backend.

`scripts/benchmark_agent_runtime.py` pins the agent's separate uv tool environment
to Python 3.12.11 and imports the actual LiteLLM backend during setup. A failed
preflight prevents model execution. Its metadata records Python, agent/library
versions, import success and the runtime hook's source hash. Optional SDKs are
recorded as absent when they are not installed. System Python, the task, its
verifier, agent version, model/effort request settings and budgets are unchanged.

Both benchmark launchers install this hook, so it applies across models and
effort levels, including Foxglove / Sonnet / medium. Already running agents keep
their existing environment. The primary runner is drained before replacement;
its admission pause is restored only after the tested launchers and planned
configurations are checked again.

`benchmark_startup_failures.py` recognizes this specific pre-model dependency
failure using the complete startup traceback, absent trajectories and absent
usage. It leaves unknown failures in review. The original result, raw reward and
logs remain unchanged; the aggregate labels this trial infrastructure and
excludes it from the model success-rate denominator.

One replacement for Foxglove / Opus / medium was added to the existing 2,520-run
sweep as `candidates-all14-efforts-20-20260913T183301Z-repair-python310-001`. It uses
the same frozen task and agent configuration and the same shared admission
limits, with one local worker. The sweep target remains 20 valid attempts per
task/model/effort combination. `launch.json` records deployment identifiers;
`activation/activation-watcher.json` records the primary runner's live rollout.

Explicit replacement jobs can run alongside a primary job. The scheduler skips
active/finished jobs and creates automatic deficit repairs only after all
existing planned work finishes, so resuming the primary does not wait for an
explicit replacement to finish and does not duplicate active work.

The final no-model smoke in `smoke-20260913T194100Z/summary.json` reproduces the
old failure and verifies the fix in the exact prewarmed Foxglove image. Both
cases keep system Python 3.10.12; the fixed agent uses 3.12.11 and imports the
backend successfully. Model imports run with no credentials and no network.
The smoke binds source and candidate hashes and removes only its disposable
containers. See `SMOKE.md` for the retained intermediate test attempts.

Twelve startup-classification tests, six scheduling tests and eight activation
tests pass. `preservation.json` binds the supplied files and original failed
trial evidence. No original take-home file or frozen candidate was edited.

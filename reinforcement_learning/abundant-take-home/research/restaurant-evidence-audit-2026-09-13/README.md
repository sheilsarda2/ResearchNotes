# Restaurant run evidence and orchestration audit

Checked September 13, 2026, at approximately 18:09 UTC. This is a read-only audit of saved runs. No job, task, verifier, original setup file, or live orchestration code was edited; no runs were started, resumed, stopped or retried. The new inventory script and these separate research artifacts are the only additions.

## Answer

The normally completed runs have enough evidence for step counts, model/effort comparisons and outcome review. **Timeout handling needs correction before relying on strict deadline and final-artifact claims.** A Python timeout does not currently ensure the in-container agent has stopped before Harbor snapshots its output.

## Coverage

The [inventory](inventory.json) covers 326 primary restaurant trial directories, plus two archived snapshots of the same unfinished recovery trial. Archived snapshots are not additional independent trials.

| Primary trial state | Count | Evidence |
|---|---:|---|
| Normally completed model runs | 287 | All have result, native and ATIF trajectories, all five verifier checks, stdout, usage, reported cost and stage timing. Agent turns range from 4 to 36. |
| Agent timeouts | 20 | All have both trajectories, all five checks, usage, reported cost and timings. Ten have later native turns absent from the earlier ATIF snapshot. |
| Other exceptions | 14 | Mostly startup/request failures; only two have native traces, one has an empty-agent ATIF and verifier output. These require separate failure classification. |
| Incomplete primary directories | 3 | No final result or trajectory. Do not count missing traces as zero-turn completions. |
| Oracle/no-op controls | 2 | Both retain final verifier evidence; agent trajectories are not applicable. |

Of 287 normal model runs, 285 record an explicit requested effort matching their configuration. Two early manual runs have no recorded effort and should remain labeled unspecified. Native and ATIF agent-turn counts agree on all 287. Workbooks exist for 285; missing output on the other two is a recorded model failure, not automatically lost logging. Across the normal model runs, three scored 1 and 284 scored 0; this pooled count is not a model/effort comparison.

The main Sonnet campaign contains 240 normal completions, 20 timeouts, one other exception and one unfinished directory. Its saved plan is **256 per effort**, or 768 trials across medium/high/max; the saved summary is paused at 260 budgeted outcomes. The queued Fable plan is 128 per effort (384 total), with zero completed main-campaign trials. Earlier pilot runs supply separate Fable observations. They must not be described as a completed balanced large-model comparison.

## Confirmed timeout race

[Timestamp and hash evidence](timeout-ordering.json) covers all 20 timeouts. **Ten** have assistant turns and tool returns after Harbor's recorded agent deadline; **nine** have turns after the verifier finished. Their native logs contain 15 additional turns in total and $0.9510835 more recorded cost than the earlier result snapshots. The latest observed response is 20.38 seconds after the deadline. These amounts are recorded telemetry, not an invoice reconciliation.

Two concrete cases:

- [`8JvaaZf`](../../jobs/sonnet-efforts-256-20260913T095218Z/restaurant-weekly-cost-control-a__8JvaaZf/result.json): recorded agent end 10:38:12.776Z, ATIF 35 turns; native trace later records turn 36 at 10:38:26.722Z.
- [`BaGpAUy`](../../jobs/sonnet-efforts-256-20260913T095218Z/restaurant-weekly-cost-control-a__BaGpAUy/result.json): deadline 10:56:14.414Z, verifier finished 10:56:19.309Z, then the agent runs its workbook-building script at 10:56:28.375Z. This is actual post-grading work, not just a delayed file timestamp.

The saved workbook is not bound by hash to the exact workbook read by the verifier. Do not assume those are identical for affected timeouts. Retain all raw evidence; flag the affected records and avoid using the final native trace as an in-budget trajectory. A full retrospective rerun is not necessary merely to recover step counts. Cases used for strict budget or artifact claims need adjudication and, where required, a corrected rerun.

Independent installed-code review identifies the shared Harbor path: `Trial` wraps agent execution in `asyncio.wait_for` and records agent completion on timeout; Docker's buffered subprocess collector does not clean up `CancelledError`, while its streamed cleanup only terminates the host Docker client. Neither guarantees termination of the in-container agent process tree. `SingleStepTrial` then synchronizes output, collects artifacts and proceeds to verification before fully stopping the agent environment. For the restaurant task, the agent can write in the shared verifier environment. Candidate tasks use a separate verifier, but submitted source is collected before stopping the agent container, so their deadline snapshot is also exposed to this lifecycle defect. The candidate runner shares this Harbor execution path; no candidate timeout corruption was established by this audit.

## Changes needed

1. **Enforce agent termination inside the container at its existing deadline**, and wait until the process tree has stopped before converting trajectories, collecting submitted artifacts or running the verifier. Killing or canceling the host `docker exec` client is insufficient. Preserve original time/resource limits.
2. **Bind one final snapshot to the result.** Record the agent-stop time, native/ATIF trace hashes, artifact snapshot hash and verifier-input identity. Require stopped agent → immutable snapshot → verification ordering. Diagnose any later writes as an orchestration error. Validate this with a deterministic delayed-writer harness, without paid model calls or changes to the supplied restaurant task.
3. **Add retrospective metrics to the summary/export:** agent turns, tool calls, usage, phase runtime, requested effort, returned model alias, trace-completeness status and timeout-censoring flags. Existing complete traces already contain these; a new agent is not needed for counting them.

Secondary improvements: narrow blanket classification of verifier timeouts as infrastructure, retain a budget-aware denominator for the earlier pilot (its old summarizer excludes all exceptions), and optionally bound infrastructure-repair attempts/campaign spend. The newer candidate runner already distinguishes some verifier-budget failures. These are smaller concerns than preventing post-deadline writes.

## Counting and measurement limits

- Agent turns are native `role=assistant` messages or ATIF `source=agent` steps on a consistent snapshot. Tools are separate: one sample timeout has nine turns and fourteen tool calls. Attempted API calls can also differ from completed turns.
- Native `model_kwargs.output_config.effort` proves the requested setting. ATIF effort is copied from configuration; it is not independent provider confirmation. Native response metadata supplies the gateway-reported model alias, not an immutable backend model snapshot.
- Result and ATIF totals contain reported usage/cost. ATIF per-step cost is apportioned by completion-token share by Harbor; use native per-response cost for call-level analysis. Preserve pre-deadline and later costs separately on affected timeouts.
- ATIF `reasoning_content` may duplicate visible assistant prose. Hidden thinking text is absent in sampled native responses even when reasoning-token counts exist. Quote actions and visible explanations without presenting them as hidden reasoning.
- Stage timings and response/tool completion timestamps are available. Exact request-start, retry and provider/network latency spans are not; they would require additional telemetry if that becomes an analysis goal.

Reproduce the inventory with `python3 scripts/audit-restaurant-evidence.py`. It reads only selected metadata into the research output and does not expose prompts, commands, environment variables or credentials. The timestamp ordering file preserves source hashes for the timeout findings. All 36 original supplied files were checked unchanged after this audit.

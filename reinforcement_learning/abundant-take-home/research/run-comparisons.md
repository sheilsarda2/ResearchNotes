# Running the candidate comparisons

The later [Huey demand review](huey-demand-review.md) downgrades the three Huey candidates on impact evidence. The prepared 60-trial plan remains a technical screening plan; it is not an impact-qualified selection. Use a new plan/prefix to benchmark a revised subset.

Use these commands from the `abundant-take-home` directory. The default `research/benchmark-configs/durable-screen-v1-lock.json` is already prepared; skip directly to **run** to use it. The plan command below shows how to create another campaign: choose a new prefix when regenerating, because existing configurations are never overwritten. The plan step checks that each selected candidate has accepted Harbor oracle/no-op evidence bound to its current file digest, verifies the saved result hashes and rewards, and rejects revisions made since validation. It writes configurations and a checksum lock without calling any model. It will reject unfinished candidates while the pack is under construction.

```bash
uv run --python 3.12 python scripts/candidate-bench.py plan \
  --models anthropic/claude-sonnet-5 anthropic/claude-fable-5-1 \
  --efforts high --attempts 3 --concurrency 2 \
  --prefix durable-screen-v1
```

With ten candidates, this plans 60 trials: three independent attempts for each of two models on every task. Each attempt has a two-hour agent limit; this is a cap, not a predicted runtime. Harbor's mini-swe-agent integration has no dollar cap by default. Start with fewer tasks or one attempt using `--tasks TASK_ID ...` and `--attempts 1` if desired. Use a new prefix for each experiment. Keep effort and budgets matched within a comparison; a later max-effort campaign is a separate condition. Additional available model versions should be separate conditions too: the current FlashFS table has Fable 5 above Fable 5.1, so a newer version alone does not establish strongest-model coverage. See the freshness audit for the exact published counterevidence.

Activate the same take-home gateway credentials used by your existing Harbor runs. The script inherits exported credentials; it does not read, print, or modify `.env`. Both gateway base URL fields are preserved in each task configuration.

The following command starts model calls. It runs the two model jobs sequentially, with up to two task trials concurrently inside each job:

```bash
uv run --python 3.12 python scripts/candidate-bench.py run \
  --lock research/benchmark-configs/durable-screen-v1-lock.json
```

`--configs CONFIG_BASENAME ...` can select individual jobs from the lock. If a job already exists, the script refuses to overwrite it. Use Harbor's explicit job-resume workflow for interrupted jobs, retaining the original job tree. A changed task or configuration requires a new comparison plan. The plan pins Harbor 0.15.0 and mini-swe-agent 2.4.6; record installed transitive harness dependencies and model service revisions where available, because package versions and model aliases alone do not guarantee permanent identical behavior.

Summarize without changing any raw result or trajectory. The summary verifies configuration hashes and each result’s configured model, effort, and task checksum before assigning it to a comparison:

```bash
uv run --python 3.12 python scripts/candidate-bench.py summarize \
  --lock research/benchmark-configs/durable-screen-v1-lock.json \
  --output research/headroom-results.json
```

The JSON keeps every trial path, exact configured model/effort, binary reward, exception, runtime, ATIF assistant-step count, tool-call count, cost, and Harbor task checksum. Per-task/model rows show budgeted success rates and Wilson intervals, with agent timeouts included in the denominator. Harbor still verifies the final artifact after an agent timeout: reward 1 counts as a budgeted success, while reward 0 or a missing reward does not. A separately named conditional scored rate excludes exceptions. Observed-attempt success includes every existing result, including infrastructure failures, and is not a pure capability measure. Missing results, other exceptions and successful step counts are reported separately. An empty or unfinished campaign stays unmeasured; it does not turn into a zero success rate. Other exceptions need explicit adjudication before ranking; agent timeouts already contribute to the budgeted rate according to their final verifier reward. Preserve failures caused by real task difficulty, and rerun documented infrastructure failures under the same frozen configuration.

The first three trials are a screening sample, with wide uncertainty. Read both successful and failing trajectories and source artifacts. Check for specification ambiguity, verifier defects, environmental failures, shortcuts, and public solution retrieval. Compare substantive failure mechanisms, not only test counts. Select the final three after headroom and the required horizon have actually been observed. Copy retained tasks into `samples/` only at that later selection stage, retaining the original jobs/ATIF and documenting any task revision.

Research notes and generated summaries remain under `research/`. The final `report/` prose is for the human author, as required by the assignment.

This pack does not yet demonstrate a supply of 1,000 independent tasks. Varying test data, ID collisions, clocks, or interruption points improves verification but does not create a new engineering task. A credible scaling claim needs a measured authoring process across additional repositories and distinct feature contracts, including rejection rates for duplicates, shallow tasks, ambiguous requirements, and invalid verifiers.

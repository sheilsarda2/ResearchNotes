# Independent measurement and verifier audit

Checked 2026-09-13 UTC. This is an independent audit of the ten candidate contracts and selected grading paths, not model-performance evidence. No model calls or external messages were made. Supplied take-home files were left unchanged; all 36 files in original commit `c1ae968` matched their original contents at the audit check.

**Disposition: all four reproduced false-negative variations now pass their corrected feature suites, and the runner issues are corrected.** Huey test restoration has been added and reviewed; the checkpoint wrapper and reshard clock checks have also been revised. Current full-Harbor acceptance and revision bindings are authoritative in [shortlist.json](shortlist.json), which is updated by final integration. This audit records historical failures and its own post-fix checks, rather than duplicating a construction-status ledger. No finding establishes that an agent has, or lacks, the requested capability.

## Reproduced contract-compliant variations that failed grading

Each experiment started from the task's pinned source and reference patch in a disposable Docker container with `--network none`. The candidate directory was mounted read-only. Only the stated implementation variation was made inside the container, and the complete independent feature test file was run. Container changes were discarded. Exact transformations, image IDs, reference-patch hashes and result counts are in [counterexample evidence](sources/independent-measurement-counterexamples.json).

| Task | Valid variation | Observed result | Why it is a false negative |
|---|---|---|---|
| Huey SQLite outbox | Return `list(envelope['task_ids'])` instead of a tuple from the receipt constructor. | 27 passed, 1 failed. | `test_staging_is_part_of_business_commit_and_rollback` compares against `('committed-work',)`. The public contract specifies deterministic ordered IDs, without prescribing a tuple. |
| Luigi generation target | Import the existing `os.open` function as `_native_open` and call that alias with unchanged arguments. | 47 passed, 1 failed. | `test_unrelated_io_errors_propagate` replaces only the current `os.open` module attribute. The identical aliased function never receives the injected error, so the test incorrectly expects `PermissionError`. |
| Luigi checkpoint task | The same `os.open` alias substitution. | 41 passed, 1 failed. | `test_io_failures_propagate` has the same injection-path dependency. Actual filesystem/error behavior is unchanged. |
| sqlite-utils schema plan | Skip unnecessary rebuilds for operations already completed by the reference's native rename/drop loop; do not recreate indexes/triggers on tables that were not rebuilt. | 117 passed, 2 failed. | Both late-authorization and process-death tests demand `SQLITE_DROP_TABLE` for table `z` on a rename-only plan. Native `ALTER TABLE ... RENAME COLUMN` is explicitly allowed and needs no drop. One test receives no injected error; the subprocess exits successfully instead of forced exit 73. |

Recommended corrections are narrow: compare ordered IDs independent of list/tuple representation; generate permission failures through a real inaccessible filesystem surface with suitable process privileges; and trigger schema interruption at an operation actually required by the selected plan. Simply adding more monkeypatch entry points would still miss imported aliases. Tests may legitimately specify an injection boundary publicly, as the generation/checkpoint contracts already do for atomic `os.replace`, but should not accidentally infer another boundary from the reference implementation.

**Remediation status at handoff:** independently rerunning the same variations against revised verifiers passed **28/28 outbox, 119/119 schema, 48/48 generation and 42/42 checkpoint tests**. The Luigi tests now use actual OS permission failures in a subprocess with appropriate credentials, so the opening alias no longer matters. [Post-fix evidence](sources/independent-measurement-postfix-variants.json). Use the accepted runs and task bindings in [shortlist.json](shortlist.json) for final full-Harbor verification status.

## Additional concrete grading and measurement issues

### Transferred Huey regressions are not protected by the separate container

The Huey tasks transfer `/workspace/repo/huey`, including `huey/tests`. Their verifier scripts execute selected `huey/tests/test_*.py` files from that transferred package. Consequently, an agent's permitted upstream-test edits can replace graded regressions in the fresh container. Independent feature tests under `/tests` remain separate; this does not by itself bypass that suite or prove a false success.

This is a distinct, simpler issue than arbitrary malicious Python importing as root. It also conflicts with the pre-correction statement that original upstream regressions are restored. Root added a pristine image-build copy at `/opt/pristine-huey-tests`; all three Huey verifier scripts now replace the transferred `huey/tests` with that copy before collection. This correction was reviewed in source. Revalidate each affected Huey task and record a fresh task identity.

### A task-budget timeout is excluded from the headline success-rate denominator

In the audited `scripts/candidate-bench.py`, `scored_without_exception` requires `exception is None`; `success_rate` and its Wilson interval use only those rows. An `AgentTimeoutError` is therefore excluded even though a two-hour agent budget is part of the experimental condition. One scored success and two agent-budget timeouts yield `success_rate = 1.0`, not the campaign's one success in three attempts. The surrounding prose discloses exclusions, but this metric can still mislead task ranking.

Root corrected the runner to expose `conditional_scored_success_rate`, `budgeted_success_rate`, `budgeted_successes` and `observed_attempt_success_rate`, with other exceptions separate. Harbor still verifies artifacts after `AgentTimeoutError`; the timeout's final reward therefore governs whether its budgeted attempt passed. Independent synthetic checks with one clean success and two timeout failures gave rates of **1, 1/3 and 1/3**. Changing one timeout's final reward to 1 gave **1, 2/3 and 2/3**. A final check confirmed that a timeout's passing 90-step artifact is included in successful-step reporting while a failing 100-step artifact is excluded. The installed Harbor `single_step.py` catch/continue path was independently inspected. [Runner check evidence](sources/independent-measurement-runner-checks.json). Do not automatically classify timeouts as infrastructure failures or artifact failures. Small screening samples still require wide uncertainty intervals and trajectory review.

### Oracle/no-op acceptance and summary labels are not fully bound to the frozen inputs

The audited `plan()` checks only `oracle_verified` and `nop_verified` booleans in `shortlist.json`; it does not read the cited results, verify their rewards, or bind their evaluated task contents to the current task digest. A task can change after accepted validation while both flags remain true, and planning accepts its new bytes. `run()` does check the plan's task/config hashes, which is useful, but cannot repair stale validation accepted when the plan was made.

Similarly, the original `summarize()` read current configuration files to label model and effort without checking the lock's `config_sha256`. Editing a config after the run could relabel recorded results. Root added accepted task digests, immutable result-evidence hashes and reward/agent/task-checksum validation at planning, plus locked configuration and recorded model/effort/task-checksum checks at summarization. The task digest now covers the entire task directory to match Harbor's checksum scope. Independent synthetic checks confirmed changed configs and stale validation digests are rejected, and a changed construction note changes the digest. [Runner check evidence](sources/independent-measurement-runner-checks.json). The final refreshed shortlist must populate these bindings from the new accepted Harbor runs.

## Static risks requiring a targeted correction or explicit decision

These were identified by code/contract review, without building a complete alternative implementation.

- **Checkpoint yield representation:** the original `suspend()` required `luigi.DynamicRequirements`, and later helpers directly accessed `.flat_requirements`. The instruction requires Luigi's normal dynamic scheduling and correct cache behavior, not this particular yield wrapper. The revised helper normalizes ordinary nested yields and wrapped requirements. This source change was independently reviewed; a complete plain-yield alternative was not built by this auditor.
- **Reshard test clock:** the original TTL test assigned epoch-1000 clocks only to `diskcache.core.time` and `diskcache.fanout.time`. A compliant new migration module consulting its own `time.time()` would see the real date and could treat fake entries as expired. The contract has no public clock-injection API. The verifier review agent replaced this with real past/future deadlines and exact stored-deadline comparisons; this removes the identified clock-domain mismatch. Final full-suite evidence is owned by the verifier review agent and the shortlist.
- **Streaming checks have bounded coverage:** the resumable-import streaming test wraps `Path.open`, so whole-file reads through another supported opening API evade that particular check. The reshard streaming check similarly needs inspection when interpreting a success. These are coverage limits, not reproduced false successes in this audit; they do not justify adding arbitrary difficulty or claiming memory guarantees have been exhaustively tested.

## Coverage and remaining limits across the ten tasks

| Candidate | Audit focus | Result of this bounded review |
|---|---|---|
| Huey SQLite leases | Package transfer, existing-worker integration, reserve/expiry rules, retry/due-batch crash hooks. | Shared upstream-test transfer defect; no additional confirmed behavioral contradiction. |
| Huey SQLite outbox | Caller transaction ownership, graph identity, receipts, publication primitive and process barriers. | Shared transfer defect plus reproduced tuple-only assertion. |
| Huey failure redrive | Terminal-error scope, replay/lineage boundary, concurrent redrive and interruption. | Shared transfer correction reviewed; current Harbor acceptance is recorded in the shortlist. |
| sqlite-utils resumable import | Batch checkpoint atomicity, immutable source identity, CLI and input guard. | No confirmed new contradiction; streaming check does not cover every read path. |
| sqlite-utils relational merge | Canonical mapping, WAL identity, nested transaction/trigger semantics, before/after-commit process exits. | No additional confirmed contradiction; fault hooks target publicly prescribed metadata tables. |
| sqlite-utils schema plan | Native renames, dependency/transaction preservation and authorizer interruption. | Two reproduced algorithm-dependent false negatives. |
| Luigi generation target | CAS publication, pinned readers, process termination, integrity/error handling. | Reproduced open-alias false negative. |
| Luigi checkpoint task | Journal transitions, dynamic scheduling, cache invalidation, fencing and errors. | Reproduced open-alias false negative; wrapper constraint found by inspection. |
| DiskCache online reshard | Public topology record, work budget, migration state, metadata barriers and TTL. | Clock risk found by inspection and handed to the verifier reviewer; current readiness is recorded in the shortlist. |
| DiskCache snapshot/restore | Snapshot inventory/verification, restoration, file payloads, shard concurrency and process barriers. | No additional confirmed contradiction in selected paths. Existing strict type identity assertions merit caution if alternate subclasses are intentionally allowed. |

The suite generally uses explicit synchronization barriers and observable durable state, rather than arbitrary elapsed-time performance thresholds. Finite 10–30-second child/barrier timeouts remain environment limits; this review did not conduct a repeated-run flakiness campaign. The reference solutions and existing mutant/oracle/no-op records demonstrate useful controls, not complete verifier correctness. The neutral variations above show why oracle=1 and nop=0 alone are insufficient.

The already documented limitations remain: submitted Python is imported as root during grading; a separate offline verifier alone does not isolate hostile runtime code. Agent public egress also remains enabled in local Docker, while apt installation is not a hermetic snapshot. These are known boundaries, not newly demonstrated exploits. Review submitted source and trajectories before accepting final successes.

## Primary-source refresh and implications

The SWE-Refactor-Bench maintainer confirmed four reproduced grader defects on **2026-09-10**. Three involved release-recording drift; the fourth had filesystem-dependent expected behavior and depressed published scores. [PR 9](https://github.com/Einsia/SWE-Refactor-Bench/pull/9) was merged that day. The maintainer described offline source/recording guards and future score reruns. This independently confirms the existing freshness note; it does not establish that every benchmark result is invalid. [Maintainer response](https://github.com/Einsia/SWE-Refactor-Bench/issues/8#issuecomment-5616543994). Fresh API responses are saved in [correction evidence](sources/independent-measurement-benchmark-corrections.json).

The **September 2026** FrontierSWE v2 methodology explicitly separates verifier containers from user/permission separation: submitted code otherwise inherits the grader's privileges. It also reports that shared-host timing changed performance rankings and that revised tests aim to give agents enough information for full credit. These observations support auditing protected surfaces, injection assumptions and reproducibility here. They do not prescribe the same harness or score for this pack. [FrontierSWE v2 methodology](https://www.frontierswe.com/blog/v2).

The live Crash-Proof Flash Filesystem table, checked **2026-09-13**, still reports Fable 5 at 0.9709 with 481 average steps and Fable 5.1 at 0.7779 with 249. These are adjacent-task scores under a different harness, not binary solve rates for these contracts. Strong adjacent performance remains counterevidence to assuming durability alone creates frontier headroom. [Task results](https://www.frontierswe.com/tasks/flash-fs).

METR's current definition, checked **2026-09-13**, measures task difficulty in estimated expert-human completion time and fits success probability against that duration. It does not interpret assistant steps as human duration. [METR methodology](https://metr.org/time-horizons/). The pack correctly labels headroom and >75-step horizon as unmeasured. A later successful trajectory exceeding 75 steps shows observed effort under that harness; it does not prove 75 steps were necessary. Do not retain a task merely because a failed agent looped for many actions.

## Handoff gate

Before paid trials, require completed corrections, fresh oracle/no-op acceptance for every changed task, current task/config hashes, and the exact budgeted scoring denominator. [shortlist.json](shortlist.json) is the authoritative gate: its accepted evidence and validated revision hashes supersede construction-time status in this audit, and the corrected planner checks those bindings. Keep this audit's original counterexample outcomes as historical evidence. Continue to describe all ten as candidates until real, adjudicated model trials measure headroom and horizon; the current pack also supplies no empirical evidence of 1,000 independent tasks.

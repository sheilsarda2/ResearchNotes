# Candidate evaluation: fifteen engineering tasks

Current comparison: the [Pugh chart](#pugh-chart-suitability-for-the-long-horizon-swe-brief) now covers all fifteen tasks. The ten-task review and its final-three recommendation below are retained as historical findings; they do not describe the five later additions.

## Original ten-task review

Checked 2026-09-13. AI-assisted review of the ten runnable candidates in `../candidates/`, written to support the user's selection of the final three. It is not the human-written client report. No model trials were run, no Docker images were rebuilt, and no candidate, research, or supplied take-home file was modified by this review except the removal of reviewer-created `__pycache__` bytecode under `candidates/sqlite-utils-schema-plan/tests/` (all ten task digests match `shortlist.json` after that cleanup).

Method: ten independent per-task reviewers each read the full instruction, hidden tests, reference patch, verifier scripts, provenance and construction notes for one task and graded it against a fixed rubric (instruction/verifier alignment, verifier robustness and gameability, reference-solution scope, Harbor packaging, horizon plausibility, novelty). The coordinator independently verified Harbor 0.15.0 separate-verifier support, accepted-run durations, no-op failure modes, and digest bindings, and folded in the concurrent Huey demand audits (`huey-demand-review.md`).

## Bottom line

All ten are runnable, oracle=1/nop=0, and packaged consistently. None is ready to ship unedited: every task has at least one place where the hidden tests pin a convention the instruction never states, and seven tasks still fault-inject at an implementation boundary that a compliant alternative design never crosses. These are the same two defect classes the independent measurement audit already fixed in four places, which shows that audit was not exhaustive. All fixes are small (instruction sentences, test-file edits, `test.sh` hardening) and none requires changing a reference solution, but each edited task needs a fresh oracle/no-op run because the planner binds digests.

| Task | Reviewer verdict | Undisclosed pins (HIGH/MED) | Injection-boundary risk | Depth (compounding vs transcription) | Saturation risk | Demand evidence | Tier |
|---|---|---|---|---|---|---|---|
| huey-sqlite-leases | TOP-3 after fix 1 | 0 / 4 | HIGH: 5 tests patch storage instance methods | Compounding (executor tail, scheduler, fork, SIGKILL) | Low-Med | Strongest in pack: reproduced SIGKILL loss, users #598/#733/#743; maintainer excludes ACKs deliberately | A |
| luigi-generation-target | TOP-3 after fix 1 | 0 / 5 | LOW: `os.replace` boundary is disclosed | Compounding (pins, prune, CAS, crash boundary) | Low | Luigi docs disclaim multi-output atomicity; PR1066 (2015) adjacent | A |
| sqlite-utils-schema-plan | FIX-FIRST, then TOP-3 | 0 / 5 | MED: authorizer interrupts assume ALTER/DROP on the live connection | Compounding (13 SQLite mechanisms); best depth of the sqlite trio | Low | Issue #849 / PR854 document current trigger loss and stale views (user-reported) | A |
| sqlite-utils-relational-merge | TOP-3 after fixes 1-2 | 1 / 5 | LOW: triggers on public metadata tables only | Compounding (one transaction, cycles, WAL identity) | Low-Med | Issue #491 / PR724 want generic merge; remapping demand unshown | A |
| luigi-checkpoint-task | TOP-3 after fixes | 0 / 3 | MED: `os.replace` disclosed; plain-yield can loop for 900s | Compounding but ~60 enumerated rules; worker.py edit only implied | Med | Docs require idempotent run(); PR3366 partial | A- |
| diskcache-online-reshard | TOP-3 conditional | 1 / 4 | LOW: `os.replace` disclosed; blocking lease deadlocks for 900s | Deepest in pack; likely bimodal, possibly 0% for Sonnet | Very low | None found publicly | B |
| diskcache-snapshot-restore | STRONG BACKUP | 0 / 2 | none (no monkeypatching) | Smallest patch; spec is transcription-grade | Med-High | None found; SQLite backup + copy is textbook | C |
| sqlite-utils-resumable-import | FIX-FIRST, then BACKUP | 2 / 4 | HIGH: fault triggers fire only on UPDATE | Compact core; pattern well known | Med | PRs #810/#812/#856 are progress display, not recovery | C |
| huey-sqlite-outbox | TOP-3 conditional (reviewer) | 1 / 4 | MED: instance patches of `enqueue_many`, `utcnow` | Compounding, but ~40% convention checks | Low | Demand audit: remove from impact picks pending direct demand | D |
| huey-deadletter-redrive | FIX-FIRST | 4 / 3 | MED: `serialize_task`, `_get_timestamp` patches | Easiest Huey task; a third of asserts are transcription | Med | Demand audit: remove pending an operator workflow | D |

Tier A: trial first after small fixes. Tier B: the hard anchor, trial only after its "may raise" defect is fixed and a per-test timeout exists. Tier C: backups with saturation risk. Tier D: demoted on the demand axis plus hidden pins; keep as archive material.

## Recommended final three, pending trials

1. **huey-sqlite-leases** (ownership and at-least-once delivery inside a live consumer).
2. **luigi-generation-target** (atomic multi-file publication with compare-and-swap and pinned readers).
3. **sqlite-utils-schema-plan** (dependency-preserving multi-table schema rebuild in one transaction).

Why these three: three repositories, three different durable boundaries, each with user-facing evidence that the gap is real on current upstream, each rated as compounding engineering rather than API glue, and each fixable with instruction sentences plus test edits that leave the reference solution untouched. Alternates in order: relational-merge (swap for schema-plan if the authorizer injection cannot be made behavioral quickly), checkpoint-task (swap for generation-target if the drop-root probe misbehaves in the client's environment), reshard (swap in if trials show the Tier A picks saturate).

Trial plan under the 48-hour clock: apply the per-task fixes below, rerun oracle and no-op for each edited task, re-plan with a new prefix, then run one Sonnet-5 high attempt on the five Tier A tasks (five trials, two-hour cap, concurrency 2 to 3, roughly four hours wall). Read those trajectories, choose the three, then run two more attempts each on the chosen three, plus the Fable comparison only if time remains. That is under a dozen model trials instead of the sixty in the current lock, and it still satisfies the brief's requirement that the final three rest on observed trajectories.

## Pack-wide defects and fix recipes

1. **Undisclosed fault-injection boundaries.** Tests inject failures by replacing a specific attribute the reference happens to call. A compliant design that routes the same write differently never hits the fault and fails, or hangs on a barrier. Instances: leases patches `h.storage.add_to_schedule` and `h.storage.enqueue` on the instance (5 tests: `test_leases.py:396,422,451,560,598`); outbox patches `h.storage.enqueue_many` after construction (`test_outbox.py:458,547`) and `huey.utils.utcnow` (`:340,345`); redrive patches `h.serialize_task` (`test_deadletter.py:337,457`) and `h._get_timestamp` (`:157,269,323`); resumable-import injects via `BEFORE UPDATE ON _sqlite_utils_imports` triggers (`test_resumable_import.py:169,256`) so an `INSERT OR REPLACE` checkpoint never fires them; schema-plan interrupts on `SQLITE_DROP_TABLE z` / `SQLITE_ALTER_TABLE z` on the live connection (`test_schema_plan.py:349,404`). Fix: either publish the boundary in the instruction (as generation-target and checkpoint already do for `os.replace`) or inject at a behavioral point (any write to a prescribed metadata table, a failing serializer object, a class-level patch).
2. **Exception types pinned but never named.** Instructions say "reject" or "must be a positive integer"; tests demand `ValueError` (outbox 10 sites, redrive 9 assertions, merge 8+ cases plus `sqlite3.IntegrityError` for trigger tampering, generation 3, checkpoint 1, reshard 1) or `TransformError` for an authorizer denial (schema-plan `:352`). `TypeError` for wrong-type arguments is idiomatic and would zero the reward. Fix: one sentence per instruction: "argument, backend and connection validation failures raise `ValueError` (or a subclass); underlying `sqlite3` errors propagate unwrapped."
3. **Container-type and equality pins.** Redrive requires hashable receipts (`task_ids` must be a tuple, `test_deadletter.py:483`), `failures()` to be a list, and `FailureRecord` value equality; merge requires `key_map` pairs as lists (`test_relational_merge.py:82,112,136,402`); snapshot requires `type(restored) is type(c)` (`test_snapshots.py:72`). Fix: compare through normalization or state the shape.
4. **Reward computation gaps.** Luigi and reshard use loose floors (`total >= 35/40`), snapshot uses `tests > 0`; Huey and sqlite-utils require passed == collected but no exact count. The three Huey `test.sh` files run `|| exit 1` before writing the default `reward.txt`, so a missing pristine copy leaves no reward file. The three sqlite-utils verifiers run `pytest.main` in-process, so import-time code in the submitted package shares the grading interpreter. Fix: exact expected counts, write `reward.txt` first, run pytest in a subprocess and derive the reward from `results.xml`, and add a tripwire grep of the submitted package for `_pytest|atexit|/logs`.
5. **No per-test timeout.** A checkpoint implementation without completion-cache bypass loops forever in `_run_get_new_deps`; a reshard implementation with a blocking lease deadlocks against the test's barrier. Both still score 0 but burn the full 900-second verifier budget and leave no diagnostic. Fix: `timeout 840` around pytest or a pinned `pytest-timeout`.
6. **Spec density versus binary reward.** Instructions run 808 to 1263 words against the sample's 667, with 13 to 34 hard-constraint verbs each; hidden suites range from 30 to 119 cases. Every reviewer flagged that roughly a third of assertions check conventions rather than the durable boundary. A reward of 0 therefore cannot by itself distinguish "missed the transaction boundary" from "used TypeError". This matters for the client pitch: failure attribution must come from trajectory review, and the report should quote only failures traceable to the claimed boundary.
7. **Dead spec.** Every instruction asks for documentation, but docs are outside the transferred package and nothing checks them. Several stated guarantees (fsync, symlink refusal, `pending` accounting, unknown top-level manifest fields accepted) have no test. Either drop them or accept them as flavor; do not claim they are verified.
8. **Packaging nits.** `diskcache-snapshot-restore/task.toml` uses `schema_version = "1.3"` plus `[metadata] name`, which is Harbor 0.15's canonical form, while the other nine use the legacy alias `version = "1.0"`; both parse, but normalize to the sample. Stray `tests/__pycache__` bytecode exists in generation-target, reshard and snapshot; `COPY . /tests` ships it into the verifier image and, because the planner digest hashes every file, any local pytest run silently changes the task identity (this happened to schema-plan during review and was reverted). Add `.dockerignore` files and delete the caches before the next validation. Huey and the sqlite trio were Docker-validated on ARM64 only; the base image is a multi-arch digest, so run one AMD64 oracle before shipping.
9. **Demand axis.** Only the three Huey tasks have a demand audit. Leases is the strongest lead; outbox and redrive were removed from impact-prioritized picks. For the other seven the proxies above come from provenance and freshness notes, not from a matched audit.

## Verified pack facts

- Harbor 0.15.0 implements separate-verifier mode (`harbor/trial/trial.py` `_run_separate_verifier`, `VerifierEnvironmentMode.SEPARATE`) and `[[artifacts]]`; the accepted oracle trials' `artifacts/manifest.json` show only the package directory transferred. The client's plain `harbor run -p <task>` will honor `tests/Dockerfile` and `[verifier.environment]`.
- Accepted verifier runs took 5 to 15 seconds (snapshot-restore the longest at 15s, running the full upstream core and fanout suites) against a 900-second limit.
- No-op baselines fail at import time for the three Huey tasks and snapshot-restore (one collected item, one collection error), and on real assertions for the other six. Both satisfy nop=0; the former is a weaker control and should be supplemented by the existing mutant evidence in the report.
- All ten `validated_task_sha256` digests match the current directories.

## Per-task findings

### huey-sqlite-leases (TOP-3 after fix 1)
Instruction 897 words; 21 functions / 29 cases; patch 395 added lines across storage, api, consumer, exceptions, `__init__`; 164 upstream regressions; oracle 193 in 3.9s.
- [HIGH] Five tests fault-inject by replacing storage instance methods with positional wrappers; a design that performs completion or due-transfer writes through its own cursor never hits the fault (`test_leases.py:396,422,451,560,598`). Instruction lines 30 and 32 only say the writes "commit together".
- [MED] `inflight_count()==1` after `KeyboardInterrupt` (`:378`) forbids releasing the lease on interrupt, which the prose ("keeps its work recoverable") permits. `reserve(now=nan)` must raise `ValueError` (`:152`); commit-time loss must raise `LeaseLost` out of `execute` (`:359`); fork safety of a parent-opened connection with process workers is implied, not stated (`:618`).
- Verifier: sound reward logic; pristine test restore closes the transfer hole; `-c /dev/null --confcutdir=/tests` blocks planted conftests; barriers use pipes with failure-only timeouts.
- Reference: faithful; separate `huey_lease` table, savepoint-nesting `db()`, five terminal paths wrapped, `enqueue_due` scheduler refactor, fork handling. One design limitation worth documenting: a poison scheduled message stalls the whole due batch under leased `enqueue_due`.
- Horizon: compounding (nested-transaction trap, holding the write lock across the task body, delayed and pre-execute-cancel paths, inherited connections). Saturation risk real but the real-consumer SIGKILL and fork tests give headroom.
- Fixes: declare in the instruction that completion and due-transfer writes route through the storage's public `enqueue(data, priority)` and `add_to_schedule(data, ts)`; state that an interrupted attempt leaves its reservation in place, non-finite `now` raises `ValueError`, and a failed commit-time ownership check raises `LeaseLost`; add a sentence about parent-opened storage remaining usable in forked workers.

### huey-sqlite-outbox (Tier D: demoted on demand; reviewer said TOP-3 conditional)
Instruction 1181 words; 20 functions / 28 cases / 106 asserts; ~330 runtime lines (`graph.py`, `contrib/sqlite_outbox.py`, storage, api).
- [HIGH] `ValueError` pinned at ten sites, never named (`test_outbox.py:126,160-174,185,399-405,447`); `TypeError` fails five of twenty functions.
- [MED] Caller graph must be unmutated on success (`:235,344`) though upstream `enqueue` itself mutates; `initialize_schema` must validate same-file (`:169`); `enqueue_many` must re-raise the original `sqlite3` error (`:190`); generator members must be rejected (`:426`).
- [MED] Instance patches of `enqueue_many` after construction (`:458,547`) and of `huey.utils.utcnow` (`:340,345`).
- Verifier otherwise sound; `test.sh:6` `|| exit 1` precedes the reward write. Competing-publisher race can pass a wrong deferred-`begin` design by luck (`:504-534`).
- Reference: faithful; immediate-mode result reading via `copy.copy(huey)` touches private attributes.
- Demand audit: producer-death loss reproduced on unmodified upstream, but no Huey user report matches same-file SQLite plus graph preservation; Honker and hexastack-events are public adjacent implementations.

### huey-deadletter-redrive (Tier D; FIX-FIRST)
Instruction 1199 words; 20 functions / 31 cases; ~265 runtime lines.
- [HIGH] Four hidden pins: `ValueError` for invalid options (`test_deadletter.py:438-443`); `FailureRecord` value equality (`:432`); `task_ids` must be hashable, so tuple (`:483`); redrive ETA/expiry must use the instance clock `h._get_timestamp` (`:157,269,323`), which upstream's natural `Task.resolve_expires()` does not.
- [MED] `serialize_task` monkeypatch (`:337,457`); `expires=None` restores the class default, so a falsy non-None sentinel is required (`:313-329`); `failures()` must be a list.
- [MED] Non-discriminating tests: deny-triggers on all tables mean `put_result` raises first with `results=True` (`:343-349`); atomicity barriers exist only on the upstream `task` table (`:451,491`), so a two-commit design passes.
- Demand audit: issue #718 is a dated question with accepted signal-hook guidance; #815 is a different, fixed problem. Construction notes already flag this as the easiest Huey task.

### sqlite-utils-resumable-import (Tier C; FIX-FIRST)
Instruction 879 words; 19 functions / 51 cases; 131 upstream regressions; runtime ~220 lines.
- [HIGH] Fault injection via `BEFORE UPDATE` triggers on the metadata table (`test_resumable_import.py:169,256`); `INSERT OR REPLACE` or DELETE+INSERT checkpoints never fire them, verified locally.
- [HIGH] `test_open_transaction_refused` passes a nonexistent source and expects `TransactionError` (`:370-378`), pinning validation order.
- [MED] `batch_size=True` rejection, case-insensitive PK distinctness, strict `bool` for `alter`/`upsert` (`:357-361`); exact checkpoint offset after the Nth line terminator (`:122,150`); raw `sqlite3.IntegrityError` unwrapped (`:172,190,212`); metadata table must not exist after a failed first batch (`:247,367`).
- Verifier: in-process `pytest.main`; streaming guard wraps only `Path.open` (known coverage gap).
- Horizon: compact, well-known pattern; reviewer expects a Sonnet-class agent to finish in 40 to 80 steps. Real core (atomic rows plus schema plus checkpoint across `insert_all` sub-batching) but likely below the 75-step bar.

### sqlite-utils-relational-merge (Tier A; TOP-3 after fixes 1-2)
Instruction 1038 words; 24 functions / 52 cases; 97 upstream regressions; runtime ~305 lines.
- [HIGH] Trigger-tamper detection must raise `sqlite3.IntegrityError` (`test_relational_merge.py:286`) where no native error occurs.
- [MED] `ValueError` for unsupported layouts and exhaustion (`:385,413`); inconsistent accepted tuples (`:364` vs `:431`); `key_map` pairs must be lists; native errors propagate unwrapped (`:205,268`); `defer_foreign_keys` is cleared by any autocommit statement, so it must be captured before introspection (`:226-241`, four cases).
- Verifier: fault triggers attach only to publicly prescribed tables; deterministic `os._exit` crash tests; in-process pytest.
- Reference: faithful, uses upstream `atomic()`, casing helpers, read-only URI snapshot, typed sha256 fingerprint, `sqlite_sequence` high-water mark, `foreign_key_check`.
- Horizon: four independent traps (cycles under an open caller transaction, pragma capture order, WAL identity, eager metadata DDL); reviewer guesses 15 to 35 percent Sonnet pass rate.

### sqlite-utils-schema-plan (Tier A; FIX-FIRST then TOP-3)
Instruction 943 words; 28 functions / 119 cases; 213 upstream regressions; runtime ~500 lines. The reviewer ran reference plus all 332 tests locally on SQLite 3.53.1 (Python 3.11 and 3.12): all pass, so the pack is not runtime-fragile.
- [MED] Hidden numeric grammar: `'1_000'` and fullwidth digits must be rejected (`test_schema_plan.py:194-196`) though Python numerics accept them; NUL in a default string (`:205`) needs the `CAST(X'..' AS TEXT)` trick; rejecting no-op PK type/nullability entries (`:235-236`); `UserDict` at all levels (`:427-433`); `TransformError` for an authorizer denial (`:352`).
- [MED] Authorizer interruption assumes `DROP TABLE`/`ALTER TABLE` on the live connection (`:349,404`); a clone-and-`backup()` or `writable_schema` design never trips it.
- Verifier: sound reward; hypothesis seeded; busy test deterministic; in-process pytest.
- Horizon: thirteen interacting SQLite mechanisms (FK pragma no-op inside a transaction, `legacy_alter_table` for temp-to-final rename, lossless DDL preservation, ON CONFLICT row loss, `defer_foreign_keys` reset at COMMIT). Best depth of the sqlite trio; low pass rate expected.
- Demand: issue #849 and PR854 document current trigger deletion and stale views; native SQLite 3.53 constraint alteration is disclosed and irrelevant on the pinned 3.40.1 runtime.

### luigi-generation-target (Tier A; TOP-3 after fix 1)
Instruction 1033 words; 26 functions / 48 cases; 9 upstream regressions; runtime 495 lines in one new module.
- [MED] Writer misuse must raise `ValueError` (`test_generation_target.py:173-189`); `keep` includes the current generation (`:304-305`); `prune()` on an absent root must not create it (`:94,97`); exact member-entry dict equality (`:123`); `writer.path` usable after `writer.open` (`:152,205-209`).
- [MED] The drop-root probe imports the transferred package as uid 65534 (`:504`); if the artifact copy is not world-readable every valid solution fails. The accepted Harbor oracle passed through the real artifact path, so the risk is lower than the reviewer implied, but a `chmod -R a+rX` in `test.sh` is cheap insurance.
- [LOW] Same-process pin detection works with `flock` but not `fcntl.lockf` (`:291-292`): a genuine discriminator, not a defect.
- Verifier: no sleeps; pipes with 15s timeouts; the `os.open` alias false negative is fixed; reference narrowings (requires a `history` field in CURRENT, 32-hex generation IDs) are untested.
- Horizon: pins, prune, CAS publication and the crash boundary interlock; reviewer estimates 80 to 150 bash steps for a strong agent.

### luigi-checkpoint-task (Tier A-; TOP-3 after fixes)
Instruction 1120 words; 23 functions / 42 cases; 8 upstream regressions; runtime ~420 lines plus a 12-line `worker.py` change.
- [MED] Invalid `reset_checkpoint` argument must raise `CheckpointValidationError` (`test_checkpoint_task.py:158-160`); native scheduling must retain PRIVATE parameters, which requires editing `worker.py` because `Task.to_str_params()` drops them (`:242-246`), and the instruction only implies worker integration; `checkpoint_status()` must succeed while another process holds the lock (`:356`).
- [MED] Plain-yield implementation without cache bypass loops forever at `:268`, burning the 900s verifier budget with no diagnostic; `os.replace` module-attribute patch (`:307-336`) is disclosed but alias-fragile.
- [LOW] The `duplicate` corruption case also lacks `data`/`sha256`, so duplicate-key detection is never actually exercised (`:284`).
- Verifier: sound; uid-drop permission test; in-process local scheduler; mutants for fence, checksum and finish gate all caught.
- Horizon: real interplay of journal, lock lifecycle across generator suspension, epoch fencing and the worker's completion cache; but ~60 enumerated rules make part of the reward transcription.

### diskcache-online-reshard (Tier B; TOP-3 conditional)
Instruction 1263 words; 21 functions / 35 cases; 10 upstream regressions; runtime 536 lines in one new module.
- [HIGH] Instruction line 32 says contention "may raise `ReshardBusyError`"; tests require it for a plain `cache['x']=...` in another process while a `transact()` is held (`test_online_reshard.py:407-410,544-550`). A blocking lease deadlocks against the barrier; a lifecycle-only lease surfaces `diskcache.Timeout`. Both readings fail.
- [MED] Adoption rejection must be `ReshardConflictError` (`:322-325`); `size_limit` reads back per-shard (`:53,328`); `collect()` returns retired-generation count (`:197,302`); exact stored-deadline equality plus `check()==[]` effectively forces raw row insertion (`:136,172,202,487`); precedence pins on `expected_epoch` and Busy inside `transact` (`:87-88,223`).
- Verifier: `-o addopts=` correctly neutralizes upstream xdist/cov; TTL clock-domain bug is fixed; `builtins.open` streaming spy is deliberately lenient; no per-test timeout.
- Reference: SQLite triggers on source caches for dirty tracking, raw row insert bypassing cull, per-operation `flock(LOCK_NB)` lease, fsynced `RESHARD.json`.
- Horizon: hardest in the pack; likely bimodal with timeouts. Valuable as headroom evidence only if at least one strong-model trial succeeds; a uniform 0 gives no screening gradient.

### diskcache-snapshot-restore (Tier C; STRONG BACKUP)
Instruction 808 words; 11 functions / 30 cases; 129 upstream regressions (full core and fanout suites); runtime ~305 lines.
- [MED] Fanout restore must reproduce per-shard `size_limit` (`test_snapshots.py:79`) because `FanoutCache.__init__` overwrites it; semi-derivable trap.
- [MED] Concurrency test (`:301-331`) requires all-`'new'` after a concurrent transaction, but the parent releases the writer before the child signals READY, so the property is timing-decided and an all-`'old'` consistent snapshot would be a false failure.
- [LOW] `type(restored) is type(c)` (`:72`); outer transaction must survive the `ValueError` (`:157`); per-member event interleaving implied (`:214-227`).
- Verifier: cleanest in the pack (no monkeypatching); mutant evidence thin (each mutant killed by exactly one test); `tests > 0` floor.
- Horizon: smallest patch; spec is transcription-grade; reviewer expects a substantial Sonnet solve rate in 40 to 80 steps.

## What this evaluation cannot establish

Model headroom, the >75-step horizon, and per-task pass rates remain unmeasured; only trials settle them. The reviews are static readings of tests and patches plus targeted local checks, not a flakiness campaign or an adversarial implementation of each task. Demand evidence beyond the three Huey audits is proxied from provenance and freshness notes. Import-time code in a submitted package still runs inside the grading interpreter as root in all ten tasks; that is a known pack-wide boundary that trajectory and source review must cover before any success is reported to the client.

## Pugh chart: suitability for the long-horizon SWE brief

Updated September 13, 2026 to include all **15 validated tasks**: the original ten Python tasks and the five [Rust/C++ additions](rust-cpp-10-to-5/selection.md). All five additions have accepted Harbor oracle=1/no-op=0 evidence in the [validation index](validation/README.md). This update changes only research comparisons; task packages and their validation bindings remain unchanged.

Datum: `sqlite-utils-relational-merge`, with every current cell 0 by definition. Scale −2..+2 relative to that same datum. Positive values mean more suitable for the criterion. The weights are unchanged. **HR and HZ are static judgments, not measured model performance or proof of a >75-step horizon.** Small reference patches inform these judgments but do not determine them by line count alone.

Criteria and weights: HR headroom magnitude (3); ATT failure attribution to the specified behavioral boundary rather than an undisclosed convention (3); HZ horizon from interacting engineering decisions rather than enumerated requirements (2); VER verifier soundness as built (2); DEM exact-workflow demand grounding (2); NOV novelty and contamination resistance (1); SCL scale-out as a template for a 1,000-task family (1); OPS operational reliability, including hangs and timing-sensitive grading (1). DEM +2 means substantially better evidence matching the task's exact scope than the datum; it does not imply many affected users or measured prevalence. OPS 0 on a new task means no demonstrated relative advantage, rather than a claim that cold Rust builds cost the same as Python checks.

Rows are ordered by **as-built weighted total**. Equal totals are ordered by DEM, then ATT, then HZ; rows still tied share a rank. This tie rule favors matched user demand and attributable failures without changing the criterion weights. “After listed fixes” remains a separate prospective estimate for the original tasks and does not determine this ranking.

| Rank | Task | HR ×3 | ATT ×3 | HZ ×2 | VER ×2 | DEM ×2 | NOV ×1 | SCL ×1 | OPS ×1 | Weighted total | After listed fixes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | [luigi-generation-target](../candidates/luigi-generation-target/instruction.md) | +1 | 0 | +1 | +1 | 0 | 0 | +1 | 0 | **+8** | +8 |
| 2 | [burn-scoped-checkpoint-remap](../candidates/burn-scoped-checkpoint-remap/instruction.md) | −1 | +1 | −1 | +1 | +2 | −1 | +1 | 0 | **+4** | +4 |
| 3 | [sqlite-utils-schema-plan](../candidates/sqlite-utils-schema-plan/instruction.md) | +1 | −1 | +1 | −1 | +1 | −1 | +1 | 0 | **+2** | +7 |
| 4 | [diskcache-online-reshard](../candidates/diskcache-online-reshard/instruction.md) | +2 | −1 | +2 | −1 | −1 | +1 | −1 | −1 | **+2** | +8 |
| 5 | [huey-sqlite-leases](../candidates/huey-sqlite-leases/instruction.md) | +1 | −1 | +1 | −1 | 0 | 0 | +1 | 0 | **+1** | +6 |
| 6 | [sqlite-utils-relational-merge (datum)](../candidates/sqlite-utils-relational-merge/instruction.md) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** | ≈+5 |
| 7 | [rerun-selected-time-export](../candidates/rerun-selected-time-export/instruction.md) | −2 | +1 | −2 | +1 | +2 | −1 | +1 | 0 | **−1** | −1 |
| 7 | [object-store-full-range-response](../candidates/object-store-full-range-response/instruction.md) | −2 | +1 | −2 | +1 | +2 | −1 | +1 | 0 | **−1** | −1 |
| 9 | [zenoh-queryable-completeness](../candidates/zenoh-queryable-completeness/instruction.md) | −2 | +1 | −2 | +1 | +2 | −1 | 0 | 0 | **−2** | −2 |
| 10 | [luigi-checkpoint-task](../candidates/luigi-checkpoint-task/instruction.md) | 0 | 0 | 0 | −1 | 0 | 0 | +1 | −1 | **−2** | +1 |
| 11 | [diskcache-snapshot-restore](../candidates/diskcache-snapshot-restore/instruction.md) | −1 | +1 | −1 | +1 | −1 | −1 | +1 | 0 | **−2** | −2 |
| 12 | [huey-sqlite-outbox](../candidates/huey-sqlite-outbox/instruction.md) | +1 | −1 | +1 | −1 | −1 | −1 | +1 | 0 | **−2** | +3 |
| 13 | [rapidjson-schema-property-membership](../candidates/rapidjson-schema-property-membership/instruction.md) | −2 | +1 | −2 | +1 | +1 | −2 | +1 | 0 | **−4** | −4 |
| 14 | [sqlite-utils-resumable-import](../candidates/sqlite-utils-resumable-import/instruction.md) | −1 | −1 | −1 | −1 | 0 | −1 | +2 | 0 | **−9** | −4 |
| 15 | [huey-deadletter-redrive](../candidates/huey-deadletter-redrive/instruction.md) | 0 | −2 | 0 | −1 | −1 | 0 | +1 | 0 | **−9** | −4 |

The [machine-readable scores](rust-cpp-10-to-5/pugh-scores.json) record the weights, ranking rule and demand correction. Existing weighted arithmetic was correct. The one substantive correction to an original row is **Huey SQLite leases DEM +2 → 0**, following the [later demand audit](huey-demand-review.md): worker recovery has real reports, but no report matches the exact SQLite contract. The datum has a comparable exact-scope gap for key remapping. Leases therefore moves from +5 to **+1**, and its prospective after-fix score from +10 to **+6**. Other original criterion scores are retained, not presented as newly re-audited.

For the original tasks, “After listed fixes” removes the ATT, VER and OPS penalties addressed by their documented fixes, leaving other criteria unchanged. Redrive retains ATT −1 because its atomicity probes need a rewrite. The datum's ≈+5 is the original projected improvement against its current version; its current datum score stays 0. The new packages have no outstanding identified fixes in those reviews, so their prospective scores equal their current scores. These projections are not completed work.

### Grounds for the five additions

| New task | Scoring grounds |
|---|---|
| Burn scoped remapping | HR/HZ −1: shared policy semantics plus PyTorch and Safetensors integration offer more work than a single call-site change, but remain smaller than the datum's transactional graph import. ATT/VER +1: explicit API/rule ordering, actual model values, direct access, partial-load reports and 15 compatibility controls; the independent review's default-coverage finding was fixed before validation. DEM +2: model-import naming constraints and maintainer-supported scope. NOV −1: the scoped-policy direction is public and an explicit-remapping workaround exists. SCL +1: checkpoint import compatibility is a reusable task family. [Evidence](rust-cpp-10-to-5/burn/findings.md), [review](rust-cpp-10-to-5/rust-other/burn-independent-review.md). |
| Rerun selected-time export | HR/HZ −2: the existing row-filter primitive does most of the hard column work; broad verification does not turn this integration into a long implementation. ATT/VER +1: public export results, row/timeline/type identity, sparse values, RRD round trips and immutable source controls; review findings fixed. DEM +2: real recording workflow and maintainer-confirmed contract. NOV −1: public cause and existing slicing primitives reduce novelty. SCL +1: export-selection/data-alignment tasks generalize. [Evidence](rust-cpp-10-to-5/rerun-zenoh/findings.md), [review](rust-cpp-10-to-5/rerun-zenoh/rerun-task-independent-review.md). |
| object_store whole-object HTTP response | HR/HZ −2: the repair resolves an existing range against the full length and reuses existing metadata/retry handling. ATT/VER +1: exact bytes and metadata, proper-subrange rejection, truncated bodies and acknowledged retry fixtures; independent review found no blocker. DEM +2: the Polars/miniserve small-Parquet workflow matches this boundary. NOV −1: public HTTP semantics and existing retry machinery guide the solution. SCL +1: range/stream compatibility recurs across HTTP clients. [Evidence](rust-cpp-10-to-5/rust-other/findings.md), [review](rust-cpp-10-to-5/rust-other/object-store-independent-review.md). |
| Zenoh queryable completeness | HR/HZ −2: one broker registration method reuses `merge_qabl_infos`; undeclaration already recomputes the aggregate. Lifecycle tests do not imply comparable implementation depth to relational merge. ATT/VER +1: public reply sets, both declaration orders, teardown/re-add and upstream controls; persistent-client cache invalidation remains a documented coverage limit. DEM +2: authoritative RocksDB and eventual S3 share the affected storage-manager session. NOV −1: maintainer-directed aggregation and analogous existing routing code. SCL 0: no demonstrated template breadth advantage over the datum. [Evidence](rust-cpp-10-to-5/rerun-zenoh/findings.md), [review](rust-cpp-10-to-5/rust-other/zenoh-independent-review.md). |
| RapidJSON schema property membership | HR/HZ −2: a compact distinction in schema property bookkeeping and validation dispatch. ATT/VER +1: independent DOM/SAX/reset checks plus protected upstream regressions. DEM +1: a concrete report and follow-up, with no named production workflow established. NOV −2: the issue author proposes a marker field that closely matches the reference design. SCL +1: schema-validator edge cases form a broad family. The 309 checks are repeated paths over 49 fixtures, not a measure of task depth. [Evidence](rust-cpp-10-to-5/cpp/findings.md), [construction review](../candidates/rapidjson-schema-property-membership/construction/README.md). |

### Interpretation and sensitivity

The current top ten are generation-target, Burn, schema-plan, reshard, leases, relational-merge, Rerun and object_store tied, Zenoh, and checkpoint-task. This replaces the earlier discretionary ten-task stack rank. It is an as-built comparison; the original final-three recommendation above is historical and remains contingent on verifier fixes, demand qualification and model trials.

Burn ranks highest among the additions because it combines matched demand and a reviewed verifier with somewhat broader implementation scope. Zenoh, Rerun and object_store have stronger exact-workflow grounding than several original tasks, but receive substantial HR/HZ penalties for compact repairs. RapidJSON ranks thirteenth because its modest scope and public design hint outweigh its clean verification.

The ranking is sensitive to judgment calls. Lowering Burn HR and HZ by one point each changes +4 to −1. Raising either HR or HZ by one for a compact task changes its total by three or two points, respectively. Ties and small score gaps should not be interpreted as measured superiority. If demonstrated >75-step work is a hard gate, **none of the fifteen qualifies yet**; actual model trajectories are still required. No model trials were run for this chart update.

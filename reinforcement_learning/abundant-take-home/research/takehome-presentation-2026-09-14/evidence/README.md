# Shortlist case evidence

The provisional subdomain is **Rust data and protocol integration**. These tasks exercise coordination across representations and modules: Rerun recording chunk optimization, Burn checkpoint ingestion, and Zenoh timestamp APIs and wire transport. They are not C++-to-Rust migrations or evidence for broad model rankings.

`shortlist-cases.json` binds exact raw trial, score, instruction, hidden-test, native trajectory, and ATIF file hashes. Every quoted excerpt is a short visible action or final claim, matched in both trajectory formats with its native assistant-turn number and ATIF step ID. A step means a saved assistant response, including tool-call turns; it is not an edit or a test invocation.

| Case | Failure | Contrast | Interpretation |
|---|---|---|---|
| Rerun | Sonnet/medium, 138 steps, reward 0 | Sonnet/max, 383 steps, reward 1 | Existing tests pass in the failure, but five split/packing/row-limit assertions fail. The pass clears all 96 tests across 13 suites. |
| Burn v4 | Sonnet/medium, 42 steps, reward 0 | Sonnet/max, 323 steps, reward 1; Sonnet/high, 251 steps, all functional groups pass; additional Fable/max pass | Medium delivers only part of the format/error-handling contract. Max passes all eight groups on the same model. High has raw reward 0 solely for retaining a baseline fixture-path literal prohibited by the explicit source constraint; do not label it a functional failure or cheating. Fable/max is a different-model success example. |
| Zenoh v3 | Sonnet/medium, 377 steps, reward 0 | Sonnet/max, 626 steps, reward 1 | Medium exposes `ZenohIdProto` where the public contract requires `ZenohId`, so client callback code fails to compile. This is independent of the old hidden-test internal-import defect repaired in v3. |

The quotations document what the agent claimed, not independent proof of correctness. Hidden verifier outcomes establish the contrast. Individual trajectories do not isolate the causal effect of effort, and differing trial counts must not be treated as a balanced model comparison.

`shortlist-control-audit.json` preserves the initial historical-control audit. `shortlist-controls-final.json` closes the Rerun gap with fresh exact-current oracle 1 / no-op 0 controls, so all three tasks now have matching control pairs. Rerun's final verification is `research/rerun-final-controls-2026-09-14/final-validation.json`; its original wrapper initially rejected the expected no-op early-compile score schema, and the separate read-only completion verifier corrects that evidence-schema assumption without changing any raw outcome or rerunning the controls. Required model-based Harbor quality reviews have now completed separately: Rerun11/11 criteria pass, Burn11/11, Zenoh10/11. The Zenoh `behavior_in_tests` criterion flags missing direct robustness/admin instrumentation coverage; `shortlist-quality-reviews.json` and the linked static assessment retain the finding. This is undercoverage, not an unfair test/spec mismatch, and no scored task or outcome was changed.

`max-effort-completions.json`, `zenoh-v3-sonnet-max.md`, and `burn-v4-max.md` provide additional audited pass details. No task, raw result, runtime, or campaign controls were changed for this evidence compilation; it made no model calls.

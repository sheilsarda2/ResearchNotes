# SQLite schema-plan verifier correction, September 14, 2026

The separate revision is `research/task-revisions/sqlite-utils-schema-plan-v2`. It copies all 31 candidate files and changes only `tests/test_schema_plan.py`: the negative parameter `{'t': {'rename': {'v': ''}}}` is removed from `test_plan_validation_is_atomic`. `verifier.patch` records the complete one-line diff. The other 30 files, including instructions, resource limits, reference solution, and verifier entry point, are byte-identical to the original candidate.

## Contract review

Instruction line 7 defines rename values as final column names. Line 15 rejects invalid option types and final-name collisions, but does not reject an empty string that SQLite can quote as an identifier. Treating that plan as necessarily invalid was unsupported. The revision only removes the rejection expectation; it does not add a new positive requirement or modify the reference implementation.

Instruction line 11 requires all string defaults to be literal values. The parameter containing `a\x00b` therefore remains unchanged. Saved Sonnet/max trial `GnfPeSr` embeds this value directly in SQL: `_default_literal_sql` only doubles quotes, leaving a raw NUL that SQLite rejects. This remains a real functional failure. The reference writes text defaults using a hex-encoded blob cast to text, which can preserve the same value.

The original trial collected 332 tests and failed exactly `test_plan_validation_is_atomic[bad6]` and `test_default_values_are_literals[a\x00b]`. Its source package, result, snapshot, and raw verifier evidence are preserved. Removing one parameter changes the final test count to 331. Later automatically numbered `bad` parameters shift down by one; the remaining parameter values and assertions are unchanged.

## Validation

`check-only.json` records a no-container input check. The harness proves the revision is exactly the original test bytes with the one parameter removed. It freezes complete candidate/revision source manifests, the saved runtime-package manifest, original result/snapshot hashes, and diagnostic/admission-tool hashes. Source copies are retained under `diagnostic-001/source-capture`.

`diagnostic-001` runs three fresh offline containers sequentially under one normally acquired shared admission claim. It uses pinned cached verifier image `sha256:9e08f5ad5384ac44441442a2eb70a24acb6fae5a092077d878f13d3ca2ca2862`, copies in the revised verifier, and retains 2 CPUs, 4096 MiB and the 900-second verifier timeout. The cached package is checked against the pinned upstream archive before each case. The saved package is checked after copy. No model calls or live scheduler/control changes are involved.

Expected outcomes are reference 331/331, unmodified baseline 213 passed/118 failed with the new API absent, and saved Sonnet/max 330 passed/one NUL failure. XML, verifier diagnostics, reward, and process exit must agree. No skipped tests or test errors are accepted. The reference container also checks quoted empty-identifier support against its actual SQLite runtime using an in-memory database.

All expected outcomes were observed in the first diagnostic:

| Implementation | Passed / collected | Reward | Result |
|---|---:|---:|---|
| Reference | 331 / 331 | 1 | Passed |
| Unmodified baseline | 213 / 331 | 0 | Correctly rejects missing feature |
| Saved Sonnet/max GnfPeSr | 330 / 331 | 0 | Only the unchanged NUL default case fails |

The saved failure is still `sqlite3.ProgrammingError: the query contains a null character`, wrapped as `TransformError`; no other assertion fails. SQLite 3.40.1 in the cached image accepts `ALTER TABLE t RENAME COLUMN v TO ""` and preserves the existing row.

The diagnostic admitted normally at **05:01:26.591 UTC**, finished at **05:01:33.893 UTC**, and released claim `sqlite-verifier-v2-diagnostic-50153` at **05:01:33.845 UTC**. All three containers were removed and frozen inputs were unchanged. No repeats were needed.

`verification-summary.json` contains final source/provenance SHA256 values and preservation checks. The final verifier test SHA256 is `94f1e7f96d8eeb6bf33e839f1bea10a0b44c818329baa1a23cc29eeced8c5029`. All 36 supplied files from commit `c1ae968`, the original 31-file candidate tree, and the saved trial package/result/snapshot remain unchanged.

## Scope

This is a verifier-only revision and diagnostic evidence, not campaign promotion or historical rescoring. No paid trial, image publication, commit, or live-control edit is performed here. The cached-image overlay validates the revised tests against the original pinned runtime; a new campaign image and admission plan remain separate root-owned steps.

# Zenoh omission mutants

These six independent patches target original gold reconstructed from the unchanged upstream archive and `tests/image-source/gold.patch`. Apply one at a time to a disposable gold submission. They modify collected Rust source only; no task, runtime, original job, hidden test or reference correction was edited. Each passed `git apply --check`, an exact changed-file/hash check, reverse-check and full collected-tree restoration.

Compilation and tests have **not** run. These are mutation candidates, not claims of detected defects. First obtain a clean corrected-reference result, then compile each mutant and run its named test under bounded shared admission. Count detection only after compilation succeeds and the expected behavioral assertion fails; startup/harness/compile errors are not mutation evidence. Preserve outputs separately. The returned-stack admin test requires the separate reference correction; the callback test targets the original interception directly.

| Patch | Test expected to detect it | Expected observable failure |
|---|---|---|
| omit-zero-config-rejection | `decoder_rejects_zero_configuration` | First Push/config0/count0 decodes successfully, so assert!(!decodes(...)) fails. |
| omit-count-rejection | `decoder_rejects_record_count_above_255` | First complete Push/count256 decodes successfully, so assert!(!decodes(...)) fails. |
| omit-append-cap | `receive_appends_at_254_and_drops_further_records_at_255` | Incoming255 gains a Receive record and exposes256 records instead of255; a downstream message drop is also an observable failure. |
| retain-unknown-point-as-receive | `unknown_point_and_bad_uhlc_are_skipped_without_dropping_publication` | Unknown custom record is retained as a valid Receive record; public stack has4 records rather than3. Bad UHLC still skips. |
| retain-undecodable-uhlc-as-custom | `unknown_point_and_bad_uhlc_are_skipped_without_dropping_publication` | Malformed UHLC is retained as Custom(empty); public stack has4 records rather than3. Unknown point still skips. |
| omit-admin-receive | `adminspace_query_invokes_receive_timestamp_callback` | Receive-only admin query has no admin Receive callback. The corrected returned-stack test should also miss the admin Receive record. |

The malformed-record mutants use existing valid public variants (`Receive` and `Custom`) to retain bad records. They should fail the same test through different extra records. The cap mutation retains wire count rejection. The admin patch changes only send_request; the separate send_push interception stays intact. Its one-line context should tolerate later Query/Reply correction, but compatibility with corrected gold must still be checked without fuzz or ad-hoc rewrites.

`manifest.json` maps each patch SHA to its test and expected failure, base/test inputs and original/mutated source hashes. `gold-source-files.json` binds all reconstructed collected files. The full temporary source is disposable and not part of the task package. No model call or container was launched.

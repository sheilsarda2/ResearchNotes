# Huey verifier correction, September 14, 2026

The new revision is `research/task-revisions/huey-sqlite-leases-v2`. It copies all 25 original candidate files, changing only `tests/test_leases.py`. The instructions, task framing, reference solution, resource limits, and verifier entry point remain identical. Original candidate files and historical trial results are preserved.

## Corrected assumptions

The context-renewal test now saves the reservation token before execution and compares the returned value and stored result with that saved token. It also checks that acknowledgement removed ownership and renewal fails. The contract permits an acknowledged Task to clear its attached lease.

The two scheduled-transfer tests inject faults at SQLite row insertion through diagnostic AFTER INSERT triggers. A public enqueue on an empty test queue discovers the written table names. SQLite callbacks reach a failure after the third inserted row, or a process-death barrier after the 501st row. This covers direct cursor SQL, executemany, and INSERT SELECT without requiring promotion to call `storage.enqueue`. Each test proves its failpoint was reached before checking full rollback, retry, and future-task/queue isolation behavior. The exception test allows further attempted inserts after the injected error; only the documented whole-batch rollback is required.

## Evidence

`probe-check-final.json` checks the actual helper against differently named queue tables and four atomic SQL write paths. All four roll back on the third-row error. A deliberately non-atomic path leaves two committed rows and is rejected.

`diagnostic-001` preserves the first full diagnostic: reference and both saved alternatives passed 193/193. Its mutant reached both injected failpoints, but the late-error test stopped at an overly strict exact-hit-count assertion because ordinary Huey catches each row error and continues. The final test changes that reachability assertion from exactly three to at least three writes. The original run is retained as an unsuccessful diagnostic, including its frozen source and raw XML.

`diagnostic-002` tested the final source and stopped after reference passed 192/193. All three repaired tests passed; the unchanged `test_real_consumer_sigkill_recovery_and_unlocked_task_body` failed because its child did not reach the existing 10-second startup barrier. The raw result is retained. No timeout or resource limit was relaxed.

`diagnostic-003` completed successfully at **04:30:25.775 UTC** after normal admission at 04:29:50.928. The final source passed every expected outcome:

| Implementation | Outcome |
|---|---|
| Reference solution | 193/193 passed |
| Saved direct-SQL transfer, GN5kRT6 | 193/193 passed |
| Saved cleared-token-after-ack, mrj9rUA | 193/193 passed |
| Broken reference transfer | Both rollback tests failed after reaching their failpoints |

The broken reference left 10 available rows after the injected insertion error and 500 after the process-death barrier, where both tests require zero. The run released claim `huey-verifier-v2-diagnostic-20739` at 04:30:25.516 UTC, removed all its containers, and confirmed unchanged inputs. No further runs were needed. The earlier startup timeout remains a validation limitation; it is not erased by the passing repeat.

`verification-summary.json` records final SHA256 hashes, source manifests, preserved historical-result identities, and cleanup. All 36 supplied files from commit `c1ae968` matched their original contents in the final check. The final verifier test SHA256 is `57750c23180603af956c74d47cf481f9f4bab68a194d49e1d620c5771d6dbb43`; the revision-tree digest is `7651bb718c22cd86f87b05982c2bd2d030f0e88a3016bca379a852669f374170`.

Validation uses the pinned cached image `sha256:43fa880b4ac5b716019174b443c7f4b70925e9f885de705e6d66b76bba1f9927`, with the corrected tests copied into fresh isolated containers. It holds one normally acquired shared admission slot, runs the cases sequentially at 2 CPUs/4096 MiB with no network and the unchanged 900-second verifier timeout, and releases the claim only after its containers are removed. No model calls or live control edits are involved.

The saved cases are exact runtime-package copies from `GN5kRT6` (direct SQL transfer) and `mrj9rUA` (cleared token after acknowledgement). Copy hashes are verified inside each diagnostic container; the unchanged verifier restores pristine upstream tests as it normally does. The broken reference uses a diagnostic-only pytest plugin replacing leased transfer with ordinary independently committed schedule removal/enqueues; the two corrected tests must fail specifically at rollback-state assertions after reaching their failpoints.

## Scope and limits

This validates the reference, both observed alternative implementations, four SQL insertion forms, and one meaningful atomicity defect. The probe registers callbacks through the standard `sqlite3.connect` and `sqlite3.dbapi2.connect` factories. A different adapter or a connection alias captured before instrumentation may require separate instrumentation; this diagnostic does not claim to validate every conceivable adapter. Queue-table discovery assumes the public enqueue and scheduled promotion write the same queue representation.

The new task has not been promoted to a campaign or prebuilt as a new verifier image here. Root owns promotion, held-task controls, and treatment of historical scores. No historical result is rewritten and no commit is authored by this subagent.

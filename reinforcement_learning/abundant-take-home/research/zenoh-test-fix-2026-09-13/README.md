# Zenoh timestamp test-contract correction

This is a defect in our adaptation of Zenoh PR #2620 into a benchmark task.
The upstream implementation and its integration test agree: the PR introduces
`zenoh_protocol::network::timestamp_stack::interception_point`, and the test
imports its constants. We copied that test while writing instructions that
promise only `zenoh::timestamp_stack` and wire values `1`, `2`, and `4`.
The instruction does not require the additional protocol module layout.

Both the Opus/medium and Sonnet/medium attempts built the stated public module
but failed to compile the hidden suite on its undocumented import. Their
results do not establish that either implementation would otherwise pass.
Our gold-pass/no-op-fail validation missed the defect because the gold patch
contains the exact layout used by the inherited test. The derivability review
checked the conversion behavior but overlooked the import needed to compile
that assertion.

The separate v3 revision replaces the three internal constants with their
specified `u8` wire values. It retains the assertions, public API, instructions,
reference implementation, nine verifier groups, time budgets and resources.
The original frozen task and historical trial outputs remain untouched.
Validation must include gold and no-op controls plus a saved alternative
implementation that lacks the undocumented module; gold/no-op alone would
repeat the original validation gap. Replay results are diagnostic, do not
spend model calls, and do not overwrite or silently relabel old rewards.

`origin.json` records the source hashes and affected historical trials.
The old revision is excluded from the main sweep and also has a task-specific
admission hold. The corrected revision requires its own identity and results,
and must not be mixed into the original cells. Both saved submissions compile
the corrected 47-test target and pass the affected conversion test, without
source changes or model calls; this is a focused diagnostic, not a full regrade.
The separate C++ Zenoh cleanup incident and sweep recovery are documented in
`../benchmark-incidents/2026-09-13-agent-cleanup-cascade/`.

The v3 reference control passed all nine verifier groups (including all 47
hidden tests); the no-op control scored zero. Both completed without Harbor
exceptions and within the unchanged budgets. `validation-proof.json` binds the
raw proof files. `frozen-task-manifest.json` identifies the validated task.
`promotion.json` records its separate 180-trial cohort: 20 attempts for each of
three models and three efforts, sharing the existing 12-slot capacity with the
main sweep. The main target is 2,160; adding this replacement restores 2,340
valid planned trials. The two invalid old-revision outcomes remain historical.

The promotion gate was checked against the verifier's native boolean `pass`
fields; its 20 tests cover failed/missing groups, altered evidence, and cache
reuse. Patch context whitespace and raw log endings are preserved verbatim.

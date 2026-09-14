# Runtime source hashes in evidence summaries

The runtime marker already records three distinct source hashes, but the
evidence summary only projected a legacy `helper_sha256` field. Add
`runtime_sha256`, `trial_containment_sha256`, and `process_guard_sha256` to
the lifecycle summary using the producer's existing names. Keep the legacy
field's meaning unchanged. This is an additive schema-1 metadata change;
it does not change grading, budgets, scheduling, counts, or cleanup decisions.

The focused evidence suite passed all 28 tests in the Harbor Python environment.
It covers distinct current hashes, legacy markers, missing/invalid values,
raw-input preservation, write-once evidence, and existing integrity checks.
Independent review also passed all 28 tests. The source and test hashes are
recorded in `projection-proof.json`.

The separate `foxglove-skhsjvs-derived-evidence.json` was freshly collected
from the completed Sonnet/max trial using its saved pre-verifier snapshot and
original deadline. Under Harbor Python 3.14, it exactly equals the saved
historical evidence after removing only the three new fields. Reward, usage,
364 turns/calls/returns, and all existing metadata remain equal. Raw inputs,
historical evidence, and the pre-verifier snapshot remain unchanged. The proof
also verifies all 36 supplied take-home files against commit `c1ae968`.

An initial host-Python comparison differed only in the last floating-point
digits of two cost sums; no files were written by that failed comparison.
The successful exact comparison uses the original Harbor interpreter.

No workers were restarted for this optional projection. Fresh processes use
the updated exporter; processes that already imported it can retain the older
summary shape. Their raw runtime markers still contain the source hashes.
Historical write-once evidence and Burn's captured validation sources are
not rewritten. This derived artifact is an audit output, not a regrade or a
replacement for the original trial evidence.

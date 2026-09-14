# Additive recovery for the confirmation image-tag collision

This revision adds an explicit independent oracle certificate to the reviewed
[001 recovery design](../controls-confirmed-001-tooling/README.md). The entire
001 directory remains byte-identical, including its 35-test proof and source
capture. `prior-tooling-preservation-before.json` binds that original tree.

The normal oracle confirmation built a new image metadata ID while the old
checksum-only private tag still identified the earlier image. Its frozen
callback correctly rejected repointing that tag. The separate actual-image
capture records the new image, distinct confirmation tag, unchanged old tag,
exact task test files, and matching filesystem layers. This capture alone
does not certify the oracle result.

The new branch requires four evidence identities in the combined proof:

- The original control run, with both original failed records preserved.
- The normal oracle confirmation, retaining its failed callback record.
- `independent_nop_validation`, which certifies the original raw nop without
  another execution and preserves its original observation error.
- `independent_oracle_validation`, which certifies the same normal oracle
  confirmation only after its raw reward is one, with no exception and all
  eleven verifier groups and required test counts independently validated.

The oracle certificate must preserve the exact original callback errors.
Only the known nonterminal `AssertionError` with message
`Private tag already identifies a different image` is accepted; other errors
require separate review. It binds the same raw result and configuration,
the complete original run's before/after file maps, the historical admission
samples, a fresh locked terminal observation, absence of the historical
runner and matching containers, and a separate original-controller absence
record. Its image proof must identify the actual normal verifier container,
new image, and distinct tag while preserving the old tag. Neither certificate
claims a new control execution or rewrites a failed historical summary.

The existing detailed packager control validator runs before selecting any
certificate branch. The packager source stays unchanged. The shared
`continuation.lock`, one canonical `reviews-final-001` output, one quality
attempt, all eleven actual rubric outcomes, six omission probes, two saved
diagnostics, and nine full saved-source regrades retain their original gates.
Children run sequentially with overall local concurrency one; there are no
automatic retries. The new coordinator writes only
`../continuation-confirmed-002/`. It does not promote a task, package a
submission, change a score, or launch an additional oracle/nop.

The source manifest binds this revision's helpers and tests, the existing
frozen downstream helpers, the independent validators and image-capture
helper, and the current read-only packager validator. Exact source bytes are
copied into `source-capture/`. Do not edit a frozen revision after use.

From the repository root, check the actual combined proof without launching:

```sh
python -B research/zenoh-coverage-followup-2026-09-14/controls-confirmed-002-tooling/continue_confirmed_validation.py \
  --controls-summary research/zenoh-coverage-followup-2026-09-14/controls-confirmed-001/summary.json \
  --controls-sha256 ACTUAL_REVIEWED_SUMMARY_SHA256
```

The validation coordinator owns the later explicit `--run`, after independent
review and terminal prior handles. Missing or incomplete controls fail closed.
The quality wrapper remains an internal child of that coordinator. Successful
regrade execution preserves separate historical and paired outcomes; it is
not a claim that every saved submission passes the corrected verifier.

# Independent review of the Rerun selected-time export package

September 13, 2026. Read-only review of `candidates/rerun-selected-time-export`; no implementation or task files edited. This review covers the source/verification design, not an independent execution of the new package. Its owner is completing local build/validation; final Harbor validation belongs to the coordinator.

**No blocking defect found in the reference patch.** It preserves static and unfiltered chunks, excludes absent timelines/disjoint intervals, shares fully covered chunks, and builds a row predicate for partial overlap. `Chunk::filtered` applies the same mask to row IDs, all timelines and component columns, creating valid independent metadata. Filtering works for unsorted and duplicate times; the existing floor/ceil conversion remains untouched. No requirement forces the use of that helper.

## Verification strengths

The fixture reconstructs expected row data before it reaches the database. Row-ID keyed comparisons cover entity, timeline values, component descriptor identities and logical Arrow values, so tests do not depend on one chunk grouping or the reference implementation. Null list entries, empty lists and variable-length lists are distinguished. There are positive controls for full export, static data, no overlap and full overlap, along with deterministic varied row selections.

Every selection is checked after Arrow decoding, full RRD encode/decode and reload into a fresh database. The unchanged full-export messages before/after establish source immutability. Store information and blueprint activation are checked explicitly. The full-export equality is appropriately limited to the untouched source/no-selection path; selected chunk identities/grouping may differ.

The grader requires exactly eight named protected tests plus two original `clear.rs` regressions and exactly two successful test summaries, with no skipped/ignored/measured/filtered cases. It uses the separate verifier's manifest, lockfile and upstream tests. Only the two disclosed source subtrees transfer. Touching transferred files before offline Cargo forces source-mtime invalidation, addressing cached baseline libraries. The image prebuild supplies the relevant locked crate dependencies. This does not claim malicious-code isolation.

## Improvements sent to the implementation owner

1. **Timeline type preservation is not independently checked in the reviewed fixture.** `Row.times` records a timeline name and i64 value, and all fixture timelines are sequences. A reconstruction that accidentally turns a timestamp or duration timeline into a sequence could satisfy these tests while changing the data's meaning. Add a timestamp/duration secondary timeline and retain each timeline's type in the expected row comparison. This is a plausible false positive for an alternative implementation, not a defect in the reference `filtered` call, which preserves the original timeline object.
2. **Assert message store identity directly.** The reviewed `inspect` helper ignores the ArrowMsg store-id field. Reload currently checks the ID through `EntityDb::add_log_msg`'s debug assertion; that method is within the transferred implementation surface. Explicitly compare all exported/decoded `message.store_id()` values with the original database's store ID in protected test code, making the required identity invariant independent of submitted checks.

The owner and coordinator received both findings. They can address these small verifier changes before freezing and final validation. There is no reason to add viewer/GPU tests or unrelated transformations.

# Temporary Huey and SQLite admission holds

At 04:05:09 UTC, new starts for `huey-sqlite-leases` and
`sqlite-utils-schema-plan` were gated using the main campaign's existing image
readiness mechanism. The control points to a new private readiness ledger; the
previous ledger remains byte-for-byte unchanged.

The Huey verifier requires an unstated `storage.enqueue` failpoint and compares
a lease token after acknowledgement may have cleared it. The SQLite verifier
requires rejection of an empty rename target without stating that restriction.
Separate revisions are required before further admissions of these versions.
SQLite's NUL-default failure remains a genuine implementation failure under the
existing contract.

The operation held the runtime activation lock, recorded intent and before/after
bytes, checked the current control before atomically replacing it, and verified
that the campaign plan, user plan, shared capacity, exclusions and budgets were
unchanged. Existing trials received no signals; historical results were untouched.

The scheduling review also identified that held cells need a separate temporary
eligibility overlay so they cannot pin the global round minimum. That overlay is
tracked in `research/scheduler-throughput-audit-2026-09-14/`; this admission hold
alone does not implement that scheduler repair.

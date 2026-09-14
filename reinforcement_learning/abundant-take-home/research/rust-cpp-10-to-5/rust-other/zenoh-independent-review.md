# Independent review: Zenoh queryable completeness

Reviewed September 13, 2026 at 10:36 UTC by the separate Rust/object_store worker. This was a read-only review of the frozen task, its pristine source archive, reference patch, protected verifier, and saved direct Docker evidence. No task files or implementations were changed and no new build was run.

**Verdict: no blocking correctness or grading issue found; ready for the coordinator's paired Harbor oracle/no-op gate.** The direct evidence supports baseline reward 0 and reference reward 1, but it does not substitute for Harbor artifact-transfer validation or establish repeated-run flake rates or model difficulty.

## Contract and reference

The instruction states the public symptom, both declaration orders, multiple same-expression siblings, teardown/re-addition, wildcard matching, unrelated expressions, local and remote queries, and ordinary replies/consolidation. It exposes the `zenoh/src/` submission boundary and avoids requiring a particular data structure or a discovery-readiness API. The concrete reported deployment is tied to upstream issue [#2614](https://github.com/eclipse-zenoh/zenoh/issues/2614); provenance distinguishes adjacent northbound aggregation [#2631](https://github.com/eclipse-zenoh/zenoh/pull/2631) and the already-merged invalidation work [#2660](https://github.com/eclipse-zenoh/zenoh/pull/2660). This review inspected that recorded evidence and did not repeat the author's public search.

The patch changes only `zenoh/src/net/routing/hat/broker/queries.rs::register_queryable`. It records the new declaration before reducing live declarations for that resource and face through existing `merge_qabl_infos`, replacing the prior last-declaration overwrite. Inspection of the pristine source confirms the merge operation preserves complete dominance and the existing distance preference; the result is independent of declaration ordering. Existing unregister code already recomputes remaining declarations and handles the final removal. The dispatcher disables affected query routes on declaration and on relevant undeclaration transitions before propagating updated aggregate information. No public API, wire format, dependency, or unrelated transport changes are introduced.

## Verifier and isolation

The six independent tests call real public Rust APIs using a local router and TCP client, disable multicast in those fixtures, and compare complete sorted reply vectors. Vector equality detects duplicate as well as missing or extra replies. The cases cover empty/single/homogeneous controls, both mixed declaration orders, wildcard and unrelated keys, removal of complete and incomplete siblings, last-complete removal, complete re-addition, and final teardown. Every stage checks `All` and `AllComplete` locally and remotely. The reference patch is not consulted by the tests.

The selected pristine upstream `test_queryable_same_session` additionally exercises reply/reply-delete/reply-error handling, ordinary complete/incomplete targeting, and None/Latest/Monotonic consolidation. It uses its original default session configuration rather than the hidden fixtures' explicit multicast setting; it is same-session and passed in the recorded network-disabled container.

`tests/Dockerfile` constructs a separate fresh baseline with locked, vendored dependencies and the hidden harness. `task.toml` transfers only `/workspace/repo/zenoh/src`; external upstream tests, manifests, vendor data, and grading code remain outside that submitted surface. Agent and verifier source archives, vendor archives, Cargo config, harness manifest and harness lockfile match byte-for-byte. The source archive contains no `completeness.rs`, solution patch/script, or verifier runner. Its SHA-256 matches provenance: `5a42c7498d7d9566e9dabcb172d3e5a977350926315e54488d56cc6fdbb8980a`.

The runner initializes reward to 0, clears relevant compiler overrides, forces `cargo clean -p zenoh`, and requires successful compilation/execution, the exact expected named tests, exact positive counts, and zero failures/ignored/measured tests. Build or timeout failures cannot earn reward 1. The declared 900-second verifier limit contains two 420-second subprocess watchdogs; recorded complete runs took about 44–52 seconds. The shell may exit normally after a semantic failure, but the reward file and JSON explicitly report 0; final Harbor must consume that reward as intended.

## Direct evidence and limits

Saved diagnostics show untouched baseline: three hidden tests pass, three fail at the intended remote `AllComplete` assertions, and the selected upstream regression passes. The reference passes all six hidden tests and the upstream regression. This provides meaningful positive controls and a clean behavioral distinction, rather than a baseline compilation failure.

Two nonblocking coverage limits remain:

- Each lifecycle stage opens a fresh remote client after synchronous router mutation. This avoids making eventual discovery a hidden timing requirement, but it does not directly test route-cache invalidation for a client retained across lifecycle changes. The inspected dispatcher paths support the reference's correctness here; a future observable-readiness test could strengthen that coverage without an arbitrary sleep.
- These fixtures cover a router-owned session and one TCP client, matching the stated defect. They do not establish multi-router, peer, optional-transport, or arbitrary concurrent-mutation behavior, and the task does not claim those scenarios.

The source repair is naturally small. The package accurately refrains from claiming a >75-step horizon or measured model headroom.

Snapshot at 2026-09-13T10:36:50Z: 39 package files; SHA-256 of the sorted compact JSON mapping relative file paths to file SHA-256 values was `6b3694a374a0202878f5119bf9f05fb37b66df94b7e5920deaec446b57d074fa`. All 36 supplied files still matched original commit `c1ae968` at this check. No changes were made to Zenoh, the frozen object_store package, or any existing task.

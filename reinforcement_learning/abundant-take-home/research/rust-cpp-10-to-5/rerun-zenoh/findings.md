# Rerun / Zenoh: three candidates for the ten-to-five screen

Checked September 13, 2026. Research only; no Harbor packages or patches constructed. Fresh GitHub HEAD responses match the earlier pins: Rerun `ddd684110e1f200ee13ba482ab42689b78dd0551`; Zenoh `646f2d1b730e584a570015a77bee9f6db08be9d1`. The first two candidates reuse same-day executable evidence at those exact commits, rather than claiming a new execution. The third has a new current-main reproduction in this directory.

| Candidate | Exact demand | Current-code evidence and controls | Public implementation overlap | Recommendation |
|---|---|---|---|---|
| Rerun [#12596](https://github.com/rerun-io/rerun/issues/12596): selected recording export retains out-of-range rows | One reporter supplied a C++ fixture and confirmed this affects their real recording export; maintainer agrees chunks should be truncated. | Existing current-main Rust integration test exports ten temporal rows for [4,7], expected [4,6]. Full export, disjoint range, static preservation and source immutability controls pass. | Exact issue PR search zero; unchanged issue timeline. Existing slicing primitives and historical export fixes do not wire row filtering into the selection boundary. | Advance. Strong, narrow contract. Potentially modest integration task; difficulty unmeasured. |
| Zenoh [#2614](https://github.com/eclipse-zenoh/zenoh/issues/2614): mixed-completeness queryables on a shared session | Reporter explains authoritative RocksDB plus eventual S3 in one storage-manager session. Maintainer prefers merging the session's declarations. | Existing main and 1.10.1 public Rust API probe: complete then incomplete gives All=2 / AllComplete=0; reversed order gives 2/1. Single and both-complete controls pass. | Exact issue PR search zero. Additional inspection of open #2631 confirms an opt-in northbound aggregation feature, not same-session registration aggregation. | Advance. Strongest Zenoh authoring choice. Likely small broker aggregation repair; no long-horizon claim. |
| Zenoh [#2767](https://github.com/eclipse-zenoh/zenoh/issues/2767): recreated querier loses first query after responder reconnect | One reporter supplies a client→router→client service repro and trace. No deployment size or business impact stated; no maintainer response yet. | New main probe: reconnect-immediate gives zero replies in all 5 cycles (69–191 µs); all 25 positive controls give one reply. | Exact issue PR search zero; no comments/work claim. Historical #2205/#2603/#2660 and open #2678/#2631 have distinct scope. | Credible third pool entry, below #2614 for Harbor selection. Strong current defect evidence; narrow contract needs care around discovery semantics. |

None has an oracle/no-op Harbor result, model success measurement, or demonstrated >75-agent-step horizon. Do not enlarge natural scope to satisfy a quota.

## Selected-export qualification

Demand links: [real-data confirmation](https://github.com/rerun-io/rerun/issues/12596#issuecomment-3811458713), [maintainer cause/expected behavior](https://github.com/rerun-io/rerun/issues/12596#issuecomment-3810831112). Evidence: [original reproduction](../../rust-package-review/reproductions/rerun/time_selection.rs), [execution log](../../rust-package-review/reproductions/rerun/time_selection-main.log), [metadata](../../rust-package-review/reproductions/rerun/time_selection-main.json). The Rust boundary directly constructs and decodes Arrow log messages; GUI/C++ execution is unnecessary for this storage-export contract.

Preserve closed interval semantics, sparse-column/timeline alignment, static data, row identity, store metadata and immutable source data. Existing `Chunk::filtered` may already do most column handling. These are verifier design suggestions, not separately measured failures.

Fresh search saved all 18 open PRs, 100 most recently updated closed PRs, exact issue timeline/comments and 167 all-state PR matches for `time selection`. The additional search did not identify an exact implementation. Earlier inspected relevant diffs remain in the existing audit: [#12239](https://github.com/rerun-io/rerun/pull/12239) handles static chunks/intersection; [#12312](https://github.com/rerun-io/rerun/pull/12312) supplies deep chunk slicing; [#11318](https://github.com/rerun-io/rerun/pull/11318) concerns incoming-data cropping; [#12062](https://github.com/rerun-io/rerun/pull/12062) changes selection loading. None repairs the reproduced current export.

## Shared-session completeness qualification

Demand links: [shared-session deployment clarification](https://github.com/eclipse-zenoh/zenoh/issues/2614#issuecomment-4695686563), [maintainer preference](https://github.com/eclipse-zenoh/zenoh/issues/2614#issuecomment-4732383239). Evidence: [original probe](../../rust-package-review/zenoh-repro/src/main.rs), [reproduction metadata](../../rust-package-review/sources/zenoh-demand-reproduction.json).

Declaration order is the controlled variable; transport and handlers work with `All`. A task can fairly cover coexistence and undeclaration without forcing a particular internal representation. It should not ask for S3/RocksDB implementation or use their performance as an asserted effect.

Extra prior-art check: [#2135](https://github.com/eclipse-zenoh/zenoh/pull/2135) previously aggregated per-face queryables in older routing structures. Current broker registration still assigns a single `Some(*info)` to face context, while client/peer implementations reduce declarations. [Open #2631](https://github.com/eclipse-zenoh/zenoh/pull/2631) does modify broker matching status to accommodate opt-in wildcard northbound forwarding, but its broker diff does not change `register_queryable`. Its feature is not an equivalent repair. Saved actual file diffs are in `sources/zenoh-pr-*-files.json`.

## Reconnect-query qualification

The new [probe](reconnect-probe/src/main.rs), [log](reconnect-probe/observed-main.log) and [structured results](reconnect-probe/observed-main.json) run distinct requester/responder sessions and an in-process router over TCP loopback with multicast disabled. The executable is built from read-only current-main source under Rust 1.97.1, two build jobs, Docker ARM64; execution has `--network none`. The process exits 0 because it records observations; it is not an intended-behavior assertion test.

| Phase | Five observed reply counts |
|---|---|
| First declaration, immediate get | 1,1,1,1,1 |
| Settled before disconnect | 1,1,1,1,1 |
| New querier after responder reconnect, immediate get | **0,0,0,0,0** |
| Same querier one second later | 1,1,1,1,1 |
| Drop/redeclare querier without responder disconnect, immediate get | 1,1,1,1,1 |
| Same redeclared querier after 250 ms | 1,1,1,1,1 |

The responder has been reconnected and declared for three seconds before the failing call. The failure therefore is not simply “all newly declared queriers need a sleep.” The existing requester session and its previously used key are important to this fixture. Reporter source was read and preserved at upstream repro commit `a0ccda7611b51118760981c64d55dfeca57bb949`; our probe adds independent control variants and uses current Zenoh. We did not execute the exact three-process reporter fixture or separately replay the release.

**Do not invent a readiness guarantee.** `QuerierBuilder::into_future` returns `Ready`, and `declare_querier_inner` sends a `CurrentFuture` interest without awaiting remote discovery. Neither API promises a network-readiness barrier. The fair proposed outcome is to preserve routing toward the reachable router while a recreated querier's routing information refreshes; it is not to guarantee receipt from peers that have yet to appear, or to make a query timeout a mandatory minimum wait.

Source provides a specific hypothesis: [`client/queries.rs`](https://github.com/eclipse-zenoh/zenoh/blob/646f2d1b730e584a570015a77bee9f6db08be9d1/zenoh/src/net/routing/hat/client/queries.rs#L171) forwards toward the northbound router when queryable interest is not finalized. [`InterestState::set_finalized`](https://github.com/eclipse-zenoh/zenoh/blob/646f2d1b730e584a570015a77bee9f6db08be9d1/zenoh/src/net/routing/dispatcher/face.rs#L90) sets the face resource flag. A current-source search finds no reset to false after initial construction. A stale finalized flag plausibly suppresses the forwarding fallback after interest recreation. We have not applied a fix or run a causal ablation, so this remains a hypothesis.

Before Harbor promotion, resolve that hypothesis and test stale-interest lifecycle with a deterministic routing fixture as well as the public API. Include live responder, permanently absent responder, multiple same-key queriers and final teardown. The current sleep-based research probe establishes the defect in five cycles; it is not the final hidden verifier design.

Public fixes/work checked by actual file diffs:

- [#2205](https://github.com/eclipse-zenoh/zenoh/pull/2205) repairs queryable info update on connection loss, an older matching-listener regression. Current source already includes it.
- [#2603](https://github.com/eclipse-zenoh/zenoh/pull/2603) postpones graph mutation until router cold-disconnect unregistering finishes; already merged. Our client reconnect fixture still fails on main.
- [#2660](https://github.com/eclipse-zenoh/zenoh/pull/2660) invalidates query routes during complete-queryable failover; already merged and does not fix this fixture.
- [Open #2678](https://github.com/eclipse-zenoh/zenoh/pull/2678) moves synchronous liveliness replay off the caller to avoid bounded-channel self-deadlock. It touches `face.rs`, but neither resets `queryable_interest_finalized` nor addresses newly recreated queriers.
- [Open #2631](https://github.com/eclipse-zenoh/zenoh/pull/2631) is the northbound aggregation experiment described above, with default-empty configuration preserving normal propagation.

Broader [#2516](https://github.com/eclipse-zenoh/zenoh/issues/2516) reports intermittent zero replies in peer-to-peer Python queries. The reporter clarifies callbacks only enqueue work and queries run separately. That supports adjacent concern, but we do not count it as an independent report of the exact client-router reconnect trigger.

## Limits, reserves and preservation

No exact public implementation was found in the refreshed bounded search: all 89 open Zenoh PRs, 100 recent closed PRs, exact issue all-state searches, comments/timelines, and targeted `queryable_interest_finalized`, `interest finalized`, `stale querier`, `querier reconnect`, `zero replies`, `queryable info`, `completeness` searches. This is not proof about private work or every unreferenced fork.

Rerun shutdown #12821 stays reserved: previous Python release evidence exceeds a 20-second shutdown watchdog, but a fair bounded shutdown contract preserving slow healthy flushes is unresolved. Zenoh package-state deletion #2778 remains a real shell-packaging defect, not a substantive Rust implementation candidate. Previously rejected leads remain rejected.

[Preservation check](preservation-check.json): all 36 supplied files match commit `c1ae968`. No existing task directory, source implementation, candidate package or shortlist changed. Only new research under this directory was written; the build uses its own cloned target cache. No external messages or paid model runs.

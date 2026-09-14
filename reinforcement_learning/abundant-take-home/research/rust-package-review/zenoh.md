# Zenoh demand and current-defect review

Checked 2026-09-13. Canonical repository: [eclipse-zenoh/zenoh](https://github.com/eclipse-zenoh/zenoh). Current main is `646f2d1b730e584a570015a77bee9f6db08be9d1` (2026-09-11); latest release is [1.10.1](https://github.com/eclipse-zenoh/zenoh/releases/tag/1.10.1), published 2026-09-07. Source lives in isolated temporary checkouts; the original take-home and ten existing candidates were not changed.

**Recommendation:** advance mixed queryable completeness (#2614) to construction. Package lifecycle data deletion (#2778) is a second, concrete impact-qualified option, but it is a small shell-packaging repair in the Rust repository and has substantial difficulty risk. Do not inflate its scope to manufacture a long task. No model headroom or >75-step result has been measured for either. If five substantial Rust-code tasks are required, count only the first Zenoh candidate and obtain the remaining four elsewhere unless a further Zenoh candidate passes the gates.

## Advance: preserve complete queryables sharing a session

The May 20 report [#2614](https://github.com/eclipse-zenoh/zenoh/issues/2614) describes missing `AllComplete` replies when two same-key queryables have different completeness flags. The reporter clarified on June 12 that the actual deployment uses two **storages within one storage-manager plugin**: authoritative RocksDB and an eventually consistent S3 fallback. They share one session. The initial maintainer response assumed different plugins/sessions and did not match that deployment. On June 17 a maintainer suggested either separate storage sessions or merging session-level queryable information, preferring the latter. [Clarification](https://github.com/eclipse-zenoh/zenoh/issues/2614#issuecomment-4695686563), [maintainer response](https://github.com/eclipse-zenoh/zenoh/issues/2614#issuecomment-4732383239).

Reported impact is loss of authoritative query results in that concrete primary/fallback topology. Neither deployment size nor downstream business loss is quantified. Using `All` or separating sessions/key expressions can avoid the trigger but changes targeting or topology. This is not a claim that all Zenoh query routing is broken.

**Measured on current main:** an independent, public Rust API reproducer runs a router and client over TCP loopback with multicast disabled. It responds directly, avoiding the report's asynchronous publish-before-query race and external storage dependencies. Results:

| Queryables declared on one key in order | `All` replies | `AllComplete` replies |
|---|---:|---:|
| complete | 1 | 1 |
| complete, complete | 2 | 2 |
| complete, incomplete | 2 | **0** |
| incomplete, complete | 2 | 1 |

The controls establish working transport, handlers and target selection; only declaration order changes the broken case. No S3/RocksDB throughput claim was measured. The latest release and main have identical `session.rs` and broker query-registration code at inspection; a separate release-source replay is recorded in the reproduction results.

A suitable task should preserve public `AllComplete` filtering for coexisting queryables, including correct updates when one is undeclared. It should allow any sound implementation. Exercise declaration order, teardown, complete/incomplete combinations and remote versus local behavior; avoid prescribing a specific internal aggregation structure. This is a proposed verifier scope derived from the reported lifecycle, not a claim that every listed variant independently fails today.

**Freshness:** all 89 open upstream PRs, 100 recent closed PRs and 50 recent commits were inspected by relevant title/body searches, plus issue comments and a targeted PR search for #2614. No matching implementation was found. This bounded absence check does not cover every fork, private implementation or unreferenced historical PR. The existing [queryable failover fix #2660](https://github.com/eclipse-zenoh/zenoh/pull/2660) handles route invalidation after a complete queryable disappears; our reproduction confirms it does not repair this shared-session declaration-order problem.

## Optional second: preserve daemon configuration and state on package lifecycle operations

A September 9 operator report [#2778](https://github.com/eclipse-zenoh/zenoh/issues/2778) says upgrades remove customized configuration and daemon state, requiring recreation. It names Ubuntu 26.04 and Zenoh 1.10.1. The report mixes an `apt update` observation with an upgrade-script explanation; **the demonstrated trigger here is `postrm upgrade`, not a plain package-index refresh**.

The current source [zenohd/.deb/postrm](https://github.com/eclipse-zenoh/zenoh/blob/646f2d1b730e584a570015a77bee9f6db08be9d1/zenohd/.deb/postrm) puts `upgrade`, `remove`, failure/abort operations and `purge` in one branch that deletes `/etc/zenohd`, `/var/zenohd` and the service user. The file is unchanged between latest release and main.

**Measured:** in a disposable, network-disabled Docker container, create synthetic config/state files; confirm both exist; execute the unmodified current `postrm` with `upgrade 1.10.2`; both directories disappear. This directly demonstrates the destructive script behavior. It is not a full dpkg upgrade test, does not establish package-index-refresh behavior, and does not quantify actual state volume lost. A constructed task should test real lifecycle arguments and permitted purge behavior with isolated synthetic files, including preserving unrelated paths. Packaging changes must also account for the old package's script running during an upgrade; merely changing a future script cannot retroactively repair already shipped old scripts.

No linked PR, comments, matching title/body entry in the inspected open/recent-closed PR inventory or targeted #2778 PR search was found. This is concrete missing behavior with user impact but likely a short repair. Do not advertise it as a demanding Rust implementation or assign an estimated agent-step count.

## Rejected or held findings

- **Read-only S3 fallback stalls writes (#2617): reject as actively implemented.** The May 21 report gives a RocksDB/S3 workload and an approximately 80 ms latency cliff after roughly 200 operations. These are reporter measurements, not ours. [Open PR #2647](https://github.com/eclipse-zenoh/zenoh/pull/2647) adds read-only capability and avoids the write subscription; its actual file diff was inspected and saved. This is the exact missing behavior, not an adjacent partial feature.
- **Wildcard deletion resurrected by stale put (#2649): reject.** [Open PR #2650](https://github.com/eclipse-zenoh/zenoh/pull/2650) implements the dispatch correction and regression test.
- **Peer-churn routing deadlock (#2581): reject as fixed on main.** [Merged #2779](https://github.com/eclipse-zenoh/zenoh/pull/2779), September 11, replaces the StartConditions async mutex/parking path. Another open PR still references the issue; open issue/PR status alone is misleading.
- **Idle REST SSE connection leak ([#2674](https://github.com/eclipse-zenoh/zenoh/issues/2674)): reject for current task selection.** The July 9 report concerns 1.9.0. On official 1.10.1 Linux ARM64 binaries, twenty idle SSE disconnects leave file descriptors unchanged at 17 and zero `CLOSE_WAIT` sockets. A live SSE stream receives the published probe; publishing does not uncover retained sockets. Current main's REST source is identical to the release on the inspected path. This bounded negative reproduction does not prove every disconnect variant fixed, but the reported trigger no longer qualifies. The plugin changed from Tide to Axum in 1.10.
- **Blocking async open ([#2609](https://github.com/eclipse-zenoh/zenoh/issues/2609)): hold.** Current main takes 2015 ms despite an enclosing 100 ms Tokio timeout, with the inner connect timeout configured to 2000 ms. The timeout does not fire. A June 15 maintainer comment acknowledges the architecture and plans a routing/API redesign. Concrete current behavior is reproduced, but the scope is broad, current-work overlap needs further adjudication, and direct workload/impact evidence is weaker than #2614. Do not add arbitrary API redesign to reach a task quota.
- Other screened reports involve existing open patches, old versions, private-chat-only reproductions, a different backend (Zenoh-pico), or insufficient current reproduction. None was promoted merely because the issue is open.

## Evidence and reproduction

[`sources/zenoh-demand-reproduction.json`](sources/zenoh-demand-reproduction.json) records pins, image digest, result values and limits. [`zenoh-repro/`](zenoh-repro/) contains the independent Rust client, locked dependency resolution, SSE probe and saved results. Rust builds used the repository's explicit 1.97.1 toolchain, TCP-only Zenoh features, two build jobs and no paid model calls. Test execution used network-disabled containers; loopback remained available. The source was mounted read-only. The versioned Cargo manifest uses `/zenoh/zenoh`, so mount the selected pinned source tree at `/zenoh` and the reproducer at `/repro`.

The shared `sources/zenoh-{head,latest-release,open-issues,open-prs,recent-closed-prs,recent-commits}.json` files preserve fresh primary API responses and fetch timestamps. Per-issue comment responses and exact PR/search evidence are stored alongside them. Research does not establish a difficulty lower bound; oracle/no-op validation and model trials are still separate gates.

# Ten candidates, five validated Harbor tasks

Decision: September 13, 2026. User requested ten candidates pruned to five, across Burn, Rerun, Zenoh or other Rust/C++ repositories, then confirmed that the selected tasks should be added to `candidates/` in the existing Harbor format. This is working research, not human submission prose.

The shortlist below selects concrete, testable changes from the evidence pool. It does **not** establish model headroom or the assignment's >75-agent-step horizon. Several natural fixes may be modest; requirements will not be expanded to manufacture difficulty. No model trials are authorized for this phase.

| Pool entry | Decision | Reason |
|---|---|---|
| Burn [#4716](https://github.com/tracel-ai/burn/issues/4716): scoped checkpoint index mapping | **Selected** `burn-scoped-checkpoint-remap` | Real model-import demand and maintainer-supported policy; current real PyTorch and Safetensors loads fail both global settings, manual controls pass. |
| Rerun [#12596](https://github.com/rerun-io/rerun/issues/12596): exact selected-time export | **Selected** `rerun-selected-time-export` | Real-data confirmation, maintainer-endorsed row contract; current Rust export fails with working full/static/disjoint/source controls. |
| Zenoh [#2614](https://github.com/eclipse-zenoh/zenoh/issues/2614): shared-session queryable completeness | **Selected** `zenoh-queryable-completeness` | Concrete RocksDB/S3 deployment; declaration-order defect reproduced on release and main; public semantics clear. |
| object_store [#806](https://github.com/apache/arrow-rs-object-store/issues/806): whole-object HTTP 200 range response | **Selected** `object-store-full-range-response` | Actual Polars/miniserve Parquet workflow; current Rust API rejects sufficient whole-object response while ordinary 200/206 controls work. |
| RapidJSON [#2296](https://github.com/Tencent/rapidjson/issues/2296): required/dependency membership vs additional properties | **Selected** `rapidjson-schema-property-membership` | User validation mismatch; current code wrongly accepts two invalid cases while four controls pass. C++ task with a deterministic public schema contract. |
| Arrow Rust [#7982](https://github.com/apache/arrow-rs/issues/7982): dictionary-value extension metadata | Reserve | Real current loss and broader natural scope, but a Field-only fix misses the original arro3 DataType path. Correct scope requires a representation/API decision and potentially coordinated arro3 work. Adjacent IPC work is publicly claimed and must be excluded. |
| Zenoh [#2767](https://github.com/eclipse-zenoh/zenoh/issues/2767): recreated querier after reconnect | Reserve | Five current-main failures and 25 passing controls, but asynchronous discovery/readiness contract needs more causal resolution; completeness is better defined for immediate construction. |
| range-v3 [#1737](https://github.com/ericniebler/range-v3/issues/1737): nth-element partition restart | Reserve | Strong production demand/current failure; likely compact loop repair and incomplete local positive controls at selection. Existing five have more complete authoring evidence. |
| CLI11 [#1450](https://github.com/CLIUtils/CLI11/issues/1450): nested help with missing parent positional | Prune this round | Current wrong-help output, but parser ambiguity policy needs clarification, a usable positional workaround exists, and local controls were incomplete. |
| Tantivy [#2666](https://github.com/quickwit-oss/tantivy/issues/2666): committed operation stamp | Prune this round | Stale accessor is reproduced and downstream demand exists, but rollback preserves committed data in our control. Do not adopt the report's unsupported broad corruption framing. |

Pool construction involved screening additional issues with existing fixes, public implementation claims, unavailable fixtures, or insufficient demand. Those exclusions are in the lane reports; they are not padded into this ten-entry pool.

Evidence and bounded public-overlap searches:

- [Burn](burn/findings.md), including new actual store/model integration and fresh source pin.
- [Rerun/Zenoh](rerun-zenoh/findings.md), including new five-cycle reconnect probe and independently refreshed unchanged source pins.
- [Other Rust](rust-other/findings.md), with three current-main offline executions and downstream demand links.
- [C++](cpp/findings.md), with exact source pins, observations, controls and limitations.
- [Independent Arrow review](rerun-zenoh/arrow-7982-independent-review.md).

All five selected directories are complete in `candidates/` and passed final frozen Harbor oracle=1 and nop=0 without exceptions. They use separate agent/verifier images, declared implementation-only artifact transfer, pinned source/licenses and toolchains, offline grading dependencies, independent behavioral tests and reference patches. Accepted paths and hash bindings are in [the validation index](../validation/README.md) and [the shortlist](../shortlist.json). The existing ten candidate directories and their comparison lock remain unchanged.

Independent package reviews: [Burn](rust-other/burn-independent-review.md), [Rerun](rerun-zenoh/rerun-task-independent-review.md), [Zenoh](rust-other/zenoh-independent-review.md), and [object_store](rust-other/object-store-independent-review.md). RapidJSON's second-author review, reset-coverage improvement and public design-hint disclosure are recorded in its [construction notes](../../candidates/rapidjson-schema-property-membership/construction/README.md). Review findings were resolved before freezing the packages; final execution evidence is authoritative.

# Final validation evidence

All fifteen frozen candidates passed Harbor 0.15.0 oracle/no-op validation after the independent verifier corrections. Oracle runs collected positive test counts with no failures, errors or skips. Baselines fail on missing feature behavior; an expected baseline collection error is not a Harbor infrastructure error. Raw results were preserved. No model trials were run.

| Task | Passing oracle tests | Oracle reward | No-op reward |
|---|---:|---:|---:|
| [huey-sqlite-leases](../../candidates/huey-sqlite-leases/instruction.md) | 193 | [1](../../research/validation/jobs/screen-final-v1-01-oracle/huey-sqlite-leases__3c4tism/result.json) | [0](../../research/validation/jobs/screen-final-v1-01-nop/huey-sqlite-leases__AX7rvUj/result.json) |
| [huey-sqlite-outbox](../../candidates/huey-sqlite-outbox/instruction.md) | 192 | [1](../../research/validation/jobs/screen-final-v1-02-oracle/huey-sqlite-outbox__QLHLnhV/result.json) | [0](../../research/validation/jobs/screen-final-v1-02-nop/huey-sqlite-outbox__w6AgZQb/result.json) |
| [huey-deadletter-redrive](../../candidates/huey-deadletter-redrive/instruction.md) | 195 | [1](../../research/validation/jobs/screen-final-v1-03-oracle/huey-deadletter-redrive__UVcFyRp/result.json) | [0](../../research/validation/jobs/screen-final-v1-03-nop/huey-deadletter-redrive__kc4Poz9/result.json) |
| [sqlite-utils-resumable-import](../../candidates/sqlite-utils-resumable-import/instruction.md) | 182 | [1](../../research/validation/jobs/screen-final-v1-04-oracle/sqlite-utils-resumable-import__JUjRUW8/result.json) | [0](../../research/validation/jobs/screen-final-v1-04-nop/sqlite-utils-resumable-import__24BjJKW/result.json) |
| [sqlite-utils-relational-merge](../../candidates/sqlite-utils-relational-merge/instruction.md) | 149 | [1](../../research/validation/jobs/screen-final-v1-05-oracle/sqlite-utils-relational-merge__KTVGeaX/result.json) | [0](../../research/validation/jobs/screen-final-v1-05-nop/sqlite-utils-relational-merge__RkpWxx2/result.json) |
| [sqlite-utils-schema-plan](../../candidates/sqlite-utils-schema-plan/instruction.md) | 332 | [1](../../research/validation/jobs/screen-final-v1-06-oracle/sqlite-utils-schema-plan__cV5gDzE/result.json) | [0](../../research/validation/jobs/screen-final-v1-06-nop/sqlite-utils-schema-plan__cEhHohB/result.json) |
| [luigi-generation-target](../../candidates/luigi-generation-target/instruction.md) | 57 | [1](../../research/validation/jobs/screen-final-v2-01-oracle/luigi-generation-target__TT8yfBW/result.json) | [0](../../research/validation/jobs/screen-final-v2-01-nop/luigi-generation-target__igYqwdR/result.json) |
| [luigi-checkpoint-task](../../candidates/luigi-checkpoint-task/instruction.md) | 50 | [1](../../research/validation/jobs/screen-final-v2-02-oracle/luigi-checkpoint-task__44rbJiq/result.json) | [0](../../research/validation/jobs/screen-final-v2-02-nop/luigi-checkpoint-task__NWrRBnB/result.json) |
| [diskcache-online-reshard](../../candidates/diskcache-online-reshard/instruction.md) | 45 | [1](../../research/validation/jobs/screen-final-v2-03-oracle/diskcache-online-reshard__2uiLbm8/result.json) | [0](../../research/validation/jobs/screen-final-v2-03-nop/diskcache-online-reshard__oQoUsRj/result.json) |
| [diskcache-snapshot-restore](../../candidates/diskcache-snapshot-restore/instruction.md) | 159 | [1](../../research/validation/jobs/screen-final-v1-07-oracle/diskcache-snapshot-restore__jocqJco/result.json) | [0](../../research/validation/jobs/screen-final-v1-07-nop/diskcache-snapshot-restore__enLNWgN/result.json) |

Total oracle tests across the ten separate tasks: **1554**. Shared upstream regressions occur in multiple tasks; this is not a count of unique behaviors or a difficulty measure.

## Rust/C++ expansion

The five tasks selected from the [ten-candidate pool](../rust-cpp-10-to-5/selection.md) passed their final frozen Harbor 0.15.0 oracle/no-op pairs with no exceptions. Only their declared implementation source was transferred to a fresh offline verifier, which rebuilt the submitted code. No paid model trials were run.

| Task | Passing reference checks | Oracle reward | No-op reward |
|---|---:|---:|---:|
| [burn-scoped-checkpoint-remap](../../candidates/burn-scoped-checkpoint-remap/instruction.md) | 24 tests | [1](../../research/validation/jobs/rust-cpp-burn-final-v1-01-oracle/burn-scoped-checkpoint-remap__db4wggh/result.json) | [0](../../research/validation/jobs/rust-cpp-burn-final-v1-01-nop/burn-scoped-checkpoint-remap__iG9ZF2z/result.json) |
| [rerun-selected-time-export](../../candidates/rerun-selected-time-export/instruction.md) | 10 tests | [1](../../research/validation/jobs/rust-cpp-rerun-final-v1-01-oracle/rerun-selected-time-export__3bacQZn/result.json) | [0](../../research/validation/jobs/rust-cpp-rerun-final-v1-01-nop/rerun-selected-time-export__4EezXtp/result.json) |
| [zenoh-queryable-completeness](../../candidates/zenoh-queryable-completeness/instruction.md) | 7 tests | [1](../../research/validation/jobs/rust-cpp-zenoh-final-v1-01-oracle/zenoh-queryable-completeness__jN2VNYE/result.json) | [0](../../research/validation/jobs/rust-cpp-zenoh-final-v1-01-nop/zenoh-queryable-completeness__NdxR3Qj/result.json) |
| [object-store-full-range-response](../../candidates/object-store-full-range-response/instruction.md) | 14 tests | [1](../../research/validation/jobs/rust-cpp-object-store-final-v1-01-oracle/object-store-full-range-response__B5nedLN/result.json) | [0](../../research/validation/jobs/rust-cpp-object-store-final-v1-01-nop/object-store-full-range-response__8LaG4tU/result.json) |
| [rapidjson-schema-property-membership](../../candidates/rapidjson-schema-property-membership/instruction.md) | 309 checks + 13 upstream tests | [1](../../research/validation/jobs/rust-cpp-rapidjson-final-v1-01-oracle/rapidjson-schema-property-member__Yanbaf4/result.json) | [0](../../research/validation/jobs/rust-cpp-rapidjson-final-v1-01-nop/rapidjson-schema-property-member__Ws2QYwD/result.json) |

Burn's baseline passes 15 compatibility controls; its scoped target cannot compile because the required API is absent. Rerun fails its eight new export cases and passes two upstream tests. Zenoh fails three completeness cases and passes four controls. object_store fails six HTTP cases and passes eight controls. RapidJSON fails 84 independent checks while all 13 protected upstream tests pass. These are intended task failures, with no Harbor infrastructure exceptions. Earlier construction mistakes remain documented in each package.

RapidJSON's 309 checks apply 49 fixtures through several validation/reset paths; they are not 309 independent behaviors. Counts across these different harnesses are not a difficulty measure. Model headroom and the required agent-step horizon remain unmeasured.

The [shortlist](../shortlist.json) records full-directory task digests, Harbor checksums, result-file hashes and accepted paths. The prepared [comparison lock](../benchmark-configs/durable-screen-v1-lock.json) binds both model configurations to the original ten Python tasks. Harbor validated both configurations; the planned campaign contains 60 trials. Its empty summary remains unmeasured.

Independent [freshness](../independent-freshness-audit.md) and [measurement](../independent-measurement-audit.md) audits document partial public solutions, corrected false negatives, valid implementation variants, negative controls and remaining limits. Earlier failed construction runs are retained as historical packaging/verification evidence, not model-performance observations.

All **36** originally supplied files match commit `c1ae968` byte for byte. No final report prose or retained `samples/` selection was authored; those follow the user's model trials.

# Candidate screening pack

Fifteen runnable engineering candidates on pinned Python, Rust and C++ repositories. All fifteen passed final Harbor oracle=1 and no-op=0 checks; [accepted evidence](../research/validation/README.md) records the exact tested revisions. The current construction/validation status and accepted Harbor evidence are in [the shortlist](../research/shortlist.json). Model headroom and the required >75-agent-step horizon are unmeasured until model trials; reference-solution success is a solvability check. The [current Pugh chart](../research/candidate-evaluation-2026-09-13.md#pugh-chart-suitability-for-the-long-horizon-swe-brief) compares all fifteen tasks using the same datum and weights.

The later [Huey demand review](../research/huey-demand-review.md) downgrades the three Huey recommendations: runnable tasks and absent APIs do not establish matched user demand. Read that review before choosing tasks for an impact-prioritized campaign. The other seven have not yet undergone that demand-matching audit.

| Candidate | Boundary under test | Prior functionality that does not settle this contract |
|---|---|---|
| [Huey SQLite leases](huey-sqlite-leases/instruction.md) | Task reservation → execution → acknowledgement/retry | Shutdown signals and ordinary dequeue do not recover SIGKILL or fence stale owners. |
| [Huey SQLite outbox](huey-sqlite-outbox/instruction.md) | Business commit → durable graph staging → queue publication | In-memory on-commit callbacks do not bridge producer death after commit. |
| [Huey failure redrive](huey-deadletter-redrive/instruction.md) | Terminal error → preserved invocation → one replacement | Consumable error results and in-memory reschedule handles are not a persistent replay inventory. |
| [sqlite-utils resumable import](sqlite-utils-resumable-import/instruction.md) | Rows and schema → committed byte checkpoint → restart | Ordinary batching and progress display do not persist a resumable source identity/offset. |
| [sqlite-utils relational merge](sqlite-utils-relational-merge/instruction.md) | Independent IDs → remapped FK graph → durable import receipt | Public concatenation/preserve-ID merge proposals do not provide this graph-remapping protocol. |
| [sqlite-utils schema plan](sqlite-utils-schema-plan/instruction.md) | Coordinated schema rebuilds → working dependent objects | Native renames and existing transform helpers are building blocks, not the whole composed plan. |
| [Luigi generation target](luigi-generation-target/instruction.md) | Multiple files → one published generation → pinned readers | Atomic replacement of one LocalTarget does not publish a coherent multi-file generation. |
| [Luigi checkpoint task](luigi-checkpoint-task/instruction.md) | Durable transition → discovered dependencies → resumed execution | Ordinary dynamic tasks restart run(); task completion is not a persisted transition journal. |
| [DiskCache online reshard](diskcache-online-reshard/instruction.md) | Live writes → resumable data movement → topology cutover | Existing Fanout transactions do not migrate a stored cache to another shard topology. |
| [DiskCache snapshot/restore](diskcache-snapshot-restore/instruction.md) | SQLite rows and external payloads → verified snapshot → atomic restore | A live directory copy or database-only backup omits cross-file consistency. |

## Rust/C++ expansion: five selected from ten

The [selection record](../research/rust-cpp-10-to-5/selection.md) records the ten candidates, demand evidence, public-overlap searches, and reasons for pruning five. These five use the same Harbor directory layout, with pinned Rust/C++ build environments, reference patches and separate offline verifiers. Each has final oracle=1/no-op=0 evidence. Natural scope varies; medium difficulty is an estimate, and no model horizon has been measured.

| Candidate | Task |
|---|---|
| [Burn](burn-scoped-checkpoint-remap/instruction.md) | Apply different numeric index policies to different checkpoint prefixes |
| [Rerun](rerun-selected-time-export/instruction.md) | Export exactly the selected time rows while preserving aligned recording data |
| [Zenoh](zenoh-queryable-completeness/instruction.md) | Preserve aggregate completeness across shared-session queryable declarations and teardown |
| [object_store](object-store-full-range-response/instruction.md) | Accept whole-object HTTP 200 range responses while preserving subrange validation and retries |
| [RapidJSON](rapidjson-schema-property-membership/instruction.md) | Distinguish required/dependency names from declared properties during schema validation |

Each task is independent and starts from the unmodified pinned upstream source. They do not depend on another candidate's reference implementation. Runtime dependencies are installed at build time; Harbor transfers submitted package source into a fresh offline verifier. The agent uses the take-home gateway and retains public network access under local Docker. See [runtime details and limits](../research/runtime-validation.md).

Read [current benchmark evidence and counterevidence](../research/benchmark-freshness.md) before interpreting difficulty. Strong agents already perform well on adjacent crash-recovery tasks. The project audits and each task's `provenance.json` disclose current features, partial public solutions, source pins, and the limits of novelty searches.

[Run the Fable/Sonnet comparison](../research/run-comparisons.md) using the plan/run/summarize script. Planning makes no model calls; running does. The default comparison is three attempts per model/task at high effort, with a two-hour agent limit and two concurrent trials per job. Select the final three only after inspecting real success/failure trajectories and verifier validity. Research stays in `research/`; the assignment's final human-written prose stays in `report/`.

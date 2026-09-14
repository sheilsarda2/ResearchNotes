# Evidence notes for the report: all completed task grids

Scope: **11 tasks, 99 counted first results**, captured at 2026-09-14 13:19 UTC. Each task has one counted result for Fable, Opus and Sonnet at medium, high and max effort.

These are source-backed facts and interpretation notes for the human-authored report. The supplied report requirement is unchanged. The three recommended Harbor samples remain Rerun, corrected Zenoh and Burn reader; the report comparison covers all eleven tasks.

A complete grid uses the existing scoring policy, including agent and verifier timeouts. It is not nine uninterrupted successful completions or twenty repeats per cell. Counts and medians below use only the earliest counted result in each cell. Later repeats and corrected-verifier regrades stay separate.

[Frozen data](data-snapshots/99-20260914T1319/results.json) · [Scope and all 99 cells](evidence/completed-task-report-scope-001.json) · [Independent raw-data audit](evidence/data99-independent-review-001/review.json) · [Additional task interpretations](evidence/completed-task-profiles-001.json)

| Task | Passes / first grid | Median steps on success | Successful runs >75 steps |
|---|---:|---:|---:|
| Foxglove parameter handler | 9/9 | 163 | 9/9 |
| rosbag2 mixed playback | 7/9 | 154 | 7/7 |
| Zenoh C++ connectivity | 8/9 | 106 | 7/8 |
| Diskcache resharding v2 | 2/9 | 93.5 | 1/2 |
| Luigi generation target | 4/9 | 52 | 1/4 |
| Rosbags storage writers | 9/9 | 44 | 3/9 |
| Zarr value codecs | 6/9 | 100.5 | 6/6 |
| Burn ONNX runtime weights | 8/9 | 225 | 8/8 |
| Burn checkpoint reader v4 | 7/9 | 144 | 7/7 |
| Rerun chunk optimizer | 8/9 | 119 | 7/8 |
| Zenoh timestamps v3 | 8/9 | 216.5 | 8/8 |

Across all eleven grids, 76/99 first trials pass; 64/76 successes exceed 75 assistant turns. Five first-result costs are censored. These descriptive counts do not establish reliable per-cell pass rates, a general model ranking or a causal effect of effort.

Grid notation: **P** = scored pass, **F** = scored failure, **A** = agent timeout, **V** = verifier timeout. Numbers are completed assistant turns. **†** marks the two specifically audited gateway/quota interruptions. Each cell links to its raw result.

## Foxglove parameter handler

Expose the existing Rust responder-based parameter API through the C FFI and C++ SDK so WebSocket and remote-access handlers can finish get/set requests later from another thread. Preserve ownership, drop/error behavior, generated-header/build integration and legacy callback behavior.

Observed first grid: **9/9 passes**; successful median **163** assistant turns, range **79–364**. **9/9** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 79](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__XdhK6NH/result.json) | [P · 134](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__mcJHqPh/result.json) | [P · 191](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__PfTWWLj/result.json) |
| Opus | [P · 82](../../jobs/candidates-all14-efforts-20-20260913T183301Z-repair-python310-001/cpp-foxglove-sdk-parameter-handl__q2eNzjP/result.json) | [P · 133](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__ZhwWXAK/result.json) | [P · 168](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__ELNBqSc/result.json) |
| Sonnet | [P · 163](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__G6AoojN/result.json) | [P · 270](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__jqTiXTr/result.json) | [P · 364](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-foxglove-sdk-parameter-handl__skHsJVS/result.json) |

**Interpretation.** All nine first cells pass, and every success exceeds 75 assistant turns (median 163, range 79–364). This supports sustained implementation depth across the tested settings; the first grid provides no observed failure headroom. The gateway path is compile/API checked rather than exercised end to end, as the instructions disclose.

**Failure validity and limits.** No failed first-result cell. The Opus/medium result comes from the explicitly adopted Python-startup repair job, not an omitted cell or an extra first result.

Source contract: [instruction.md](../../candidates_v2/cpp-foxglove-sdk-parameter-handler/instruction.md). Exact instruction hash: `f26df0025448cc4f68625890a804598e9a49ed43e6e50ac43f0cf8e7fba5c3cb`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `cpp-foxglove-sdk-parameter-handler`.

## rosbag2 mixed playback

Read and play bags containing mixed serialization formats, tag messages with delivered formats, identify undeliverable topics, resolve requested/excluded topics before playback, and preserve raw rewrite behavior with equivalent SQLite3 and MCAP results.

Observed first grid: **7/9 passes**; successful median **154** assistant turns, range **112–381**. **7/7** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 112](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__AnWqDSD/result.json) | [P · 146](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__nEuowBK/result.json) | [P · 194](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__jf5Q9Ki/result.json) |
| Opus | [F · 99](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__XonizBd/result.json) | [P · 125](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__PozQCx4/result.json) | [P · 155](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__tKGbcUC/result.json) |
| Sonnet | [P · 154](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__REVuzNz/result.json) | [F · 251](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__8tAPNNm/result.json) | [P · 381](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-rosbag2-mixed-serialization__4PrpmTa/result.json) |

**Interpretation.** Seven of nine first cells pass; all nine attempts exceed 75 turns and successful depth is median 154 (112–381). Opus/medium XonizBd fails four CLI warning checks despite passing differential checks: explicitly excluded or unselected incompatible topics are still mentioned. Sonnet/high 8tAPNNm fails only the writer-side converted-format assertion; the existing contract review calls that extension ambiguous. Do not present both zeros as equally established behavioral headroom.

**Failure validity and limits.** C6 explicitly forbids mentioning explicitly excluded topics; its requested/implicit selection distinction supports the warning failures. The Sonnet/high writer-boundary assertion is not an unambiguous reader-contract violation. A later Fable/max repeat uqBGJ4B aborts in a pre-existing action playback fixture; that repeat is outside this first grid and its source/teardown cause is unresolved.

Source contract: [instruction.md](../../candidates_v2/cpp-rosbag2-mixed-serialization-playback/instruction.md). Exact instruction hash: `1bf70285c40dbd505511e422157c72c8d6c3bb13a8840339d254327136e0bb6e`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `cpp-rosbag2-mixed-serialization-playback`.

## Zenoh C++ connectivity

Wrap transport/link information and connectivity events in the header-only C++ API over zenoh-c and zenoh-pico, including ownership, callback/background listeners, history and undeclare behavior, and repair the specified optional interop helpers. Match Python connectivity reports and preserve both backend regression suites.

Observed first grid: **8/9 passes**; successful median **106** assistant turns, range **62–331**. **7/8** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 97](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__NepaoJw/result.json) | [P · 115](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__XPsWyNj/result.json) | [P · 184](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__eqRagrv/result.json) |
| Opus | [P · 62](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__L2NocVX/result.json) | [P · 85](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__iNXNSXx/result.json) | [F · 134](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__4gQVPQy/result.json) |
| Sonnet | [P · 87](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__2q8Zzzp/result.json) | [P · 165](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__Gwf85pb/result.json) | [P · 331](../../jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__uy7US2n/result.json) |

**Interpretation.** Eight of nine first cells pass; successful depth is median 106 (62–331), with 7/8 successes above 75 turns. The only first failure is Opus/max 4gQVPQy: an existing zenoh-pico advanced pub/sub test segfaults after 13.36 s, leaving 31/32 CTests passing. Both new connectivity tests and the Python parity group pass. This establishes observed depth but not a diagnosed connectivity implementation defect.

**Failure validity and limits.** Existing upstream regressions are explicitly required, so the failing suite is in scope. A segfault without an assertion/stack or replay does not distinguish a submitted-source regression from a pre-existing or timing-dependent problem. No hidden-test/spec mismatch is established by this inspection.

Source contract: [instruction.md](../../candidates_v2/cpp-zenoh-cpp-connectivity-api/instruction.md). Exact instruction hash: `b54f7ff525a8a1695241aba3c4505b657d0304dac3064682b0e33d6372b80e72`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `cpp-zenoh-cpp-connectivity-api`.

## Diskcache resharding v2

Add a managed FanoutCache that persists topology and incrementally copies/reconciles data to a new shard count while reads and writes continue. Use epoch fencing, nonblocking process leases, atomic topology publication, crash recovery and bounded migration work while preserving TTLs, serialization, native transactions and named stores.

Observed first grid: **2/9 passes**; successful median **93.5** assistant turns, range **70–117**. **1/2** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 70](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__QHomzuG/result.json) | [F · 74](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__q8EFeAF/result.json) | [F† · 24](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__uuqa2f6/result.json) |
| Opus | [F · 59](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__DDiwNFh/result.json) | [P · 117](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__nDEaGXc/result.json) | [F · 143](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__pWxf5v9/result.json) |
| Sonnet | [F · 60](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__Mhaqxc7/result.json) | [V · 83](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__8V4HQnb/result.json) | [A · 39](../../jobs/candidates-diskcache-v2-efforts-20-20260913T234023Z/diskcache-online-reshard-v2__pnV8fNg/result.json) |

**Interpretation.** Two of nine first cells pass; success median 93.5 (70–117), with only 1/2 successes above 75 turns. The grid contains seven scored outcomes, one agent timeout and one verifier timeout. Its Fable/max uuqa2f6 zero follows a provider usage-limit rejection after 24 accepted turns, not a clean completed model attempt. Some ordinary failures also show stale values resurrecting after clear/cull/migration and configuration/adoption property disagreements.

**Failure validity and limits.** Mutation reconciliation and non-resurrection are explicit requirements. The several size_limit/property assertions have only been observed here, not independently adjudicated against native FanoutCache property semantics; do not count each as proven contract-grounded headroom. The quota-interrupted and timeout cells remain policy-counted with their confounds visible.

Source contract: [instruction.md](../task-revisions/diskcache-online-reshard-v2/instruction.md). Exact instruction hash: `1b5b468f531182cbc9e0ea2db0a9c567be344e4f99521ede83d8fab524e04984`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `diskcache-online-reshard-v2`.

## Luigi generation target

Add LocalGenerationTarget for atomically publishing a complete collection of local output files. Support compare-and-swap writers, integrity manifests, stable pinned snapshots, interprocess locks, pruning and crash-safe CURRENT publication while working with ordinary Luigi task/worker scheduling.

Observed first grid: **4/9 passes**; successful median **52** assistant turns, range **41–110**. **1/4** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [F · 67](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__kSYmWv5/result.json) | [P · 41](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__cziFpEx/result.json) | [A† · 20](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__6ooqjT5/result.json) |
| Opus | [P · 47](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__jur3DjC/result.json) | [P · 57](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__qxwmpMC/result.json) | [P · 110](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__YTiAvMD/result.json) |
| Sonnet | [F · 63](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__6D7GoXc/result.json) | [F · 96](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__9fuVagX/result.json) | [A · 32](../../jobs/candidates-all14-efforts-20-20260913T183301Z/luigi-generation-target__tvHP5oC/result.json) |

**Interpretation.** Four of nine first cells pass; successful depth is median 52 (41–110), with 1/4 successes above 75 turns. Three scored zeros concern observed retention, absent-read side effects, unclosed writer streams and symlink handling; two other cells time out. Fable/max 6ooqjT5 timed out after 20 accepted turns amid repeated gateway failures, so the policy zero is service/budget confounded.

**Failure validity and limits.** No-store creation on absent reads, closing owned streams after failure and rejecting symlink corruption are explicit. Not every lifecycle exception-type or retention assertion has received an independent contract/source audit; do not label all remaining checks proven defects. Preserve the first timeout separately from excluded transport attempts and the standalone streaming canary.

Source contract: [instruction.md](../../candidates/luigi-generation-target/instruction.md). Exact instruction hash: `5ab4ed2b26a4708a6e23c4e80219f5ee9451b5f1fb8ea9ffc9a0f4144bf9ac6f`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `luigi-generation-target`.

## Rosbags storage writers

Implement the missing storage writer backends beneath the existing rosbags Writer. Produce genuine rosbag2 SQLite schema-v4 and indexed/chunked MCAP files, preserving metadata, opaque payloads, timestamps, compression modes and round trips through independent readers and recorded ROS2 bags.

Observed first grid: **9/9 passes**; successful median **44** assistant turns, range **20–173**. **3/9** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 20](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__FmGSwjK/result.json) | [P · 44](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__d9dcBsK/result.json) | [P · 90](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__kiNQ8dd/result.json) |
| Opus | [P · 22](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__nZqwEjP/result.json) | [P · 29](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__6p5M5sT/result.json) | [P · 80](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__wquToRp/result.json) |
| Sonnet | [P · 43](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__tLW2L3M/result.json) | [P · 53](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__teztrdP/result.json) | [P · 173](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-rosbags-rosbag2-storage-write__9nmWAK2/result.json) |

**Interpretation.** All nine first cells pass. Successful depth is median 44 (20–173); only 3/9 exceed 75 turns. The task demonstrates broad interoperability work, but this first grid supplies neither observed failures nor consistent long-solution depth at the tested settings.

**Failure validity and limits.** No failed first-result cell. Passing groups cover upstream tests and independent SQLite/MCAP checks as specified; this is not a claim that every possible file or compression corner case is proved.

Source contract: [instruction.md](../../candidates_v2/py-rosbags-rosbag2-storage-writers/instruction.md). Exact instruction hash: `c300e2e676ee8738c2ac91a337d32bce99ea984890761f0ce0970c935ba38eb9`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `py-rosbags-rosbag2-storage-writers`.

## Zarr value codecs

Implement composable Zarr v3 array-to-array numeric conversion and affine scale/offset codecs, including exact JSON metadata, scalar maps, rounding and range policies, validation and dtype preservation. Match an independent Rust cast-value implementation byte for byte and exact integer scale/offset arithmetic.

Observed first grid: **6/9 passes**; successful median **100.5** assistant turns, range **88–335**. **6/6** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [F · 63](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__rHJvFyv/result.json) | [P · 93](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__EZrcJhU/result.json) | [P · 153](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__665b4Gz/result.json) |
| Opus | [P · 88](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__cxFrZah/result.json) | [P · 105](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__Hywv23R/result.json) | [P · 96](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__sBejKrD/result.json) |
| Sonnet | [F · 117](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__6Fa8oyL/result.json) | [F · 142](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__q7MZcL8/result.json) | [P · 335](../../jobs/candidates-all14-efforts-20-20260913T183301Z/py-zarr-python-cast-value-scale__v7mstoG/result.json) |

**Interpretation.** Six of nine first cells pass; success median 100.5 (88–335), and all 6 successes exceed 75 turns. All three failed first runs still pass 3,790 cast-value comparisons,12 foreign-metadata checks and 505 upstream tests. Fable/medium and Sonnet/medium fail specified validation/error-contract checks (and Sonnet/medium JSON form); Sonnet/high q7MZcL8 additionally fails integer scale/offset paths with int.astype errors and passes only 32/100 exact-reference cases.

**Failure validity and limits.** CV-4 explicitly fixes normalized JSON form; CV-6 and SO-4 require ValueError messages for unsupported/unrepresentable inputs; SO-5 requires working exact integer arithmetic and dtype preservation. These observed failure classes map to the written contract, but no exhaustive submitted-source/root-cause audit or replay was performed. Do not treat all numeric conversion as broken when its differential matrix passes.

Source contract: [instruction.md](../../candidates_v2/py-zarr-python-cast-value-scale-offset/instruction.md). Exact instruction hash: `39f73decae62f3c1d1b40808208c98e1475222eb4f4db9ba0683932b6c250660`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `py-zarr-python-cast-value-scale-offset`.

## Burn ONNX runtime weights

Make generated GRU/LSTM/RNN models consume runtime W/R/B inputs rather than uninitialized module parameters, repair batch-first layout axes and direction/output-subset handling, reject unsupported or mixed weight configurations, and preserve initializer and upstream regression behavior.

Observed first grid: **8/9 passes**; successful median **225** assistant turns, range **134–446**. **8/8** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 162](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__GDuU3WF/result.json) | [P · 194](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__VKwLBpw/result.json) | [P · 260](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__KZm2zZM/result.json) |
| Opus | [P · 134](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__VzRc3tg/result.json) | [P · 230](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__6AHBGQa/result.json) | [P · 220](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__VY34jfU/result.json) |
| Sonnet | [F · 217](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__FT2MwFs/result.json) | [P · 288](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__7nmAzEe/result.json) | [P · 446](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-onnx-rnn-runtime-weights__ekkDDw7/result.json) |

**Interpretation.** Eight of nine first cells pass; success median 225 (134–446), and all 8 successes exceed 75 turns. This is the strongest already-built depth alternative by this descriptive median. The sole first failure, Sonnet/medium FT2MwFs at 217 turns, passes runtime/numerical groups but fails nine opset snapshots after adding runtime_weights:false to the rendered IR. These are one observed snapshot regression cluster, not nine independent runtime defects.

**Failure validity and limits.** Preserving the existing onnx-ir integration/snapshot regressions is explicit. The prior review does not dismiss that zero as an invalid hidden test, while distinguishing it from numerical correctness. Greater median depth alone does not establish that this is a stronger final task than the reader task.

Source contract: [instruction.md](../../candidates_v2/rs-burn-onnx-rnn-runtime-weights/instruction.md). Exact instruction hash: `cf4be2a9c4fbcefb2579577bcf3068a339066e4fb41b64d6c21f52be8b6ecf5c`.

Detailed verifier observations and bound source references are in [the task-profile evidence](evidence/completed-task-profiles-001.json), under `rs-burn-onnx-rnn-runtime-weights`.

## Burn checkpoint reader v4

Read PyTorch checkpoint formats into Burn while preserving lazy tensors and validating malformed, unsupported and resource-sensitive inputs.

Observed first grid: **7/9 passes**; successful median **144** assistant turns, range **76–323**. **7/7** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 92](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__x8gLSab/result.json) | [P · 138](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__amL9q9w/result.json) | [P · 158](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__8dJTVHw/result.json) |
| Opus | [P · 76](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__ppNrLD4/result.json) | [P · 144](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__3nLtT5y/result.json) | [P · 158](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__roFDdeS/result.json) |
| Sonnet | [F · 42](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__vXNtQ5E/result.json) | [F · 251](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__BrRKozk/result.json) | [P · 323](../../jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__X6pyirF/result.json) |

**Interpretation.** Seven of nine first cells pass, with a successful median of 144 turns. Sonnet/medium fails functional checks after 42 turns; Sonnet/high passes functional checks but violates a source constraint after 251; Sonnet/max passes all verifier groups after 323. Keep the source-constraint failure distinct from parser/validation failures.

**Failure validity and limits.** The final reader revision removes the earlier unsupported Debug requirement. The retained functional and source-constraint checks map to the task instructions. Later repeat outcomes remain separate.

Source contract: [instruction.md](../task-revisions/rs-burn-store-pytorch-reader-v4/instruction.md). Exact instruction hash: `d908b9f47e6a61fa531e5300852732a7ace9134c8eb6e175a0c233e782bde270`.

Detailed contract mappings and successful contrast traces are in [the shortlist evidence](evidence/shortlist-cases.json).

## Rerun chunk optimizer

Implement bounded streaming optimization of recording chunks while preserving every data cell and the specified split, packing and row-limit layout.

Observed first grid: **8/9 passes**; successful median **119** assistant turns, range **72–383**. **7/8** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 115](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__iJcBBQN/result.json) | [P · 110](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__MJCzq4n/result.json) | [P · 154](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__8tSsQED/result.json) |
| Opus | [P · 72](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__rnfDPM4/result.json) | [P · 104](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__S4dbqtL/result.json) | [P · 123](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__VLUCaM4/result.json) |
| Sonnet | [F · 138](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__EeEppp5/result.json) | [P · 193](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__cEHE3Gu/result.json) | [P · 383](../../jobs/candidates-all14-efforts-20-20260913T183301Z/rs-rerun-chunk-optimizer__iUZ6GKK/result.json) |

**Interpretation.** Eight of nine first cells pass. The Sonnet/medium failure at 138 turns violates five required layout checks while existing regressions pass; Sonnet/max passes after 383 turns. The successful median is 119 turns; seven of eight successes exceed 75. This supplies contract-grounded failure evidence and a successful contrast, without proving a causal benefit from effort.

**Failure validity and limits.** The reviewed first failure maps to the written chunk-layout contract. Later repeat failures remain separate observations.

Source contract: [instruction.md](../../candidates_v2/rs-rerun-chunk-optimizer/instruction.md). Exact instruction hash: `639972e3545e9bf54171f598aad90cab4747c87af3c5cd36097140d0ee5e83b7`.

Detailed contract mappings and successful contrast traces are in [the shortlist evidence](evidence/shortlist-cases.json).

## Zenoh timestamps v3

Carry timestamp metadata through public APIs, wire encoding and publish/receive paths, preserving interoperability and bounded decoding behavior.

Observed first grid: **8/9 passes**; successful median **216.5** assistant turns, range **166–626**. **8/8** successes exceed 75 turns.

| Model | Medium | High | Max |
|---|---|---|---|
| Fable | [P · 166](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__XsLhxGQ/result.json) | [P · 188](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__D3tzpaW/result.json) | [P · 216](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__QYtUfZA/result.json) |
| Opus | [P · 176](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__X54KUJt/result.json) | [P · 217](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__32sQasx/result.json) | [P · 245](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__YDTpWKS/result.json) |
| Sonnet | [F · 377](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__YtCswNv/result.json) | [P · 474](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__3TNwuZ8/result.json) | [P · 626](../../jobs/candidates-zenoh-v3-efforts-20-20260913T221317Z/rs-zenoh-timestamp-instrumentati__gSYDMEG/result.json) |

**Interpretation.** Eight of nine first cells pass, with a successful median of 216.5 turns. Sonnet/medium uses the wrong public TimestampContext.zid type after 377 turns; Sonnet/max passes after 626. The corrected verifier independently regraded these same nine saved submissions with eight passes and the same failure. These regrades add no model trials.

**Failure validity and limits.** The public ZenohId type is explicit. Corrected coverage has exact controls and a passing quality review; the failing regrade stops at compilation and does not execute later runtime groups. Historical v3 outcomes stay separate from the corrected package.

Source contract: [instruction.md](../task-revisions/rs-zenoh-timestamp-instrumentation-v3/instruction.md). Exact instruction hash: `31ea9c624ba8ab89828aaa90c7e4a63efa945c65ad5fe64e6e6ccb26668fb85b`.

Detailed contract mappings and successful contrast traces are in [the shortlist evidence](evidence/shortlist-cases.json).

## Service interruptions and historical revisions

Diskcache Fable/max `uuqa2f6` ended on a provider usage-limit rejection after 24 completed turns; the existing abnormal-exit policy counts its verifier result as zero. Luigi Fable/max `6ooqjT5` reached its 7200-second agent limit after 20 completed turns amid gateway retries. These observations establish collected coverage, with a limited basis for attributing failure to model capability. The report preserves both raw outcomes and the service caveats. The user cannot raise the quota and has no gateway deployment access or contact.

The held SQLite NUL audit remains separate validity context: all 30 audited attempts failed the valid NUL-default test, but only three failed solely on it. It is not an additional current task in the frozen 99-cell grid. Other held or superseded revisions are not merged into current task scores.

The corrected Zenoh package has nine complete saved-submission regrades: eight passes and one unchanged documented public-type failure. The recorded first grid above is the original v3 cohort. [Revision mapping](../zenoh-coverage-followup-2026-09-14/final-revision-mapping-001.json).

## Evidence packaging

The strict review pack retains three recommended task packages and their 27 first-result jobs. Supporting evidence retains the other 72 first-result jobs in full, plus 13 cited counted repeats and two operational incidents. Thus every cell reported here has its full raw directory preserved. The other built task packages remain under `archive/`. No provider request, replay or historical rescoring is needed to expand report coverage.

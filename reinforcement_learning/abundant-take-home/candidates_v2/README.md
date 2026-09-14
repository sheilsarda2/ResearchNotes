# candidates_v2: long-horizon SWE tasks in robotics and ML data infrastructure

Working design for a second task pack, built from first principles after an evidence review of where frontier coding agents fail on long-horizon software engineering (September 2026). It supersedes nothing in `../candidates/`; v1 remains an authored, single-boundary pack. v2 is a sourced pack spanning Python, Rust and C++.

## The sub-domain and why

Robotics and ML data infrastructure: log formats (MCAP, .rrd, rosbag2), pub/sub middleware (zenoh, rmw), tensor frameworks and model import (burn, burn-onnx, onnx), and columnar or chunked storage (zarr, parquet, lance). Three properties make this the right narrowing for a long-horizon pack:

1. **Multi-language ecosystems with a canonical spec.** Every project here ships two or more implementations of the same contract and a harness that compares them: rerun runs each snippet in Python, Rust and C++ and diffs the `.rrd` output (`docs/snippets/compare_snippet_output.py` plus `rerun rrd compare`); MCAP has a conformance corpus with reader and writer runners for six languages (`tests/conformance`); burn has one backend test suite shared across all backends (`burn-backend-tests`) and burn-onnx scores against the full upstream ONNX node test corpus (`onnx-official-tests`); zarrs ships `zarrs_conformance` and zarr-python compatibility fixtures; zenoh's storage manager tests any `Volume` against the same workload. Verification can therefore be **differential against an independent implementation or corpus**, which is the verifier design with the best evidence (FrontierSWE, SWE-Marathon) and the one v1 lacked.
2. **Feature-scale, cross-cutting work is the norm.** A storage backend, a codec, an index, an operator family, a protocol feature. Mined merged PRs in these repos routinely touch 15 to 60 files with 500 to 3,000 added lines and ship their own tests.
3. **Real users and real churn.** All pool repos pushed within the last week; zenoh, burn, mcap and rerun each merged hundreds of PRs this year, so recency-based contamination control is available.

## What the evidence says the pack must do

Primary sources reviewed on 2026-09-13 (details in `../research/candidate-evaluation-2026-09-13.md` and the conversation record):

- **Scope, not language, drives headroom.** Python feature-scale benchmarks sit at 11 to 25% for the best agents (FeatureBench 11.0%, SWE-EVO 25%); RoadmapBench across five languages tops out at 39.1%. Single-PR bug fixes are saturated; feature-scale work is not.
- **Strong models fail by dropping or misreading requirements**, not by syntax: over 60% instruction-following failures for GPT-5 on SWE-EVO, "forgotten requirement" at roughly one third of labels in SWE-INTERACT, the "one branch shipped" pattern in DeepSWE, "edge-case and compatibility behaviours" in FrontierSWE.
- **Crash consistency is unsolved for most of the field but not for the strongest models.** FrontierSWE flash-fs: Claude models 0.78 to 0.97, every other frontier model at or below 0.02.
- **Differential and clean-room verification is what the strongest benchmarks use**, and cheating is real: SWE-Marathon saw reward-hacking attempts in 13.8% of rollouts; FrontierSWE documents confirmed incidents.
- **Budgets at the cliff are long.** SWE-Marathon attempts average 27.2M tokens; FrontierSWE gives 20 hours. A 2-hour cap measures the harness, not the model.

Consequences: tasks are feature-scale with many interacting requirements; verification is differential or corpus-based and rebuilt clean-room from the submission; agent budgets are raised; failure attribution is designed in (partial scores logged alongside the binary reward) so a 0 can be traced to a dropped requirement rather than a convention miss.

## Task forms

| Form | Source of spec | Source of verifier | Oracle | Contamination | Use |
|---|---|---|---|---|---|
| **A. Mined feature PR** | Issue, PR body, release note, repaired by a derivability pass | PR's own tests plus the project's conformance or differential harness | The merged patch | Controlled by recency (prefer merged after 2026-06) | Default |
| **B. Feature excision with cross-implementation oracle** | Project docs and the other-language implementation's behavior | Project conformance harness comparing the target implementation to its siblings or to reference outputs | Restore the excised code | High unless interface-shifted; acceptable for RL data, weak for headroom claims | Scale |
| **C. Spec-driven extension against an external reference** | External spec (ONNX operator schema, MCAP spec, zarr v3 spec, zenoh `Volume` trait) | External reference (onnxruntime, Go mcap CLI, zarr-python, memory backend) | Must be authored | Low | Flagship only |

Each task records its form, oracle type and contamination tier in `provenance.json`.

## Pool

| Language | Primary repos | Differential or conformance oracle available |
|---|---|---|
| Python | zarr-python, mcap (python/), onnx (reference evaluator, shape inference), rosbags, rerun_py, xarray | zarrs fixtures; MCAP conformance corpus and Rust/C++ readers; onnxruntime and ONNX node tests; rosbag2 fixtures; rrd compare |
| Rust | rerun (store crates), zenoh (storage manager, backends, session), burn (store, backend tests), burn-onnx, mcap (rust/), zarrs, arrow-rs parquet, lance, datafusion, redb, fjall | rrd compare and snapshots; memory backend and replication tests; burn-backend-tests; ONNX node tests and onnxruntime; Go mcap CLI; zarr-python; pyarrow; sqllogictest |
| C++ | rerun_cpp, zenoh-cpp, mcap (cpp/), foxglove-sdk (cpp/), rosbag2, rmw_zenoh, RocksDB, arrow C++ | rrd compare against Python/Rust; zenohd interop and zenoh-python; MCAP conformance; Rust/Python SDK parity; sqlite3 vs mcap storage plugins; rmw conformance vs fastrtps; fault-injection gtests |

Mining output for each repo is in `mining/` (merged PRs since 2026-01-01 with size, files, tests and theme hits). RocksDB is mined by commit because it lands imported diffs rather than merged PRs.

## Funnel and yield accounting

Every candidate passes through recorded stages and `funnel.json` keeps counts per stage: mined, passed size and test filters, form assigned, environment builds offline, oracle passes, no-op fails, spec derivability pass complete, contamination tier assigned, rollout gate (one Sonnet-5 attempt, reject if solved under 40 steps), Harbor oracle and no-op recorded. Rejections are kept under `rejected/` with a one-line reason. The yield table is the scale-plan evidence.

## Status (2026-09-13): all fifteen tasks Harbor-validated

Fifteen tasks (5 Python, 5 Rust, 5 C++). Every `task.toml` validates under Harbor 0.15.0's `TaskConfig`; every solution patch dry-applies to its shipped tarball; every task has a recorded Harbor oracle run with reward 1 and a no-op run with reward 0 (`validation/<task>.json`, `funnel.json`, per-task `STATUS.md` validation logs). Validation host: arm64 Docker Desktop, VM ~8 GB until about 17:00 UTC and ~32 GB afterwards (`validation/README-host-notes.md`). No task has been run against a model; headroom and horizon remain unmeasured.

| Task | Harbor oracle | Harbor no-op | Runs to pass | What the failed runs found |
|---|---|---|---|---|
| py-onnx-reference-rnn | 1 | 0 | 1 | |
| py-rosbags-rosbag2-storage-writers | 1 | 0 | 1 | |
| py-xarray-zarr-align-chunks | 1 | 0 | 1 | |
| py-zarr-python-cast-value-scale-offset | 1 | 0 | 3 | submission looked up under `/logs/artifacts` instead of its re-materialized source path; overlay deleted the in-place submission |
| py-mcap-writer-chunk-summary | 1 | 0 | 4 | scoring-helper keyword collision; upstream Go conformance reader crashes on schemaless channels; count floor |
| cpp-mcap-indexed-reader | 1 | 0 | 3 | amd64-only Go tarball on an arm64 host; same Go reader crash (fixed with a nil-schema-guarded copy of the upstream reader); Linux-only unit-test count |
| rs-mcap-cli-recover-parity | 1 | 0 | 2+ | nonexistent corpus variant name; Go `filter` emits no chunk for attachment-only files; expectation classes at exact record boundaries |
| rs-burn-store-pytorch-reader | 1 | 0 | 2 | linker OOM-killed at 18 parallel cargo jobs on 8 GB (`CARGO_BUILD_JOBS=4`) |
| rs-burn-onnx-rnn-runtime-weights | 1 | 0 | 4 | aarch64 `+fp16` for `gemm-f16`; missing OpenSSL headers for the `ort` dev-dependency; official corpus count differs by 2 between macOS and Linux |
| rs-rerun-chunk-optimizer | 1 | 0 | 2 | anti-cheat "Rust files only" flagged pristine insta `.snap` files |
| cpp-zenoh-cpp-connectivity-api | 1 | 0 | 3 | zenoh-c tests CMake failed on macOS AppleDouble `._*` members in the tarball (repacked); "musl standalone" zenohd zips are dynamically linked (switched to gnu builds); listener channel primitives absent at the pinned zenoh-c, instruction amended in three sentences |
| rs-zenoh-timestamp-instrumentation | 1 | 0 | 5 | cargo freshness after overlay (`touch`); test-name lists were empty because cargo prints binary headers on stderr, which also made the regression group a false pass (now fail-closed); pyo3 enum used as dict key; authored zenoh-ext test lacked `timestamping`; nine multicast/gossip upstream tests excluded under `no-network` with proof |
| cpp-rosbag2-mixed-serialization-playback | 1 | 0 | 3 | ROS `setup.bash` not `set -u` safe; gtests needed ament env via `ctest`; differential compared uninitialized CDR padding (now zero-filled); domain IDs moved out of the ephemeral-port band; colcon build measured 3 min, not 40 to 70 |
| cpp-foxglove-sdk-parameter-handler | 1 | 0 | 5 | Dockerfile pinned Rust 1.85 (declared MSRV), too old for `zune-jpeg` NEON (1.87) and the repo's own let-chains (1.88): moved to 1.95.0, the stable at the base commit; `cargo fetch --target host` starved cbindgen's `cargo metadata`; Catch2 cases need one process each (`ctest`), count corrected 60 to 76 |
| cpp-rerun-cpp-ffi-bridge | 1 | 0 | 3 | pruned tarball dropped Cargo workspace members (regenerated with a member guard); Catch2 v3 "All tests passed" summary shape not parsed; ninja parallelism bounded |

Every defect found by live Harbor runs so far has been verifier plumbing, reference-tool limitations, or host portability. None has been a task-contract or reference-solution defect. The pattern argues for validating on both amd64 and arm64 before delivery and for treating "oracle=1 on the author's machine" as no evidence at all. Nothing here has been run against a model.

## Measured validation (Harbor 0.15.0, arm64 host)

Wall time is the oracle trial end to end including cached image setup; verifier seconds is the verifier phase alone. Image sizes where an agent measured them. Gold patch size is the non-test diff.

| Task | Lang | Form | Oracle wall s | Verifier s | Images GB (agent/verifier) | Gold +lines/files | Contamination tier | Oracle type |
|---|---|---|---|---|---|---|---|---|
| cpp-foxglove-sdk-parameter-handler | cpp | A | 56 | 49 | 8.05/8.15 | 1294/17 | A_recent | cross_impl_differential |
| cpp-mcap-indexed-reader | cpp | B | 61 | 45 | ?/2.4 | 1064/5 | C_public | conformance_corpus+cross_impl_differential+authored |
| cpp-rerun-cpp-ffi-bridge | cpp | B | 445 | 423 | 4.35/4.6 | 1064/16 | C_public | cross_impl_differential |
| cpp-rosbag2-mixed-serialization-playback | cpp | A | 278 | 270 | 2.97/2.98 | 338/16 | A_recent | cross_impl_differential |
| cpp-zenoh-cpp-connectivity-api | cpp | A | 793 | 586 | 3.19/3.46 | 1182/27 | A_recent | cross_impl_differential |
| py-mcap-writer-chunk-summary | python | B | 94 | 81 | ?/? | 379/3 | C_public | conformance_corpus+cross_impl_differential |
| py-onnx-reference-rnn | python | B | 170 | 88 | ?/? | 608/4 | C_public | conformance_corpus |
| py-rosbags-rosbag2-storage-writers | python | B | 91 | 52 | ?/? | 352/2 | C_public | cross_impl_differential |
| py-xarray-zarr-align-chunks | python | A | 97 | 34 | ?/? | 372/5 | C_public | cross_impl_differential |
| py-zarr-python-cast-value-scale-offset | python | A | 30 | 23 | ?/? | 810/5 | C_public | cross_impl_differential |
| rs-burn-onnx-rnn-runtime-weights | rust | A | 165 | 141 | ?/? | 1931/11 | A_recent | cross_impl_differential |
| rs-burn-store-pytorch-reader | rust | A | 352 | 135 | ?/? | 3497/20 | A_recent | cross_impl_differential |
| rs-mcap-cli-recover-parity | rust | A | 125 | 102 | 5.45/6.69 | 1142/6 | A_recent | reference_binary |
| rs-rerun-chunk-optimizer | rust | A | 79 | 56 | ?/? | 2445/16 | A_recent | pr_tests |
| rs-zenoh-timestamp-instrumentation | rust | A | 1042 | 994 | ?/? | 1588/35 | A_recent | cross_impl_differential |

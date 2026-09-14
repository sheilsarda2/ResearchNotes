# candidates_v2 evaluation: Pugh chart and stack rank

Checked 2026-09-13. AI-assisted assessment of the fifteen `candidates_v2/` tasks, written to support selection for `samples/`. Companion to `candidate-evaluation-2026-09-13.md` (the ten authored v1 tasks); same criteria and weights so the two packs compare directly. Not the human-written client report.

Evidence base: the fifteen construction reports (feature choice, gold patch, oracle design), the per-task `STATUS.md` validation logs (every Harbor oracle/no-op run, every defect and fix), and the measured table in `candidates_v2/README.md`. Every task has oracle 1 and no-op 0 under Harbor 0.15.0. No task has been run against a model, so the headroom column is a static estimate, not a measurement.

## Criteria and weights

| Code | Criterion | Weight | How scored here |
|---|---|---|---|
| HR | Headroom magnitude | 3 | Scope and interacting mechanisms of the gold patch; discounted where the code is public and recall-prone (Form B excisions, older PRs) |
| ATT | Failure attribution: a 0 traces to the engineering, not to a convention the spec never stated | 3 | Independence of the oracle (reference binary, sibling implementation, external corpus) versus PR tests and authored assertions; validation evidence that mutations are caught |
| HZ | Horizon from compounding decisions | 2 | Crates/packages touched, layers crossed, whether decisions constrain each other |
| VER | Verifier soundness as validated | 2 | Runs needed, relaxations recorded, residual flake or drift risk |
| DEM | Demand grounding | 2 | Merged PR with linked issue and recency beats "the feature exists"; robotics-native use counts |
| NOV | Contamination resistance | 1 | Provenance tier: A_recent versus C_public |
| SCL | Scale-out as a template for 1,000 tasks | 1 | Does the sourcing and oracle pattern repeat across repos and features |
| OPS | Operational cost and fragility | 1 | Image size, verifier wall time, external dependency drift |

## The fifteen tasks at a glance

Measured under Harbor 0.15.0 on the arm64 validation host. "Runs" is the number of oracle runs needed before oracle 1 and no-op 0 both held; every failed run traced to verifier plumbing or host portability, never to the task contract or the reference solution (one instruction amendment, cpp-zenoh-cpp, is noted below).

| Task | Lang | Form | Tier | Feature date | Gold +lines/files | Oracle | Verifier s | Verifier image GB | Runs |
|---|---|---|---|---|---|---|---|---|---|
| py-mcap-writer-chunk-summary | Python | B | C_public | excision | 379/3 | MCAP conformance corpus + Go/Rust readers, byte-identical | 81 | small | 4 |
| py-onnx-reference-rnn | Python | B | C_public | excision | 608/4 | ONNX node corpus + onnxruntime differential | 88 | small | 1 |
| py-rosbags-rosbag2-storage-writers | Python | B | C_public | excision | 352/2 | real rosbag2 fixtures + mcap CLI | 52 | small | 1 |
| py-xarray-zarr-align-chunks | Python | A | C_public | 2025-06-05 | 372/5 | 51 authored checks incl. instrumented store | 34 | small | 1 |
| py-zarr-python-cast-value-scale-offset | Python | A | C_public | 2026-04-30 | 810/5 | zarrs binary, 3,790 byte-identical cases + 100 exact-arithmetic | 23 | small | 3 |
| rs-burn-onnx-rnn-runtime-weights | Rust | A | A_recent | 2026-08-21 | 1,931/11 | onnxruntime differential + 860-case official corpus | 141 | n/m | 4 |
| rs-burn-store-pytorch-reader | Rust | A | A_recent | 2026-09-10 | 3,497/20 | PyTorch-generated accept/reject matrix under ulimit | 135 | n/m | 2 |
| rs-mcap-cli-recover-parity | Rust | A | A_recent | 2026-06-01 | 1,142/6 | Go mcap CLI as reference binary, 2,235 cases | 102 | 6.69 | 2+ |
| rs-rerun-chunk-optimizer | Rust | A | A_recent | 2026-09-02 | 2,445/16 | PR tests + authored contract tests | 56 | n/m | 2 |
| rs-zenoh-timestamp-instrumentation | Rust | A | A_recent | 2026-07-21 | 1,588/35 | gold-built peer interop + zenoh-python parity + 47 hidden tests | 994 | 8.03 | 5 |
| cpp-foxglove-sdk-parameter-handler | C++ | A | A_recent | 2026-05-23 | 1,294/17 | wire-transcript parity against the Rust SDK | 49 | 8.15 | 5 |
| cpp-mcap-indexed-reader | C++ | B | C_public | excision | 1,064/5 | 199 analytic fixture checks + Go/Rust siblings | 45 | 2.4 | 3 |
| cpp-rerun-cpp-ffi-bridge | C++ | B | C_public | excision | 1,064/16 | 104 cross-language `.rrd` comparisons | 423 | 4.6 | 3 |
| cpp-rosbag2-mixed-serialization-playback | C++ | A | A_recent | 2026-09-11 | 338/16 | storage-plugin differential (sqlite3 vs mcap) + ctest | 270 | 2.98 | 3 |
| cpp-zenoh-cpp-connectivity-api | C++ | A | A_recent | 2026-03-13 | 1,182/27 | zenoh-python parity on two backends, `-Werror` | 586 | 3.46 | 3 |

"n/m" means the image size was not recorded in the measured table; "small" means a Python-only image well under 2 GB.

## Pugh chart

Datum: `rs-mcap-cli-recover-parity` (Form A, June 2026, Go CLI as reference binary over 2,235 cases, mid-size images). Scale −2..+2 relative to it.

| Task | Lang | Form | HR ×3 | ATT ×3 | HZ ×2 | VER ×2 | DEM ×2 | NOV ×1 | SCL ×1 | OPS ×1 | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rs-burn-store-pytorch-reader | Rust | A | +1 | +1 | +1 | +1 | +1 | +1 | +1 | 0 | **+14** |
| rs-zenoh-timestamp-instrumentation | Rust | A | +2 | +1 | +2 | −1 | +1 | +1 | 0 | −2 | **+12** |
| rs-burn-onnx-rnn-runtime-weights | Rust | A | +1 | +1 | +1 | 0 | +1 | +1 | +1 | −1 | **+11** |
| cpp-foxglove-sdk-parameter-handler | C++ | A | +1 | +1 | +1 | 0 | 0 | 0 | +1 | −2 | **+7** |
| py-zarr-python-cast-value-scale-offset | Python | A | 0 | +1 | 0 | 0 | +1 | −1 | +1 | +1 | **+6** |
| cpp-zenoh-cpp-connectivity-api | C++ | A | +1 | +1 | +1 | −1 | 0 | 0 | +1 | −1 | **+6** |
| cpp-rosbag2-mixed-serialization-playback | C++ | A | 0 | +1 | 0 | 0 | +1 | +1 | 0 | −1 | **+5** |
| py-rosbags-rosbag2-storage-writers | Python | B | 0 | 0 | 0 | +1 | 0 | −1 | +1 | +1 | **+3** |
| rs-rerun-chunk-optimizer | Rust | A | +1 | −1 | +1 | 0 | 0 | +1 | 0 | 0 | **+3** |
| rs-mcap-cli-recover-parity (datum) | Rust | A | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| cpp-rerun-cpp-ffi-bridge | C++ | B | 0 | +1 | +1 | 0 | −1 | −1 | 0 | −2 | **0** |
| cpp-mcap-indexed-reader | C++ | B | −1 | +1 | 0 | 0 | −1 | −1 | +2 | +1 | **0** |
| py-xarray-zarr-align-chunks | Python | A | −1 | 0 | 0 | 0 | +1 | −2 | 0 | +1 | **−2** |
| py-onnx-reference-rnn | Python | B | −1 | 0 | −1 | +1 | −1 | −1 | +2 | +1 | **−3** |
| py-mcap-writer-chunk-summary | Python | B | −1 | 0 | −1 | −1 | −1 | −1 | +2 | +1 | **−7** |

## Stack rank

1. rs-burn-store-pytorch-reader
2. rs-zenoh-timestamp-instrumentation
3. rs-burn-onnx-rnn-runtime-weights
4. cpp-foxglove-sdk-parameter-handler
5. py-zarr-python-cast-value-scale-offset
6. cpp-zenoh-cpp-connectivity-api
7. cpp-rosbag2-mixed-serialization-playback
8. py-rosbags-rosbag2-storage-writers
9. rs-rerun-chunk-optimizer
10. rs-mcap-cli-recover-parity
11. cpp-rerun-cpp-ffi-bridge
12. cpp-mcap-indexed-reader
13. py-xarray-zarr-align-chunks
14. py-onnx-reference-rnn
15. py-mcap-writer-chunk-summary

## Score rationale, one line per task

- **rs-burn-store**: three-day-old merged fix for two filed bugs (silent zero substitution, broadcast-view OOM); PyTorch itself generates the 428-tensor accept matrix and 32 reject cases run under a memory ulimit; only defect in validation was host memory. Pattern generalizes to any foreign-format reader with the origin tool as oracle.
- **rs-zenoh**: the widest horizon in the pack (protocol, codec, session, zenoh-ext; 35 files) with three independent oracles (gold-built peer interop, zenoh-python parity, 47 hidden tests). Penalized for cost (16.6-minute verifier, 8 GB verifier image, five runs to pass) and for nine upstream tests that cannot run under `no-network` and had to be excluded with proof.
- **rs-burn-onnx**: two crates, ONNX operator semantics as external spec, onnxruntime differential plus the 858-case official corpus; four runs but every failure was environment (fp16, OpenSSL, a two-case platform count).
- **cpp-foxglove-sdk**: C++ plus C ABI over a fixed Rust core, graded by wire-level transcript parity against the Rust SDK; five runs, all environment (toolchain pin too old for the repo's own let-chains, non-host crates, Catch2 process isolation). The 8 GB images and the drifting toolchain pin are the cost; the verifier itself runs in under a minute.
- **py-zarr**: the only Python task with a second implementation as oracle (zarrs, 3,790 byte-identical cases) plus 100 exact-arithmetic reference cases; April release makes it public-tier.
- **cpp-zenoh-cpp**: six owned types and six listener variants compiled under `-Werror` on two backends, parity with zenoh-python field for field; penalized for upstream one-second-sleep tests and an instruction amendment (channel primitives absent at the pin).
- **cpp-rosbag2**: two days old, robotics-native, plugin-differential oracle; the gold patch is only 338 lines across six packages, so horizon is moderate, and ROS rolling debs drift unless snapshotted.
- **py-rosbags**: two real storage formats graded by real rosbag2 fixtures and the mcap CLI; passed first run; excision of public code.
- **rs-rerun**: newest and largest Rust feature (4 to 11 days old, 2,445 lines, three crates) but the oracle is PR tests plus authored contract tests with no independent implementation, and upstream tests pin observable shapes (adjacent-only merging, run ordering).
- **rs-mcap-cli** (datum): reference-binary oracle over 2,235 corruption cases with a streaming memory bound; strongest attribution in the pack, moderate horizon. Its own validation dropped expectation classes at exact record boundaries where the Go CLI's behaviour is not a contract.
- **cpp-rerun-cpp**: excising the whole FFI bridge and grading by 104 cross-language `.rrd` comparisons is the cleanest attribution of any C++ task, but the code is public and the cold image build is the longest in the pack.
- **cpp-mcap**: index-driven read path with 199 analytic fixture checks and Go/Rust siblings; light and very scalable as a pattern, but public code and moderate horizon.
- **py-xarray**: 2025 feature with the algorithm described in the PR body; 51 independent checks including an instrumented store, and a real silent-data-loss issue behind it, but exact alignment tuples are pinned and contamination is highest.
- **py-onnx**: three reference-evaluator ops; the onnxruntime differential caught two mutations the official tests missed, which is the right verifier shape, but the horizon is a single module and `op_lstm.py` is public.
- **py-mcap**: 379-line writer subsystem with a byte-identical 208-variant oracle; four runs to pass because of a scoring-helper bug, a crashing upstream Go tool, and a count floor; small horizon, public code.

## Recommended final three, pending trials

One per language, chosen from the top of each language column:

1. **rs-burn-store-pytorch-reader** (Rust, rank 1).
2. **cpp-foxglove-sdk-parameter-handler** (C++, rank 4). Alternate: **cpp-rosbag2** (rank 7) once its rosdep set is snapshotted into the image; it is the more robotics-native story but the smaller patch.
3. **py-zarr-python-cast-value-scale-offset** (Python, rank 5).

If language balance does not matter, the top three by score are the three Rust tasks (burn-store, zenoh, burn-onnx), and zenoh's verifier cost is the only reason not to lead with it.

## Tradeoffs the chart makes explicit

- **Form A beats Form B on every axis except scale.** All six excision tasks land at or below the datum: their oracles are strong (conformance corpora, sibling implementations) but the code is public, demand is inferred rather than observed, and horizons are bounded by the excised module. They are the right shape for a 1,000-task generator and the wrong shape for a headroom claim.
- **Independent oracle versus recency.** rs-rerun is the freshest task and scores mid-table because its verifier is PR tests plus authored assertions; rs-mcap-cli is older and scores as the datum because a reference binary grades it. For RL reward quality, the oracle wins.
- **Cost is the tax on breadth.** The three slowest verifiers (rs-zenoh 994 s, cpp-zenoh-cpp 586 s, cpp-rerun-cpp 423 s) and the two largest images (cpp-foxglove 8.05 GB, rs-zenoh 8.03 GB) all belong to wide-horizon tasks; every Python task verifies in under 90 s. A client running thousands of rollouts will feel that.
- **Python is where this pack is weakest on headroom.** Only py-zarr has a second implementation as oracle; the rest are excisions of public code or a 2025 feature. The Python side of a production pack should be mined from newer PRs across more repos than the five used here.

## Sensitivities

- Raising NOV to weight 3 (evaluation use rather than training data) drops every Form B task by 2 points and py-xarray by 4; the bottom three stay the same tasks in the same order (py-xarray −6, py-onnx −5, py-mcap −9), and the top three are unchanged.
- Raising OPS to weight 3 (client compute) moves rs-zenoh from 2nd to 3rd (tied with py-zarr at +8) and cpp-foxglove from 4th to 7th (+3, tied with rosbag2 and rs-rerun); rs-burn-store stays first at +14 and rs-burn-onnx moves to 2nd.
- Raising SCL to weight 3 (scale plan emphasis) lifts the two mcap excision tasks and py-onnx by 4 points each, putting cpp-mcap (+4) and py-onnx (+1) above the datum; py-mcap stays last.
- Any single Sonnet-5 success within 40 steps should lower that task's HR by one and the chart should be rerun before final selection.

## Relation to the v1 chart

The v1 pack's best fixed scores (leases +10, generation-target +8, reshard +8, schema-plan +7) were driven by headroom and horizon, with attribution as the weakness. The v2 pack inverts that: attribution is the strength (twelve of fifteen have an independent implementation, reference binary, or external corpus as oracle), and headroom is the open question because ten of fifteen are merged features a strong model may partially recall. Both packs share the same unmeasured column, and the same next step: one Sonnet-5 attempt per task through Harbor, then trajectory review.

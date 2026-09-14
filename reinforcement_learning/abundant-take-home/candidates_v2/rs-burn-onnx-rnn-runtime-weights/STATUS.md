# rs-burn-onnx-rnn-runtime-weights: status

Stage: **drafted** (2026-09-13). Task directory complete; Docker images not built and no Harbor
run yet (phase rule). Oracle, no-op and count measurements were done locally with a scratch
Rust 1.98.1 toolchain against git worktrees of the shared clone.

## Choice

- Upstream: tracel-ai/burn-onnx, PR #466 "RNN family: consume weights supplied as runtime graph
  inputs" (merged 2026-08-21, closes #458). Base = first parent of merge `438d2cdb` =
  `6fc6bacec1c5264d1160efd4e2c904591082e2f8` ("Bump the burn rev to 9cdb20df (#465)").
- Why over the alternates: two crates (onnx-ir type inference + constant lifting, burn-onnx codegen
  for three operators), 11 non-test files, +1931/-505, many interacting requirements (runtime vs
  build-time weights x 3 ops x 3 directions x 2 layouts x optional B / initial states x output
  subsets, a drift-gated corpus promotion, and import-time rejection semantics); it ships four
  integration tests with committed fixtures and promotes 11 upstream corpus rows, and onnxruntime
  is a clean external reference for exactly this operator family.
- Rejected: #445 custom-op hooks (60 files / +5575 incl. a 1141-line design doc and an example
  crate; tests mostly inline in src; no external reference for vendor-domain ops); #270 constrained
  Einsum (March 2026, single-operator subset, weaker differential story).

## Deliverables

| Path | Content |
|---|---|
| `environment/upstream.tar.gz` | `git archive` of the base commit, 31.98 MB (includes the 35 MB vendored ONNX node corpus); sha256 `b4d65369...8e225` |
| `environment/Dockerfile` | `rust:1.98.1-slim-bookworm@sha256:ebd900ba...`, git init of the tree, Python venv with `onnx==1.19.0` only, `cargo-insta`, `cargo fetch --locked`, warm `cargo test --no-run` for onnx-ir / burn-onnx / onnx-tests / onnx-official-tests + `onnx2burn`, `CARGO_NET_OFFLINE=true` |
| `solution/changes.patch`, `solve.sh` | PR non-test diff (11 files) applied with `git apply`; `git apply --check` passes on the base tree. `crates/onnx-tests/build.rs` is deliberately excluded (it registers hidden fixtures) |
| `tests/Dockerfile` | same base; `/opt/ref` venv with `onnxruntime==1.30.0 onnx==1.22.0 numpy==2.4.6`; hidden overlay; warm build; differential corpus generated at build; `pristine.tar.gz` |
| `tests/hidden-overlay.tar.gz` | PR's `crates/onnx-tests/tests/{gru,lstm,rnn}` (mod.rs + generators + 5 `.onnx` fixtures), PR `build.rs`, PR `expectations.toml` |
| `tests/gen_rnn_cases.py` | 24 authored RNN-family models in upstream node-test layout with onnxruntime expected outputs, expectations rows, 3 reject models, manifest |
| `tests/test.sh`, `tests/score.py` | clean-room overlay, anti-cheat, build, 4 test groups + reject probes, `reward.txt` + `score.json` |
| `instruction.md`, `task.toml`, `provenance.json` | per CONVENTIONS |

## Oracle design

1. **PR tests (hidden)**: 4 integration tests in onnx-tests (3 single-direction runtime-weight
   models via `Model::from_file` vs reference sums; bidirectional GRU runtime vs initializer
   variant). onnxruntime reproduces the PR's hardcoded sums to 7 digits.
2. **Conformance corpus**: the 11 promoted rows (expected outputs from the ONNX distribution) plus
   the whole 830-row harness and drift checks as regression, via the repo's own harness.
3. **onnxruntime differential**: 24 cases generated at verifier-image build and dropped into
   `vendor/node/test_rnnrt_*` with `status = "pass"` rows, so the repository's harness compares
   the submission's generated code against onnxruntime at `Tolerance::default()` (rel 0.5 %,
   abs 1e-5). onnxruntime has no `layout=1` kernel for GRU/LSTM/RNN, so those references are
   computed on the layout-0 twin and permuted per the spec; the permutation reproduces the
   distribution's `*_batchwise` outputs to 1.2e-7. Every case is cross-checked: forward vs
   `onnx.reference`, reverse via the flip identity, bidirectional via unidirectional halves
   (max deviation 2.4e-7). `onnx.reference` was rejected as reference: it silently ignores
   `direction=reverse` (returns the forward result) and has no bidirectional or layout=1+initial
   state support.
4. **Reject probes**: `onnx2burn` on a mixed initializer/graph-input weight group (accepted at
   base, rejected by the gold patch with exit 101 and a clear message), `sequence_lens`, and
   the corpus peephole model; plus an accept probe.
5. **Anti-cheat**: symlinks, non-`.rs/.proto/.md/.snap/.toml/.txt` files, and strings
   `/logs`, `reward.txt`, `score.json`, `onnxruntime`, `\bort::`, `ort_sys`, `test_data_set`,
   `vendor/node`, `expectations.toml`, `output_N.pb`, `test_rnnrt`, `/opt/task`, `/opt/ref`
   (none present in pristine sources).

Exact counts gated: onnx-tests 601, onnx-official-tests 858 (830 + 4 + 24), onnx-ir
integration 553, 3 rejections + 1 acceptance.

## Local validation (authoring Mac, Apple Silicon, rustc 1.98.1)

| Check | Result |
|---|---|
| `git apply --check` of `changes.patch` on base | OK |
| Gold: onnx-tests | 601 passed, 0 failed (incl. the 4 hidden tests) |
| Gold: onnx-official-tests with 24 differential rows | 858 passed in 9 of 10 runs; one run failed only `verify_fail_compare_still_fails` (upstream drift gate re-running 96 fail-compare rows). Not reproduced in 9 further runs; `test.sh` retries that single test up to twice and records retries |
| Gold: onnx-ir integration | 553 passed |
| Gold: reject probes | mixed group rc=101 ("input #1 is a build-time value while input #2 is supplied at run time ... must both be initializers or both be graph inputs"); sequence_lens rc=101; peepholes rc=101; accept rc=0 with `.rs` + `.bpk` |
| No-op (base src + hidden tests): onnx-tests | 597 pass, 4 hidden tests panic in the generated model at `from_file` (missing tensors) |
| No-op: official | all 11 promoted rows FAIL; 22 of 24 differential rows FAIL (the 2 passing are initializer-weight layout-0 regressions); drift checks pass |
| No-op: reject probes | mixed group accepted (rc=0, code emitted) -> discriminating; sequence_lens/peepholes already rejected at base |

Measured build: `cargo fetch` 24 s, 1.2 GB (registry 1.0 GB + git 168 MB). Full
`cargo test --no-run -p onnx-ir -p onnx-tests -p onnx-official-tests`: 72 s wall / 236 s CPU
(6 jobs), target 2.9 GB (3.8 GB after the gold rebuild). Incremental rebuild after the gold
patch: 25 s wall / 55 s CPU. onnx-official-tests full run 22-41 s; onnx-tests 1.4 s.

Estimates for Docker on 4 vCPU / 8 GB: agent image ~6.5-7.5 GB, build 15-25 min (fetch ~2,
warm build 8-15 incl. burn-onnx tests and the `ort` binary download, cargo-insta 2-3, pip 1);
verifier image ~6-7 GB, build 15-20 min; verifier run 5-10 min (incremental rebuild 3-8 min,
suites ~2 min). `build_timeout_sec = 3600` has margin; `storage_mb = 30720` has margin.

## Risks and knobs

1. **`ort` dev-dependency of burn-onnx** (onnxruntime Rust bindings, `download-binaries` on):
   its build script downloads ONNX Runtime binaries during the agent image's warm
   `cargo test -p burn-onnx --no-run`. Needed so the agent can run burn-onnx's inline snapshot
   tests offline, but it puts an onnxruntime shared library into the agent image (as a build
   artifact; not the Python reference). Disclosed in `instruction.md`; anti-cheat rejects any
   `ort` use in the submitted sources, and the test crates cannot link it. If the download
   fails in Docker (glibc 2.36 on bookworm vs the rc.13 binaries built for 2.38+ only matters at
   link/run time of the export tests, which are feature-gated), drop `-p burn-onnx` from the
   warm build and document that burn-onnx unit tests cannot run offline.
2. **Drift-gate flake** (`verify_fail_compare_still_fails`, 1 failure in 10 local runs, cause
   unidentified): mitigated by the bounded retry. If it recurs in Docker, identify the row and
   mark it `flaky` in the verifier's expectations.toml (upstream-sanctioned status).
3. **Memory of the onnx-official-tests build**: one test crate with ~950 generated models;
   compiled fine locally, not yet measured under an 8 GB limit. If rustc OOMs, raise
   `memory_mb` or split the differential rows into a second small crate.
4. Agent-image Python has `onnx.reference`, the project's documented ground truth, whose reverse
   / bidirectional gaps are called out in the instruction (they are real-world pitfalls, not
   verifier tricks). Removing the venv is a knob to make the task harder.
5. `burn-onnx` and `onnx-ir` inline tests (the PR's snapshot updates) travel with the gold patch
   and are not used as oracle; the verifier does not run in-source tests at all.

## Next steps

Docker build of both images on a host with >= 30 GB free; Harbor oracle (`solve.sh`) and
no-op runs; record `build` and `validation` in `provenance.json`; one Sonnet rollout gate.

## Validation log (2026-09-13)
- Harbor oracle run 1 (arm64 host): agent image build failed compiling `gemm-f16` (inline asm requires `fullfp16`). Added per-target rustflags `+fp16` for `aarch64-unknown-linux-gnu` in CARGO_HOME config (no effect on amd64). Rerun queued.

## Validation log addendum (2026-09-13)
- Build parallelism bounded with `CARGO_BUILD_JOBS=4` in both Dockerfiles after rs-burn-store OOM-killed the linker (`ld terminated with signal 9`) when cargo used all 18 host cores inside an 8 GB Docker VM.
- Harbor oracle run 2: `openssl-sys` build script failed (no OpenSSL headers / pkg-config in the slim image; pulled in by the `ort` dev-dependency tree). Added `libssl-dev pkg-config` to both Dockerfiles' apt install. Rerun queued.
- Harbor oracle run 3: everything passed (onnx_ir 553, onnx_tests 601 incl. 4 hidden PR tests, differential 24/24, rejects, drift gates) except the exact count for the `official` corpus: 860 passed on Linux vs the 858 measured on macOS (two platform-conditional cases). Expected count set to 860 (Linux, measured aarch64; confirm on amd64). Rerun queued.

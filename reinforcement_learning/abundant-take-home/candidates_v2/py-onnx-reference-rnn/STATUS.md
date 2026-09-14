# py-onnx-reference-rnn: status

Stage: **drafted** (no Docker build, no Harbor run yet). Authored 2026-09-13.

## What the task is

Form B excision on `onnx/onnx` main `c9f169adac34bd690bf0d628e9aae7fde3d4be85` (2026-09-12, VERSION_NUMBER 1.24.0, default-domain opset 29). The pure-Python reference evaluator loses its `RNN`, `GRU` and `LSTM` implementations (`onnx/reference/ops/op_rnn.py`, `op_lstm.py`, `op_gru.py`, 601 lines) and their registration in `_op_list.py` (7 lines). The agent must implement all three operators (all directions, both layouts, every optional input, output subsets, float16/32/64, opsets 7/14/22) from the ONNX schema. Restore patch: `solution/changes.patch`, 4 files, +608/-0, `git apply --check`ed against the excised tree and against the extracted `upstream.tar.gz`.

Also removed from the agent tree because they are in-tree copies of the same math (verifier restores pristine copies): `onnx/backend/test/case/node/{rnn,lstm,gru}.py` (node-test generators carrying `RNNHelper`/`GRUHelper`/`LSTMHelper`), `tests/python/reference_evaluator_test.py::test_lstm_y_c` (12 parametrizations), the `#### Examples` blocks for the three ops in `docs/Operators.md` and their sections in `docs/TestCoverage.md` (summary count adjusted, ops listed under "No Cover"). Excision total: 10 files, -3269/+10. The operator specification itself (`onnx/defs/rnn/defs.cc`, `old.cc`, `docs/Operators.md` spec text, `docs/Changelog.md`) stays. The excised tree is committed on local branch `excised-rnn` (`33d858e99ca711c9beb02171dc0fd73caa5ed655`) in `research/cache/v2/onnx__onnx`.

Alternates considered and not taken: Sequence ops (`SequenceAt/Construct/Empty/Erase/Insert/Length`, `SplitToSequence`, `ConcatFromSequence`) are tiny list manipulations with fewer interacting attributes; `Resize` + `GridSample` have the most attributes but their reference code is ~800 lines of interpolation modes where onnxruntime and the reference already disagree on several `coordinate_transformation_mode`/`antialias` corners, which would force a hand-curated differential. RNN was the assignment's preference and has a clean independent oracle.

## Oracle design

Verifier (`tests/test.sh`, offline, separate image) rebuilds a clean room: snapshot the uploaded `/workspace/repo/onnx/reference`, reset it to the excised baseline kept at `/opt/excised/reference`, overlay the submission, restore the pristine generators and test files from `tests/upstream/`. Groups (all exact counts, `reward = 1` iff all pass; per-group detail in `/logs/verifier/score.json`):

| group | count | source |
|---|---|---|
| `node_tests` | 18 | official ONNX node tests for the family, generated in memory from the pristine generators, upstream tolerances (rtol 1e-3, atol 1e-7) |
| `ort_node_models` | 18 | same models, reference vs onnxruntime 1.30.0 CPU (rtol 1e-4, atol 1e-5) |
| `ort_generated` | 164 | generated float32 models: direction x layout x B x initial_h core matrix, output-subset sweep, LSTM initial_c x P, GRU linear_before_reset, RNN Affine with per-direction alpha/beta, explicit default activations, sequence_lens = seq_length, opsets 7 and 14 |
| `dtype_float16` | 18 | float16 vs onnxruntime float16 kernels (rtol 2e-2, atol 2e-3) |
| `dtype_float64` | 18 | float64 output dtype required; values vs onnxruntime float32 (rtol 1e-4, atol 1e-5) |
| `error_cases` | 6 | unknown `direction`; `W.shape[0]` inconsistent with `direction`; must raise RuntimeError/ValueError, `NotImplementedError` subclasses rejected |
| `upstream_reference_evaluator_test` | 463 collected / 459 passed / 4 skipped | pristine `tests/python/reference_evaluator_test.py` (skips: torch/torchvision absent); `test_conv` deselected exactly because upstream drives onnxruntime with a Conv bias shape that 1.30.0 rejects |
| `upstream_backend_reference_test_cpu` | 2052 collected / 2021 passed / 31 skipped | pristine `tests/python/backend_reference_test.py -k cpu` (the `_cuda` twins are device-skipped upstream; the 31 skips are upstream exclude patterns) |
| `anti_cheat`, `build_status`, `no_reference_backend_leak` | | static grep/file checks; import smoke test; harness phase 1 runs the submission with `onnxruntime`/`torch`/`jax`/`tensorflow` imports blocked via a meta-path finder |

onnxruntime CPU cannot run `layout=1` ("Batchwise recurrent operations (layout == 1) are not supported") nor float64 for these ops; the harness emulates layout 1 by the spec's transposition of inputs/outputs and compares float64 against float32 execution. Expected node outputs are never written to disk in either image.

## Build decision

**From-source build is necessary; wheel overlay is infeasible.** The newest wheel is onnx 1.22.0 (opset ~23, 12 of the 18 family node cases); repo main is 1.24.0 with opsets to 29 and protobuf changes: overlaying the repo's Python onto the wheel's compiled layer fails at import (`onnx/_mapping.py` references `TensorProto.FLOAT6E2M3`, absent from the wheel's `onnx_ml_pb2`), before even reaching version-skew issues in `onnx.defs`. Both Dockerfiles therefore build onnx from the excised tarball with `pip install -e . -v --no-build-isolation --no-deps` (scikit-build-core 1.0.3, pip cmake 4.4.3 and ninja 1.13.2, Debian bookworm `libprotobuf-dev`/`protobuf-compiler` 3.21.12 with `-DONNX_USE_PROTOBUF_SHARED_LIBS=ON`, `CMAKE_BUILD_PARALLEL_LEVEL=4`), mirroring upstream Linux CI. Editable redirect mode: Python edits under `/workspace/repo` are live; compiled `onnx_cpp2py_export` and generated `*_pb2` modules live in site-packages.

Measured locally (macOS arm64, 8 parallel jobs, with a from-source protobuf/absl the Docker recipe avoids): 129 s wall, 605 objects of which 62 are onnx's own. **Estimate for Docker on 4 CPUs: 4 to 10 minutes per image** (two images). `build_timeout_sec = 3600` leaves margin. Base image `python:3.12.11-slim-bookworm` pinned by digest (same digest as v1). Pins: numpy 2.5.3, protobuf 6.31.1, ml_dtypes 0.6.0, typing_extensions 4.16.0, pillow 12.3.0, pytest 9.1.1, pytest-xdist 3.8.0, parameterized 0.9.0; verifier adds onnxruntime 1.30.0 (+flatbuffers 25.12.19).

## Local validation (not Docker)

Editable build of the base commit in a scratch worktree, Python 3.12.13, numpy 2.5.3, onnxruntime 1.30.0, `tests/test.sh` run with path overrides:

- gold (`solution/changes.patch` applied, i.e. pristine `onnx/reference`): every group passes, **reward 1**; harness 4 s, reference_evaluator_test 2 s, backend_reference_test cpu 12 s.
- no-op (excised `onnx/reference` submitted): `node_tests` 0/18, `ort_*` 0/200, upstream suites 12 and 18 failures, **reward 0**.
- mutation checks on the gold code: swapping LSTM `o`/`f` gate order → 99/164 generated pass (node tests still 18/18); GRU ignoring `linear_before_reset` → 154/164 (node tests 18/18); RNN reverse without re-flipping `Y` → 156/164 (node tests 18/18, they only check `Y_h` for reverse). The onnxruntime differential catches what the 18 official cases do not.

Expected counts in `tests/harness/expected_counts.json` were measured on macOS; the platform-conditional excludes in `backend_reference_test.py` (32-bit, Windows, numpy<2, pillow<10) do not apply to the pinned Linux image, so the counts should transfer, but they must be re-confirmed on the first Docker run.

## Risks

1. **Docker build of onnx main against Debian's protobuf 3.21.12** with python protobuf 6.31.1 at runtime: upstream Linux CI does exactly this today, but any drift (a `.proto` feature needing a newer protoc, absl linkage, memory pressure at `-j4` in 8 GB) breaks both images. Fallback: `-DONNX_BUILD_CUSTOM_PROTOBUF=ON` (adds ~10 min) or a lower parallel level.
2. **Contamination**: the excised code is public and the base is main; a model that reproduces upstream `op_lstm.py` from memory solves the task. Recorded as C_public. The generated differential still requires a complete implementation (the mutation checks show partial recall fails).
3. **Exact upstream counts** (463/459/4 and 2052/2021/31) are pinned to this dependency set; onnxruntime 1.30.0 rejects upstream `test_conv`, which is deselected by exact node id. Any pin change requires re-measuring `expected_counts.json`.

Lesser: `sequence_lens` shorter than `seq_length`, `clip`, `input_forget` and non-default GRU/LSTM activations are declared out of scope because the upstream reference ignores them (`noqa: ARG002`); a future Form-C variant could author them against onnxruntime. Harbor uploads artifacts to their source path in the verifier; `test.sh` also falls back to searching `/logs/artifacts` for an `onnx_reference` directory.

## Next steps

1. `docker build` both images on a host with more than 8 GB; record build minutes and image sizes in `provenance.json`.
2. Harbor oracle (`solution/solve.sh`) and no-op runs; confirm the exact counts on Linux; fill `validation`.
3. Rollout gate: one Sonnet-5 attempt, reject if solved in under 40 steps.

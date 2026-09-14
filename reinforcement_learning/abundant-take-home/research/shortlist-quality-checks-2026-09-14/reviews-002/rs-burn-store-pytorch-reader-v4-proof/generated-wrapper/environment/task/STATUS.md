# rs-burn-store-pytorch-reader — status

**Stage: drafted** (no Docker build, no Harbor run yet).

## Choice

tracel-ai/burn PR **#5593** "fix(store): harden and restructure the PyTorch reader" (squash-merged 2026-09-10 as `33df298f`; base = its single parent `9cc7f63a`). Picked over #5349 (burnpack streaming writer) and #5064 (burn-pack extraction) because it is a reader for a foreign durable format with an independent reference implementation (PyTorch) that can generate an unbounded fixture matrix; the gold is +2867/−2718 across 8 source files in one crate, closes two filed bugs (#5412 zero substitution, #5506 broadcast-view memory exhaustion), and ships 16 new tests plus regenerated fixtures. Alternates recorded:

- #5349 (Aug 20, +1980/25 files): writer-side streaming; oracle would be burn's own reader reading burn's own writer, weaker differential. Touches 5 crates (burn-optim, burn-std, burn-core) so artifact transfer gets wide.
- #5064 (Jun 16, +2636/−6108, 42 files): mostly a crate move; large mechanical diff, low requirement density.

## Layout

```
environment/Dockerfile      rust:1.95.0-slim-bookworm@sha256:d7482085…  (MSRV; multi-arch index digest)
environment/upstream.tar.gz git archive of 9cc7f63a, 9.8 MB gz / 24.9 MB tar (no .git), sha256 7bb9b940…
solution/changes.patch      full PR diff incl. tests + binary fixtures (293 KB), sha256 1d46c8dd…; applies with `git apply --binary` outside a repo
solution/solve.sh           git apply + cargo build --offline -p burn-store
tests/Dockerfile            same first layers + /opt/pristine + torch 2.13.0+cpu venv + generated /opt/fixtures + harness precompiled
tests/test.sh               clean-room overlay, exact counts, matrix, 32 reject processes, score.json
tests/score.py              groups.env -> score.json + reward.txt
tests/hidden/               crates/burn-store/src/pytorch/tests at the merge commit (50 files; 71 reader + 37 store tests)
tests/harness/fixture_matrix.rs  integration test compiled against the submission (public API only)
tests/fixtures/gen_fixtures.py   torch-driven generator (28 accept / 32 reject files, 428 tensors, manifest.tsv)
```

`[[artifacts]]` = `/workspace/repo/crates/burn-store/src` only. Harbor re-materializes it at the same path in the verifier (`artifact_handler._upload_target_source` returns `artifact.source`), so the pristine tree lives at `/opt/pristine` and `test.sh` restores `src/pytorch/tests` and `src/safetensors/tests` from there / from `/tests/hidden`. `burn-pack/src` is not touched by the PR (`MAX_TENSOR_SIZE` already exists at base) and is not transferred.

## Oracle groups (all must pass; exact counts)

| group | source | count |
|---|---|---|
| pytorch_unit | hidden `src/pytorch/tests` (reader 71 incl. 16 new, store 37) | 108 |
| safetensors_unit | pristine `src/safetensors/tests` | 52 |
| integration | pristine `crates/burn-store/tests/*.rs` | 20 |
| pytorch_tests_crate | `crates/burn-store/pytorch-tests` (real nn.Module .pt via PytorchStore) | 37 |
| fixture_matrix | torch 2.13-written files: 30 entries rows + 28 meta + 4 pickle_int | 62 checks / 428 tensors |
| reject_cases | 32 malformed/unsupported files, one process each, `ulimit -v 6 GiB`, 180 s | 32 |
| anticheat | grep for /logs, /tests/, /opt/, manifest.tsv, BURN_PT_, fixture_matrix, Command::new, include_bytes!, include_str!, cfg!(test) | 0 hits |

## Local checks done (this machine, no cargo available)

- `git apply --check --binary` of `changes.patch` on a fresh extraction of `upstream.tar.gz`: OK; applied tree has `storage.rs`, no `lazy_data.rs`; `tests/hidden/reader/mod.rs` is byte-identical to the gold tree's.
- `gen_fixtures.py` executed with torch 2.13.0 (uv venv, macOS arm64 wheel; same torch version as pinned): 28 accept, 32 reject, 428 tensor rows, 19.6 MB; every torch-written accept file `torch.load`s back. `pickletools` confirms torch 2.13 emits `_rebuild_tensor_v3` + `torch.storage.UntypedStorage` for uint16/32/64, `_rebuild_from_type_v2` for the subclass, `_rebuild_parameter`, `_rebuild_qtensor`, `_rebuild_sparse_tensor`, and writes `.format_version`, `.storage_alignment`, `byteorder`, `.data/serialization_id`.
- Anti-cheat grep on the gold sources: no hits. `mod tests` wiring present in gold `pytorch/mod.rs` (`pub mod tests;`) and `safetensors/mod.rs`.
- `task.toml` validated against harbor 0.15.0 `TaskConfig`; `test.sh` passes `bash -n`; `score.py` compiles.

## Not yet done

- Docker builds of both images; measure minutes/GB (estimates: agent 15–25 min / 5–7 GB; verifier +5 min / +1.3 GB; verifier run 6–12 min).
- `fixture_matrix.rs` has **not been compiled** (no cargo locally). It uses only `PytorchReader::{new,with_top_level_key,keys,get,len,metadata,read_pickle_data}`, `bridge::to_data`, `TensorData::{dtype,shape.to_vec(),num_elements(),as_bytes()}`, `DType` variants, `PickleValue::Int`. The verifier Dockerfile compiles it at image build (`cargo test --no-run --test fixture_matrix`), so a compile error surfaces there, not at grading.
- Oracle run (`solve.sh` then verifier) and no-op run (base tree: expected to fail the 16 new reader tests, most reject cases, and matrix rows for protocol 4/5, v3 dtypes, TAR, archive-root metadata).
- Confirm the expected-count constants in `test.sh` against the real libtest output (108/52/20/37) once built; confirm `--test-threads=2` is stable for the store tests (they write temp files).

## Risks

1. Harness compile risk (unverified locally); mitigated by compiling at verifier image build.
2. Build time/memory: burn-core + gemm family on 4 vCPU / 8 GB; if rustc OOMs, set `CARGO_BUILD_JOBS=3` in both Dockerfiles.
3. Instruction over-specification: the error-message fragments the upstream tests assert on (`Big-endian`, `Unrecognized byteorder`, `does not hold a dictionary`, `extends beyond the end of the file`, `only N are available`, `holds N elements but the pickle declares M`, `little_endian bool`, `Invalid checksum`, `Pickle`) are stated verbatim in instruction.md; a solution with different wording fails those tests by convention rather than behavior. The remaining 16-test surface is behavioral.
4. `--test-threads=2` in the unit groups: upstream runs the default; if any hidden test is order-sensitive this could flake — switch to `--test-threads=1` if seen.

## Validation log (2026-09-13)
- Pre-emptive portability fix (same dependency tree as rs-burn-onnx): per-target rustflags `+fp16` for aarch64 Linux so `gemm-f16` compiles; amd64 unaffected.

## Validation log addendum (2026-09-13)
- Build parallelism bounded with `CARGO_BUILD_JOBS=4` in both Dockerfiles after rs-burn-store OOM-killed the linker (`ld terminated with signal 9`) when cargo used all 18 host cores inside an 8 GB Docker VM.

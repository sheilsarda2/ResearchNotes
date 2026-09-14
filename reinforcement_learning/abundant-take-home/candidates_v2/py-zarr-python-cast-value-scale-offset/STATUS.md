# STATUS: py-zarr-python-cast-value-scale-offset

Form A (mined feature PR), Python. Drafted 2026-09-13. Nothing has been built in Docker or run
through Harbor yet; everything below marked *unverified* is deferred to the orchestration phase.

## What is done

| item | state |
|---|---|
| PR selection | zarr-developers/zarr-python **#3874** "feat: add cast_value and scale_offset codecs" (d-v-b, merged 2026-04-30, released in zarr 3.2.0). Base commit = first parent of merge `23e96350…` = **`279d400a19d510df6384bb1e93c4817dc867072b`** (2026-04-29). |
| `environment/upstream.tar.gz` | `git archive` of the base commit, 640 KB, sha256 `b416260c…` (recorded in provenance.json). `.git_archival.txt` is export-substituted (`v3.1.6-49-g279d400`) so hatch-vcs derives a version without a pretend variable. |
| `environment/requirements.txt` | `uv pip compile` of the base `pyproject.toml` (`--group test --extra remote --extra optional --extra cli`) plus `cast-value-rs==0.4.0`, `hatchling`, `hatch-vcs`, `editables`, resolved for `x86_64-manylinux_2_28`, Python 3.12, `--exclude-newer 2026-05-01`. 68 pins (numpy 2.4.4, numcodecs 0.16.5, pytest 9.0.3, hypothesis 6.152.4). |
| `environment/Dockerfile` | `python:3.12.11-slim-bookworm@sha256:519591d6…` (same digest as v1, confirmed live on Docker Hub); installs pins, editable-installs the repo without build isolation, git baseline commit. |
| `solution/changes.patch` + `solve.sh` | PR diff restricted to non-test files: `changes/3874.feature.md`, `pyproject.toml`, `src/zarr/codecs/__init__.py`, `src/zarr/codecs/cast_value.py`, `src/zarr/codecs/scale_offset.py` — 5 files, **+810 / −0**. `git apply --check` passes on a fresh extraction of the archive. |
| `tests/oracle/` | `zarrs_oracle` Rust crate (`check` / `retrieve` / `store` / `batch` on a filesystem store) depending on **zarrs git rev `b111080abe9c8fbeaa10fd4ae7bf075344cf8ced`** (2026-09-12; `cast_value` landed in zarrs #409 on 2026-07-18 and is *not* in the 0.23.14 crates.io release). `Cargo.lock` committed; builds with `--locked`. Compiled locally with rustup 1.92.0 in 26 s (macOS arm64, `default-features = false, features = ["filesystem"]`). |
| `tests/differential/` | `cast_value_matrix.py` (zarr-python vs zarrs, both directions, byte-identical), `foreign_metadata.py` (hand-written / zarrs-written arrays, invalid documents), `scale_offset_reference.py` (authored exact-arithmetic reference), `common.py`. |
| `tests/hidden/` | PR test files with two derivability edits (see below). 63 tests. |
| `tests/test.sh` | Clean-room: locate `/logs/artifacts/submission/src/zarr`, anti-cheat (symlinks; pattern counts vs pristine for `_pytest atexit /logs reward score.json zarrs_oracle sitecustomize usercustomize conftest subprocess pytest_ monkeypatch os.system( os.exec`), overlay, restore `src/zarr/testing` + `tests/` + `pyproject.toml` from `/pristine`, six groups with exact counts, `reward.txt` + `score.json`. |
| `tests/Dockerfile` | Two-stage: `rust:1.92-slim-bookworm@sha256:f1f73538…` builds the oracle; final stage = agent base + pins + pristine tree + `/pristine` copy + `/usr/local/bin/zarrs_oracle` + `/tests`. |
| `instruction.md` | 2.9k words, requirement IDs `G-1..4`, `CV-1..11`, `SO-1..7`; all error types and message fragments the tests match are stated. |
| `provenance.json` | Full schema incl. 51-row derivability table covering all 63 hidden tests and every differential group; contamination tier `C_public`. |
| `task.toml` | Validates against Harbor 0.15.0 `TaskConfig` (checked with the installed harbor package). |

## Local validation (macOS arm64, Python 3.12, no Docker)

`tests/test.sh` was dry-run on a simulated container layout (env overrides `REPO PRISTINE TESTS
LOGS ORACLE PYTHON WORK`, `PYTHONPATH` pointing at the overlaid tree):

| submission | reward | groups |
|---|---|---|
| gold (`src/zarr` from the patched tree) | **1** | import ok; pr_tests 63/63; upstream 505/505; cast_value_matrix 3790 compared / 0 mismatches (4270 generated, 480 excluded, 607 agree-on-error); foreign_metadata 12/12; scale_offset_reference 100/100 |
| no-op (pristine `src/zarr`) | **0** | import fails (`CastValue` missing); upstream still 505/505 (attribution: nothing broken, nothing implemented) |

Wall time of the whole verifier locally: ~1 min (matrix ~12 s).

## Unverified (build-related)

* Docker image builds (both), image sizes, build minutes. Estimate: agent ~4 min, verifier ~9 min
  (cargo fetch + compile of ~140 crates on 4 vCPU; blosc/zstd/etc. are off).
* `cargo build --locked` inside `rust:1.92-slim-bookworm` (needs `git` for the git dependency —
  installed in the stage; the registry index is fetched during build, network is available then).
* Harbor artifact path inside the separate verifier: `test.sh` expects
  `/logs/artifacts/submission/src/zarr` (from `[[artifacts]] destination`), with fallbacks
  `/logs/artifacts/workspace/repo/src/zarr` and `/submission/src/zarr`; it lists `/logs/artifacts`
  in `verifier.log` if none matches. Confirm on the first Harbor run.
* That the editable install in the verifier image resolves `zarr` to `/workspace/repo/src`
  after `test.sh` replaces that directory (it should; hatchling editable uses a path hook).
* `pip install --no-build-isolation -e .` with `hatchling 1.29.0` + `editables 0.6` from the pins
  (worked locally via uv; pip path untested).

## Build / validation commands for the next phase

```bash
cd candidates_v2/py-zarr-python-cast-value-scale-offset
docker build -t zarr-cvso-env ./environment
docker build -t zarr-cvso-verifier ./tests            # ~9 min; builds the zarrs oracle
docker run --rm zarr-cvso-verifier zarrs_oracle       # prints usage; exit 1

# Harbor oracle and no-op (host with >= 8 GB free for Docker; record host in provenance.build)
harbor run -p candidates_v2/py-zarr-python-cast-value-scale-offset -a oracle -y
harbor run -p candidates_v2/py-zarr-python-cast-value-scale-offset -a nop -y
# then: reward.txt 1 / 0, score.json groups exact (63 / 505 / 3790 / 12 / 100),
# update provenance.json build + validation, funnel stage -> built / oracle_pass / nop_pass
```

## Findings worth knowing (upstream deviations from the zarr-extensions spec)

1. **`wrap` on floating-point arrays.** The merged `CastValue.validate` checks the *array* dtype
   where the spec restricts `wrap` by the *target* dtype; the PR's own test
   (`wrap-float-target`) asserts the array-dtype rejection. zarrs accepts float→int wrap, so 456
   matrix cases (all "float source + wrap") are excluded and the instruction states the
   zarr-python behaviour (CV-6). Everything else agreed byte-for-byte with zarrs on the first run,
   including directed rounding for float→float16 and clamp semantics.
2. **int64 extreme underflow.** `ScaleOffset` widens to int64 for overflow checking, which is a
   no-op for int64 arrays: `offset=1` on `-2**63` wraps silently instead of raising. The authored
   reference does not include that case; noted in the harness.
3. Gold's `PERMITTED_DATA_TYPE_NAMES` literal has two missing commas (`"int64uint2"`,
   `"uint64float4_e2m1fn"`), so `uint2` / `float4_e2m1fn` targets would be rejected; harmless at
   this commit because those dtypes do not exist yet in zarr-python.
4. zarrs' own interop test skips float→uint64 `wrap` with `towards-zero`/`towards-negative`
   (zarr-python rounds the decoded value near 2**64 to nearest); subsumed by exclusion 1.

## Derivability edits to hidden tests

* `test_decode_fits_natively_negative_scale` removed (asserts an internal fast-path predicate).
* `test_compute_encoded_size` (scale_offset) calls `compute_encoded_size(100, None)` positionally
  (the reference's `_chunk_spec` parameter name is incidental).

## Risks

1. **Contamination.** Merged 2026-04-30, public in zarr 3.2.0 and in zarrs' interop script
   (`zarr_python_cast_value_matrix.py`). Tier C. The hidden tests are the PR's; a model that
   recalls the merged files can pass. Mitigation is the differential matrix (new) and the
   authored reference; headroom claims should rely on the rollout gate.
2. **Oracle drift.** The oracle pins a zarrs git commit that is `0.24.0-dev`; if the commit
   disappears (force-push) the verifier build fails. Consider vendoring the crate source or
   a prebuilt `zarrs_oracle` binary into `tests/` once the Docker build has been done once.
3. **Verifier build weight.** ~140 crates compiled at verifier-image build; within the 3600 s
   budget on 4 vCPU but the heaviest part of the task. `--locked` protects reproducibility.
4. **Harness determinism.** The matrix writes ~8.5k small arrays under `/tmp/verifier-work`;
   disk and inode use is small (< 50 MB). Byte comparisons are exact; NaN payloads are compared
   through the same IEEE ops on both sides.
5. **`filterwarnings = error`** in the project's pytest config: any DeprecationWarning from an
   agent's implementation fails a hidden test. Stated in the instruction (G-3).

## Alternates considered

* **#3925 get_ranges** (1252 additions, store API): no cross-implementation oracle; would rely on
  the PR's tests plus fsspec/obstore behaviour. Kept as fallback.
* **#3679 memory store registry**: mostly API plumbing; oracle would be authored only.
* **#3781 structured dtypes**: smaller (441 additions), differential possible via zarrs
  structured dtype support but interface churn in `zarr.core.dtype` since then.
* **#3802 rectilinear chunks**: 6k additions across 38 files, experimental flag, no zarrs
  counterpart for the exact metadata; too large for a single verifier and too unstable.

## Validation log (2026-09-13)
- Harbor oracle run 1: reward 0 — verifier looked for the submission under `/logs/artifacts`; Harbor re-materializes `[[artifacts]]` at the source path. Fixed lookup to check `$REPO/src/zarr` first.
- Harbor oracle run 2: reward 0 — overlay step deleted the in-place submission before copying it. Fixed by moving an in-place submission to `$WORK/submission` before the overlay. Rerun queued.

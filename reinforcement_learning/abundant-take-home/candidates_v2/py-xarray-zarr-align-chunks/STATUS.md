# py-xarray-zarr-align-chunks: status

Form A, Python. pydata/xarray PR #10336 "Automatic Dask-Zarr chunk alignment" (merged 2025-06-05, closes #9914) composed with its follow-up fix PR #10516 (merged 2025-09-08, closes #10501). Base commit `d21d79e91646c883a2daff8faeb7f948080e73ed` (2025-06-04), the only parent of the #10336 squash merge.

## Stage: drafted (authoring phase, no Docker builds or Harbor runs yet)

| Item | State |
|---|---|
| `environment/upstream.tar.gz` | git archive of base, 438 entries, sha256 `d5f5ccf8…6572`; verified free of `xarray/backends/chunks.py` and `test_backends_chunks.py` |
| `environment/Dockerfile` | `python:3.12.11-slim-bookworm@sha256:519591d6…`; 67 hash-pinned wheels resolved with `uv pip compile --exclude-newer 2025-06-05` (numpy 2.2.6, pandas 2.2.3, zarr 3.0.8, dask/distributed 2025.5.1, netCDF4 1.7.2, h5netcdf 1.6.1, scipy 1.15.3, pytest 8.4.0, pytest-xdist 3.7.0, mypy 1.15.0 + pytest-mypy-plugins 3.2.0 so the repo `addopts` work); editable install; git baseline commit |
| `solution/changes.patch` | 6 files, +381/-89 (non-test, non-doc: 5 files, +372/-89); `git apply --check` clean against a fresh extraction of the archive |
| `tests/` | separate offline verifier: `Dockerfile` (same base, pristine copy at `/opt/pristine/repo`), `test.sh` -> `verify.py`, `hidden/` (upstream `test_backends_chunks.py`, `test_backends_align.patch`, authored `test_zarr_align_crosscheck.py`, `deselect_upstream.txt`, `expected.json`) |
| `instruction.md` | 1.5k words; interface, alignment contract with worked examples, behaviour, error contract, scope |
| `provenance.json` | 22 derivability entries mapping every hidden assertion group to instruction sentences |
| `task.toml` | validated with Harbor 0.15.0 `TaskConfig` (agent 21600 s, verifier 3600 s, build 3600 s, 4 cpus, 8192 MB, 30720 MB, verifier `no-network`, artifact `/workspace/repo/xarray` excluding `tests`) |

## Local validation (macOS arm64, Python 3.12.13, same pins via the macOS resolution of `requirements.in`)

`tests/verify.py` was run against three simulated uploads (pristine tree + `xarray/` package without `tests/`, exactly what Harbor re-materialises at `/workspace/repo/xarray`):

| Run | pr_tests_chunks | pr_tests_backends | crosscheck | upstream_regression | anti-cheat | reward | wall |
|---|---|---|---|---|---|---|---|
| gold (composite patch) | 18/18 | 16/16 | 51/51 | 950/950 | ok | **1** | 51 s |
| no-op (pristine) | collection error (module missing) | 0/16 | 1/51 | 934/950 | ok | **0** | 38 s |
| tamper (stray `xarray/conftest.py`, `numpy.testing` monkeypatch in `__init__`) | not run | | | | **failed** (conftest + 3 pattern deltas) | **0** | 2 s |

The one cross-check case that passes on the no-op is the negative control (`test_unsafe_unaligned_write_is_left_alone`), which asserts pre-existing behaviour.

Exact counts come from these runs. The verifier deselects 120 upstream node ids that skip or xfail under the pinned wheels (zarr-v2-only tests, `requires netcdf`, the always-skipped `test_open_mfdataset_manyfiles`, ZipStore reopen cases) so the contract is "collected == passed" with zero skips. Counts must be re-measured inside the Linux verifier image in the build phase; a platform-dependent skip would show up as a count mismatch, not a false pass.

## Oracle design and independence

- `crosscheck` (51, **independent of the PR tests**): every store written through the feature is re-read with zarr-python directly (never `xr.open_zarr`); values are compared to NumPy eager expectations; `chunks`, `shape`, `dtype` and dimension metadata are checked; a `MemoryStore` subclass counts `set()` per chunk key to prove each Zarr chunk is written by exactly one Dask task (with a negative control showing the counter detects overlap); region/append writes must leave data outside the region untouched; 150 seeded property cases check the alignment helpers (sum, boundary alignment, size bound, idempotence, values untouched). No case or expected value is shared with the PR tests.
- `upstream_regression` (950, independent of the PR tests): the zarr selection of the pristine `test_backends.py`.
- `pr_tests_chunks` (18) and `pr_tests_backends` (16): the PRs' own tests. Upstream's `test_dataarray_to_zarr_align_chunks_true` was unconditionally skipped under zarr-python 3 (it passed `tmp_store` as the skip reason); the hidden copy un-skips it, so 2 of its variants now run for the first time.
- Not included: a `zarrs` (Rust) reader. Building `zarrs_tools` needs a Rust toolchain and ~10 minutes of network fetches; zarr-python is the format's reference implementation and covers values, chunk layout and metadata.

## Why this PR (and what was rejected)

Nothing merged into pydata/xarray in 2026 is feature-scale on the zarr/encoding/indexing theme. Mined 2026-01-01..2026-09-13 (220 merged PRs, `candidates_v2/mining/pydata__xarray.json` plus a looser re-mine in scratch):

- **#11398** (2026-06-29, +1063/-350, 37 files) "map_blocks and testing support for array query expressions": the only 2026 PR at feature scale. Rejected: its core path depends on the external `dask_array` package's private `map_blocks_multi_output`; the author calls it incomplete; 28 of the 37 files are test edits that mostly add strict `xfail_with_dask_array` marks, so the oracle would fail an agent whose implementation works *better* than the reference. No linked issue.
- **#11318** (2026-08-12, +99/-14) remove `BooleanCoder` from zarr writes; **#11151** (2026-04-09, +109/-6) complex `FillValueCoder`; **#11067** (2026-07-31, +74/-47) harmonise h5netcdf/netCDF4 encoding; **#11345** (2026-05-20, +38/-4) zarr v3 fill_value round-trip. All on-theme, all far below feature scale (a composite would be three unrelated specs at conflicting bases).
- **#11232** (2026-07-29, +77/-352) "Support zarr>=3.0": dependency-floor removal. **#11456** (2026-07-31) docs migration touching `zarr.py` cosmetically.

2025 alternates kept in reserve, all C_public: **#10327** async `load_async` (2025-08-14, +803/-110, 26 files, 4 test files, zarr + indexing; its upstream tests are latency-timed, which CONVENTIONS forbid, and the hidden API surface is large); **#10624** netCDF in-memory IO cleanup (2025-09-16, +680/-351; half typing/refactor); **#10571** `DataTree.to_netcdf` to file-like/bytes (2025-08-08, +464/-162; netCDF4/h5py as independent readers). #10336+#10516 was chosen because it is the assignment's exact oracle shape (zarr-python reads what the feature writes), has a strong demand trail (#9914, #10501, #8882), and the composite is a coherent single feature.

## Build plan and estimates (not yet measured)

- Agent image: apt (bash, git) ~0.5 min; pip install of 67 hash-pinned wheels (numpy, pandas, scipy, dask, distributed, zarr, netCDF4, h5py, mypy...) ~2-4 min on a fast link; extract + editable install + compileall ~0.5 min. **Estimate 4-6 min, ~1.6 GB.**
- Verifier image: same layers plus a second extraction; **~1 min more if layers are shared, else 5-7 min**, ~1.7 GB.
- Verifier run: 51 s locally with 4 workers; **estimate 2-4 min** in the container. Well inside the 3600 s budget.

## Risks

1. Count drift on Linux: 120 deselected ids were measured on macOS. Any platform-conditional skip (e.g. netcdf-c version) changes `upstream_regression` from 950 and fails the gold. Mitigation: build phase re-measures and updates `expected.json`/`deselect_upstream.txt`; the harness fails closed.
2. Contamination is C_public: both PRs and their release notes are public since 2025. Mitigation in the oracle: 51 authored differential cases and an un-skipped upstream test that never ran publicly; a memorised gold still has to pass them (it does), but headroom claims should rely on the rollout gate.
3. Spec tightness vs. algorithm freedom: the PR's `test_grid_rechunk` asserts exact tuples, so `instruction.md` states the greedy left-to-right policy as a behavioural contract with all nine worked examples. An agent implementing a different but valid alignment (aligned, bounded, idempotent) would pass the 51 cross-checks but fail up to 9 exact-tuple cases. Accepted: the policy is part of the requested behaviour (memory bound and chunk preservation are user-visible), and it is fully derivable from the text.
4. `pytest-mypy-plugins`/`mypy` are in the pinned set only so the repo's `addopts` work; if the plugin misbehaves in the Linux image the verifier can pass `-o addopts="--strict-config --strict-markers"` instead (one-line change in `verify.py`).

## Next steps

Build both images on a host with >8 GB Docker memory, run `verify.py` for oracle and no-op inside the verifier container, re-measure counts, record image sizes and minutes in `provenance.json.build`, then advance the funnel stage.

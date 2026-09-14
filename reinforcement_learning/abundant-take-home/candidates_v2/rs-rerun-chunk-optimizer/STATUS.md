# rs-rerun-chunk-optimizer — STATUS

Stage: **drafted** (2026-09-13). No Docker build or Harbor run yet (phase rule); everything below
that says "measured" was measured on the authoring Mac with the pinned toolchain.

## What the task is

Form A, Rust. rerun-io/rerun store crates. The agent turns the one-week-old
`crates/store/re_chunk_optimizer` skeleton (chunk-index view + mergeability analysis) into the
memory-bounded, chunk-index-planned optimizer that upstream landed in two consecutive commits:

| commit | date | title | store-crate non-test lines |
|---|---|---|---|
| `bef4ed8d92` | 2026-09-02 | Basic end-to-end chunk-index-based optimizer | ~1,200 |
| `38a25c277e` | 2026-09-09 | Add support for `own_chunk` to chunk optimizer | ~1,370 |

Base = `c1ea47a3b8` (first parent of `bef4ed8d92`). The second commit cherry-picks cleanly onto the
first (no intermediate commit touched `re_chunk_optimizer` or `re_log_encoding`). Composed gold
patch: 16 files, +2,445/−109 (1,566 added lines outside inline `#[cfg(test)]` modules), across
`re_chunk_optimizer/src` (5 new modules, 2 rewritten), `re_log_encoding/src` (in-memory provider,
temporal-map parsing fix), `re_chunk_store/src` (`LazyStore: ChunkProvider`), plus three one-line
`use` fixes in `re_server`. rerun_py bindings from the upstream commits are dropped (not built).

Why this over the January preference list (#12390, #12312, #12277, #12557): the public repo's PR
flow stopped in February 2026 (development moved to the private `reality` repo and is synced back
as direct commits), so the mining JSON (PR-based) missed 442 store-crate commits since May. Mining
those by commit via the GitHub API surfaced this stack: newest feature-scale store work with real
tests (1,177 lines of upstream tests), merged 4–11 days before construction (tier A_recent), no
viewer or Python build needed. #12390 (+~250 real lines, mostly a return-type change) and #12312
(a ten-line `MutableArrayData` slice printed in its own PR body) were rejected as too small;
#12557 touches the viewer; #12277 needs video decoding.

## Environment plan

- Base image `rust:1.96.0-bookworm@sha256:5e2214ab…` (matches `rust-toolchain` channel 1.96.0);
  apt: build-essential, cmake, pkg-config, clang, libssl-dev, python3, rsync, git, jq. rustfmt,
  clippy and the wasm32 target are installed at build so rustup never touches the network.
- `environment/upstream.tar.gz` (59.7 MB gzip, 127 MB extracted): `git archive` of the base with
  the three crate manifests + `Cargo.lock` pre-seeded with the gold's dependency declarations (so
  `[[artifacts]]` can be `src/`-only and the agent never edits manifests), and with
  `tests/assets` (622 MB of .rrd/.mcap fixtures), `examples/python`, `examples/notebook`,
  `rerun_js`, `rerun_notebook` excluded. `cargo metadata --locked` still resolves (all 100+
  workspace members are present). `git apply --check` of the gold patch passes on the extracted
  archive.
- Build steps: `cargo fetch --locked --target x86_64-unknown-linux-gnu` (whole lockfile, needed
  because `cargo metadata`/`--offline` want every member's dependencies), then
  `cargo test -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store --no-run` and a
  `cargo check --tests` of the same crates. Runtime `CARGO_NET_OFFLINE=true`.
- Package selection matters: `re_log_encoding`'s own integration tests only compile when it is
  built together with `re_chunk_optimizer` (feature unification supplies `decoder`/`encoder`), and
  changing the selection recompiles all workspace crates. Image warm-up, agent hint and verifier all
  use the same three-package selection.

### Estimates (to be replaced by measured values after the first Docker build)

| | measured on Mac (many cores) | estimate at 4 vCPU / 8 GB |
|---|---|---|
| deps + workspace, `cargo test … --no-run` (2 crates) | 4m34s wall, 33.4 CPU-min | ~10–12 min |
| + `re_chunk_store` test targets | 34 s wall, 3.75 CPU-min | ~1.5 min |
| `cargo fetch` (full lockfile, ~1,500 crates) | — | ~2–4 min, ~1.2 GB in `$CARGO_HOME` |
| target dir after warm-up | 2.9 GB (opt-level 1/2 dev profile) | ~3 GB |
| image size | — | ~5–6 GB (base 1.6 GB + registry 1.2 GB + target 3 GB); storage_mb 30720 leaves room for agent rebuilds |
| image build total | — | ~15–20 min, inside `build_timeout_sec = 3600` |
| verifier rebuild (workspace crates only, warm deps) | 25–53 s wall, ~3–7 CPU-min | ~2–3 min build + ~15 s tests (analysis snapshot ~11 s), inside 3600 s |

Memory: the dev profile compiles dependencies at opt-level 2; datafusion v54 is in the closure
(`re_arrow_util` dev feature) and its heaviest crates peak around 1–1.5 GB each; with
`CARGO_BUILD_JOBS=4` the observed peak on the Mac stayed below 6 GB. If the first Docker build
OOMs, set `CARGO_BUILD_JOBS=2` for the warm-up layer only.

## Verifier

`tests/test.sh` (separate offline container, `[verifier.environment] network_mode = "no-network"`):
1. Snapshot the three transferred `src/` dirs, `rsync --delete` the pristine tree from
   `/opt/pristine` over `/workspace/repo` (keeping `target/`), put the sources back.
2. Install hidden tests: upstream `optimize.rs` (22), upstream `footers_and_manifests.rs` (10, one
   new), authored `optimizer_contract.rs` (15).
3. Anti-cheat: non-`.rs` files in `src/`, `std::process`, `include!`/`include_str!`, `env!`,
   `#[path]`, `/logs/`, `reward.txt`, `no_mangle`, `INSTA_`, custom test frameworks → reward 0.
4. One `cargo test --offline --locked -p re_chunk_optimizer -p re_log_encoding -p re_chunk_store
   --no-run --message-format=json-render-diagnostics` (2400 s timeout); executables are taken from
   the JSON and run directly (900 s each, `--test-threads=2`).
5. Exact counts per binary: optimize 22, optimizer_contract 15, footers_and_manifests 10,
   analysis 5, arrow_encode_roundtrip 1, compact 18, correctness 4, dataframe 3, drop_time_range 1,
   formatting 1, gc 5, reads 10, stats 1 — 0 failed, 0 ignored. `memory_test` is excluded
   (allocation-count based).
6. `reward.txt` 0/1, `score.json` with anticheat/build/per-binary/per-group attribution.

Oracle design: property-based (the PR's own tests + authored contract tests), not gold-output
comparison — merged chunks receive random `ChunkId`s and cut points legitimately depend on the
accumulation strategy, so an `rrd compare` against gold output would be unfair; the tests instead
pin data survival (cell sets), identity, ordering within runs, band/guard arithmetic, idempotence,
lazy loading and the error surface.

## Local validation (macOS arm64, rustc 1.96.0, 2026-09-13)

- Gold tree (`c1ea47a3b8` + both commits): optimize 22/22, footers_and_manifests 10/10,
  analysis 5/5, arrow_encode_roundtrip 1/1, re_chunk_store 43/43 (+ memory_test 1/1),
  authored optimizer_contract 15/15.
- No-op (seeded base + hidden tests): fails to compile (`E0432` unresolved `optimize`,
  `OptimizationSettings`, `InMemoryChunkProvider`; `E0277 LazyStore: ChunkProvider`;
  `E0599` missing `Error::LoadChunks`) → reward 0.
- `task.toml` validates against Harbor 0.15.0 `TaskConfig`.

## Risks

1. **Build budget/memory in Docker.** 33 CPU-min of dependencies at opt-level 2 on 4 vCPU plus a
   full `cargo fetch`; ~15–20 min expected but unmeasured. Mitigation: lower `CARGO_BUILD_JOBS`
   for the warm-up layer; the agent/verifier rebuild only recompiles workspace crates.
2. **Spec over-/under-specification.** The upstream tests pin observable shapes (e.g. `[3,3,2]`
   after a split, colors run before rest run, adjacent-only merging). `instruction.md` states every
   such rule (R2–R4); an implementation that follows them but uses a different accumulation
   strategy could still differ on `tiny_chunks_file` (must beat first-fit by measuring merged
   content) — R4.3 spells that requirement out.
3. **Contamination / difficulty calibration.** Code is 4–11 days old (tier A_recent) but the
   crate name and design are public; the rollout gate will tell whether Sonnet-5 solves it in
   <40 steps. Horizon hypothesis >150 steps (five new modules, 22 interacting requirements).
4. **`EntityPathFilter::all()` semantics** (R3.2 relies on the resolved filter not matching
   `/__properties`); pinned by an upstream plan test and by one authored test — if upstream ever
   changes this, the base is fixed so the task stays consistent.

## Alternates considered

- EARLIEST_AT stack 1/6 + 2/6 (`8559836c67` 2026-08-27, `f0c95defd4` 2026-08-31; +609 non-test,
  +1,144 test lines in re_chunk/re_chunk_store): excellent metamorphic oracle (mirror of
  `latest_at`) but four intermediate commits (incl. a 300-file rename) sit between the two, and the
  feature mirrors an existing one — kept as the alternate if this task is rejected at the rollout
  gate.
- `1e2790d55b` "Always put `IsKeyframe` into their own chunks" (2026-08-27, +814): touches viewer.
- `d1771d4042` "Make `Chunk.from_record_batch` more flexible" (2026-06-17, +1,448 store): mostly
  re_sorbet migration code, 3 test files.
- January preference list (#12390, #12312, #12557, #12277): rejected as above.

## Deliverables

```
environment/Dockerfile, environment/upstream.tar.gz (59.7 MB)
solution/changes.patch (16 files, +2445/-109), solution/solve.sh
tests/Dockerfile, tests/test.sh, tests/hidden/{optimize.rs,footers_and_manifests.rs,optimizer_contract.rs},
tests/image-source/upstream.tar.gz (copy for the verifier build context)
instruction.md, task.toml, provenance.json, STATUS.md; funnel.jsonl line appended (stage "drafted")
```

## Validation log (2026-09-13)
- Portability fix before first build: `cargo fetch --target x86_64-unknown-linux-gnu` replaced with the host triple so arm64 builds have their dependencies offline.

## Validation log addendum (2026-09-13)
- Build parallelism was already bounded via `CARGO_BUILD_JOBS` in both Dockerfiles; kept as is. (Context: rs-burn-store OOM-killed the linker when cargo used all 18 host cores inside an 8 GB Docker VM.)
- Harbor oracle run 1 (both images built in 927 s on 8 GB): reward 0 from the anti-cheat "only Rust files" rule, which flagged three pristine insta `.snap` files under `re_chunk_store/src/snapshots/`. Fix: snapshot dirs are now always restored from pristine after the overlay (they are test expectations) and `.snap` under `snapshots/` is exempt from the rule. Rerun queued.

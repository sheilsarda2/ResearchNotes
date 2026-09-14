# cpp-rerun-cpp-ffi-bridge: status

Stage: **nop_pass** (2026-09-13). Harbor 0.15.0 oracle reward 1 (`cpp-rerun-cpp-ffi-bridge-oracle-20260913T173202Z`) and nop reward 0 (`cpp-rerun-cpp-ffi-bridge-nop-20260913T174015Z`) on Docker Desktop arm64; measured numbers are in the validation log at the end, the table below keeps the pre-build estimates.

## What the task is

Form B excision on rerun-io/rerun `ddd684110e1f200ee13ba482ab42689b78dd0551` (main, 2026-09-12, `0.38.0-alpha.1+dev`). The hand-written bridge between the C++ SDK (`rerun_cpp/`) and the Rust `rerun_c` static library is removed: 14 `.cpp` files plus two private helper headers (`arrow_utils.hpp`, `component_type_registry.hpp`), 1064 lines. Every public/private declaration stays in the headers with its doc comments; generated code, tests and the snippet corpus are untouched, so the SDK library still compiles but `snippets` and `rerun_sdk_tests` cannot link. The agent reimplements `RecordingStream` (lifecycle, sinks, time, log/send_columns/log_file forwarding), `Error` (rr_error/arrow::Status mapping, strict mode, log handler), Arrow C Data Interface export for `ComponentBatch`/`ComponentColumn`/`TimeColumn`, once-per-descriptor component type registration, entity path escaping, spawn options and the version check.

Why Form B: `git log --first-parent --since=2026-03-01 -- rerun_cpp/src rerun_cpp/tests` (mining JSON has zero rerun_cpp PRs after its size/test filters) shows no PR with >= 200 hand-written non-test lines in `rerun_cpp/src`. Largest: `514a49b9a` GrpcServerSink multisink (+155/-15, needs a live gRPC client to observe). Full rejected list in `provenance.json`.

## Oracle

Upstream's own conformance harness, reproduced without pixi/uv: the Rust `snippets` binary (same commit) writes reference `.rrd` files at verifier-image build for the 104 snippets that `docs/snippets/snippets.toml` marks as comparable for C++; the verifier runs the 112 runnable C++ snippets with the roundtrip environment and compares with `rerun rrd compare --unordered --ignore-chunks-without-components` (rerun-cli built at the same commit with `--no-default-features`, i.e. no viewer). Python is not used as a baseline (every C++-runnable snippet has a runnable Rust sibling; a Python wheel would add a maturin build of `rerun_py`). Plus the 45 Catch2 test cases of `rerun_cpp/tests`, restored from pristine.

`tests/snippet_plan.py` re-derives the plan from `snippets.toml` (validated locally: 112/104, identical to `compare_snippet_output.py --no-py` selection); `test.sh` requires the plan recomputed at verify time to equal the image-build plan and the hard-coded 112/104/45.

## Build plan and estimates (4 cpus, 8 GB)

Both Dockerfiles share stages `base` -> `rust-build` -> `agent` verbatim (same instruction text, same `upstream.tar.gz` bytes at the same context path) so BuildKit reuses layers when the verifier image is built after the agent image.

| Stage | Work | Est. minutes | Est. size |
|---|---|---|---|
| base | Ubuntu 24.04 by digest; gcc 13 / cmake 3.28 / ninja 1.11 / python 3.12 via apt snapshot `20260912T000000Z` (falls back to live archive) | 2 | 0.6 GB |
| rust-build | rustup 1.96.0; `cargo build --release --locked -p rerun_c -p snippets -p rerun-cli --no-default-features` with `CARGO_BUILD_JOBS=3`, LTO off, codegen-units 16 (workspace release profile uses thin LTO + codegen-units=1, too slow/heavy here) | 35-50 | intermediate only (target/ 8-12 GB, discarded) |
| agent | extract tree; copy `librerun_c.a` to `/opt/rerun_c/{debug,release}` (where `crates/top/rerun_c/CMakeLists.txt` expects cargo output; `CARGO_TARGET_DIR=/opt/rerun_c`); `cargo` stub satisfying the `cargo build -p rerun_c` custom command; configure Debug/Ninja with a 1-job link pool; build Arrow 18.0.0 (upstream ExternalProject, md5 pinned) + loguru + Catch2; compile all rerun_sdk/snippets/tests objects (`-k 0`, links fail by design) | 25-40 | 6-8 GB |
| verifier | pristine `rerun_cpp/` + `docs/snippets`; Rust snippets binary; 104 reference rrds; `runner` user; `/opt/reference` chmod o-rwx | 5 | +0.5 GB |

Total first build: roughly 70-100 minutes; `build_timeout_sec = 10800`. Agent budget 8 h (relink 2-3 min; editing a header included by generated code forces a 15-20 min rebuild). Verifier 90 min budget vs 8-35 min expected.

Verifier flow (`tests/test.sh`): restore `c/`, generated dirs, `datatypes.hpp`, `rerun_cpp/tests`, `rerun_cpp/docs`, `docs/snippets` from pristine (records which protected dirs the agent touched) -> anti-cheat greps (hand-written files only; verified to pass the gold sources) and binary-file check -> `touch` exactly the files that differ from pristine so ninja rebuilds them despite transferred mtimes -> `cmake --build build/debug --target snippets rerun_sdk_tests` (3000 s cap) -> `ldd` check -> Catch2 (`--list-tests` count, console summary `test cases: 45 | 45 passed`, junit failures/errors 0) -> snippets as user `runner` with `/opt/reference` unreadable, 4 parallel, 180 s each -> `rerun rrd compare` -> `reward.txt` and `score.json` with per-group and per-snippet results.

## Risks (candid)

1. **Image build weight.** Release build of rerun_c + snippets + rerun-cli on 4 cpus / 8 GB is the long pole (35-50 min) and the only stage with OOM risk (`re_types`/`re_sdk_types` are huge generated crates; `CARGO_BUILD_JOBS=3` and LTO off mitigate, unverified). Arrow 18 debug build adds 10-15 min. If the host cannot absorb ~90 min builds, the fallback is to download the official prebuilt `rerun_cpp_sdk.zip`/`rerun-cli` for a *released* tag and rebase the task to that tag (loses the "current main" commit but cuts 40 min).
2. **`rerun-cli --no-default-features` may not compile** at this commit (feature-gated code paths are mostly exercised with the viewer on). Fallback: `--features base` minus `native_viewer` is not expressible; use `--no-default-features --features oss_server,perf_telemetry` or, worst case, build with default features (adds the native viewer: +20 min, +150 MB, needs no system X11 libs at build time since winit loads them dynamically).
3. **Ninja rebuild fidelity in the verifier.** The warm build dir was produced from the excised tree; the submission overlays files with arbitrary mtimes. `test.sh` touches every file that differs from pristine, and `CONFIGURE_DEPENDS` globs pick up new files; the `cargo build -p rerun_c` custom command re-runs through the stub whenever ninja considers it dirty (first build after image creation logs the command, so later builds do not rerun it). Untested end to end; a miss here shows up as the oracle failing to link and is fixable by forcing `ninja -t recompact` or a `--target clean` of only the hand-written objects.
4. Smaller: apt snapshot availability (falls back to live archive, weakening the pin); Catch2 multi-reporter CLI syntax (`--reporter console::out=... --reporter junit::out=...`, supported since v3.0.1); `runuser` reading assets under `/workspace/repo` (world-readable by default); `RERUN_PANIC_ON_WARN=1` turning any rerun_c warning during a C++ snippet into a failure (matches upstream CI, so the oracle passes if rerun_c behaves).

Docker Desktop on the authoring machine (8.3 GB total) cannot host a 4-cpu/8 GB container plus the daemon comfortably; validate on a Linux host with >= 16 GB and >= 60 GB free disk (rust target dir is transient but large).

## Contamination and difficulty

Tier C_public (the removed code is upstream-public; not interface-shifted). Difficulty drivers: three foreign boundaries at once (Arrow C Data Interface ownership rules, the rerun_c ABI incl. special handles and error codes, C++ error-handling contract incl. strict mode/log handler dispatch that `error_check.hpp` inspects); 104 byte-level semantic equivalences against Rust across static/temporal logging, columnar sends, properties, file importers and entity paths; slow feedback loop. A reference-only shortcut does not exist in the agent image (no Rust snippets, no reference rrds, no checked-in `tests/assets/rrd`).

## Alternates considered

* Excise only `RecordingStream` (recording_stream.cpp, 400 lines): smaller, leaves Arrow export in place; kept as a fallback if the full bridge proves too long for the budget.
* Excise `Collection`/`ComponentBatch` templates (collection.hpp): would make the whole generated corpus fail to compile until finished, hurting incremental progress; rejected.
* Interface-shifted variant (rename private detail helpers, change `to_c_ffi_struct` return type) to reach tier B: requires regenerating nothing but touching tests/snippets that call `try_log_data_row`; deferred.
* Ship the Rust `snippets` binary in the agent image to make the task self-verifiable (turns the differential oracle into visible tests); deferred, note for the scale variant.

## Files

```
environment/Dockerfile, environment/upstream.tar.gz (38.8 MB, sha256 daa10139...; 7023 members)
tests/Dockerfile, tests/upstream.tar.gz (same bytes), tests/test.sh, tests/snippet_plan.py, tests/run_snippets.py, tests/gen_reference.sh
solution/solve.sh, solution/changes.patch (16 files, +1064; git apply --check passes)
construction/make_upstream.sh, construction/filter_tar.py, construction/excised_files.txt
instruction.md, task.toml, provenance.json, STATUS.md
```

## Validation log addendum (2026-09-13)
- Build parallelism was already bounded via `CARGO_BUILD_JOBS` in both Dockerfiles; kept as is. (Context: rs-burn-store OOM-killed the linker when cargo used all 18 host cores inside an 8 GB Docker VM.)

## Validation log (2026-09-13, Harbor fix loop on Docker Desktop arm64, 18 host cores / 8.3 GB VM, cpus=4 memory_mb=8192)

1. **Run 1 (`cpp-rerun-cpp-ffi-bridge-oracle-20260913T124714Z`): image build failed in `rust-build` at `cargo fetch --locked`** with
   `failed to load manifest for workspace member /src/tests/rust/log_benchmark ... No such file or directory`.
   Cause: `construction/filter_tar.py` dropped the whole `tests/rust/` prefix (meant to shed the 43 MB of viewer snapshot PNGs),
   but `tests/rust/{log_benchmark,plot_dashboard_stress,re_integration_test,test_*}` are `[workspace] members` in the root
   `Cargo.toml`, so cargo could not load the workspace at all. Fix: keep `tests/rust/*` (0.5 MB of sources + manifests) and
   exclude only `tests/rust/*/tests/snapshots/**` (the LFS PNGs); `filter_tar.py` now parses the root `Cargo.toml`
   `[workspace] members/exclude` and refuses to emit an archive that drops any member manifest. Regenerated with
   `construction/make_upstream.sh` from a `git worktree` of the base commit: 7023 members, 38,827,956 bytes,
   sha256 `daa1013964aa1132192636cbcead54a9cb8f37ab994ab9794e8471bfd301ca48` (same bytes at `tests/upstream.tar.gz`).
   Verified: the 16 excised files are absent; `git apply --check` + `git apply solution/changes.patch` on the excised worktree
   leaves `git status` empty (byte-for-byte restore); `cargo metadata --locked --no-deps` (152 members) and
   `cargo fetch --locked` (33 s, 1.5 GB registry, no pyo3/maturin in the closure of rerun_c/snippets/rerun-cli) pass in a
   `rust:1.96.0-bookworm` probe container. `concepts/send_chunks` (the only snippet referencing `tests/assets/rrd`) is opted
   out for C++ in `snippets.toml`, so dropping `tests/assets/rrd` stays safe; the mcap/AV1/rvl/glb assets the plan needs are
   present (LFS smudged).
2. **Latent OOM defect fixed pre-emptively (not yet observed): ninja parallelism was unbounded.** `cpus = 4` is a CPU quota,
   not a cpuset, so `nproc` inside the container reports all 18 host cores and ninja defaults to 20 parallel g++ jobs on
   Arrow and the generated SDK sources (the same failure mode that OOM-killed rs-burn-store's linker on this host). Fix:
   `ENV CMAKE_BUILD_PARALLEL_LEVEL=4` in the `agent` stage of both Dockerfiles (bounds every `cmake --build`, including
   Arrow's ExternalProject, at image build and for the agent's own builds) and an explicit
   `-j "${CMAKE_BUILD_PARALLEL_LEVEL:-4}"` on the verifier's `cmake --build` in `tests/test.sh`. No check was weakened.
3. **Run 2 (`cpp-rerun-cpp-ffi-bridge-oracle-20260913T131420Z`, 556 s wall): both images built, reward 0.** restore, anticheat, build (36 s
   relink), snippets_run 112/112 and snippets_compare 104/104 passed; `catch2` failed with `cases=-1`. Cause: `test.sh` parsed only the
   Catch2 console summary shape that appears when something fails (`test cases: 45 | 44 passed | 1 failed`); when every test passes Catch2
   v3 prints `All tests passed (1067 assertions in 45 test cases)`. Fix: the parser accepts both shapes (still requiring exit code 0,
   `--list-tests` == 45, cases == passed == 45 and JUnit failures == errors == 0); checked against the run's real `catch2.log`
   (passes) and a synthetic failing summary (fails). Not a relaxation: the same exact-count conditions apply.
4. **Run 3 (`cpp-rerun-cpp-ffi-bridge-oracle-20260913T173202Z`): reward 1** (all six groups; build 47 s, Catch2 45/45 with 1067
   assertions, 112/112 run, 104/104 compare). Env setup 16 s (agent image fully cached), verifier phase 423 s (verifier image's last
   layer rebuilt because `test.sh` changed: reference generation ~2.5 min, then tests). Wall 445 s.
5. **Run 4 (`cpp-rerun-cpp-ffi-bridge-nop-20260913T174015Z`): reward 0** in 41 s wall (env 12 s, verifier 23 s): `build` group fails after
   6 s with undefined references to the excised bridge (`ComponentBatch::from_loggable` ... in `rerun_sdk_tests`/`snippets`), as designed.

Measurements. Agent image 4.35 GB, verifier image 4.6 GB (`docker images` during the trials). Cold `rust-build` stage not observed
in these runs: when run 2 started, its layers were already in the BuildKit cache (run 2's agent image took 5 min 27 s in total, which
only fits with the Rust release build cached), so the 35-50 min estimate for `cargo build --release -p rerun_c -p snippets -p rerun-cli`
stands unmeasured; the cmake/Arrow/SDK stage (2.83 GB layer) and the verifier's reference generation did build cold in run 2.
Verifier for the oracle: ~7 min including the verifier-image rebuild, ~4 min for `test.sh` alone (47 s build, ~40 s Catch2, ~2.5 min
snippets + compare). Memory (VM-wide `/proc/meminfo` sampled every 15-20 s): run 2 on the 8.3 GB VM peaked at 3.5 GB used
(MemAvailable never below 4.4 GB, Rust stage cached); runs 3-4 ran after the Docker Desktop VM had been resized to 32 GB (MemTotal
32293 MB) and peaked at 11.3 GB used VM-wide with ~8 GB of that belonging to other agents' containers before the run started;
largest own process `ld` at 1.47 GB RSS during the verifier relink, `cc1plus` x3 at <= 230 MB each (the `-j4` bound is honored).
No OOM kill, no check weakened. Note for RL use: the release-build memory profile of the Rust stage on a real 8 GB build host is
still unverified; `CARGO_BUILD_JOBS=3`, LTO off and codegen-units 16 remain the mitigation.

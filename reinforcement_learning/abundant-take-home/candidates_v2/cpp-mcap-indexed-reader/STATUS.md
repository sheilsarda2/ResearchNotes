# cpp-mcap-indexed-reader — status

Stage: **nop_pass** — Harbor 0.15.0 on arm64 Docker Desktop: oracle reward 1 (`cpp-mcap-indexed-reader-oracle-20260913T114708Z`, 61 s wall), nop reward 0 (`cpp-mcap-indexed-reader-nop-20260913T114708Z`, 20 s wall). See the validation log at the end.

## What the task is

Form B excision of the C++ MCAP library's index-based read path at foxglove/mcap
`aebd536bd474a2f242e13e4e5bce496b873bf7ee` (main, 2026-09-09). Removed and stubbed with a clear
`StatusCode::NotImplemented`:

| Piece | Lines restored by the gold patch |
|---|---|
| `McapReader::readSummary` + `readSummarySection_` + `readSummaryFromScan_` (Summary parse, dedupe/sort of chunk indexes, MissingStatistics retention, fallback scan synthesizing indexes and statistics) | ~225 |
| `McapReader::byteRange` narrowing over chunk intervals | ~20 |
| `IndexedMessageReader` (chunk/message-index driven ordered reading, filters, decompression, chunk slots, error statuses) | ~170 |
| `internal::ReadJobQueue` (forward/reverse priority with position tie-break) | ~90 |
| `errors.hpp` placeholder, `reader.hpp` private members | ~10 |

Gold patch: 5 files, +1064/−28 (library files only: 4 files, +478/−28; the rest is the pristine
`cpp/test/unit_tests.cpp`, from which the six indexed-path test cases were stripped in the agent
copy so the base builds green: on Linux 12/10 cases pass on the base and 18/16 on the restored
tree; macOS counts one fewer in each variant because `FileWriter reports filesystem write errors`
is `#if defined(__linux__)`).

Kept intact on purpose: `intervaltree.hpp` (generic, third-party-derived; not spec content), the
whole file-order reader, the writer. Alternate considered and rejected: excising the streamed
writer's chunk/index/summary emission. The writer conformance oracle is byte-exact against TS-written
files, which would over-constrain layout choices (message-index record order, summary group order,
compression heuristics) that the spec leaves open; the reader excision has more interacting
requirements and a behavioural, not byte-level, oracle.

## Oracle

1. Pristine `unit_tests.cpp`, both compile variants: exactly 18 and 16 Catch2 cases (Linux), exit 0.
2. Conformance corpus: the 16 variants `CppIndexedReaderTestRunner` supports (OneMessage x8,
   TenMessages x8), vendored in `tests/corpus/` (LFS content resolved from the shared clone),
   compared by `tests/harness/conformance_compare.py`, a Python re-implementation of
   `run-tests/index.ts` (`expectedResult` + stable stringify). The TS harness was not vendored
   (Yarn 4 workspace + `@mcap/core` build for 60 lines of logic).
3. Fourteen generated fixtures (`tests/harness/gen_fixtures.py`, self-contained raw MCAP writer,
   seeded, deterministic) with analytic ground truth in `tests/fixtures/manifest.json`: 42
   `readSummary`/`byteRange` checks and 157 `readMessages` queries (orders x windows x topic
   filters) run through `tests/harness/indexed_probe.cpp`, which is compiled against the
   submission. The upstream runner could not be used here: it dereferences a null schema for
   schemaless channels.
4. Cross-implementation: Go `test-read-conformance <f> indexed` and Rust
   `conformance_indexed_reader` (built in the verifier image) must yield the same message
   sequence as the C++ LogTimeOrder read (after `readSummary(NoFallbackScan)`, like the upstream
   runner) on 9 fixtures = 18 comparisons. The verifier Dockerfile runs the full `test.sh`
   against the pristine headers as a build-time self-check, so any disagreement between the
   sibling readers and the analytic expectations fails the image build instead of mis-scoring.
5. Anti-cheat: header-only submission, forbidden patterns (process spawning, verifier paths,
   log/reward paths, reference binary names), size and symlink limits.

Reward 1 requires every group; `score.json` carries per-group and per-check attribution.

## Local pre-validation (this machine, no Docker)

Built everything with Apple clang 21 (`-Werror` upstream flags plus
`-Wno-deprecated-literal-operator` for nlohmann 3.10.5 under the newer clang), brew lz4/zstd,
single-header Catch2 2.13.8 / nlohmann 3.10.5.

| Configuration | build | unit cases | corpus | fixtures | reward |
|---|---|---|---|---|---|
| pristine (oracle) | ok | 17 / 15 (macOS; 18 / 16 on Linux) | 16/16 | 199/199 | 1 |
| excised (no-op) | ok | 11 / 9 (fail: expected 17/15 on macOS; 12/10 vs 18/16 on Linux) | 0/16 | 30/199 | 0 |

`git apply --check solution/changes.patch` passes on the extracted `environment/upstream.tar.gz`
without a git repository; the restored `cpp/` is byte-identical to the base commit. Fixture
generation is deterministic (two runs diff-identical). Siblings were not run locally (no Go/Rust).

## Build plan and estimates (unmeasured)

* Agent image: `ubuntu:22.04@sha256:829f6d…`, apt toolchain (gcc 11.4, clang 14, cmake 3.22.1),
  `conan==2.32.0`, `./build.sh --build-tests-only` (Conan builds lz4/1.9.4, zstd/1.5.2,
  catch2/2.13.8 from source; nlohmann header-only), unit tests run, then
  `conan remote disable conancenter`. Estimated 10–15 min, ~2.5 GB. Agent rebuild cycle
  ~2–4 min (header-only: every TU recompiles).
* Verifier image: same base + apt `catch2 nlohmann-json3-dev liblz4-dev libzstd-dev`, Go 1.26.8
  (pinned sha256), Rust 1.98.1 via rustup, pristine `cpp go rust Cargo.*` tarball, Go reader
  build, Rust `cargo build -p mcap --example conformance_indexed_reader` (rust/cli dropped from
  the workspace), build-time self-check. Estimated 15–25 min, ~3–4 GB.
* Verify: six parallel clang `-O0` compiles (~2–3 min on 4 cpus) + checks (<1 min).

## Risks / open items for the build phase

1. Sibling agreement on generated fixtures is unproven until the verifier image builds
   (self-check gates it). Most likely friction: Rust `SummaryReader` on the `no_summary_offsets`
   or padded fixtures, or Go's chunk-load order on ties. Remedy: flip `sibling_check` for the
   offending fixture and adjust `--expected-sibling-checks`.
2. Conan in the agent image: `conan profile detect` under clang 14 plus recipe builds of
   catch2/zstd on jammy are what upstream CI does, but Conan 2.32 is newer than what upstream
   pins (`>=2.0,<3`); a recipe-revision or `compiler.cppstd` hiccup would need a version bump
   down (2.1x) in `environment/Dockerfile`.
3. `-Wunused-private-field` and friends: the verifier compiles with clang `-Werror`; a gcc-only
   developer could ship a header that fails on clang. The agent image defaults to clang, and the
   instruction says so.
4. Contamination is C_public and the agent tree includes the Go/Rust/Python readers; headroom
   claims should not rest on this task.

## Housekeeping

* Shared clone `research/cache/v2/foxglove__mcap`; this task's detached worktree is
  `<scratchpad>/cpp-mcap-indexed-reader/wt` with the excision applied (excised tree object
  `649e24ee0c1d9a47fd06c97af1d6ef6287c09a1b`, written with `git write-tree`, no branch created).
  The excision is reproducible with `<scratchpad>/cpp-mcap-indexed-reader/excise.py <worktree>`.
* Fixtures: regenerate with `python3 tests/harness/gen_fixtures.py tests/fixtures` (needs
  `lz4==4.4.5`, `zstandard==0.25.0`).

## Validation log (2026-09-13)
- Harbor oracle run 1 (arm64 host): verifier image build failed — Go tarball was hard-coded to linux-amd64, so the emulated amd64 `go` drove the native gcc with `-m64`. Download is now architecture-aware with per-arch checksums. Rerun queued.
- Harbor oracle run 2 (arm64): verifier image build failed at the build-time self-check. Two
  defects, both in the verifier, none in the headers or the patch:
  1. `unit_tests` expected 17/15 Catch2 cases; the pristine `cpp/test/unit_tests.cpp` lists 18/16
     on Linux because `FileWriter reports filesystem write errors` is `#if defined(__linux__)`
     (the 17/15 came from the macOS pre-validation). Fixed `EXPECTED_UNIT_CASES` to 18/16 and the
     same sentence in `instruction.md` ("How your work is checked", item 1). Not a relaxation: it is
     the exact count on the platform the verifier (and the agent) runs on; the excised tree gives
     12/10 there, so the no-op still fails this group.
  2. `siblings`: the upstream Go conformance tool (`go/conformance/test-read-conformance`,
     `readIndexed`) panicked with a nil `*Schema` dereference on 8 of the 9 cross-checked fixtures
     -- `indexedMessageIterator.NextInto` returns a nil schema for `schema_id 0` and the tool never
     checks it; every generated fixture has the schema-less `/log` channel (the TS harness simply
     never feeds it such variants). Remedy (a) from the fix brief: the Go reference reader is now
     `tests/harness/go_indexed_reader.go`, the upstream `readIndexed` with a nil-schema guard,
     compiled with `go build -overlay tests/harness/go_overlay.json` on top of the pristine
     `test-read-conformance` module so it links the same `go/mcap` and dependency versions as the
     upstream tool (binary `/opt/ref/bin/go-indexed-reader`; the name was added to the anti-cheat
     patterns; `go vet` runs over the overlaid package). The Go module download moved to its own
     cached layer; the Go build runs after `COPY harness`. The self-check `RUN` now prints
     `score.json` when it fails. Output shape and `run_checks.py` parsing are unchanged.
- Local rerun of `test.sh` in the debug image (self-check layer stripped) with these fixes:
  17/18 sibling checks. `overlap_heavy/go` differed at #43/#44 only: two messages from different
  chunks with the identical `log_time` 1700000360000000000 swapped (multisets equal, both sequences
  non-decreasing; Rust == C++ exactly). Cause: Go's indexed iterator orders equal timestamps by
  chunk *load* order (`go/mcap/indexed_message_iterator.go`: "if messages in different chunks have
  the same timestamp, the one from the earlier-loaded chunk is returned first ... The offset field
  of the message index is not comparable between indexes of different chunks"), C++ and Rust by
  file offset; the spec does not define the cross-chunk tie order. **Relaxed check, documented:**
  `run_siblings` now compares runs of consecutive equal-`log_time` messages as sorted multisets
  (`tie_runs()`), keeping order across distinct timestamps, message content and counts strict.
  The C++ offset tie-break (R3.6) is still asserted exactly, with offsets, by the 157 fixture
  queries; `score.json` records `exact` and `tie_runs` per sibling comparison (only
  `overlap_heavy/go` is non-exact). Local result after this change: reward 1, 18/18 siblings.
- Harbor run 3 (arm64, concurrent Harbor jobs on the host): **oracle reward 1.0**
  (`cpp-mcap-indexed-reader-oracle-20260913T114708Z`, wall 61 s; environment_setup 4.9 s with the
  agent image cached, verifier phase 44.8 s = verifier image build 33.1 s [go mod download 3.2 s,
  Rust example 14.4 s, Go harness build+vet 7.3 s, self-check 4.8 s; toolchain layers cached] +
  test.sh ~12 s). score.json: build ok, anti_cheat ok, unit_tests 18/18 and 16/16 cases,
  corpus 16/16, fixtures 199/199, siblings 18/18 (only `overlap_heavy/go` is `exact: false`).
  **nop reward 0.0** (`cpp-mcap-indexed-reader-nop-20260913T114708Z`, wall 20 s; verifier 11.3 s):
  build ok (excised headers compile), anti_cheat ok, unit_tests fail (indexed cases), corpus 0/16,
  fixtures 30/199 (FileOrder-only queries), siblings 0/18. Funnel stage `nop_pass`.
- Not changed: `solution/changes.patch`, `environment/upstream.tar.gz`, `environment/Dockerfile`,
  fixtures, corpus. `instruction.md` changed in one sentence only (18/16 case counts). Portability:
  the Go download is per-arch with pinned checksums; everything else is architecture-neutral.

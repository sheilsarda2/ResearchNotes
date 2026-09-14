# rs-mcap-cli-recover-parity — status

**Stage:** nop_pass — Harbor oracle reward 1, nop reward 0 on 2026-09-13 (Docker Desktop,
darwin arm64, 18-CPU / 7.9 GB VM). Measurements are in the validation log at the end; the
design sections below keep their original wording except for corrected case counts.

## Choice

- **PR:** foxglove/mcap #1647 "rust cli: Bring recover command to parity" (merged
  2026-06-01, merge f5dbff37). Base = its parent `cb592839` ("Standardize on 1 MiB default
  chunk size", #1659). Form A, oracle = Go reference binary built from the same commit.
- **Gold patch (non-test):** 6 files, +1142/-380; code only (excluding README): 5 files,
  +968/-345 (`recover.rs` rewrite 830/310, `common.rs` +80 streaming input,
  `commands.rs` exit-code plumbing, `main.rs` `ExitCode`, `cli.rs` flag changes).
  `git apply --check` against the base tree: OK. Zero changes under `rust/mcap/src`.
- **Not composed with #1646 (cat parity):** #1647 alone clears the 300-line bar with a
  dozen interacting requirements; #1648 and #1649 land between the two PRs, so composing
  would need a rebased combined diff for little extra horizon.
- **Rejected alternates:** #1648 ros2 db3 conversion (the Go converter uses ament-prefix
  lookup, not embedded schemas; no usable Go oracle for the Rust behavior). #1645 CLI
  conformance framework was studied and mirrored (its `recover` cases and the
  `truncate@300` known-difference case are reproduced) but not executed: it needs the
  Yarn 4 monorepo install plus Node in the verifier; the same Go binaries drive a Python
  harness instead.

## Layout produced

- `environment/upstream.tar.gz` (4.9 MB gz / 14.6 MB tar, 1646 files): `git archive` of
  the base commit with all 456 LFS pointers replaced by real content; deterministic.
  Copied to `tests/image-source/` because the verifier build context is `tests/`.
- `environment/Dockerfile`: `rust:1.95.0-bookworm` (digest pinned; stable at the PR
  date), `cargo fetch --locked`, warm `cargo build -p mcap-cli`, `cargo test --no-run` for
  both crates, offline env, git repo initialised with tag `base`.
- `tests/Dockerfile`: stage 1 `golang:1.22.12-bookworm` builds `go/cli/mcap` (cgo,
  `sqlite_omit_load_extension`) and `go/conformance/test-read-conformance`; stage 2 = the
  agent recipe + `/opt/pristine/repo` + `/opt/reference/bin/{mcap-go,mcap-go-dump}` +
  `gen_fixtures.py` (2235 cases, ~110 MB + 512 MiB streaming input) + `selfcheck.py`
  (oracle chain vs the 416 conformance `.json` listings; doctor must reject a bad CRC).
- `tests/test.sh`: anti-cheat grep/symlink scan -> restore manifests, lockfile, build.rs,
  `rust/mcap/{tests,examples,benches}`, corpus from pristine -> `cargo build --offline
  --locked -p mcap-cli` -> `cargo test -p mcap` integration tests (exact 17) ->
  `cargo test -p mcap-cli` -> `run_differential.py` -> `score.json` groups + `reward.txt`.
- `solution/changes.patch` + `solve.sh` (applies, rebuilds offline).

## Oracle design

Go `recover -a` (decode chunks) is the content reference; Rust output and Go output are
both dumped by the Go conformance reader and compared on schemas/channels (by id),
ordered messages, attachments, metadata, stopping at Data End. Expectations per class:
same input (valid, truncated), pristine input (bad CRC, unknown opcodes, structural,
corrupt header), pristine truncated at the bad chunk's offset (undecodable chunk, absurd
lengths), pristine filtered (unparsable/removed schema/channel/message/metadata/
attachment). Exit code and the two stderr lines come from the instruction contract.
Output validity via Go `mcap doctor`; header and chunk-codec checks via a 150-line
Python record walker (`mcaplite.py`) independent of both implementations. Memory bound
via `/proc/<pid>/status` `RssAnon` sampling (file-backed mmap pages excluded on purpose).

Corrupted-file generation (deterministic, seed 20260601; counts as built): 429 valid,
1194 truncations (every top-level record boundary + interior offsets of 40 files incl. the
413-chunk fixtures; the DataEnd body-absent offset is skipped, see log 4b), 53 CRC flips,
36 undecodable chunks (codec string `xxxx`, zeroed zstd frame magic), 225 unparsable
bodies, 135 removed/undefined records, 27 unknown-opcode insertions (3 inside a chunk with
recomputed CRC), 87 structural (no summary/footer/DataEnd, trailing garbage, u64::MAX
lengths, empty/garbage files), 14 codec-selection, 7 surface, 26 io-mode, 2 memory.

## Estimates (superseded by the measurements in the validation log)

- Agent image ~6.5 GB (rust image 1.8 GB + registry 0.4 GB + debug target with test
  binaries and criterion ~3.5 GB), build ~12 min on 4 CPUs.
- Verifier image ~7.5 GB (+ Go stage discarded, fixtures 0.6 GB), build ~16 min (Go
  module download for aws/gcs SDKs is the slow part).
- Verifier run ~8 min: incremental rebuild 1-2 min, mcap tests 1 min, cli tests 1-2 min,
  2289 differential cases ~3 min (5 process spawns per case), memory cases ~30 s.

## Open items for the build/validation phase (all closed 2026-09-13, see validation log)

1. Build both images; record sizes/minutes; confirm `cargo test -p mcap --test ...`
   reports 17 passed and pin `EXPECTED_MCAP_TESTS` if the count differs.
2. Oracle run (`solve.sh`) must give reward 1; no-op must give 0 (base recover buffers
   stdin, always exits 0, keeps `--always-decode-chunk`, defaults to zstd).
3. Risky checks to watch on the oracle run: Go `doctor` on Rust outputs written through
   the non-seekable stdout pipe; Go `filter --output-compression lz4` on unchunked
   corpus inputs (variants); Go `-a` recover on `zstd_chunk_with_padding.mcap` (padding
   after the frame may confuse the Go lexer, in which case drop that file from the valid
   class); `RssAnon` presence under Docker Desktop's kernel; the 120 s per-case timeout.
4. If the differential run exceeds ~10 min, thin the truncation offsets (interior offsets
   per record) — the count is only pinned via the generated manifest.
5. Consider a Sonnet rollout gate after validation.

## Risks

1. Expectation errors in classes derived by reading the reader code rather than running
   it (truncation-at-boundary = clean; unsupported codec = stop; unparsable body = skip).
   Mitigation: the oracle run will surface any class-level disagreement immediately since
   every class has dozens of cases.
2. Doctor strictness on writer output produced with `disable_seeking` (stdout mode).
3. The gold is itself AI-written upstream code merged 2026-06-01; contamination tier A
   but the PR body is public and describes the behaviors; the instruction is written
   from the contract, not the diff.
4. Agent egress: the Docker backend cannot allowlist hosts (only public / no-network) and
   the agent needs the model gateway, so the agent container keeps public network and
   could in principle fetch the merged PR diff. `task.toml` carries a commented
   allowlist block for backends that support it; otherwise scan trajectories for
   `github.com/foxglove/mcap/pull/1647` or `gh pr` usage.

## Housekeeping

- Shared clone `research/cache/v2/foxglove__mcap` was only read; two detached worktrees
  were added under the scratchpad (`rs-mcap-cli-recover-parity/wt` at cb59283 and
  `wt_after` at f5dbff37) using the lock directory. Remove them with
  `git worktree remove --force` (under the lock) once every mcap task is built.

## Validation log (2026-09-13, Docker Desktop darwin arm64, 18 CPUs / 7.9 GB VM)

Method: after run 1, the verifier image was built without its fixture step and the harness
was iterated inside a `--network none` container with `tests/harness` bind-mounted
(`gen_fixtures.py`, `selfcheck.py`, then the full `test.sh` with the PR patch applied and
again with pristine sources); the Harbor pair was run once everything passed there.

1. **Run 1 (Harbor) — verifier image build failed** in `gen_fixtures.py`: `KeyError` on the
   hard-coded variant `OneSchemalessMessage-ch-chx-mx-pad-rch-rsh-st-sum` (schemaless
   corpus files carry no `rsh` flag). Fix: `...-ch-chx-mx-pad-rch-st-sum`; the other five
   hard-coded names were checked against the corpus.
2. **gen_fixtures — `assert comps` failed on `variants/OneAttachment-ax-pad-st-sum-lz4`.**
   `mcap-go filter --output-compression lz4` emits no Chunk for a file holding only an
   attachment (or only metadata); a codec "variant" of such a file is meaningless. Fix:
   dropped `OneAttachment-ax-pad-st-sum` and `OneMetadata-mdx-pad-st-sum` from the variant
   sources (5 sources x lz4/zstd remain).
3. **selfcheck — "oracle header mismatch ... ('', 'mcap go v1.8.0') vs ('', '')".** The Go
   writer stamps its own `library` on every output header (appending the input's when
   non-empty), so Go output can never match the corpus header. Fix: selfcheck compares the
   profile only. `run_differential.py` was already checking the Rust output header against
   the *input* header, not against Go, so no Rust-facing check changed.
4. **Differential on the oracle: 2240/2281.** Four clusters:
   - a. `zstd_chunk_with_padding.mcap` (valid x1, bad-CRC x1, truncation x17). Its 12
     zstd-chunked messages reference channel 0, which is never defined; Go `recover -a`
     exits 1 ("unrecognized channel 0") and the Rust gold drops all 12 (exit 3,
     "discarded 12 messages"). It is not a valid input and Go cannot be its reference.
     Fix: removed from the valid / bad-CRC / truncation classes; added
     `missing/fixtures/zstd_padding/nochannel` (exit 3, expected stream = Go on the
     header-only prefix, header preserved). Its undecodable-payload case (`g0`) is kept.
   - b. 23 truncation cases at exactly `DataEnd.offset + 9` (opcode and length present,
     4-byte body absent): the upstream `LinearReader` marks that prefix as read before
     requesting the body, so EOF there is reported as a clean end and the gold exits 0,
     while the contract wording ("inside a Data End") implies 3. Every other interior
     offset of the Data End (+1, +5, +11, +12) exits 3 as specified. **Relaxation:** that one
     offset per file is no longer generated. Nothing recoverable is lost there (the Data
     End CRC is regenerated), so neither exit code is now rewarded or penalized.
   - c. 3 truncation cases inside an Attachment's trailing CRC (`end - 1`): the Go lexer
     hands the attachment to the writer before reading the CRC, so Go keeps an attachment
     the contract says to drop; the gold drops it. Fix: for offsets inside an Attachment
     record, the expected stream is Go on the prefix ending at that attachment.
   - d. `chunk_size/fixtures/compressed/4096` expected >= 50 output chunks, got 32.
     `compressed.mcap` holds only ~104 KiB of message payload (826 messages; its 2 MB is
     the two ros1 schemas repeated in each of its 413 chunks), so a 4096-byte target can
     produce at most ~34 chunks. **Relaxation/recalibration:** 4096 -> >= 20, plus a new
     1024 -> >= 50 case (Rust writer yields 32 / 110, Go 413 / 826).
5. **Dev-container reruns:** oracle differential 2235/2235 in 69.5 s; nop `test.sh` reward 0
   (527/2235; `memory/stdin` peak RssAnon 513 MiB > 256, `-a` accepted, exit 0 on
   truncation, ...). No harness-side errors (timeouts, reference failures) on either.
   Oracle memory cases: peak RssAnon 2 MiB (VmHWM 11 MiB) for file and stdin.
6. **Harbor pair:** oracle job `rs-mcap-cli-recover-parity-oracle-20260913T114725Z`
   reward **1.0**, wall 125 s (environment setup 14 s with cached layers, agent 5.3 s,
   verifier phase 102.5 s including the fixture layer and the 69.6 s differential);
   nop job `rs-mcap-cli-recover-parity-nop-20260913T114725Z` reward **0.0**, wall 94 s
   (verifier 80.9 s). Groups on the oracle: build, 17/17 mcap tests, 230 CLI tests, all 12
   differential groups. Run 1 measured the cold agent-image build at 110 s on this host.

Measured sizes/timings (arm64): agent image 5.45 GB, verifier image 6.69 GB (incl. 623 MB
fixtures); cold agent-image build ~1.8 min, warm 0.2 min; verifier phase 1.7 min warm
(differential 70 s, mcap+cli tests ~35 s, incremental build ~6 s). The client's amd64 host
will be slower; budgets in `task.toml` are unchanged. All fixes are architecture-neutral
(no downloads or arch-specific paths were added).

Checks relaxed: 4b (one truncation offset per file untested) and 4d (chunk-count
thresholds recalibrated to the fixture's real payload); 3 changed only the build-time
selfcheck. Everything else was an expectation or generator defect, and the oracle remains
the unmodified upstream PR diff.

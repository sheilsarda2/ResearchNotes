# py-mcap-writer-chunk-summary: status

Stage: **drafted** (no Docker build or Harbor run yet; nothing run against a model).

## What exists

- `environment/upstream.tar.gz`: foxglove/mcap@aebd536bd474a2f242e13e4e5bce496b873bf7ee
  (2026-09-09, current `main`) pruned to `python/mcap`, `website/docs/spec`, `testdata/`,
  `LICENSE`, `README.md`, plus `tests/conformance/data/OneMessage/OneMessage.{mcap,json}` for
  an upstream unit test; writer chunk/index/summary subsystem excised (see `solution/changes.patch`
  for the inverse). Built with `construction/build_archives.py` (deterministic tar, LFS content
  smudged locally; `git archive` would emit LFS pointers). Hashes in `construction/archives.sha256`.
- `tests/image-source/pristine.tar.gz`: same pruning, pristine package, full conformance corpus
  (416 `.mcap` + 416 `.json`).
- `tests/image-source/reference-src.tar.gz`: `Cargo.toml`, `Cargo.lock`, `rust/` (minus LFS
  test data), `go/` for the verifier's Rust/Go reader builds.
- Verifier: `tests/run_conformance.py` (port of the TS harness for the three Python runners plus
  Rust/Go self-consistency on Python output), `tests/authored_checks.py` (byte-level structural
  checks + Rust/Go differential on authored files), `tests/test.sh` (orchestration, anti-cheat,
  upstream pytest with exact counts, `score.json` + `reward.txt`).

## Local measurements (macOS, Python 3.10.20 venv, no Docker)

| Check | pristine (oracle) | excised (no-op) |
|---|---|---|
| py-writer byte-identical, 208 non-pad variants | 208/208 | 0/208 |
| py-streamed-reader, 416 variants | 416/416 | 416/416 |
| py-indexed-reader, 16 variants | 16/16 | 16/16 |
| authored byte-level checks (no Rust/Go) | 167/167 | fails at `generate` (NotImplementedError) |
| upstream pytest (36 selected) | 36 passed | 20 passed, 16 failed |
| `git apply --check solution/changes.patch` on shipped tree | OK; restored tree == pristine package | |

Rust/Go cross-checks (expected 208 + 208 streamed, 16 Rust indexed, 8 Go indexed on corpus
output; ~47 reader checks on authored files) have not been executed: no cargo/go on this host and
Docker builds are out of scope for this phase.

## Next commands

```bash
cd candidates_v2/py-mcap-writer-chunk-summary
docker build -t py-mcap-agent environment/                   # est. 2-3 min
docker build -t py-mcap-verifier tests/                       # est. 10-15 min (Rust release build dominates)
# oracle, by hand:
docker run --rm -v $PWD/solution:/solution py-mcap-agent bash -c 'bash /solution/solve.sh && tar -C /workspace/repo/python/mcap -cf - mcap' > /tmp/sub.tar
docker run --rm -i -v /tmp/logs:/logs py-mcap-verifier bash -c 'rm -rf /workspace/repo/python/mcap/mcap && tar -C /workspace/repo/python/mcap -xf - && /tests/test.sh' < /tmp/sub.tar
cat /tmp/logs/verifier/reward.txt /tmp/logs/verifier/score.json
# no-op: same without solve.sh -> expect reward 0 with conformance:py_writer 0/208, upstream_pytest 20/36
# Harbor (repo convention: harbor run -p TASK -a AGENT -m MODEL): harbor run -p candidates_v2/py-mcap-writer-chunk-summary -a oracle ; then -a nop
```

Validation host requirement: verifier build needs network for crates.io / proxy.golang.org / PyPI;
runtime is `no-network`. Memory 8 GB suffices (Rust build of `mcap` examples is small).

## Risks

1. **Rust/Go reader JSON assumptions untested locally.** `run_conformance.py` compares reader
   output on Python-written vs expected files (self-consistency), so format details cannot break
   the oracle there; `authored_checks.py` does parse Go/Rust JSON (`fields` pairs with string
   values, `messages` list) and could mis-assume a detail. Fix on first verifier build.
2. **Authored total threshold.** `test.sh` requires >= 210 authored checks and specific ids; the
   expected total with readers is 214. Adjust to the exact number after the first build.
3. **Contamination.** Tier C_public: the reference solution is upstream `main`; the TypeScript,
   Go, Rust and C++ writers are public. The agent tree omits them, but a model may recall them.
   Interface shift (phase 2 option): rename `IndexType` members / change `Writer` kwargs to a
   `WriterOptions` dataclass and regenerate the runner mapping.
4. **Upstream quirks pinned by tests**: zero-length Summary Offset records for empty enabled
   groups (`test_record_size_limit` expects 10 records) and compressor default output sizes
   (`test_decode_read` expects 741/779 bytes). Both are stated in instruction.md (R5, R12).
5. Unspecified edge: a chunked writer whose buffer holds Schema/Channel records but no message
   (upstream drops the buffer). Not tested, not stated.

## Alternates considered

- Excising the indexed read path (`SeekingReader` summary/chunk-index/message-queue): weaker
  oracle since the indexed conformance output is indistinguishable from a streaming fallback.
- Excising only `records.py` serializers: too small (77 lines).

## Validation log (2026-09-13)
- Harbor oracle run 1: all conformance groups passed (208/208 writer, 416/416 streamed, 16/16 indexed, Rust/Go cross-checks green) but the scoring helper crashed on a `passed=` keyword collision. Fixed (`group(name, ok, **info)`).
- Harbor oracle run 2: reward 0 from a single authored check, `multi_lz4:go_indexed_reads`: the Go conformance indexed reader segfaults (nil schema dereference at `test-read-conformance/main.go:364`) on the LZ4 fixture while Go streamed, Rust indexed and Python indexed reads all agree. Treated as a reference-tool crash: recorded under `skipped`, excluded from the required set (total 212 ≥ 210). Rerun queued.
- Harbor oracle run 3: the Go conformance indexed reader segfaulted on all three multi-chunk fixtures (nil-schema dereference; the fixtures include schemaless channels, which the upstream Go runner also excludes via `supportsVariant`). Removed `multi_zstd:go_indexed_reads` from the required-ID set; Go streamed, Rust indexed, Python indexed and the 8 corpus-variant Go indexed reads still cover the output. Rerun queued.
- Harbor oracle run 4: authored checks 206/206 with zero failures, but the ≥210 floor did not account for the 4 skipped Go-indexed pairs. Floor now adds back 2 per skipped reference-tool check. Rerun queued.

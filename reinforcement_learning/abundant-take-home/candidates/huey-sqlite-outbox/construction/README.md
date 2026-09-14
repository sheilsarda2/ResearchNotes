# Construction evidence

Built against Huey commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, checked September 13, 2026. The environment archive is a clean snapshot including the MIT license and upstream tests; it contains neither this verifier nor the reference implementation.

The golden patch integrates a graph preparation API, transaction-participating SQLite bulk enqueue, and the outbox contrib module, with user documentation and two public examples/tests. It stages immutable invocations in the caller's business transaction, then publishes each complete graph with its receipt in one SQLite transaction. Nested chords retain their generated identities across publication failures and restarts.

Validation completed:

- Local Python 3.12.13:192 passed, consisting of 28 independent task cases and 164 selected upstream regressions; the two new public tests also passed separately.
- Fresh Docker oracle, Python 3.12.11 ARM64, `--network none --cpus 2 --memory 4g`:192 passed, zero skips/errors, reward 1.
- Separate fresh untouched-baseline container with the same settings:missing outbox API caused a collection error, reward 0.
- `git diff --check` passed for the reference patch.
- All 36 original supplied files matched commit `c1ae968` byte-for-byte; see `originals-check.json`.

The independent tests exercise business commit/rollback/savepoints, same-main-file validation, batch insertion failure that preserves business writes, persistent key conflicts, frozen mutable graphs, nested/empty chords, normal result shapes, signed compressed messages, expiry/ETA, immediate mode, legacy rows, and queue isolation. Barrier-controlled subprocess cases kill a producer after commit, kill a publisher after partial graph insertion, and race four publishers over 17 three-member graphs. No sleeps are used to establish race ordering.

Reproduce from this task directory after building:

```sh
docker build -t abundant-huey-sqlite-outbox:baseline environment
docker run --rm --network none --cpus 2 --memory 4g \
  -v "$PWD/tests:/tests:ro" -v "$PWD/solution:/solution:ro" \
  -v "$PWD/construction/docker-oracle:/logs" \
  abundant-huey-sqlite-outbox:baseline \
  bash -c 'bash /solution/solve.sh && bash /tests/test.sh'
docker run --rm --network none --cpus 2 --memory 4g \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/construction/docker-baseline:/logs" \
  abundant-huey-sqlite-outbox:baseline bash /tests/test.sh
```

`docker-oracle/verifier/` and `docker-baseline/verifier/` contain the actual JUnit, diagnostic JSON, logs and binary rewards. `docker-image.json` records the inspected image. `provenance.json` records source and artifact hashes, bounded public-prior-art checks, and limitations.

These checks establish that the pack is runnable and its reference satisfies the verifier. They do not establish a 75-step working horizon or measured model headroom. No paid model trials were run. Root integration owns final Harbor and isolated verifier packaging.

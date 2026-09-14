# Construction evidence

Built against current Huey commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, checked September 13, 2026. The vendored environment contains the clean source snapshot, MIT license and upstream tests. The golden implementation and hidden verifier stay outside the agent image.

The reference patch adds opt-in executor snapshots and terminal recording, SQLite archive schema, a failure inventory/redrive module, documentation and two public tests. It keeps the existing Message wire format; durable task-ID lineage lives in SQLite. Redrive regenerates all ordinary success/error callback identities, detaches old chord bindings, and commits queue insertion, lineage and a persistent receipt together.

Validation completed:

- Local Python 3.12.13: **195 passed**, consisting of 31 independent feature cases and 164 selected upstream regressions. Two reference-added public tests passed separately.
- Fresh offline Docker oracle, Python 3.12.11 ARM64, `--network none --cpus 2 --memory 4g`: **195 passed**, no skips/errors, reward **1**.
- Separate fresh untouched baseline with the same settings: absent `huey.contrib.deadletter` produced a collection error, reward **0**.
- `git diff --check` passed for the reference implementation.
- All 36 supplied original files matched commit `c1ae968` byte-for-byte; see `originals-check.json`.

The verifier checks pre-hook/task mutation, the terminal attempt after retry/backoff, result-disabled storage and result flushing, timeout/lock/rate-limit/cancellation/periodic classification, real consumer execution, signed compressed snapshots, fresh callback IDs and expiry, old chord isolation, and lineage through retries/restart/parent deletion. SQLite triggers force transactional insertion failure and pause a redrive after insertion for SIGKILL. Five simultaneous processes obtain one replacement identity. Race ordering uses process barriers rather than sleeps.

An expiry edge was found during construction: serializing `None` can restore a Task class's absolute default when deserialized. The reference explicitly disables an old absolute deadline, and a behavioral regression verifies execution after that deadline on both root and callback.

Reproduce from this task directory:

```sh
docker build -t abundant-huey-deadletter-redrive:baseline environment
docker run --rm --network none --cpus 2 --memory 4g \
  -v "$PWD/tests:/tests:ro" -v "$PWD/solution:/solution:ro" \
  -v "$PWD/construction/docker-oracle:/logs" \
  abundant-huey-deadletter-redrive:baseline \
  bash -c 'bash /solution/solve.sh && bash /tests/test.sh'
docker run --rm --network none --cpus 2 --memory 4g \
  -v "$PWD/tests:/tests:ro" \
  -v "$PWD/construction/docker-baseline:/logs" \
  abundant-huey-deadletter-redrive:baseline bash /tests/test.sh
```

Actual logs, JUnit, diagnostic JSON and rewards are under `docker-oracle/verifier/` and `docker-baseline/verifier/`. `docker-image.json` records the inspected image. Source/artifact hashes, prior-art checks and limitations are recorded in `provenance.json`.

These runs establish pack functionality, not a measured 75-step horizon or model headroom. This candidate may be easier than the other Huey tasks; trial trajectories must determine whether it belongs in the final selection. No paid model trials were run. Root owns final separate-verifier packaging and Harbor oracle/nop validation. Work stops here for review.

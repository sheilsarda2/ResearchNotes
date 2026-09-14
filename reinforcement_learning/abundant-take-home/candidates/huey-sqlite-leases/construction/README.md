# Construction validation

The task uses the current upstream Huey commit `dbd1aef45adf57d2169e80bdc9f18ea1cd704759`, not an old release selected to omit newer fixes. The vendored environment contains the clean source archive and pinned dependencies only. `solution/` and `tests/` are supplied separately by Harbor; construction evidence is outside the environment.

Validation completed September 13, 2026:

- Local Python 3.12.13: 193 acceptance/regression cases passed, zero skipped/errors. Two public regression cases added by the reference patch also passed separately.
- Pinned Docker image, Python 3.12.11, Linux ARM64, two CPUs, 4 GiB, `--network none`: oracle reward 1, 193 passed, zero skipped/errors. Logs, JUnit XML and machine-readable diagnostics are in `docker-oracle/verifier/`.
- Same clean image and verifier without applying the solution: reward 0. The expected missing new exported class causes a collection error. Logs and diagnostics are in `docker-baseline/verifier/`.
- Reference patch passes `git diff --check`. Only seven intended upstream source/documentation/test files change. `environment/upstream.tar.gz` was generated from `git archive` of the clean commit, with no `.git` history or reference patch.

Acceptance cases include real Consumer thread/process execution, killing a blocked consumer with SIGKILL, concurrent process/thread reservations, lease expiration and stale token fences, user task renewal, and lack of a write lock during task bodies. Two additional process-kill cases stop after retry or due-transfer insertions and reopen persistent state. An injected failure after callback insertion verifies rollback of the result and queue message. A 503-message due batch exercises transfer across the existing SQLite schedule deletion chunk boundary. Lease clocks and interprocess barriers control boundaries; task horizon is not manufactured by waiting.

The selected upstream suite covers API/callback/chord/retry behavior, registry/serializer/immediate mode, SQLite storage, and consumer integration/configuration. The existing upstream `TestError` exception class produces one harmless PytestCollectionWarning; no test is skipped. The verifier rejects collection failures, empty collections, failed cases, and skipped/xfail cases.

`originals-check.json` records a byte-for-byte check of supplied files against `c1ae968`. No paid model trial was run, no >75-step horizon was measured, and these oracle/no-op runs establish task solvability rather than model headroom. Root is responsible for final Harbor oracle/no-op runs and the user will run the candidate model trials.

# Construction evidence

This is a new AI-assisted task package. It does not change the supplied take-home materials or the ten existing validated tasks.

## Behavior and repair

A router session can own complete and incomplete queryables under one expression. Broker registration previously replaced the face's aggregate queryable information with the latest declaration, making mixed declarations order-dependent. The reference patch stores the declaration and reduces all live declarations for that face and expression using the existing `merge_qabl_infos` operation. The existing teardown path already recomputes the aggregate. No public API or protocol changes.

The natural implementation is small. Metadata labels the task medium; neither model headroom nor a >75-step horizon is measured. Do not infer difficulty from the tests or package size.

## Independent verification

Six protected public-API tests cover:

1. Empty/single/homogeneous complete and incomplete controls.
2. Complete then incomplete declarations.
3. Incomplete then complete declarations.
4. Same wildcard expression and unrelated expressions.
5. Removing complete siblings, removal of the last complete sibling, re-addition and final teardown.
6. Incomplete sibling churn while one or more complete siblings remain.

Every stage checks exact reply payload sets for local-session and remote-client `All` and `AllComplete` queries. The reference and baseline share the same tests. The router finishes each synchronous mutation before a fresh TCP client opens, avoiding a client retained across stale discovery states. No sleep or latency threshold determines success. Ordinary 15-second per-query and 420-second subprocess watchdogs prevent hung tests from exhausting the overall 900-second verifier limit.

A protected copy of the original `zenoh/tests/queryable.rs` is also compiled and its `test_queryable_same_session` regression runs. The grader requires exactly the six expected new test names and that upstream test, all passing, with no ignored/measured tests. JSON diagnostics and complete compiler/test output are retained. A forced `cargo clean -p zenoh` ensures source artifact timestamps cannot reuse a precompiled baseline implementation.

## Environment and direct results

Source pin: `646f2d1b730e584a570015a77bee9f6db08be9d1`. Rust/image pin and source/vendor hashes are in `../provenance.json`. The source archive is a clean `git archive` including upstream licenses and tests; the solution and verifier are not present in the agent image. The minimal harness has a locked dependency graph with 263 vendored crates (326 MB unpacked, about 40 MB compressed), independent of optional default transports. Build and grading use two jobs and offline Cargo. The agent and verifier each receive independent baseline archives and vendored dependencies.

The verifier image built successfully on Linux ARM64. Local image: `abundant-zenoh-completeness-verifier-local`, digest `sha256:f981efc10ad77bd68caa51d6669eb6ee5452c9a36a2a5398715ce67fa31d41c6`, approximately 630 MB as reported by Docker. The final agent-image build is exercised by the coordinator's Harbor run; its recipe uses the same pinned baseline/vendor/harness build without hidden tests.

| Run | Reward | Result |
|---|---:|---|
| Untouched baseline, fresh verifier, network disabled, 2 CPU / 4 GB | 0 | Three intended remote `AllComplete` assertion failures; three new control tests and upstream regression pass. About 52 seconds including forced compilation. |
| `solution/solve.sh` applied to fresh baseline verifier under identical limits | 1 | All six new tests and the upstream regression pass. About 44 seconds including forced compilation. |

Authoritative diagnostics: [baseline](docker-baseline/diagnostics.json), [reference](docker-oracle/diagnostics.json). Full image build: [verifier-image-build.log](verifier-image-build.log). Additional cached local reference run: [oracle-local.log](oracle-local.log). The first offline vendor attempt found un-cached cross-platform crates; the subsequent locked fetch populated all vendors before image construction. This was tooling preparation, not defect evidence.

Reproduction commands from the repository task workspace:

```bash
docker build -t abundant-zenoh-completeness-verifier-local \
  -f candidates/zenoh-queryable-completeness/tests/Dockerfile \
  candidates/zenoh-queryable-completeness/tests

docker run --rm --network none --cpus 2 --memory 4096m \
  -v "$PWD/candidates/zenoh-queryable-completeness/construction/docker-baseline:/logs/verifier" \
  abundant-zenoh-completeness-verifier-local bash /tests/test.sh

docker run --rm --network none --cpus 2 --memory 4096m \
  -v "$PWD/candidates/zenoh-queryable-completeness/construction/docker-oracle:/logs/verifier" \
  -v "$PWD/candidates/zenoh-queryable-completeness/solution:/solution:ro" \
  abundant-zenoh-completeness-verifier-local \
  bash -c 'bash /solution/solve.sh && bash /tests/test.sh'
```

## Submission boundary and remaining coordinator gate

Only `/workspace/repo/zenoh/src` transfers into the fresh verifier. Manifests, dependency lock/vendor data, original external tests, grading code, and build scripts outside that surface remain protected. The instruction states the boundary. This separation does not claim perfect security against malicious Rust that executes arbitrary filesystem/process operations.

The task is ready for independent review and paired Harbor oracle/no-op validation. These direct Docker results are not Harbor results. The coordinator must freeze the final task, run Harbor 0.15.0 oracle=1 and nop=0, and bind those results before adding the task to a validated index. No paid models or external messages were used.

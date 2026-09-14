# Construction evidence

This new AI-assisted Harbor package uses RapidJSON commit `24b5e7a8b27f42fa16b96fc70aade9106cf7102f`. It leaves the supplied take-home materials and the ten existing validated tasks unchanged.

## Demand, scope, and prior art

[Issue #2296](https://github.com/Tencent/rapidjson/issues/2296) reports an object accepted despite an undeclared required property and `additionalProperties:false`. The same author supplies a precise follow-up reproducer and diagnoses the shared internal property table. This is concrete demand from one author; a named production application, multiple independent affected users, and quantified impact were not established.

The task preserves instance-validation semantics. An unsatisfiable schema need not be rejected during construction. Required/dependency presence does not declare properties. Declared properties and matching patterns determine which member schemas apply; otherwise the additional-properties rule applies. Required and dependency checks remain active. These requirements follow Draft-4 sections [5.4.3–5.4.5 and 8.3](https://json-schema.org/draft-04/draft-fge-json-schema-validation-00). The dependency and schema-valued additional-property cases extend the same dispatch defect; they are not separately reported demand.

The [author's comment](https://github.com/Tencent/rapidjson/issues/2296#issuecomment-2385100070) explicitly suggests adding a `Property` member to distinguish declared names. That public design hint closely matches the reference approach, although the bounded public review found no equivalent implemented fix. The reference marks actual `properties` entries as declared, records presence independently, and routes names mentioned only by required/dependencies through normal additional-property handling. It changes one header and no public API. The natural repair is modest; the medium label is provisional. There is no measured model headroom or asserted 75-step horizon.

[PR #2377](https://github.com/Tencent/rapidjson/pull/2377) was inspected at the diff level: it guards non-string dependency-array values before property lookup and adds a crash regression. Invalid dependency-array entries are explicitly outside this task. Older [PR #977](https://github.com/Tencent/rapidjson/pull/977) addresses adjacent pattern/additional-property assertion behavior already in the baseline. The research reviewed 116 open PRs, 100 recently updated closed PRs, 50 recent commits, issue comments/timeline, and targeted searches. This is bounded evidence, not proof about unreferenced forks or private work. Records are in `../../../research/rust-cpp-10-to-5/cpp/sources/`; failed rate-limited search files are not absence evidence.

## Independent verification

The protected harness uses public `SchemaDocument`, `SchemaValidator`, `Reader`, and DOM APIs. There are 49 fixed fixtures and 309 checks across DOM, SAX, and continued error collection. Ordinary fixtures run twice with `Reset()`. Three state sequences change instances between resets, testing valid → missing dependent/required value → inactive/valid recovery. Successful instances must complete traversal as well as leave the validator valid.

Coverage includes forbidden and schema-valued additional properties, allowed/default behavior, required names, property/schema dependency triggers and targets, overlapping patterns, empty and referenced property schemas, nested objects, array items, `allOf`, Unicode, and embedded-null property names with a prefix-collision control. Positive controls catch blanket rejection, removed presence tracking, and accidental validation of declared or pattern-matched members against the additional schema.

The verifier separately compiles the original protected `unittest.cpp` and `schematest.cpp` with GoogleTest and executes exactly the 13 upstream `SchemaValidator.Object_*` regressions. Exact expected names, collection count, run status, and absence of failures/errors/skips are checked. Upstream test source is copied into `/opt/pristine-tests` when the fresh verifier image is built and is outside the submitted headers. No patch-content or internal-field assertion determines reward.

Every grading run builds both executables against the transferred source headers. No precompiled RapidJSON test implementation can mask changed source timestamps. All grading is offline and deterministic; process watchdogs bound compilation and execution. Binary reward is initialized to zero before compilation and becomes one only if both suites pass.

## Environment and evidence

The agent and verifier each start from equal clean source archives. All 313 regular archive files were compared byte-for-byte with the pinned Git tree, including upstream tests and `license.txt`; there were no mismatches. The header library uses the MIT license. The complete source archive preserves the bundled BSD and JSON-license notices described in that upstream file.

Both images use the same digest-pinned Debian Bookworm toolchain base. Its Rust installation is incidental; this task compiles only C++ with GCC 12.2.0. GoogleTest is apt-pinned to `libgtest-dev=1.12.1-0.2`. Python 3.11.2 runs the protected orchestration without third-party Python packages. See [toolchain.log](toolchain.log), image build logs, and `../provenance.json` for exact image/archive pins.

Direct isolated Linux ARM64 checks run with networking disabled, one CPU, and 2 GiB RAM, within the task's two-CPU/4-GiB limits. The final results and image identities are recorded in [local-validation.json](local-validation.json), with authoritative [baseline diagnostics](docker-baseline/diagnostics.json) and [reference diagnostics](docker-reference/diagnostics.json). The original [host-baseline.log](host-baseline.log) came from the earlier 38-fixture harness and is historical only; final Linux results use the strengthened 49-fixture verifier.

| Run | Reward | Independent checks | Upstream regressions | Compile and test time |
|---|---:|---|---|---:|
| Untouched baseline | 0 | 84 failures / 309 checks | 13 / 13 pass | 49.7 s |
| Reference applied by `solve.sh` | 1 | 309 / 309 pass | 13 / 13 pass | 55.7 s |

Reproduce from the task workspace:

```bash
docker build -t abundant-rapidjson-membership-verifier-local \
  -f candidates/rapidjson-schema-property-membership/tests/Dockerfile \
  candidates/rapidjson-schema-property-membership/tests

docker run --rm --network none --cpus 1 --memory 2g \
  -v "$PWD/candidates/rapidjson-schema-property-membership/construction/docker-baseline:/logs/verifier" \
  abundant-rapidjson-membership-verifier-local bash /tests/test.sh

docker run --rm --network none --cpus 1 --memory 2g \
  -v "$PWD/candidates/rapidjson-schema-property-membership/construction/docker-reference:/logs/verifier" \
  -v "$PWD/candidates/rapidjson-schema-property-membership/solution:/solution:ro" \
  abundant-rapidjson-membership-verifier-local \
  bash -c 'bash /solution/solve.sh && bash /tests/test.sh'
```

Only `/workspace/repo/include/rapidjson` transfers into the fresh verifier. Other source, build files, tests, and grader fixtures are protected. This boundary does not claim perfect security against malicious C++ executing arbitrary filesystem/process operations. The coordinator must run and bind Harbor 0.15.0 oracle/no-op validation after freeze. Direct Docker validation is not a Harbor result. No paid model trials or external messages were used.

# Construction evidence

This AI-assisted Harbor task starts from Burn commit `1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d` (0.22.0-pre.3). The source archive matches all 2,170 tracked entries, including 36 upstream symlinks and both root licenses. Source, Cargo configuration and dependency locks match across the agent and verifier build contexts. See `source-audit.json`.

The reference adds a prefix policy to the existing index mapper and connects it to the PyTorch and Safetensors stores. It changes four implementation files under `crates/burn-store/src`; no dependency or public format change is needed. Cache clearing in its setters is incidental; the task only requires configuring a store before the first read.

The independent checks exercise original-prefix matching, ordered regex rules, nested and root indices, numeric sorting, preserved tensor metadata/bytes/order, actual model loading, direct tensor lookup, key enumeration, explicit key remapping, setter precedence, and missing/unused reports. Six small fixture files contain genuine PyTorch and Safetensors checkpoints. The policy tests include distinct parameter values, rather than testing names alone. Three separate controls preserve untouched store defaults; twelve protected tests preserve the original key-remapper regressions. There are 24 grading tests in total.

The new API is absent in the baseline, so the nine scoped tests cannot compile there. The verifier runs the twelve upstream tests and three default controls as separate targets first. Earlier actual store reproduction demonstrates why both existing global flags fail the mixed model and verifies the working explicit-remapping control; see `../../../research/rust-cpp-10-to-5/burn/store-probe-final.json`.

The pinned Rust 1.98.1 image builds the small locked dependency harness, including the CPU Flex backend. ARM64 uses the upstream-required FP16 target feature through our own Cargo configuration. Dependencies are fetched only during image construction; evaluation is offline. The agent image has no independent tests or solution. Only submitted `burn-store/src` is transferred to a fresh verifier, and the verifier forces that package to rebuild before running the protected targets. This is ordinary task isolation, not a guarantee against arbitrary hostile Rust programs.

`reference-initial.log` retains a verifier-authoring compile error: calling `as_slice()` on Burn's Bytes type selected an unstable library method. The test was corrected to compare dereferenced byte slices. `reference-final.log` then records all original 21 checks passing. The final image checks add the three untouched-default controls and are recorded separately in `baseline-verifier/` and `reference-verifier/`.

Build with `docker build -t abundant-burn-scoped-agent-local environment` and `docker build -t abundant-burn-scoped-verifier-local tests` from the task directory. In a fresh verifier container with networking disabled, run `bash /tests/test.sh`; for reference validation, mount `solution/` at `/solution` read-only and first run `bash /solution/solve.sh`. Persist `/logs/verifier` outside the container.

Final direct results and image IDs are recorded in `provenance.json`. Harbor oracle/no-op validation is performed after these files are frozen and recorded outside this directory, in the repository validation index and shortlist. No paid model trials were run. The natural fix may be modest; model headroom and the greater-than-75-step horizon are unmeasured.

The final isolated Linux runs produced baseline reward **0** in 9.80 seconds (all 15 original/default controls pass; scoped target cannot compile without the new API), and reference reward **1** in 13.81 seconds (all 24 tests pass). These times include container setup and a forced burn-store rebuild. Both used 2 CPUs, 4 GiB, and no network.

`initial-login-shell/` retains an invocation mistake during construction: using `bash -lc` reset the image's Cargo PATH, so no Rust tests ran. The final commands use `bash -c`, preserving the image environment. This was not an upstream or reference failure and is not accepted validation evidence.

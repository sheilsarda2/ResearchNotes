# candidates_v2 task conventions

Harbor 0.15.0 layout, identical in shape to the client sample and to v1, with the additions needed for compiled languages and differential verification.

## Directory

```
<lang>-<repo>-<slug>/
  instruction.md          # implementation-independent spec; every hidden assertion must trace to a sentence here
  task.toml               # keep the [environment.env] gateway block verbatim from the sample
  environment/
    Dockerfile            # pinned base digest; toolchain pinned; ALL dependencies fetched at image build
    upstream.tar.gz       # clean `git archive` of the base commit (no .git, no solution, no hidden tests)
    ...                   # vendored deps, prebuilt reference binaries, fixtures
  tests/
    Dockerfile            # verifier image: same base + reference implementations/corpora + hidden harness
    test.sh               # clean-room rebuild from submitted sources, run harness, write reward
    ...                   # harness, expected outputs, conformance corpus subset
  solution/
    solve.sh              # applies changes.patch (Form A: upstream PR diff; Form B: restore patch; Form C: authored)
    changes.patch
  provenance.json         # see schema below
  STATUS.md               # build/validation state, measured build minutes, image sizes
```

## task.toml

- `version = "1.0"` (sample form), `[metadata]` with `difficulty`, `category = "software-engineering"`, `tags` including the language and form.
- Budgets for v2: `[agent] timeout_sec = 21600` (6h) by default; raise per task if the reference build cycle exceeds 5 minutes. `[verifier] timeout_sec = 3600`. `[environment] build_timeout_sec = 3600`, `cpus = 4`, `memory_mb = 8192`, `storage_mb = 30720`. Record the actual host these were validated on; Docker Desktop on the authoring machine has 8.3 GB total, so validation may need a larger host.
- Separate offline verifier: `[verifier.environment] network_mode = "no-network"` with its own build settings, and `[[artifacts]]` entries transferring only the agent-editable source directories (for example `src/`, `crates/foo/src/`, `python/mcap/mcap/`). Tests, build scripts and reference binaries are never transferred.

## Environment rules

- Base images by digest. Toolchains pinned (`rust-toolchain.toml` honored or `rustup toolchain install <exact>`; `cmake`, `gcc`/`clang` versions from the pinned distro; Python by exact patch).
- Network is available only during image build. Rust: `cargo fetch` (and `cargo build` of dependencies) into the image so `cargo build --offline` works; use `CARGO_NET_OFFLINE=true` at runtime. C++: FetchContent, conan or vcpkg populated at build; system packages via apt at build. Python: wheels installed at build.
- Warm build caches inside the image (`target/`, CMake build dir, `sccache` off) so the agent's first build is incremental, and the verifier rebuild fits its timeout.
- Reference implementations for differential verification (Go mcap CLI, onnxruntime, zarr-python, zenohd, rerun CLI, Python SDK) live only in the verifier image, or in the agent image only when the instruction says the agent may use them.

## Verifier rules

- **Clean-room rebuild.** `test.sh` copies the transferred source directories over a pristine tree in the verifier image, restores all upstream test directories from the pristine copy, then builds. The agent cannot edit anything the verifier executes except its own sources.
- **Differential or corpus first, author-written assertions last.** Prefer: the project's conformance runner, cross-implementation comparison (`rerun rrd compare`, MCAP conformance JSON, onnxruntime outputs, zarr-python roundtrip), the PR's own tests, upstream regression suites. Author-written tests only for behaviors none of those reach, and each such assertion must be derivable from `instruction.md`.
- **Binary reward plus attribution.** Write `/logs/verifier/reward.txt` as 0 or 1. Also write `/logs/verifier/score.json` with per-group results (conformance fraction, differential matches, regression pass count, build status) so failures can be attributed in trajectory review. Reward 1 requires all groups to pass unless the task states a threshold.
- **No timing-decided tests.** Barriers and deterministic fault injection only. Per-test timeouts via `timeout` or the harness's own mechanism.
- **Exact counts.** Require the expected number of collected cases; fail on skips.
- **Anti-cheat checks before scoring**: the build must not link or exec the reference binary; grep transferred sources for `_pytest`, `atexit`, `/logs`, and for the reference binary name; reject if found. Record checks in `score.json`.

## Instruction rules

- State the public interface, the behavioral contract, the compatibility requirements and the scope boundaries. Name exception or error types where tests check them. Name any fault-injection boundary the verifier relies on (for example "publication must go through `os.replace`").
- Say which reference implementations exist in the environment and whether the agent may run them.
- Do not describe the algorithm or the file layout of the reference solution.
- Run the derivability pass: list every hidden assertion and the instruction sentence it traces to in `provenance.json` under `derivability`. Unmapped assertions are dropped or the instruction is amended.

## provenance.json schema

```json
{
  "task_id": "...", "language": "python|rust|cpp", "form": "A|B|C",
  "upstream": {"repo": "...", "base_commit": "...", "base_commit_date": "...", "license": "..."},
  "feature": {"kind": "pr|excision|spec", "ref": "PR URL or subsystem", "merged_at": "...", "gold_patch": {"files": 0, "additions": 0, "deletions": 0}},
  "oracle": {"type": "conformance_corpus|cross_impl_differential|reference_binary|pr_tests|authored", "reference": "..."},
  "contamination": {"tier": "A_recent|B_interface_shifted|C_public", "notes": "..."},
  "horizon": {"proxy": "files, lines, subsystems", "hypothesis_steps": ">75", "measured": null},
  "demand": {"evidence": ["issue/PR/release-note links"]},
  "derivability": [{"assertion": "...", "instruction_ref": "..."}],
  "build": {"agent_image_gb": null, "verifier_image_gb": null, "build_minutes": null, "verifier_minutes": null, "host": "..."},
  "validation": {"oracle_reward": null, "nop_reward": null, "harbor_job": null},
  "funnel": {"stage": "drafted|built|oracle_pass|nop_pass|derivability_done|rollout_gated|accepted|rejected", "rejection_reason": null}
}
```

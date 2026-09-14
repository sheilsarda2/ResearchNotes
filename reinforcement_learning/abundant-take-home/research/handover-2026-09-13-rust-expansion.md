# Handover: five-task expansion into Rerun, Burn, and Zenoh

Checkpoint: September 13, 2026, approximately 10:00 UTC / 03:00 Pacific. This is AI-assisted working material, not the human-written submission report.

## What the user wants

Add approximately **five tasks to the existing ten**, drawing from **Rerun, Burn, and Zenoh**. The user explicitly removed the robotics-relevance filter. They want parallel research and one implementation owner per task, and previously asked to aim for five research agents. This runtime permits only three workers alongside the coordinator, so additional independent reviews must run in later waves.

Selection must demonstrate that people care about the exact problem, that it still fails on current code, and that an equivalent fix is not already available or publicly being implemented. An open issue, repository popularity, or a missing API alone is insufficient. Cite primary sources and show executable evidence with positive controls. The user challenged the earlier Huey choices on precisely these grounds.

The latest instructions were “keep going,” then “what did you hit roadblock wise? can you backtrack,” then a request for this handover. We backtracked from trying to fill five slots to verifying stronger candidates. Research workers have checkpointed and paused for handover. The expansion remains unfinished.

## State at handover

**Ten existing tasks remain validated. Zero new Rust tasks are runnable or Harbor-validated. Three new leads have decisive current-code reproductions. Five have not been selected.**

| Lead | Evidence now established | Status and remaining qualification |
|---|---|---|
| Burn: scoped checkpoint index remapping, [#4716](https://github.com/tracel-ai/burn/issues/4716) | Current shared remapper cannot simultaneously preserve gapped indices for one prefix and collapse them for another. Both global-switch settings fail the mixed fixture; all-collapsible and manual per-index remapping controls pass. Maintainer describes a real model-import workflow. | Advance to construction. This is missing automatic scoped policy, **not impossible import**: an explicit `KeyRemapper` workaround exists. Actual PyTorch/Safetensors store integration is not yet tested. |
| Zenoh: complete queryables sharing a session, [#2614](https://github.com/eclipse-zenoh/zenoh/issues/2614) | Actual Rust API reproduces on release 1.10.1 and main. Declaring complete then incomplete queryables yields `All=2`, `AllComplete=0`; reverse order yields `2/1`. Single-complete and both-complete controls pass. Reporter describes authoritative RocksDB and eventual S3 storage in one session. | Advance to construction. A small broker aggregation repair may suffice; no difficulty or long-horizon claim. |
| Rerun: selected-time export includes out-of-range rows, [#12596](https://github.com/rerun-io/rerun/issues/12596) | Current-main Rust test exports rows `[2,4,6,8,10,12,14,16,18,20]` for selection `[4,7]`, expected `[4,6]`. Full-export, disjoint-selection, static-data and source-immutability controls pass. Reporter confirms the same issue in real data; maintainer confirms whole chunks need truncation. | Strongest Rerun lead; construction deferred during backtracking. Existing chunk-filter primitives may make the repair modest. |
| Rerun: shutdown with unavailable gRPC sink, [#12821](https://github.com/rerun-io/rerun/issues/12821) | Python binding on current 0.37.2 exceeds a **20-second shutdown watchdog** after one million row logs. One-row and million-row columnar controls exit in about three seconds. Logging finishes before watchdog timing begins. | Reserve lead. Exact C cleanup/current-main Rust reproduction and a fair shutdown contract remain unresolved. Do not call this infinite blocking or claim the C fixture was reproduced. |
| Zenoh: package upgrade deletes config/state, [#2778](https://github.com/eclipse-zenoh/zenoh/issues/2778) | Running the current `postrm upgrade` script in a disposable container deletes synthetic `/etc/zenohd` and `/var/zenohd` contents. Operator reports lost customization/state. | Optional reserve, probably a short shell-packaging task in a Rust repository. Not a substantial Rust implementation. Probe did not reproduce a full dpkg transaction or plain `apt update`. |

No matching exact active implementation was found for the first three in the documented public searches. That is a bounded finding, not proof about private work, every fork, or unreferenced branches. Model headroom and the assignment's >75-agent-step horizon remain **unmeasured for every task**, including the original ten.

## Read these files first

All paths below are relative to `abundant-take-home/` unless absolute.

- [Preservation instructions](../AGENTS.md).
- [Current ten-task shortlist](shortlist.json), [candidate index](../candidates/README.md), and [accepted Harbor evidence](validation/README.md).
- [Burn audit](rust-package-review/burn.md), [Zenoh audit](rust-package-review/zenoh.md), [Rerun audit](rust-package-review/rerun.md). These contain primary links, exact scope, freshness checks and rejected alternatives.
- [Earlier Huey demand correction](huey-demand-review.md). All three Huey recommendations were downgraded; the other seven have not undergone the later exact-demand audit. Runnable does not mean impact-qualified.
- [Preservation and validation-binding check](rust-package-review/handover-preservation-check.json): all **36 original files** match commit `c1ae968` byte-for-byte; all ten current task digests and saved oracle/no-op result bindings verify.

`research/rust-package-review/sources/` preserves timestamped GitHub API responses: repository, current HEAD, latest release, all open issues/PRs, 100 recently updated closed PRs, and 50 recent commits per repository. Further issue comments, PR searches and actual diffs are saved there. Do not describe the 100 closed PRs as all historical PRs. Shared collection script: `research/tmp/fetch-rust-review.py`.

## Why we backtracked

**Open issues were stale.** Burn [#5481](https://github.com/tracel-ai/burn/issues/5481), initially attractive as checkpoint data loss, is already repaired by [#5494](https://github.com/tracel-ai/burn/pull/5494), merged August 27. Actual current-main execution writes the correct extensionless path, refuses a second save, and preserves the checkpoint. The saved diff is `rust-package-review/sources/burn/5494.diff`. Do not revive this candidate.

Other exclusions:

- Burn checkpoint memory usage #5219: fixed by #5349. Several September Flex bugs have exact open PRs; #5607 has an explicit work claim. #4359 download reliability has a stale but explicit public work claim. CPU evidence cannot qualify the reported GPU-only failures.
- Zenoh S3 write slowdown #2617: active #2647. Wildcard deletion #2649: active #2650. Peer-churn deadlock #2581: fixed by #2779. REST SSE leak #2674: does not reproduce on 1.10.1; twenty disconnects retain the original 17 FDs, with a working live-stream control. Async open #2609 does block past a Tokio timeout, but maintainers have announced a redesign; held for overlap and scope.
- Rerun context reuse #12828: current release does save only 1/5 rows while controls save 5/5, but context exit intentionally finalizes the file and API/error semantics are under discussion. Do not invent a contract to force this into a task. Camera #12711 already has a published patch (#12880), and other promising Rerun issues are fixed, already implemented downstream, or lack matched demand.

**Impact and benchmark difficulty are separate.** Zenoh #2614 may be roughly a 20-line repair because peer/client hats already implement aggregation. Rerun #12596 already has suitable row-filter primitives. Preserve natural scope; do not add unrelated requirements to manufacture difficulty or reach five tasks.

**Build setup needed repair, not a change in task requirements.** There is no host Rust installation. Docker is ARM64 with about 8 GB RAM. Burn's upstream ARM assembly required `RUSTFLAGS='-C target-feature=+fp16'`; the corrected build succeeded. Rerun's initial probe had an ambiguous `.into()` and then a missing `rustfmt` component; those were probe/tooling failures, not upstream bug evidence. The final Rerun run compiled and failed on the intended export assertion. Its final log is authoritative; an earlier log was overwritten during retries.

## Exact repository and runtime checkpoints

| Repository | Source pin | Release checked | Runtime |
|---|---|---|---|
| `tracel-ai/burn` | `1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d` | Stable 0.21.0; main identifies as 0.22.0-pre.3. Stable not separately executed. | Rust 1.98.1 chosen explicitly; repository has no pinned toolchain/MSRV field. |
| `eclipse-zenoh/zenoh` | `646f2d1b730e584a570015a77bee9f6db08be9d1` | 1.10.1, separately executed | Repository-pinned Rust 1.97.1. |
| `rerun-io/rerun` | `ddd684110e1f200ee13ba482ab42689b78dd0551` | 0.37.2 Python probes; current-main Rust export probe | Repository pins Rust 1.96.0; initial CPU Rust probe used 1.98.1. |

Available images:

```text
rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa
rust:1.97.1-slim-bookworm@sha256:2775a09d208ff0d7c1f50490c45b62db929e87ba1dcbc3f2132ac71a704bcdd3
```

Use `CARGO_BUILD_JOBS=2` and coordinate heavy builds across workers. Do not prune the user's Docker images, volumes or shared devcontainer. No task-related builds or containers remain running at this checkpoint.

### Burn

- Reproducer and locked dependencies: `research/rust-package-review/burn-repro/`.
- Observations: `observed-main.json` and `observed-main.log` in that directory.
- Clean upstream clone: `research/cache/burn-review`.
- Clean construction clone: `research/cache/burn-scoped-checkpoint-remap-work`.
- Docker cache volume: `abundant-burn-cargo`.
- **Only construction artifact:** `candidates/burn-scoped-checkpoint-remap/environment/upstream.tar.gz`, SHA256 `acc08ecc6e83756f77ef4a3a8fd899157f89dba3fcbc40510efc3188b82ed08f`. No instruction, task TOML, Dockerfile, patch, or verifier exists. It is not listed in the ten-task shortlist.

Reproduce from `reinforcement_learning/` on this ARM machine:

```bash
docker run --rm \
  -e CARGO_BUILD_JOBS=2 \
  -e 'RUSTFLAGS=-C target-feature=+fp16' \
  -v "$PWD/abundant-take-home/research:/research" \
  -v abundant-burn-cargo:/usr/local/cargo \
  -w /research/rust-package-review/burn-repro \
  rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa \
  cargo run --locked
```

The `+fp16` flag is ARM-specific; omit on x86. This tests actual shared-remapper behavior and actual Burnpack saves. It does not yet prove scoped policy through either checkpoint loader.

### Zenoh

- Saved portable research: `research/rust-package-review/zenoh-repro/`, including `README.md`, Cargo files, `src/main.rs`, release log, SSE and package-lifecycle probes/results.
- Consolidated evidence: `research/rust-package-review/sources/zenoh-demand-reproduction.json`.
- Clean main checkout: `/tmp/zenoh-demand-audit-20260913`.
- Release checkout: `/tmp/zenoh-demand-release-source`.
- Existing executable: `/tmp/zenoh-demand-repro/target/debug/zenoh-demand-repro`, last built against release 1.10.1. Do not relabel it as a main binary.
- Docker cache: `zenoh-demand-cargo`.
- `candidates/zenoh-queryable-completeness` **does not yet exist**.

Follow the saved reproducer README: mount the selected source read-only at `/zenoh`, the reproducer at `/repro`, build with `cargo build --locked`, then execute in `--network none` using TCP loopback and disabled multicast. The optional `open` argument tests blocking async open. **Run `postrm_probe.py` only in a disposable container** because it intentionally exercises deletion at the daemon's absolute paths.

### Rerun

- Source: `research/rust-package-review/rerun-source/main` at the pinned commit; the temporary integration test is added for the probe, without implementation changes.
- Rust test: `research/rust-package-review/reproductions/rerun/time_selection.rs`.
- Final log and structured record: `time_selection-main.{log,json}` in that directory. The JSON records the exact command and environment. Test execution 0.01 s; final cached build 9.99 s; exit 101 from the intended assertion.
- Release probes: `context_loss.py`, `context_loss-0.37.2.json`, `shutdown_probe.py`, `shutdown_probe-0.37.2.log` in the same directory.
- Docker caches: `abundant-rerun-cargo`, `abundant-rerun-target`.
- Source ledger: `research/rust-package-review/sources/rerun-audit-sources.json`.
- No Rerun Harbor directory or reference implementation exists.

## Resume from here

1. Read the three audits and confirm final checkpoints. Do not repeat repository-wide discovery or count the incomplete Burn directory as a completed task. Refresh upstream state if resuming materially later.
2. Construct the three strongest leads with one owner each. Burn needs a shared scoped policy preserving boolean compatibility, original-prefix semantics and real PyTorch/Safetensors loads. Zenoh needs correct declaration and teardown behavior for local/remote targeting. Rerun needs exact selected rows, aligned sparse columns/timelines, static preservation, immutable source and serialization round trips. Do not prescribe an internal implementation when the public contract suffices.
3. Find two further candidates only if evidence supports them. The shutdown and packaging reserves are not automatically accepted. A root-only initial screen also noted Zenoh #2767 (reconnect query returns zero replies) as an **unverified lead**, while #2734 has a published matching PR #2736. Burn #4549 has a maintainer implementation-review comment. None has a new root reproduction; do not promote these from titles.
4. Build self-contained Harbor packages: clean source/license, pinned toolchain, locked and cached/vendored Cargo dependencies, independent protected verifier tests, reference patch and `solve.sh`, instructions and provenance. Verify genuine baseline failure and reference success, then have another agent audit fairness and meaningful regressions.
5. Run final Harbor oracle=1/no-op=0, with no exception, on frozen final task files. Only then append validated entries to `research/shortlist.json` and update indexes. If creating a comparison plan for the additions, use a **new prefix/lock**; preserve the existing ten-task plan. Do not run paid model trials for the user.

The earlier implementation-owner agent names were reused: `/root/audit_huey_outbox` owns Burn, `/root/measurement_audit` owns Zenoh, and `/root/audit_huey_leases` owns Rerun. All have paused. Their old names do not describe the current assignment. Reuse them if available; otherwise assign new owners from this checkpoint.

## Harbor conventions and existing work to preserve

The original ten are Huey SQLite leases/outbox/redrive; sqlite-utils resumable import/relational merge/schema plan; Luigi generation target/checkpoint task; DiskCache online reshard/snapshot restore. Their **entire directories are hash-bound**, including construction notes. Editing an old task invalidates saved validation and comparison locks. Do not change them as part of this expansion.

Existing tooling:

- `scripts/candidate-bench.py`: `plan`, `run`, `summarize`; planning makes no model calls. It checks final task hashes, oracle/no-op evidence, config hashes and raw model identity. Model success/headroom is not inferred from oracle results.
- `research/benchmark-configs/durable-screen-v1-lock.json`: existing 60-trial plan, ten tasks × two models × three attempts. Fable 5.1/Sonnet 5 at high effort; no model-trial results yet.
- `research/tmp/final-harbor-validation.py`: paired oracle/no-op validation and shortlist bindings. Inspect before reuse; its two concurrent runs may be too memory-intensive for new Rust builds.
- `scripts/prepare-candidate-verifier.py`: **Python-package-specific**. Do not blindly reuse it for Rust.

Use Harbor 0.15.0 and mini-swe-agent 2.4.6 unless explicitly changing and recording the harness. Example validation from `abundant-take-home/`:

```bash
uv tool run --from harbor==0.15.0 harbor run \
  -p candidates/TASK_ID -a oracle \
  --job-name UNIQUE_NAME --jobs-dir research/validation/jobs
```

Run a separate `-a nop` job too. A new candidate needs a separate verifier environment with `network_mode="no-network"`; agent networking remains available for the gateway. Prefetch/build dependencies at image-build time and grade using offline Cargo. Transfer only the declared implementation surface into the fresh verifier and keep manifests, dependencies and independent tests protected where appropriate. The proposed Zenoh transfer surface is `zenoh/src`; disclose this boundary in instructions. Do not claim perfect isolation from malicious submitted Rust/build code.

Preserve gateway configuration in new task environments:

```toml
ANTHROPIC_BASE_URL = "https://take-home-automation.vercel.app"
ANTHROPIC_API_BASE = "https://take-home-automation.vercel.app"
```

Existing tasks use a 7200-second agent limit and 900-second verifier/build limits. Measure Rust needs before choosing new limits; do not silently change old ones. No paid model runs, external PRs, issue comments, emails, or other messages were sent during this research.

The original 36 take-home files in commit `c1ae968` are read-only, including the supplied README, PDF, restaurant task, devcontainer and `scripts/doctor.sh`. Final submission prose in `report/` remains human-written. New notes belong in `research/`, runnable tasks in `candidates/`, and later selected submission tasks can go in `samples/`. All current research/new tooling is uncommitted; do not discard it or commit unrelated workspace changes.

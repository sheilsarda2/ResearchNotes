# Burn candidate: selectively renumber checkpoint layer indices

Research checkpoint: September 13, 2026. This is candidate-selection evidence, not a runnable Harbor package or submission prose.

**Recommend retaining Burn #4716 for the five-task selection.** There is a real model-import report, a maintainer-proposed bounded policy, and now actual PyTorch and Safetensors store/model reproductions on unchanged current main. The natural implementation spans shared index mapping and two store APIs. Model difficulty and the >75-step horizon remain unmeasured.

## Demand and contract

- [Issue #4716](https://github.com/tracel-ai/burn/issues/4716) reports parameter-name mismatches importing a user's model. Its `model_g.flow.flows.{0,2,4}` names must retain gaps, while another sequence needs contiguous numbering.
- The [maintainer explanation](https://github.com/tracel-ai/burn/issues/4716#issuecomment-5576304561) explicitly proposes a per-prefix opt-in/opt-out policy. It is a proposed enhancement to automatic mapping, not an assertion that importing this model is impossible.
- Existing explicit `KeyRemapper` patterns work. The user's original checkpoint and private Discord discussion were not obtained. Our small fixture implements the reported mixed naming constraint with six distinct scalar weights.

## Current source and public overlap

Fresh HEAD is still `1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d`; the local upstream checkout is clean. `sources/` contains fresh issue/comments, HEAD, all 24 open PRs (single page), and repository-wide PR searches for `4716`, `remap`, `contiguous`, and `prefix`. Each search has fewer than 100 results; the saved records carry request URLs/timestamps.

Exact issue PR search returned zero. Relevant search results were inspected: [#4150](https://github.com/tracel-ai/burn/pull/4150) introduced the existing global renumbering; [#4051](https://github.com/tracel-ai/burn/pull/4051) handles enum variant names; neither implements a scoped policy. Open [#5656](https://github.com/tracel-ai/burn/pull/5656) moves the PyTorch reader into a separate crate. Its body and previously saved diff show adjacent reader work, not a change to the shared remapper policy. No matching public implementation/work claim was found in this bounded review. Private work, every fork, and unreferenced branches are not exhaustively covered.

## Executed store integration

`generate-fixture.py` uses real PyTorch `torch.save` and `safetensors.torch.save_file`. PyTorch version is recorded in `fixture-generation.json`. The tiny derived Burn model contains `flow.flows: Vec<Option<Layer>>` at indices 0,2,4 and `encoder.layers: Vec<Layer>` at indices 0,1,2. The absent options stand in for parameter-free positions; this is a naming/loader fixture, not the original neural network architecture.

Expected loaded weights, in destination order: `[10,12,14,20,22,24]`.

| Store / setting | Observed values | Result |
|---|---|---|
| PyTorch, mapping disabled | `[10,12,14,20,-1,22]` | Missing-parameter error; mixed contract fails |
| PyTorch, mapping enabled | `[10,14,-1,20,22,24]` | Missing-parameter error; mixed contract fails |
| Safetensors, mapping disabled | `[10,12,14,20,-1,22]` | Missing-parameter error; mixed contract fails |
| Safetensors, mapping enabled | `[10,14,-1,20,22,24]` | Missing-parameter error; mixed contract fails |
| PyTorch, global disabled + explicit encoder renames | `[10,12,14,20,22,24]` | Pass, no missing/unused parameters |
| Safetensors, same manual control | `[10,12,14,20,22,24]` | Pass, no missing/unused parameters |

`-1` is the destination's initial sentinel. Erroring loads can partially apply parameters; atomic failure behavior is not part of this candidate and is not an extra proposed requirement.

Final authoritative evidence: `store-probe-final.{log,json}`, `probe/src/main.rs`, `probe/Cargo.lock`, and generated `.pt`/`.safetensors` fixtures. JSON binds source, fixtures, code, lock, and raw log by SHA256. The final run used `cargo run --locked --offline` in a network-disabled ARM64 Docker container with Rust 1.98.1 and `RUSTFLAGS=-C target-feature=+fp16`. The flag is ARM-specific.

Run from the take-home directory:

```sh
docker run --rm --network none \
  -e CARGO_BUILD_JOBS=1 -e 'RUSTFLAGS=-C target-feature=+fp16' \
  -e CARGO_TARGET_DIR=/research/rust-package-review/burn-repro/target \
  -v "$PWD/research:/research" -v abundant-burn-cargo:/usr/local/cargo \
  -w /research/rust-cpp-10-to-5/burn/probe \
  rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa \
  cargo run --locked --offline
```

The prior helper-only evidence remains in `research/rust-package-review/burn-repro/`. Initial fixture generation lacked NumPy, and the copied Cargo lock needed offline regeneration; these were probe setup failures, not Burn bug evidence. Final fixtures and locked run succeeded.

## Natural Harbor scope

Allow explicit policy by original parameter prefix; preserve existing boolean behavior/defaults and explicit key-remapping compatibility; exercise real PyTorch and Safetensors loads with distinct tensor values, mixed and nested prefixes, and missing/unused reports. Exact public API remains to be designed. Do not impose an internal algorithm, silently change tensor data, or add unrelated checkpoint durability work.

Additional Burn screen: #3969 is superseded by the backend API change described by its maintainer (#4717); #1312 includes a public fix commit; #4970's discussion resolves the reported workflow using untracked inner-backend inspection and turns to documentation. None is added as another candidate. The earlier overwrite and memory candidates remain rejected as already fixed.

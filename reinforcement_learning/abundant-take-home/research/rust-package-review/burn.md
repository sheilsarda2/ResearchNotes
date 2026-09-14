# Burn evidence review

Checked September 13, 2026. Canonical repository: [tracel-ai/burn](https://github.com/tracel-ai/burn). Current main is `1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d`, September 11. Latest stable is 0.21.0 (May 7); current main identifies itself as 0.22.0-pre.3. Stable and main must be distinguished: a still-open issue on 0.21 may already be fixed on main.

One candidate survives the evidence pass. The second candidate was eliminated by actual current-source execution: its still-open issue was already fixed on main. Neither is qualified by GitHub popularity, and neither has measured model difficulty or a >75-step claim.

| Candidate | Direct evidence and scope | Current gap | Fix / prior-art check | Decision |
|---|---|---|---|---|
| Scoped checkpoint index remapping | [#4716](https://github.com/tracel-ai/burn/issues/4716), April 2, reports a Discord user's model import with `model_g.flow.flows.{0,2,4}` and parameter-name mismatch. Maintainer September 7 explains that some prefixes must retain gaps while another Sequential must collapse them. Exact problem applies to PyTorch and Safetensors stores through one shared remapper. | Source still has one all-or-nothing flag and renumbers every numeric prefix. Neither flag setting satisfies mixed requirements. Actual-crate reproduction confirms neither boolean value satisfies the mixed naming contract, while all-collapsible and explicit-remapping controls pass. | No matching open PR or matching recent closed PR found after body searches; adjacent reader extraction #5656 does not change the remapping function or policy. Existing arbitrary KeyRemapper patterns offer an explicit per-index workaround; disclose this rather than calling import impossible. | Survives: current source gap reproduced; next step is the bounded policy API and actual store/model integration tests. |
| Preserve checkpoints when store path-extension handling is disabled | [#5481](https://github.com/tracel-ai/burn/issues/5481), August 25, maintainer reports a guard on a different path than the writer uses. | **Already fixed on main:** actual save writes exact `model`, refuses the second save, and preserves the checkpoint. | Path-specific commit history identifies [#5494](https://github.com/tracel-ai/burn/pull/5494), merged August 27, commit `84a88b5f`. The open issue is stale. | **Reject: shipped fix.** |

## Candidate 1: index policies must follow the actual model structure

The [September 7 maintainer comment](https://github.com/tracel-ai/burn/issues/4716) gives a concrete distinction: the Burn model's flows preserve parameter-free positions, whereas another sequence collapses absent parameter slots. A global on/off option cannot encode both. This is much better demand evidence than an unrequested new format feature: a real import produces mismatched parameter names and disabling the existing option still breaks the other sequence.

There are limits: the underlying Discord discussion was not accessed, the exact model checkpoint was not downloaded, and the maintainer explicitly labels their diagnosis as source reading. The proof fixture therefore mirrors the reported naming constraints; it is not the original user's full model.

Relevant source at the pin:

- [shared remapper](https://github.com/tracel-ai/burn/blob/1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d/crates/burn-store/src/keyremapper.rs), particularly `map_indices_contiguous` and original-prefix collection;
- [PyTorch store](https://github.com/tracel-ai/burn/blob/1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d/crates/burn-store/src/pytorch/store.rs), default mapping true;
- [Safetensors store](https://github.com/tracel-ai/burn/blob/1414c8a14e5169ef5e5fc67f9b8ab01a25d6352d/crates/burn-store/src/safetensors/store.rs), default mapping false.

A natural task would add a prefix-scoped mapping policy while preserving the old boolean behavior; both stores should share it. Nested prefixes must match original names so a parent renumbering does not change which child policy applies. Actual loaded tensor values, missing/unused reports, and the existing explicit key-remapping interaction should be checked. These are integration consequences of the report, not arbitrary task inflation. The exact public API has not yet been frozen.

## Rejected candidate: overwrite path mismatch is already fixed

The [maintainer's issue #5481](https://github.com/tracel-ai/burn/issues/5481) is still open, but current source calls `writer.auto_extension(false)` after the store resolves its path; loading uses `Reader::from_file_exact`. Independent execution confirmed that an extensionless path remains extensionless, `overwrite(false)` refuses the second save, and the original checkpoint bytes remain unchanged. Default extension handling also refuses the second save.

A path-specific history query traced the repair to [#5494](https://github.com/tracel-ai/burn/pull/5494), commit `84a88b5fd8a76aef3952a48861ddd9ce76dfda50`, August 27. It was not found in the first generic latest-100-closed-PR scan. This illustrates why a fresh open issue plus a broad PR search is insufficient: direct source and execution changed the decision.

## Reproduction setup and status

The [burn-repro project](burn-repro/) imports actual unchanged current crates, not copied algorithms. It tests the shared remapper and actual Burnpack save operations using a derived model with a tracked weight. The original probe incorrectly used a bare constant tensor, which is not collected as a parameter; that probe failure is retained separately and is not evidence of an upstream failure. The upstream clone is `research/cache/burn-review`, kept clean. A separate Cargo workspace avoids inheriting example feature unification, itself a known current issue (#5657).

Pinned Docker image: `rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa`. The repo has no rust-toolchain file or declared workspace MSRV; current general CI's previous toolchain is 1.95.0. Rust 1.98.1 is an explicit build choice. Compilation uses `CARGO_BUILD_JOBS=2` and a per-repository Cargo cache volume. Source/download/build cost is real and will be recorded before proposing Harbor time limits.

**Execution completed.** [Raw run log](burn-repro/observed-main.log) and [machine-readable observations](burn-repro/observed-main.json):

| Check | Observed result |
|---|---|
| Shared global mapping enabled on mixed preserve/collapse prefixes | Wrong preserved-prefix names: `flows.2` becomes `flows.1`, `flows.4` becomes `flows.2`. |
| Shared global mapping disabled | Correct flow names but uncollapsed encoder names. |
| Positive control: every sequence wants collapse | Pass. |
| Existing manual per-index KeyRemapper workaround | Pass. This is a usable existing workaround, not an unfixable import. |
| Previously reported overwrite bypass | Does not reproduce: exact extensionless file created, second save refused, old bytes unchanged. |

Initial compilation without a target flag failed inside upstream `gemm-f16` inline assembly on ARM. This Docker host advertises `fphp`/`asimdhp`; setting `RUSTFLAGS=-C target-feature=+fp16` resolves compilation without a source patch. That flag is ARM-specific and must not be blindly applied on x86. The successful cold build took about 61 seconds after dependency download; final incremental build took 6.64 seconds. No GPU behavior is inferred, and the stable 0.21 release has not separately been executed.

Re-run from `reinforcement_learning/` on this ARM host:

```bash
docker run --rm -e CARGO_BUILD_JOBS=2 -e 'RUSTFLAGS=-C target-feature=+fp16' \
  -v "$PWD/abundant-take-home/research:/research" \
  -v abundant-burn-cargo:/usr/local/cargo \
  -w /research/rust-package-review/burn-repro \
  rust:1.98.1-bookworm@sha256:9a73a5088750b4c95158ab26629c854c3d6fc4b173cb7bc8079ad252d8ed7bfa \
  cargo run --locked
```

The parent selected exactly one new candidate, `burn-scoped-checkpoint-remap`. Construction was paused for the user-requested handover after creating only a clean source archive and an unchanged separate worktree. No policy implementation, verifier, Dockerfile, or task.toml exists yet. Frozen existing tasks remain untouched.

## Rejected or deferred leads

| Issue / tempting candidate | Why it is not eligible now |
|---|---|
| [#5219](https://github.com/tracel-ai/burn/issues/5219), eager materialization of large checkpoint tensors | Already fixed by [#5349](https://github.com/tracel-ai/burn/pull/5349); fresh issue page is closed and current source has deferred tensor providers. A stale issue search had still shown it open. |
| [#5192](https://github.com/tracel-ai/burn/issues/5192), f32/f64 device setting conflict | Open patch PR [#5210](https://github.com/tracel-ai/burn/pull/5210) explicitly targets 0.21; maintainer says default backend element types were removed by #5000 on main. |
| [#5607](https://github.com/tracel-ai/burn/issues/5607), imprecise SIMD layer normalization | September 9 comment explicitly claims work. Fails the no-public-work-in-progress criterion before reproduction is worthwhile. |
| #5604 avg-pool gradients / #5606 indexed bounds / #5609 NaN clamp / #5610 remainder / #5612 attention parallelism | Current open PRs #5653 / #5640 / #5658 / #5652 / #5636 respectively already implement them. |
| [#4359](https://github.com/tracel-ai/burn/issues/4359), unreliable download of a 14.96 GiB model | Strong actual-user evidence and current downloader still unwraps body errors, but April 8 commenter says they picked it up. No linked PR found; activity is stale, yet cannot claim nobody is working on it. Maintainer calls it a lower-priority quality-of-life improvement. |
| [#5399](https://github.com/tracel-ai/burn/issues/5399), default atomic save | Opt-in atomic implementation already shipped; remaining question is changing a default and its compatibility/cost policy. Too easy to overstate as absent crash-safe storage. |
| [#5590](https://github.com/tracel-ai/burn/issues/5590), full-model pickle; [#5594](https://github.com/tracel-ai/burn/issues/5594), config defaults; [#5595](https://github.com/tracel-ai/burn/issues/5595), tensors in lists | Real reader limitations identified in another review, but direct workload demand is less clear and a large reader extraction PR #5656 is active. Not used as substitutes merely to meet a count. |
| [#5162](https://github.com/tracel-ai/burn/issues/5162), LoRA gradients NaN on Metal; [#5212](https://github.com/tracel-ai/burn/issues/5212), training memory growth | Concrete workloads, but reported GPU paths require matching hardware/current reproduction; CPU tests cannot qualify them. Recent autodiff/fusion changes also need careful supersession checks. |
| [#5651](https://github.com/tracel-ai/burn/issues/5651), hostile MNIST allocation | Tiny guard fix with a public proposed patch; report's reproduction copies function body, and no real workload incident was reported. Does not satisfy the desired substantial task profile. |

## Scope of freshness search

Used root-collected current GitHub envelopes: 307 open issue/PR records, all 24 open PRs, 100 recent closed PRs, HEAD, latest stable release, recent commits. Relevant issue comments were fetched separately under `sources/burn/`. Open PR titles were only a filter: matching/adjacent bodies were read and the reader refactor diff downloaded for overlap inspection. Older historical PRs, private work, all forks and the private Discord thread are not exhaustively covered. Exact “no fix in progress” claims remain bounded by this public evidence.

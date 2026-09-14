# Benchmark freshness and limits

AI research working notes, checked 2026-09-13 UTC. These notes support candidate selection and experimental design. They are not the human-written take-home report, and no published result below measures any of our ten original feature contracts.

## What changed the selection

The initial SQLAlchemy migration suggestions were historical targets with public implementations. [SQLAlchemy-Continuum added support in 1.4.0](https://github.com/sqlalchemy-continuum/sqlalchemy-continuum/blob/main/CHANGES.rst) (2023), [sqlacodegen had a 3.0.0rc3 release](https://pypi.org/project/sqlacodegen/3.0.0rc3/) in 2023, and [dataset now has a 2.0 release](https://pypi.org/project/dataset/2.0.0/) (April 2026). Replaying those original migrations would have substantial solution overlap. Current project source audits instead selected new, explicitly bounded contracts on pinned repositories. See `huey-candidates.md`, `sqlite-candidates.md`, and `workflow-candidates.md` for project-level findings and disclosed partial solutions.

## Current benchmark evidence

| Source and observation | What it supports | What it cannot establish |
|---|---|---|
| [FrontierSWE v2, September 2026](https://www.frontierswe.com/blog/v2): 34 tasks, a 20-hour Proximus harness, Fable 5.1 aggregate 56.29% with Opus fallback for content-filtered tasks. Four v1 tasks retired for saturation or inability to score deterministically. | Current results and verifier versions matter; older aggregate figures become stale. | A failure probability for our Python feature tasks, or comparability to mini-swe-agent with a two-hour limit. |
| [Crash-Proof Flash Filesystem task](https://www.frontierswe.com/tasks/flash-fs): current table reports Fable 5 score 0.9709, Fable 5.1 0.7779, Opus 5 0.8559. Fable 5 averages 481 steps; Fable 5.1 249. Scores combine task correctness/efficiency and are not binary solve rates. | Strong agents can perform very well on adjacent crash-recovery work. This is counterevidence to assuming durability implies unsolved work. | Supersession of a specific new Huey, Luigi, sqlite-utils, or DiskCache API. The language, implementation scope, harness, scoring, and time budget differ. |
| [SWE-Refactor-Bench leaderboard](https://lab.einsia.ai/swe-refactor-bench/leaderboard/), accompanying [paper](https://arxiv.org/abs/2608.23564): low acceptance rates on the published suite. | Large refactors can expose compatibility failures. | Clean contemporary headroom without checking grader corrections; aggregate pooled rates are not the strongest configuration's rate. |
| [SWE-Marathon current site](https://swe-marathon.vercel.app/): v1.1 reports materially stronger results than the original release, including Opus 5 at 50%, Fable 5.1 at 45.6%, and Sonnet 5 at 30% under Claude Code. | Version and harness must accompany every quoted score. | Difficulty of our contracts, or mini-swe-agent performance. |
| [DeepSWE](https://arxiv.org/abs/2607.07946), July 2026: original specification-based engineering tasks and verifier design. | A useful precedent for original task contracts rather than replaying public fixes. | Our tasks' empirical hardness or a guarantee that generated verifiers are correct. |
| [METR time horizons](https://metr.org/time-horizons/) and [TH1.1 update](https://metr.org/blog/2026-1-29-time-horizon-1-1/). | Human task duration and agent action counts are distinct; confusing/gameable tasks can invalidate measurements. | Interpreting >75 assistant steps as a particular number of expert-human hours. |

Our inference is that cross-boundary integration merits a pilot, not that it is an established frontier weakness. Near-perfect performance on the adjacent flash task raises the risk that some of these smaller Python tasks will prove saturated. Keep easy candidates in the screening record and replace them after pilot evidence rather than padding their contracts.

## Verified correction and new reported grader problems

The SWE-Refactor-Bench maintainer [confirmed four reproductions and the fw05 score impact](https://github.com/Einsia/SWE-Refactor-Bench/issues/8#issuecomment-5616543994) on September 10. The first three involved release-time recording drift; fw05 included filesystem-dependent expectations. The maintainer said fw05's published scores were depressed and would be rerun. These cases must not be generalized into a claim that every published score is invalid. [PR9](https://github.com/Einsia/SWE-Refactor-Bench/pull/9) contains repairs. The exact API response is saved in `sources/refactor-issue8-comments.json`.

The live issue listing also contains these reports, checked September 13. We read the issue bodies and their cited mechanisms, but have not independently rerun the full submissions or assigned corrected final scores:

- [Issue10](https://github.com/Einsia/SWE-Refactor-Bench/issues/10): contract ambiguity around optional checks represented as positive-weight skips.
- [Issue12](https://github.com/Einsia/SWE-Refactor-Bench/issues/12): a broad name matcher can choose a NOEXECSTACK capability variable instead of an SSP option. The reporter reproduced selection, not successful behavior under the correct switch.
- [Issue13](https://github.com/Einsia/SWE-Refactor-Bench/issues/13): exact header-string comparison appears inconsistent with the task's order-insensitive clause contract. Passing the repaired check would only enable subsequent grading.
- [Issue14](https://github.com/Einsia/SWE-Refactor-Bench/issues/14): a truncated human log is reused as machine-readable Cargo JSON. Synthetic data reproduces data loss; complete original stdout was unavailable for exact attribution.

Saved metadata and bodies: `sources/refactor-issues-current.json`. Treat open reports as reports, distinct from maintainer-confirmed corrections.

## Consequences for this screening pack

1. Freeze source, dependencies, public contracts, tests, reference solutions, and per-comparison checksums. Disclose public feature overlap in each provenance file.
2. Require Harbor oracle=1 and nop=0, inspect positive test collection, and reject unexpected skips/errors. Reproduce state transitions, process interruption, and corruption rather than comparing reference source text.
3. Use a fresh offline verifier that receives the submitted package source, with original upstream regression tests restored from the pinned source. Deliberately broken implementations test important verifier assertions.
4. Record exact model identifier, reasoning effort, mini-swe-agent version, task checksum, failures, exceptions, cost, runtime, ATIF assistant-step count, and tool-call count. Preserve original Harbor jobs and trajectories.
5. Start with three independent trials per model/task at a fixed effort. This is screening, not a precise success-rate estimate. Expand promising candidates; inspect failures before calling them capability gaps and inspect successes for shortcuts or verifier manipulation.
6. Retain the final three using observed success rates, genuine failure mechanisms, and successful trajectories exceeding 75 steps where the assignment requires that horizon. Neither line count nor test count establishes horizon. A failed agent that loops for 75 steps is insufficient evidence of necessary work.

No finite search rules out newer unpublished work. Research records state the checked date and scope. Before a later benchmark campaign, refresh upstream HEAD/release/PR metadata and task-level strongest-model results; use a new frozen pack/version if a substantive correction changes a task.

The final [independent freshness audit](independent-freshness-audit.md) adds current repository/fork coverage and task-level SWE-Marathon counterevidence. In the publisher's September 4 records, Fable 5.1/max solves zstd-decoder 8/8 with 31–62 reported steps; that harness's step convention differs from our ATIF count. This is another reason to measure horizon rather than infer it from specification size. Exact configurations and source bundle hashes are preserved in that audit.

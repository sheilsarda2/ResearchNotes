# Proposed scaling design

These are proposals for the user's report, not measured production results or a claim that training has already improved a model.

| Stage | Public input or method | Acceptance evidence |
|---|---|---|
| Source | Rerun recording/storage changes; Burn checkpoint and tensor-format changes; Zenoh protocol/API changes. Use issues, merged PRs, commit stacks and public tests. | Pinned base, source links, license metadata, a substantive reproducible change, and an independently stated contract. |
| Construct | Remove the implementation from the base; retain necessary dependency declarations. Compose compatible dependent changes or vary format/layout combinations. | At least one real integration boundary; avoid padding steps with repetitive edits or incidental build work. |
| Verify | Outcome tests for data preservation, compatibility and failure handling; compile public clients; exercise known-bad variants. | Oracle succeeds; no-op fails; plausible alternate implementations pass; deliberately broken behavior fails; tests map to explicit instructions. |
| Calibrate | Run fixed model/effort cells and inspect failures before scaling. Measure successful-run steps, tool calls, work phases and human time on a sample. | Distinct implementation failures, valid grading and substantial work; report repeats and uncertainty. |
| Split | Deduplicate PR stacks and task templates; hold out repositories or feature families. | Training and evaluation do not share the same patch, narrow bug, fixture family or near-duplicate instruction. |

Start with batches of 25 and 100 accepted tasks. Measure source-to-acceptance yield, authoring/review time, invalid-test rate and execution cost before committing to 1,000. For illustration, a 20% acceptance yield would require 5,000 screened candidates; that yield has not been measured here.

The proposed learning target is checking an implementation against the whole contract: derive obligations, exercise module boundaries, investigate failures and complete the integration. Binary task reward remains intact; verifier group attribution can diagnose learning without retrospectively relaxing pass criteria. Test the training hypothesis against held-out families and a fixed pre-training baseline before claiming improved model capability.

Public source examples inspected for this preparation:

- [Rerun optimizer source commit](https://github.com/rerun-io/rerun/commit/bef4ed8d920e58a6355496abe93ad45a188567a8): separates index-based planning from execution and explains why merged size must be measured.
- [Burn reader PR](https://github.com/tracel-ai/burn/pull/5593): the upstream change used for the checkpoint-reader candidate.
- [Zenoh timestamp PR](https://github.com/eclipse-zenoh/zenoh/pull/2620): source recorded and pinned in the task provenance; browser retrieval failed during this preparation, so details rely on the local pinned source and provenance.

External measurement context:

- [SWE-Bench Pro, v2](https://arxiv.org/abs/2509.16941v2) describes complex multi-file software tasks. It motivates the task family but does not establish results for these tasks or models.
- [METR, Measuring AI Ability to Complete Long Software Tasks, v4](https://arxiv.org/abs/2503.14499v4) defines a human-time-calibrated completion horizon. Assistant turns in this pilot are not that metric; no human duration is inferred from them.

- [SWE-Marathon, June 2026 paper](https://arxiv.org/abs/2606.07682v1) reports 20 tasks and fewer than 30% solved by the agents evaluated in that paper. Its failure categories motivate our narrower self-verification probe; its rates are not comparable to this pilot.

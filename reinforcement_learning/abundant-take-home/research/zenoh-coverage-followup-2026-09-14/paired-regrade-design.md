# Pair original Zenoh v3 attempts with the corrected grader

Status: provenance design, not a claim that the new revision or regrades have passed. Keep the 99-cell scope and historical v3 rewards unchanged. A tests/reference-only correction can evaluate the original submissions without new model generation, provided the agent-facing task inputs remain identical and the relationship is explicit.

## Three identities, three distinct records

1. **Original attempt:** retain the complete original Harbor job/trial directory and v3 task identity. The historical Harbor checksum is `4e4a5fa7e33f699c6c46ccf1e4651439378ad4f4eaa0c2cc36753ec14593d3f2`; the frozen whole-directory digest is `2529942aca5c15f1d1f414a917317ac76e02582de23012badfb11e55e966959e`. Its original reward, model, effort, steps, cost and terminal time remain authoritative for the frozen sweep.
2. **Final candidate:** freeze the separately named corrected revision, full file manifest, Harbor checksum and whole-directory digest. New tests and corrected reference necessarily change this package identity. Its exact reference/no-op controls and substantive quality rubric belong to that new identity.
3. **Paired regrade:** a separately named verifier-only execution links an original submission to the corrected grader. Record `kind=paired_verifier_regrade`, `model_calls=0`, `counted_sweep_trial=false`, original trial/checksum/reward, submitted-tree hash, corrected task/grader hashes, regrade reward and each group outcome. It is neither another model attempt nor an overwrite of the old result.

## Agent-facing identity proof

Create a byte-level comparison of old and final `instruction.md`, every file under `environment/`, and `task.toml`. Keep the declared agent time/resource limits, environment variables, artifact collection paths, base checkout, dependencies, tools, workdir and visible helper files unchanged. Enumerate every changed file; permit only reviewed verifier/reference/provenance changes. Bind the reasoning for any metadata-only difference separately, without describing the full task as byte-identical.

The original anchors are already available:

| Input | SHA-256 |
|---|---|
| v3 instruction.md | `31ea9c624ba8ab89828aaa90c7e4a63efa945c65ad5fe64e6e6ccb26668fb85b` |
| v3 task.toml | `cabf63e79fb10644d52eec42a0678f6d15c25f7a030f67f8c82e88d1985c5cc2` |
| environment/Dockerfile | `46a66c0f688df2c0ece0eea5736759ee2b80be83697b0f360a750d10ea14e1aa` |
| environment/upstream.tar.gz | `3e4d52aa66d566a42426b8177c01eaab827421a9485f83d8f12493cb88073a2e` |
| First user prompt, both inspected successes | `12991f552c4bc94644cc2c50207728d46cffc5d19d3b15cd267a9b021624ec3c` |

The Sonnet/max `gSYDMEG` and Fable/max `QYtUfZA` native trajectories contain the exact instruction text in their first user prompt. Sonnet's saved config has multiplier1, no extra instructions, no resource overrides, mounts, extra compose files, skills or MCP servers. Its agent model is `anthropic/claude-sonnet-5`, effort max. Audit these fields for every paired trial; hash full config files while publishing only safe configuration fields. Preserve runtime/tool preflight records and original native/ATIF prompts and trajectories.

The agent Dockerfile copies only its upstream archive; tests and reference are outside that build context. Therefore a reference-only fix is not new information supplied to the model. Source/context equality does not prove every runtime byte was identical: the inspected raw trial does not provide a verified immutable agent image ID, and the Dockerfile includes package installation. Claim identical task files and declared configuration; retain any existing image/runtime evidence and disclose its limits. No new agent environment is needed to regrade an existing submission.

## Regrade execution and packaging gates

Use the original pre-verifier source snapshot: the four collected source trees under `commons/zenoh-protocol/src`, `commons/zenoh-codec/src`, `zenoh/src`, and `zenoh-ext/src`. Check all source files against the original benchmark evidence manifest, copy them to an isolated grading workspace, restore the corrected verifier's pristine dependencies/tests, and preserve originals before/after. Record exact verifier image ID, commands, resources/deadline, compilation, complete test/group results, cleanup and failure classification. A focused new-test probe is not a full final-grader pass. Full paired results require the entire corrected verifier and unchanged resource limits.

Pair all nine earliest-counted Zenoh cells once available, including failures, to avoid selecting only submissions that pass the new checks. Initial two-source diagnostics may establish feasibility but must stay labelled partial. Original cost/steps are reused by reference; regrade elapsed time is separate. Corrected successes may have different membership and medians, which must be displayed as a separately labelled regraded analysis. The current 99-cell charts remain v3 data.

Current `scripts/package-takehome-evidence.py` (reviewed SHA `7f643cece1099f4757740fc45f2d79020fb3544c9a6906fefebea22f6f132279`) deliberately couples retained packages to frozen campaign checksums: `verify_selection`, `control_sources`, `make_plan` and `verify_plan` reject silent substitution. Keep raw selection/checksum gates. A future explicit mapping must separate historical selected task IDs from final package paths, validate the paired identity proof, and bind final controls to the final package rather than the old campaign definition. Archive the unchanged v3 task and all other unretained revisions. Adding a revision also changes the discovered task inventory; generate a new packaging plan and preserve prior plans.

Preserve the four main areas: corrected candidate under `samples/`; original Harbor attempts under their unchanged `jobs/<original-job>/<original-trial>/` paths, including Sonnet5; old task/regrade provenance in a clearly named `archive/` evidence location (or the explicitly declared supporting bundle); human-authored report under `report/`. Never manufacture a new Harbor model result for a custom replay. The report should say “original v3 Harbor attempts, paired with the corrected verifier under unchanged agent-facing inputs,” with both outcomes visible.

## When another model attempt is actually necessary

No fresh model call is inherently required merely because hidden tests or an unseen reference implementation change. It is required to claim a *new final-revision model attempt*, or if a contractual requirement explicitly demands such an attempt/checksum. It is also necessary for a fair final-task outcome if instructions, visible repository/dependencies/helpers, tools, permissions, budgets or task behavior change in a way the old agent did not receive. Missing/unverifiable original submission inputs prevent reliable regrading. Adding tests for undocumented requirements is not repaired by relabelling old results.

Actual pending gates are freezing the final corrected package, proving unchanged stimulus, validating its reference/no-op and quality findings, and executing complete paired regrades. Those are not themselves evidence that a fresh paid attempt is required. Existing v3 controls and source inspection cannot substitute for these gates.

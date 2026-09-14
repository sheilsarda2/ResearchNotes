# Optional final Zenoh revision in the review pack

This is packaging-tool documentation, not report prose or a claim that pending Zenoh validation has passed. No real final-revision mapping is supplied here. The existing 82-cell plans and their captured inputs remain historical snapshots.

The default packager keeps the original provisional shortlist. The optional `--final-revision-mapping PATH` argument is accepted only with `--plan-only` and only after all referenced evidence exists. It captures that mapping in the new plan's immutable inputs. Assembly revalidates the mapping and every source, and still requires all 99 frozen cells. A partial mapping, partial replay, or successful focused probe cannot produce a mapped plan.

## Mapping schema

The input is a JSON object with `schema_version: 1`, `kind: "final_revision_packaging_mapping"`, and a one-element `mappings` array. The supported historical task ID is exactly `rs-zenoh-timestamp-instrumentation-v3`. Each entry requires:

| Field | Required evidence |
| --- | --- |
| `historical_task_id` | The original v3 task ID, which continues to identify the selected model trials. |
| `final_task_manifest` | A `{path, sha256}` reference to the final task manifest containing `task`, `harbor_task_checksum`, and the complete `task_file_sha256` map. |
| `revision_lineage` | A `{path, sha256}` reference to `revision.json`, binding parent and corrected task paths, both file maps, every changed file, unchanged visible inputs, and the reference failure/correction artifacts. |
| `reviewed_changed_files` | The sorted, exact list of changed or added/deleted files. Only verifier, reference, `STATUS.md`, or `provenance.json` changes are accepted. |
| `paired_inputs_manifest` | A `{path, sha256}` reference to the captured original nine earliest-counted v3 submissions, including original failures. |
| `controls_summary` | A `{path, sha256}` reference to the corrected task's complete immutable oracle/no-op controls. |
| `final_quality` | A `{path, sha256}` reference to the completed corrected-task quality audit. |
| `paired_regrades_summary` | A `{path, sha256}` reference to all nine complete saved-source regrades with the corrected full verifier. |

All evidence references use repository-relative paths. No symlink, traversal, destination collision, or content drift is accepted. A changed task checksum is explicit; it never replaces the historical model-trial checksum.

## What the gates check

The old v3 directory must still match its frozen campaign digest. Old and corrected `instruction.md`, `task.toml`, and every file under `environment/` must match byte for byte. The corrected full file map must match its manifest and lineage. The lineage also binds the original gold failure result/log and the precise `solution/admin-reply-stack.patch` correction.

Each original input is linked to the frozen collector's earliest-counted result for its cell. The gate rehashes original raw files, the entire saved submission, and the pre-verifier source snapshot. It checks the original prompt/instruction marker and declared configuration, including task path, extra instructions, skills/MCP, mounts, and resource/time overrides. These checks preserve the original outcome and do not turn a regrade into a new model attempt.

The corrected oracle and no-op must have terminal, exception-free raw results, zero model usage, expected rewards 1/0, matching task/config identities, all 11 verifier groups, unchanged raw artifact manifests, and completed admission cleanup. The oracle must execute all 47 original instrumentation tests, five robustness tests, both admin tests, six gold interop tests, and four Python parity tests without skips. Existing historical v3 controls remain required independently.

The quality gate rehashes the actual delivered task copy, raw review trial, and full check report. It requires all 11 rubric criteria to pass and the established Sonnet 5/high/Mini 2.4.6 identity. A successful review execution or reward does not conceal a failed criterion. The review is separate supporting evidence and does not count toward the 99-cell sweep.

The paired summary must identify all nine originals exactly once, with `kind: "paired_verifier_regrades"`, `validation_passed: true`, `all_regrades_complete: true`, `model_calls: 0`, and `counted_sweep_trials: 0`. Each case must identify itself as a complete `paired_verifier_regrade`, retain the original result/input-record/source hashes and reward, bind the corrected checksum, and include equal before/after source and full test-file maps. Its raw result, score, logs and all other case artifacts are hashed and copied separately. The raw verifier-process record must show normal full-verifier completion without timeout; its captured input maps must match the summary. All 11 score groups must be present; valid regrade rewards of either zero or one are accepted. Regrade success is not required for provenance validity.

The summary's controls reference must be exactly the mapping's controls reference. Its verifier image proof must be the proof bound by the normal oracle; the filtered Docker inspection must identify that oracle's compose project and image. Every regrade must use the same immutable image and observed swap/storage settings. The summary also binds its run inputs and six distinct omission probes to those controls. Their raw artifact hashes, expected runtime assertion signatures and cleanup are checked; these probes remain separate from the nine full regrades.

The paired resource schema matches the execution harness: declared four CPUs, 8192 MB RAM, 30720 MB storage, `verifier_timeout_seconds: 3600`, `network_mode: "no-network"`, local cap 1 and shared cap 14. Docker observations use `network_mode: "none"`. An unconfigured disk quota is represented truthfully as `storage_mb: null` with the captured empty/null `storage_opt` and explanatory `storage_note`; it is not asserted to be an enforced 30720 MB quota. The case must prove both container and claim absence after execution.

## Output identity and remaining work

Only the explicitly mapped final task is added outside the three existing task-discovery roots. Validation job copies are not recursively discovered. The corrected task is copied exactly to `samples/<corrected-basename>/`; the unchanged v3 task goes to `archive/<v3-basename>/`. All other built task directories keep their existing archive treatment. Basenames must be unique across samples and archive, including case/Unicode normalization.

Raw model attempts remain under `jobs/<original-job>/<original-trial>/`, with original filenames, results, native/ATIF trajectories, verifier artifacts and saved source. Original and paired rewards appear separately in the plan's lineage evidence. Full final controls, quality artifacts, paired inputs/regrades and lineage proofs go in the supporting evidence bundle under their original workspace paths. No synthesized Harbor result is written into the raw jobs tree.

Runtime history has practical limits: the original captured task/config equality does not establish an unrecorded immutable agent-image identity. Existing weak identity evidence and missing optional runtime markers remain visible in the mapped plan. Packaging verifies evidence consistency; it does not independently recreate runtime execution or judge reviewer explanations.

After the new controls, quality audit, nine full regrades and final 99-cell collector are ready, the coordinator can author the concrete mapping and a fresh plan filename. The packager must not be given a placeholder mapping with invented hashes. Neither planning nor assembly writes report prose. The take-home still needs the user's human-written report; even an assembled bundle is labelled a review pack until its report and content are reviewed.

Regression checks are in `test_packager.py` and `test_final_revision_mapping.py`. The latter uses temporary synthetic evidence only, including changed original/regraded outcomes; its fixtures are not benchmark observations.

# Full paired verifier regrades — scripts awaiting review

No execution is authorized by the presence of these files. The controller defaults to a file-only check; `--run` is explicit. It must run under the existing Harbor Python interpreter in the Linux devcontainer after coordinator review and final controls/probes have passed. No model provider is invoked.

Required arguments:

```
python -B run_paired_regrades.py --check-only \
  --task-manifest ../task-manifest.json \
  --controls-summary <exact-final-controls-summary.json> \
  --diagnostics-summary <completed-six-mutants-summary.json> \
  --verifier-image-id sha256:<exact-normal-oracle-verifier-image-id> \
  --verifier-image-proof <verifier-image-proof.json>
```

Use `--run --output <fresh-directory-under-paired-regrades>` only after the complete check passes and the coordinator has reviewed this harness. Do not replace an existing run directory. `--admission-timeout` bounds each queue wait (default six hours); queue waiting does not consume the verifier's workload budget. A filesystem lock prevents two queues from running concurrently. Every case takes one ordinary shared claim at the existing cap 14, with no priority or cap changes. The frozen admission helpers are loaded from `immutable-tooling-v1`.

The gate requires the frozen original nine-cell manifest (SHA256 `93f4f62c362feac77e1ce6ce82070aa52a8fc2e8a4b504a4ed337e5c6c0efc7d`), exact original source/capture/prompt records, equal old/final agent-facing files, the exact final whole-task checksum and file map, normal oracle 1/nop 0 controls, six successful omission probes, and an image proof from the normal oracle verifier. The image proof binds filtered Docker inspection to the final checksum and original oracle result. No old-image test overlay is permitted: the image's complete `/tests` file map must already equal the corrected task's `tests/` map before execution.

Each new container receives only the original four collected source trees, copied to `/workspace/repo`. It executes the final `/tests/test.sh` in full, including all nine original groups and two follow-up groups. The existing fail-rest behavior is retained. A complete reward 0 is a valid paired result; the harness does not expect the original eight successes to remain successes. An outer verifier timeout or incomplete score is recorded as incomplete execution and stops the queue, without manufacturing a score or counting a new sweep attempt.

Runtime resources are four CPUs, 8192 MB RAM, no network, and 3600 seconds for the complete verifier process. The swap setting matches the observed normal oracle. The task still declares 30720 MB storage; Docker `StorageOpt` is captured and compared with the oracle. An absent quota is reported as observed `storage_mb:null`, never as enforced 30720 MB. Replay setup is separately bounded at 180 seconds; workload termination has a short grace period; cleanup is bounded at 120 seconds. The container is stopped before output collection, removal is attempted independently of collection failures, and a shared claim is released only after Docker confirms the uniquely named container is absent. Failed absence proof retains the claim for recovery and stops the queue.

Each case's `result.json` retains the original and final identities, full-grader flag, actual reward/group outcomes, source/test maps before and after, declared/observed limits, and cleanup proof. The outer summary additionally includes `result_path`, `result_sha256`, and `artifact_file_sha256`, a hash of **every file** under that case's `artifact_root`, including its result, logs, copied source, request, driver and local control. The file map is deliberately outside the raw result to avoid self-reference. Source-map paths are relative to the collected submission root; test-map paths begin `tests/` and are relative to the task root. Artifact-map paths are relative to `artifact_root`. All original job paths and scores remain unchanged.

`validation_passed` and `all_regrades_complete` describe execution/provenance completeness, not all rewards being 1. Quality review and final packaging remain separate gates. Historical missing tool-runtime markers and unverified original agent image identities remain documented in `paired-inputs`; this harness cannot reconstruct them.

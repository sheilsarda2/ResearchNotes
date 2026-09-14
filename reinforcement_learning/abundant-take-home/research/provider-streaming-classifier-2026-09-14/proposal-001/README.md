# Offline classifier proposal — no deployment

The unchanged classifier labels the terminal Luigi streaming canary `scored` with reward 0. The copied proposal labels it `infrastructure` with detail `MidStreamFallbackError`, leaves `raw_reward` at 0, and leaves the counted reward unset. This is a classification correction, not a task regrade or a transport fix. No original result, campaign record, source helper, control, or plan was edited.

The saved trial is `jobs/streaming-canary-luigi-fable-max-20260914-001/luigi-generation-target__RigDBsV`. Its outer Harbor error is `NonZeroAgentExitCodeError`; native Mini terminal metadata records `MidStreamFallbackError`. The verifier completed with 9 of 57 checks passing and raw reward 0. Independent terminal evidence establishes 27 HTTP attempts: 17 complete responses and 10 incomplete attempts, separated by nine retry delays for the failed logical query. The recorded completed-response cost is $0.75210325; incomplete-request charges remain unknown.

## Cause and proposed change

The original `inspect()` gateway regex sees only Harbor's generic exception message. Its structured native-type check uses `common.INFRA_ERRORS`, which includes `ServiceUnavailableError` but omits the concrete subclass name `MidStreamFallbackError`. It subsequently accepts the completed verifier outcome through `validate_abnormal_exit`.

The captured Mini 2.4.6 source at `package-source/mini-default.py:74–84` writes `type(e).__name__` as the terminal `exit_status`. The exact captured LiteLLM 1.100.1 source at `package-source/litellm-exceptions.py:1086` declares `MidStreamFallbackError(ServiceUnavailableError)`. Its bytes were checked against the earlier execution-identity manifest, without importing the agent environment or contacting a provider.

`proposal.patch` adds one exact structured pair: outer `NonZeroAgentExitCodeError` plus native terminal `MidStreamFallbackError`. It does not search assistant, tool, submission, or traceback prose. Before the new infrastructure return, it requires guarded evidence and rechecks the existing complete frozen snapshot. This retains the integrity protection that the later abnormal-exit path would otherwise have performed. Unguarded or changed evidence raises into the supervisor's existing `review` handling. Existing timeout and ordinary abnormal-exit behavior is unchanged.

The new classification applies independently of verifier reward. It does not mark every abnormal exit as infrastructure. It does not alter token/cost accounting; the exact saved canary's existing evidence already marks usage censored and possible missing provider charges.

## Validation

`validation.json` binds all inputs, copied sources, test source/log, projections, 196 unchanged raw job files, and all 36 unchanged supplied originals from `c1ae968`. Its SHA-256 is `9b6213df7506af28ea25c1cb0434517ae8fa2c566c37e125948be623e2787476`.

Thirteen pure regressions passed under installed Harbor Python 3.14.7. They cover exact saved baseline/proposed results; ordinary completed-verifier abnormal failure and success; misleading assistant/tool/submission text; near-match terminal names; unchanged agent timeout and existing infrastructure handling; reward-independent stream exclusion; unguarded evidence; changed native status; corrupt result binding; and copied import/source/raw-artifact preservation. Temporary synthetic fixtures contain no real prompts, thinking, signatures, tool commands, or model-produced source. Socket connections, process creation, and shell execution are blocked during test cases.

The final proof command was executed in the existing `keen_black` devcontainer, without creating a workload container or using a shared admission slot:

```sh
docker exec -w /workspaces/sheil_research/reinforcement_learning/abundant-take-home keen_black \
  /home/vscode/.local/share/uv/tools/harbor/bin/python -B \
  research/provider-streaming-classifier-2026-09-14/proposal-001/run_validation.py
```

The proof runner refuses to overwrite its outputs. `test_proposal.py` can be rerun as a pure read-only regression; it only creates temporary synthetic fixtures. The classifier imports are restricted to the nine copied local dependencies. This proof tests classification, not the Python 3.12.11 agent lifecycle or provider-route behavior; those earlier distinct proofs remain unchanged.

## Requirements before any future rollout

1. Review this exact source patch and its classification policy. An existing provider failure is not evidence that streaming resolves the approximately 290-second EOF.
2. Check the deployed classifier and its nine captured local dependency hashes against `input-manifest.json`; rebase and rerun meaningful tests if they differ. The exception mapping depends on the reviewed Mini 2.4.6 and LiteLLM 1.100.1 behavior.
3. Coordinate a separately authorized runtime transition. Current helpers are hash-bound and in-flight processes already hold imported code and cached classifications. Editing a file cannot safely update those processes or invalidate their in-memory cache. Use a reviewed new capture/process and explicit provenance; do not alter frozen snapshots or current controllers to apply this proposal.
4. Preserve historical raw results and projected summaries. Any later summary reclassification must declare the new classifier identity and its evidence, and must not silently replace the frozen take-home cohort. This standalone canary remains outside the campaign, unadopted, and excluded as a transport failure. Neither this proposal nor a passing verifier authorizes another canary, a retry, or campaign adoption.

Parent/root review is required before such deployment; this directory contains only an offline proposal. All existing task framing, instructions, grading, resource limits, runtime settings, retries, and live plans remain unchanged.

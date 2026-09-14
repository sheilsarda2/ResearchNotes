# First final-task quality review: output-placement failure

The reviewer completed 40 assistant turns and submitted, at a recorded cost of $0.7069734. Its normal Harbor result has reward 0 and no agent exception. The official report contains no criterion judgments because `check-result.json` was missing from the artifact collection path.

The generated instruction says not to modify `/app/task` and to write `check-result.json` in the working directory. The unchanged checker configuration sets that directory to `/app` and collects `/app/check-result.json`. Native message 78 instead writes a complete JSON here-document to `/app/task/check-result.json`; the following tool response reports return code 0 and `valid json`. The reviewer then submits without creating the required artifact.

`emitted-review-json.DIAGNOSTIC.json` reconstructs the exact JSON bytes in that saved tool command. They contain eleven pass judgments and satisfy the named criterion/field requirements. They are diagnostic evidence only, not a successful official report or replacement raw artifact. The missing artifact, raw zero, official error, source transcript and stopped continuation are preserved by hash in `diagnosis.json`.

Root authorized one additional canonical review with the same final task, rubric, generated instruction, model, effort, version and resource limits. `retry-authorization.json` bounds that authorization to `reviews-final-002` and a new once-only continuation. No third automatic review is authorized, and the original invalid result must not be relabelled.

# Luigi streaming canary: transport failure, excluded

The one authorized attempt finished at 09:34:55 UTC on 2026-09-14. It completed
17 assistant turns and 17 tool calls, then exhausted ten retries for the next
turn. All ten long responses ended incomplete after 290.347–290.624 seconds.
The checked adapter rejected every incomplete response and closed its captured
connection. Mini used its original backoff and ten-attempt limit. There were 27
observed HTTP responses in total: 17 complete and ten incomplete.

Harbor recorded `NonZeroAgentExitCodeError`; the native exit was
`MidStreamFallbackError`. Harbor subsequently ran the verifier, which returned
zero. The independent outcome is **transport failure, excluded from the cohort**.
It is not a task-quality judgment. No canary adoption, campaign reload, second
attempt or runtime rollout occurred.

The current ordinary classifier would mark the raw result `scored` with reward
zero because it does not recognize this wrapped terminal streaming error.
`run-001/classifier-observation.json` preserves that read-only result. The raw
trial, score, classifier and campaign files have not been rewritten to change it.

Recorded completed-response cost was $0.75210325. Complete-response usage was
437,032 input tokens, including 394,323 cache-read tokens, and 2,395 output tokens.
Both Harbor evidence and this review flag usage as censored: usage and charges
for the ten incomplete responses are unknown. Mini's native counter records 18
logical queries; it does not count the nine extra transport retries separately.

The original task checksum, delivered instruction, first user prompt, model,
max effort, adaptive thinking, 64,000-token setting, cost behavior and retry
configuration were preserved. The declared streaming class and two stream flags
are the explicit transport differences. The 27 captured files, including all 17
validated prototype identities, remain unchanged. The two immutable benchmark
snapshot checks pass, including 173 submitted source files. All 36 supplied files
still match `c1ae968`.

The actual agent container used 2 CPUs and 4,096 MB memory. Its guard retained the
7,200-second budget and recorded clean quiescence at 09:34:22 UTC, well before the
10:40:06 deadline. No post-deadline activity occurred. The verifier ran within its
900-second budget, from 09:34:39 to 09:34:55. Both exact container IDs and the
shared admission claim are absent after completion; all owned runner and
observer identities are gone.

The verifier used a separate Compose project. The live resource listener was
restricted to the agent project, so it did not capture the verifier's actual
HostConfig. Historical Docker lifecycle events establish its create, start,
stop and removal. Its declared resource limits and unchanged Harbor source are
bound, but this review does not invent an observed verifier memory/storage
configuration. No Docker storage quota was observed for the agent either.

The event log records query entry, HTTP response availability and closure; it
does not record individual SSE event times. Thus the repeated cutoff supports a
recurring transport failure but cannot establish whether a fixed gateway limit,
buffering or upstream inactivity caused it. Real nonempty signed thinking also
remains untested: the accepted live responses contained empty signed blocks.

`terminal-proof.json` binds the raw job, source maps and review evidence.
`terminal-artifact-manifest.json` hashes this preparation/evidence directory,
excluding only that manifest itself. The preliminary certificate remains frozen
at `ae7f684d70a3993279df9663b0402266c6b0eaf7e6a9adc23d3c46b9bd821315`;
launch authorization and terminal outcome are separate records.

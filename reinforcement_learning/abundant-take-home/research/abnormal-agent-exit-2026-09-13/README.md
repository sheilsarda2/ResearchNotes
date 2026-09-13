# Completed grading after an abnormal agent exit

The Zenoh C++ connectivity attempt `cpp-zenoh-cpp-connectivity-api__2q8Zzzp`
(Sonnet 5, medium) ended with `NonZeroAgentExitCodeError` after 87 saved model
turns. Its unchanged verifier then completed successfully: reward 1, all nine
expected groups passed. Cleanup completed before grading, the submitted inputs
remained frozen, and the evidence records no activity after the agent deadline.
The supervisor previously classified every otherwise unknown nonzero exit as
review, pausing new admissions at 2026-09-13 20:43:37.738 UTC.

The summary now accepts this class of completed verifier outcome as `scored`,
retaining the raw exception and adding `agent_exit_abnormal` and
`outcome_basis=completed_verifier_after_agent_exit`. The same requirements apply
to reward 0 and reward 1; verified failures remain counted failures and receive
no replacement attempt. Startup and provider failures retain their existing
infrastructure classification. Missing, inconsistent, legacy, or changed
evidence still requires review.

Acceptance requires matching model configuration and version, native and ATIF
model work, complete timings, confirmed cleanup before grading, no recorded
post-deadline activity, unchanged submitted inputs, a completed binary verifier
outcome with matching reward marker/check evidence, and all expected verifier
groups when applicable. The full saved artifact/trace/verifier snapshot is
rehashed for this exceptional path. Raw job records are never rewritten.

The last printed command included `pkill -f "zenohd"`. Because the agent launch
arguments also contained task instructions mentioning `zenohd`, self-termination
is a plausible explanation. The saved logs do not prove which PID received the
signal. This change resolves the queue classification; it does not change the
agent's commands or runtime behavior.

The console had begun printing step 88, while both saved trajectories contain
87 turns. Recorded costs and step counts therefore remain censored; they are
not a complete provider billing statement. Both usage-censoring flags are
preserved, including explicit flags on the accepted summary record.

`audit.json` binds the observation to the original result, evidence, cleanup,
and verifier hashes. The implementation changes only our supervisor tooling;
task definitions, verifier scoring, and resource budgets are unchanged.

Validation: 23 regression tests for abnormal exits and 46 existing benchmark
tests pass in the Harbor environment. All 36 supplied files match commit
`c1ae968`; all 14 locked manifest definitions and saved controls remain valid
(the active campaign excludes Burn-scoped and still targets 2,340 trials).

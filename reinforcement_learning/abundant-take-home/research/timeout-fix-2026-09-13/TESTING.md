# No-model deadline regression

The harness in `scripts/tests/test_benchmark_deadline.py` constructs isolated,
temporary Harbor tasks and runs the real installed `SingleStepTrial` with a
custom `BaseAgent` that invokes Python through `DockerEnvironment.exec`.
It configures no model and makes no model calls. All fixture containers use
unique `deadline-test-*` names and Harbor removes only those environments.

The writer launches a normal child, a child in a new session, and a double-fork
daemon. Each continuously changes an artifact and an agent trajectory file,
then creates a delayed sentinel after 1.6 seconds. The verifier samples both
artifact and trajectory hashes across two seconds. The timeout fixture has a
one-second agent budget; the normal-return fixture exits before its children.

The guarded suite covers:

1. Agent timeout with Harbor's streamed output collector.
2. Normal agent return with background descendants still running.
3. Agent timeout with Harbor's buffered output collector.
4. A one-millisecond startup deadline, before the writer can start.
5. Rejected cleanup proof: actual fixture cleanup succeeds, but an injected
   `AgentQuiescenceError` must prevent artifact collection and grading.

The assertions also inspect host Docker exec PIDs at agent-output sync,
artifact collection, and verifier entry. Process command lines are read only
to match the exact fixture project and are never written to evidence. The
guard's recorded cleanup time must precede those boundaries, and every
recorded execution must report quiescence. A failed proof must leave the
result unscored and omit the grading snapshot.

Each final invocation copies its four relevant source files into its output
directory, binds their SHA-256 values in `summary.json`, and verifies they did
not change during execution. The activation check should accept only a guarded
summary with `overall_passed: true`, all five cases passing, and source hashes
matching the files to activate.

Example, from the development-container workspace:

```sh
PYTHONPATH=scripts:scripts/tests \
  /home/vscode/.local/share/uv/tools/harbor/bin/python \
  scripts/tests/test_benchmark_deadline.py --docker \
  --output research/timeout-fix-2026-09-13/guarded-recheck-01
```

`--output` must not exist. Adding `--unguarded` in a separate process runs the
three ordinary failure reproductions without installing the fix.

The earlier `unguarded-03` run demonstrates both an ordinary timeout and a
normal return leaving writers active during verification. Earlier
`unguarded-01` and `unguarded-02` directories retain harness-authoring failures
(direct trial construction and fixture working-directory setup); they are
not evidence about the timeout fix. `guarded-01` is an intermediate run made
while the implementation and harness were still being finalized; use the
source-bound final summary for activation.

`unguarded-buffered-01/buffered-timeout/summary.json` adds a direct negative
control for host client cleanup. The same live Docker exec PID remained at
agent-log sync, artifact collection, and verifier entry; the verifier observed
continued writes and returned 0. The disposable environment's final cleanup
eventually removed the client. No unrelated container was stopped.

All five cases in `guarded-final-01` passed, but its overall source-integrity
check correctly rejected the run because the evidence helper changed during
execution. The final activation evidence is `guarded-final-02/summary.json`.

The final run passed all five cases with all source hashes unchanged. The four
cases allowed to verify returned 1; rejected cleanup proof returned no reward
and never entered the verifier. A fresh check after the run confirmed no
`deadline-test-*` containers remained. The independent preservation check in
`preservation-check-test-agent.json` confirms that all 36 supplied files still
match commit `c1ae968` byte for byte.

These are process-lifecycle tests, not model-difficulty measurements or a
security boundary against an agent deliberately attacking its supervisor.

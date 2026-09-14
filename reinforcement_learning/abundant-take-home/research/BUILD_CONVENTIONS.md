# Candidate build conventions

Each assigned worker owns ONE task at a time and writes only its assigned `candidates/<id>/` directory. Original take-home files and other workers' directories are read-only. Cache/work trees go under ignored `research/cache/<id>-work/`. Research notes live outside the agent environment.

## Required files

- `instruction.md`: complete, implementation-independent public behavior and APIs, failure/recovery semantics, backward compatibility, exact scope. No hidden requirement or reference-patch matching.
- `task.toml`: Harbor 0.15.0 compatible; metadata; 7200s agent, 900s verifier, 900s build; 2 CPUs/4096MB/10240MB. Preserve take-home gateway URLs in `[environment.env]`.
- `environment/Dockerfile`: Python 3.12.11 slim-bookworm, available for ARM64 and AMD64. Prefer digest `python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7`. Include bash, git, curl, build tools as needed. Workdir `/workspace/repo`.
- `environment/upstream.tar.gz`: vendored clean `git archive` of exact source commit. Include its license; no .git history, oracle, or hidden tests. Extract in Dockerfile and install editable with pinned dependencies. No build-time moving git branches. Keep original upstream tests available to model.
- `environment/requirements.txt`: pin task and test dependencies; root may tighten transitive lock later.
- `solution/solve.sh` plus `solution/changes.patch`: executable golden solution against the supplied baseline. Put ONLY intended source changes in patch. No grader tampering. Include added files with `git diff --no-index` or `git add -N` where needed.
- `tests/test.sh`, `tests/test_*.py`: self-contained hidden behavioral verification, injected by Harbor only after agent. Write `/logs/verifier/reward.txt` as binary 0/1 and detailed JUnit/XML or JSON diagnostics. Validate positive collection and fail on unexpected skip/xfail/error. Avoid LLM grading, network, sleep-based races, exact source matching, and tests that merely mirror implementation.
- `provenance.json`: upstream URL/commit/date/license, source archive SHA256, sources with checked dates, original task contribution, public solution overlap, horizon hypothesis and no measured model headroom yet.

## Quality

Use real integrations and state transitions, including subprocess termination/reopen and controlled concurrency where in contract. Fake clocks/barriers are preferable to timing-dependent tests. Public requirements may specify an injectable clock or observable lifecycle hooks if relevant to users; do not require arbitrary hidden test-only hooks. Verify backward compatibility on selected meaningful upstream tests and exercise the actual package/CLI.

A missing new feature must fail the untouched baseline. The oracle must pass all task checks plus selected upstream regressions. Do not lower the requested scope to make an oracle pass, add arbitrary complexity to force 75 steps, or claim task horizon from code length alone. Document unresolved design limitations rather than hide them.

Do not run paid model trials. Root will run Harbor oracle/nop and independent adversarial checks. Workers may run direct local validation or isolated Docker checks, saving evidence inside their task's construction log outside environment. Do not leave validation stubs; complete the reference implementation and test both baseline and oracle.

## Runtime networking

Root will configure and verify model gateway access plus limits on online answer retrieval consistently across the pack. Leave network policy at default in initial worker task.toml; all dependencies must be installed at image build time and verifier must run without network. Root also owns final runner configuration and result summarizer.

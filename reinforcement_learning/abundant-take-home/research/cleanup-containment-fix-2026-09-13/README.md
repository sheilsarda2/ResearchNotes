The September 13 queue cancellation came from a failed trial escaping Harbor's
recovery path. In `cpp-zenoh-cpp-connectivity-api__znS7CQo`, cleanup could not
confirm quiescence. The output-sync guard raised `AgentQuiescenceError`; Harbor
recorded the failure, then recovery called the same guarded sync and raised
again. Finalization wrote the ungraded result and emitted END, but the escaped
exception cancelled the TaskGroup's eleven sibling trials. This establishes the
cascade; the containment fix does not establish what caused the initial agent
exit code 137.

`benchmark_trial_containment.py` contains only that escaped guard error, and only
when all of these conditions hold:

- Guard state explicitly says `quiescent=false`; no verifier ran or produced a
  result.
- The saved, finished result contains an exception and exactly matches Harbor's
  in-memory result.
- Existing END hooks completed, and Harbor's environment-stop path completed.
- A separate Docker query finds no running container with the trial's exact
  Compose project label.
- The task is not being cancelled, and the saved exception is not cancellation.

The wrapper returns the existing ungraded failure and adds
`benchmark-containment.json`, which binds its claims to the untouched result's
SHA256. Guard checks, task budgets, scoring, and raw results remain unchanged.
Missing proof, failed teardown queries, unrelated errors, and cancellation still
propagate.

Validation used **zero model calls**. `unit-summary.json` records ten passing
async tests against Harbor's actual Trial.run and TrialQueue code. The control
reproduces sibling cancellation without containment; the fixed case keeps the
healthy sibling running and emits each END hook once. Negative cases cover
cancellation, failed hooks, missing/changed/unfinished results, verifier activity,
and unconfirmed teardown.

`docker-regression-final/summary.json` records five passing real Docker cases:
timeout, normal background children, buffered-output timeout, startup timeout,
and injected cleanup-proof failure. The last case returns an ungraded failure
normally, never enters the verifier, and confirms teardown. Source stability
passed; all 36 supplied take-home files also matched commit `c1ae968` after
validation. The final summary SHA256 is
`4a8f248d5f9f781f356eab5475f7d72483a48f268b771901b2d74b8968509d3d`.

Run these from the take-home directory inside the development container, using
its installed Harbor interpreter and a fresh output directory:

```sh
PYTHONPATH=scripts:scripts/tests /home/vscode/.local/share/uv/tools/harbor/bin/python -m unittest discover -s scripts/tests -p test_benchmark_trial_containment.py -v
PYTHONPATH=scripts:scripts/tests /home/vscode/.local/share/uv/tools/harbor/bin/python scripts/tests/test_benchmark_deadline.py --docker --output research/cleanup-containment-recheck
```

`commit-files.txt` lists the minimal proof files relative to this directory:
this note, unit output and summary, the final source snapshot and summaries,
each raw result/config/runtime/deadline record, the containment marker, and the
verifier check/reward files. Together with the implementation and unit-test
source committed under `scripts/`, these support inspection and reproduction.
Full raw artifact/snapshot archives and verbose logs remain local. The earlier
`docker-regression-001` is excluded: its assertions passed, but sources changed
while it ran, so its source-stability check failed. No interim outputs were
rewritten or deleted.

# Agent runtime smoke evidence

The final smoke passed: [machine-readable summary](smoke-20260913T194100Z/summary.json).

| Check | Original setup | Fixed setup |
| --- | --- | --- |
| Task system Python, before and after | 3.10.12 | 3.10.12 |
| Agent tool Python | 3.10.12 | 3.12.11 |
| mini-swe-agent | 2.4.6 | 2.4.6 |
| LiteLLM | 1.100.1 | 1.100.1 |
| CLI `--help` | Succeeds | Succeeds |
| Import actual model backend | `ImportError: NotRequired` | Succeeds |
| Recorded installation preflight | Not present upstream | Passed |
| API credentials in probe environment | None | None |

`scripts/tests/test_benchmark_agent_runtime.py` invokes the actual installed Harbor
`MiniSweAgent.install()` method using a minimal Docker execution adapter. Each
case creates and removes its own disposable container from the frozen Foxglove
agent image in the development container's Docker daemon:

```
candidate-v2-prewarm-cpp-foxglove-sdk-parameter-handler:agent
sha256:c1bc2b2f9f3d218562a00260f5cd402c9e410e48b933b3fdfb4f228e46379c98
```

The adapter forwards no host environment or credentials. After dependency
installation it disconnects the container from its network before independently
importing LiteLLM and `minisweagent.models.litellm_model.LitellmModel`. The runtime
patch's earlier preflight also sets `LITELLM_LOCAL_MODEL_COST_MAP=True`. Neither
case constructs a model, calls `agent.run()`, executes a task/verifier, or submits
an API request. This validates installation and backend loading; it does not
claim to validate a subsequent model response or task outcome.

The final summary contains before/after hashes of every frozen Foxglove candidate
file and the two tested scripts. All matched. Runtime source SHA-256:
`023de660ffb4085e6b8091e25aebd8baa9460d5480e34262ab79ccfaf81be60e`.
Safe installation commands, stdout/stderr, runtime metadata, and tested source
copies are retained under each attempt directory.

Attempt accounting:

- `smoke-20260913T201100Z`: no container started. Initial discovery selected an
  image from the host Docker daemon, unavailable in the active runner's separate
  daemon. [Attempt record](smoke-20260913T201100Z/attempt.json).
- `smoke-20260913T201200Z`: reproduced the original crash and confirmed the
  Python fix reached the backend import, then caught an overly strict metadata
  check for the optional `anthropic` SDK. Its absence is valid for this LiteLLM
  installation. Both disposable containers were removed. [Summary](smoke-20260913T201200Z/summary.json).
- `smoke-20260913T194100Z`: repeated both cases after optional package metadata
  was made nullable. Every assertion passed; both containers were removed.

Directory names are attempt labels; the embedded `started_at`/`finished_at`
timestamps are authoritative. Historical benchmark jobs and task files were not
modified by this smoke.

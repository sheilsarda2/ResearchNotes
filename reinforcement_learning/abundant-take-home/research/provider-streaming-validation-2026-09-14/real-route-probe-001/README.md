Prepared only; no real probe has been executed.

`run_probe.py` uses captured Mini 2.4.6, exact LiteLLM 1.100.1 sources, and the reviewed synchronous iterator in one isolated model process. It never imports the offline fixture module or registers synthetic prices. The first request asks for a fixed harmless bash call; no tool executor exists. The second request receives an explicitly synthetic observation and repeats that call.

The configured take-home `/v1/messages` route, Fable 5-1, max effort, adaptive thinking, and 64,000 output-token setting are enforced at the HTTP send boundary. At most two sends are allowed, with Mini/LiteLLM/HTTP transport retries disabled. A process alarm bounds execution at 240 seconds; the proposed command adds a 240-second external timeout with a five-second kill grace. HTTP connect/read-idle bounds are 15/90 seconds. Failed requests stop the sequence; no retries or extra attempts chase signed-thinking coverage.

Credentials load internally from `.env` using `TAKE_HOME_TOKEN`, like the existing screen script. No headers, full environment, thinking text, or signatures are printed or persisted. Source/result/event hashes, event timing, usage counters, signed-block lengths/order/hashes, fixed-tool-match booleans, and HTTP status are retained. The original decoded provider block hash must equal the Mini block hash and second request's serialized native block hash. If no signed thinking appears, the signed round-trip is inconclusive even when both requests succeed. Local cost estimates are labelled unverified; provider charge remains unknown.

`--check-only` passed against 2,318 installed LiteLLM source hashes without reading credentials or importing network-capable packages. Eleven pure request-limit/privacy guards passed on host and Harbor Python. These checks do not authorize or execute the probe. `attempt-001` does not exist.

Proposed command, **for root review, not executed**:

```sh
docker exec -w /workspaces/sheil_research/reinforcement_learning/abundant-take-home keen_black timeout --signal=TERM --kill-after=5s 240s /home/vscode/.local/share/uv/tools/harbor/bin/python -B research/provider-streaming-validation-2026-09-14/real-route-probe-001/run_probe.py --run --output research/provider-streaming-validation-2026-09-14/real-route-probe-001/attempt-001
```

Only a short stream and signed-block/API acceptance can be demonstrated. This cannot establish heartbeat support past 291 seconds, gateway-failure relief, or safe integration with live trial concurrency/deadlines. No benchmark control, task, installed package, or global environment file is changed.

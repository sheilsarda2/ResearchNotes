Draft for review — not sent.

We are seeing long model requests fail through `https://take-home-automation.vercel.app/v1/messages`. In the saved Luigi canary `luigi-generation-target__RigDBsV`, a streaming request ended after 290.510 seconds; request 18 entered at 2026-09-14 08:41:48.069808 UTC, its HTTP response opened at 08:41:50.641990 UTC, and its resources closed at 08:46:38.579392 UTC. The separate failure observation was recorded later, at 08:48:02.887435 UTC. These request times are converted from the saved query epochs. The client rejected the incomplete response and executed no partial action. It then followed the existing retry policy.

The request used `claude-fable-5-1`, max effort, adaptive thinking and `max_tokens=64000`. Please preserve those settings and the original task budget while investigating. Two short requests completed with SSE pings, but we lack long-request frame timing and cannot tell whether this is an absolute duration limit, upstream request timeout, idle timeout or another connection closure.

Please correlate this request with deployment/CDN/upstream logs; inspect the deployed runtime and plan, Fluid configuration, per-route `maxDuration`, and upstream read/request/abort deadlines. If idle timeout or buffering is involved, please verify end-to-end SSE flush and heartbeat forwarding. If an absolute limit is involved, please identify a supported longer-duration setting or equivalent authorized route for the same request configuration.

We have not changed the gateway or benchmark budgets, and we are not proposing HTTP/2 as an established fix. Exact saved evidence and hashes are listed in the accompanying `findings.json`.

# Preserve workers waiting for the next dispatch round

The interleaving scheduler waits before emitting resource status. DiskCache's
empty worker was correctly waiting for other task/model/effort cells to reach
its round, but the watchdog treated its unchanged resource heartbeat as idle
and restarted it twice, at 00:34:11 and 00:34:41 UTC.

The watchdog now verifies the shared round policy before classifying an empty
primary worker as idle. Its process identity and campaign control must match;
every unfinished cell belonging to that job must be ahead of the global
minimum. Repair jobs, invalid policies, completed jobs, and workers with an
eligible cell retain the existing recovery behavior. The policy is checked
again under the admission lock immediately before any recovery signal.

All 22 watchdog tests pass, including stale heartbeats, real idle recovery,
repair jobs, reused process IDs, and a policy change before signaling. The
three active watchdogs were reloaded without signaling any trial worker or
supervisor. DiskCache now reports the exact round wait and keeps its worker;
Zenoh reports shared-capacity waiting and the main campaign continues with
12 active trials. The activation records bind the source and test hashes.

Burn v3 remains intentionally paused for its separately validated v4 task
correction. This watchdog change does not alter task inputs, scoring, model
settings, resource limits, or historical trial results.

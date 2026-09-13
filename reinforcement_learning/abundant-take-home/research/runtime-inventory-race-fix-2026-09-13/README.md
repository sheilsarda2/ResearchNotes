# Rolling activation inventory race

At 23:23:51 UTC the runtime activation watcher stopped while draining the main
campaign. Docker removed container `9946a02c8ad4` between the old inventory's
list and inspect calls. The resulting `CalledProcessError` stopped only the
watcher; both campaign runners and the owned main admission pause remained.

`scripts/activate-benchmark-tool-runtime.py` now takes its own metadata-only
inventory. It retries the entire list and inspection, at most three times,
only when Docker reports an exact missing container from the requested set.
It discards partial output and retains newly listed containers. Daemon errors,
unexpected diagnostics, malformed or incomplete metadata, and exhausted retries
remain failures; they cannot establish an empty campaign.

All 24 activator tests passed, including seven new race/error tests. A read-only
live check found the two main and six Zenoh containers under the original runner
identities. `validation.json` binds those results and the reviewed source hashes.

The original blocked state and prior source are archived here. Under the global
activation lock, `rebind-review.json` verified that only the activator source had
changed, while runtime code, proof, workloads, runner identities, controls and
phases retained their approved values. The state now also binds this review,
tests and validation. No trial was interrupted or task limit changed.

At 23:29:01 UTC the same activation resumed with watcher PID 63850, start identity
2662180. It retained the original deadline for waiting and the main `draining`
and Zenoh `pending` phases. `resume-result.json` records that point-in-time
confirmation; current progress remains in the activation's live state file.

# C++ Zenoh saved-submission regression replay

The Opus/max trial `cpp-zenoh-cpp-connectivity-api__4gQVPQy` scored zero after
`test_advanced_pub_sub_zenohpico` terminated with a segmentation fault. The
connectivity checks, remaining upstream regressions and parity checks passed.
`zenoh-cpp-4gqvpqy-regression-crash-audit.json` records the original result and
24 passing evidence-consistency checks. No missing task requirement was found.

Diagnostic-002 replayed the unchanged 64 saved headers in the original pinned
verifier image, with unchanged test and dependency files, four CPUs, 8 GB of
memory and no network. It used the normal shared admission pool after Burn's
temporary validation priority had restored the original campaign controls.
It made no model calls and did not perform a full regrade.

The target passed three isolated runs and then passed again in the original
ten-test sequence, for thirteen successful test executions in total. No cases
were skipped. The diagnostic finished and removed its container and shared
claim in 123.14 seconds, within its 1,800-second bound. The replay did not
reproduce the earlier crash. This does not establish whether the original
failure came from the submission, an upstream race, or another runtime cause.
The historical reward remains zero; no test or task was excluded.

`zenoh-cpp-replay-completion-audit.json` independently checks all log and input
hashes, captured sources, XML case names and statuses, outcome classification,
cleanup, the unchanged historical result, and all 36 supplied take-home files.
All sixteen checks pass. The frozen replay records and logs are in
`zenoh-cpp-saved-source-diagnostic-002/`.

Diagnostic-001 was stopped before admission after review identified ownership
and cleanup edge cases in its helper. It ran no tests and owned no container
or shared claim. Its original source capture and abort record remain intact.
The separate v2 helper records ownership before output, checks shared state
during cleanup, reserves 150 seconds for teardown, and rejects incomplete or
skipped test evidence. Sixteen adversarial checks and the read-only preflight
passed before diagnostic-002 was admitted; their source hashes and review are
recorded in `zenoh-cpp-v2-harness-review.json`.

The coordinating helper-agent response stopped after an automatic content
check. The diagnostic process had already completed normally. The root agent
finished the independent, read-only result and cleanup verification.

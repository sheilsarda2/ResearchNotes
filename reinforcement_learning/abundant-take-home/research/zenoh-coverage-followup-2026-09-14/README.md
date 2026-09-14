# Separate Zenoh coverage validation

This work follows the Harbor quality review's missing direct coverage for behavioral contracts 6 and 7. The frozen `rs-zenoh-timestamp-instrumentation-v3` task, its running campaign and its historical scores are unchanged. This folder does not promote a replacement task or count a new model trial.

The five authored robustness tests construct the specified optional id-7 ZBuf extension using existing public codec and network interfaces. They check complete Push/Request/Response messages at valid and invalid count boundaries, zero configuration rejection, public subscriber delivery around unknown interception points and undecodable UHLC records, and Receive recording at 254 and 255 incoming records. They import none of a solution's new internal protocol types or modules. A solution may filter malformed records in the codec or when creating the public stack. These probes do not exhaust separate Send/Route cap branches.

The admin tests separately check Receive callback participation and preservation of the received query stack in the reply. Contracts 3 and 7 give admin replies the same inheritance rule as ordinary replies. The untouched reference reproduced the missing inherited stack in `harness/gold-smoke-003`: six new tests passed, and the returned-stack test reached its explicit runtime assertion and failed. `admin-reply-stack.patch` corrects only the admin query's stored stack in the separate revision. The original reference and v3 files are unchanged.

The clean separate task is frozen in `task-manifest.json` (Harbor checksum `67f1fdca75fd66f4cbdd6e4729a582c9fbbdac428a0299eefc74104dffa59fab`). Full oracle/nop controls started at 06:30:33 UTC on September 14 and are tracked in `harbor-controls-final/summary.json`; no pass is claimed before completion. Controls, omission probes and paired verifier regrades have zero model calls, local concurrency 1 and the existing shared capacity/memory gates. Each focused case has the original 4 CPU, 8192 MiB and 3600-second limits. Full controls use captured immutable Harbor runner sources and unchanged task budgets. Nine paired full verifier regrades will preserve the original trials and scores. One separately authorized final Harbor quality review uses the same routed Sonnet 5/high review settings as the shortlist checks after exact controls pass; it is distinct from solver attempts and sweep coverage.

Files:

- `codec-test/timestamp_robustness.rs` and `codec-test/design.json`: tests and coverage limits.
- `admin-test/`: independent callback and returned-stack tests, with requirement/source mapping.
- `saved-source-audit/`: hashes and static review of two different successful implementations and the pristine public transport interface.
- `immutable-tooling-v1/`: unchanged captured orchestration sources; `immutable-import-inspection.json` verifies loaded paths.
- `prepare_revision.py`: gated creation of a new validation task; never overwrites the parent or an existing revision.

The old differential reference peers remain valid controls for the existing interoperability scenarios. The prospective admin-only reference correction belongs to the solution patch of the new task and does not silently replace the old peers or historical evidence.

`bytecode-preflight-correction/` preserves a rejected, unexecuted draft and the generated Python bytecode that caused its checksum drift. Only generated files were removed; both the original v3 directory digest and Harbor checksum were restored and independently checked, and no v3 trial was created during the interval. New validation commands disable bytecode writes.

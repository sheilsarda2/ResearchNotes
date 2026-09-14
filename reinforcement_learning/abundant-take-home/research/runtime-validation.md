# Runtime and validity boundaries

The pack is built for Harbor 0.15.0 and mini-swe-agent 2.4.6. Oracle and no-op checks make no model calls. Model comparisons are a later user-run stage.

Harbor 0.15's local Docker provider advertises internet disabling but does not advertise dynamic network switching or hostname allowlists. A public agent environment plus an offline verifier phase in the same container is therefore not a valid configuration. We verified this in the installed provider and trial network-policy source, then configured separate verifier environments.

Each task has two independent image build contexts. The agent receives a clean pinned source archive, its original tests, and installed dependencies. The verifier starts from the same source/dependencies with its own tests baked in, has runtime `network_mode = "no-network"`, and receives only `/workspace/repo/<package>/` as the submitted source artifact. Changes to installed binaries, environment files, or background processes in the agent container do not transfer. Huey stores its upstream tests inside the runtime package, so its verifier restores that test directory from a pristine image copy before collecting tests. The other projects keep regression tests outside the transferred package. The public instruction states that runtime feature code must remain inside this package.

Both image builds may download pinned Python requirements and OS build packages. Network disabling applies to verifier execution, not image construction. Source is vendored by commit. Python's base image is digest-pinned; Debian package mirrors are not a hermetic apt snapshot, so record the resulting image/runtime with each campaign. Existing downloaded build layers can be reused.

The agent retains public network access for harness installation and the take-home API gateway. We do not claim a gateway-only allowlist is enforced by local Docker. The new contracts are unpublished within this workspace, and the supplied source omits upstream Git history. Trajectory review should still identify external solution retrieval. If strict egress policy is required for a later campaign, use a provider that supports it or a separately tested proxy deployment; do not silently change the harness mid-comparison.

A separate verifier reduces the opportunities for pre-run tampering. It is not a proof against arbitrary malicious Python executed during grading. The grader imports submitted code, and post-run trajectory/source review remains necessary before declaring final benchmark success. The task suite does not use an LLM judge.

Construction errors remain in the saved Harbor jobs rather than being overwritten. Early import/generation verifier builds failed due to a source-archive COPY path; an early snapshot run inherited incompatible upstream pytest addopts. The corrected configurations have fresh passing runs. Those earlier failures are packaging errors, not evidence of model headroom.

`shortlist.json` points to the current accepted oracle and no-op run for each completed candidate. All model-headroom and horizon fields stay unmeasured until actual model trials.

The independent measurement audit reproduced false negatives with behavior-preserving implementation changes, including native SQLite renames and alternate Python file-opening paths. The corrected verifiers accept those variants. Huey's nested upstream tests are restored before grading, and final validation is repeated after these changes. Historical counterexamples and corrected runs are retained in [the measurement audit](independent-measurement-audit.md).

The comparison planner binds the whole task directory to accepted oracle/no-op results, verifies their saved hashes and rewards, and freezes both task and configuration identities. Summarization checks the raw result's task checksum, model and effort. Synthetic checks cover stale evidence, altered configurations, and budget accounting; these are runner tests, not model trials.

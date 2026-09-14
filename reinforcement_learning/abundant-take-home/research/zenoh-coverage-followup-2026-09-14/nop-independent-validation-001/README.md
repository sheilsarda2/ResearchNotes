# Independent validation of the original no-op control

This is a new validation certificate for an existing normal Harbor no-op run. It is not another execution or a replacement raw result. No model call, container launch, claim acquisition, task change, or original artifact rewrite was performed.

The original controller remains failed because its unlocked admission observer encountered a transient missing-state read. The child was reaped with exit code 0, and its raw trial finished with reward 0, no exception, and the exact final task checksum. The missing observation instants remain disclosed; their cause has not been established.

`summary.json` independently recomputes the existing raw-result, task, resource configuration, captured source, process-guard and verifier-group checks. It binds the original 1,240 successful admission samples, which show one acquired slot followed by zero own claims after trial completion. `terminal-absence.json` adds a fresh read under the existing shared lock, confirms zero own participants, and checks that the historical runner identity and matching trial containers are absent. The entire original control directory is hashed before and after this validation and remains unchanged.

The original failed summary and its no-op record are included by hash and copied verbatim in this certificate. `controls.nop` is the separate independently computed projection of the same raw no-op result. A later combined-control certificate may select that projection alongside a newly validated oracle; it must retain this distinction and the original failures.

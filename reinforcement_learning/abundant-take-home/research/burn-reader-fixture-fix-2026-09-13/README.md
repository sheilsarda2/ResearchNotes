# Burn TAR count fixture revision

The separate task at `research/task-revisions/rs-burn-store-pytorch-reader-v3`
copies all 64 files from `candidates_v2/rs-burn-store-pytorch-reader`. Its only
change adds a valid protocol-2 `sys_info` pickle to
`test_tar_absurd_storage_count_is_an_error` in `tests/hidden/reader/mod.rs`.
The fixture retains its count of 2^40 storages, empty tensor table, empty object,
and every original assertion. Instructions, budgets, resources, scoring, oracle,
and all other files are unchanged. The validated revision now runs in its own
180-trial replacement cohort.

The prior fixture omitted `sys_info`, although instruction line 108 lists it
among the TAR entries. Trial `rs-burn-store-pytorch-reader__dVXYtU8` rejected that
missing member before inspecting the absurd count. Its sole failing assertion
required the error text to contain `Pickle`. The reference implementation allows
missing `sys_info`; the instructions do not specify that optionality or which
malformation must be reported first. Adding the member isolates the intended
count check without relaxing it.

That trial also has a separate, documented defect: submitted `reader.rs` lines
1025–1035 return `InvalidFormat` for a count exceeding the entry size, while
instruction line 108 requires a `Pickle` error. Therefore this fixture correction
does not demonstrate that the submission passes. Its raw zero remains unchanged.

`origin.json`, the two file manifests, and `fixture.diff` bind the exact source
and revision. `sys_info.pickle` records the inserted bytes. Reproduce the
standard-library validation from the repository root:

```sh
python3 research/burn-reader-fixture-fix-2026-09-13/validate_fixture.py
```

`validation.json` records the initial byte, pickle, TAR-layout, and preservation
checks. The subsequent standard Harbor controls in
`harbor-validation-20260913T232058Z` passed: oracle reward 1, nop reward 0,
with no exceptions or model calls. The oracle passed all eight groups, including
108 PyTorch unit tests, 52 safetensors unit tests, 20 integration tests, 37
PyTorch crate tests, 62 fixture checks covering 428 tensor rows, and 32 rejects.
`frozen-task-manifest.json` binds the validated revision and raw evidence.

`saved-submission-diagnostic-002` replays only the affected test against the
unchanged saved submission. It reaches the excessive-count guard and still
fails with the documented `InvalidFormat` versus `Pickle` defect. Source hashes
match before and after execution; the container stopped and its shared claim
was released. This is a focused diagnostic, not a full regrade.

The first diagnostic observed the same test failure but its cleanup check missed
Docker's lowercase missing-container message after successful removal. Its raw
result and exact script are preserved. `diagnostic001-claim-cleanup.json` records
the independent absence/owner checks and removal of only its stale shared claim.

`planned-cohort.json` records the original preparation of the replacement cohort.
The later promotion is recorded in `../revision-promotion-2026-09-13/`. Its
campaign is `candidates-burn-reader-v3-efforts-20-20260913T232621Z`, with the same
models, efforts and 20 attempts per cell. The old task retains its readiness
hold and is excluded from main's current cells; all six historical attempts,
including four previously counted outcomes, remain unchanged. An audited
activation rebase allowed this cohort to start during the remaining rollout.
The combined plan is main 1,800 + three 180-trial revision cohorts = 2,340.

## Reproduce the Harbor controls

`validate_controls.py` runs one ordinary Harbor `oracle` trial, followed by one
`nop` trial, through `scripts/harbor-resource-runner.py`. Run it inside the
devcontainer with Harbor's installed interpreter:

```sh
docker exec -w /workspaces/sheil_research/reinforcement_learning/abundant-take-home keen_black /home/vscode/.local/share/uv/tools/harbor/bin/python research/burn-reader-fixture-fix-2026-09-13/validate_controls.py
```

The harness creates a new timestamped output directory and its own admission
file. It admits at most one control at a time through the existing shared pool;
the pool's pause, concurrency, memory, and fairness checks remain effective.
Local admission reserves 4096 MiB plus 1536 MiB of startup headroom, requires
at least 30000 MiB total Docker memory, and uses the existing pressure/stagger
checks. These admission settings do not override the task's 4 CPUs, 8192 MiB
memory, 30720 MiB storage, 21600-second agent budget, or 3600-second verifier
budget. The verifier remains offline. No model agent is launched.

Before and after the controls, the harness checks the exact task manifests and
hashes the runtime helpers. Oracle must pass all eight groups with native `ok`
booleans and the unchanged exact test counts. Nop must finish normally with
reward zero and at least one failing group; pristine-source build failure is an
expected negative outcome. Both require confirmed agent quiescence before the
verifier. Full trial-file hashes are retained in `summary.json`.

After a passing run, create a new selection file using that exact summary:

```sh
/home/vscode/.local/share/uv/tools/harbor/bin/python research/burn-reader-fixture-fix-2026-09-13/freeze_revision.py --controls research/burn-reader-fixture-fix-2026-09-13/harbor-validation-TIMESTAMP/summary.json --output research/burn-reader-fixture-fix-2026-09-13/validated-task.json
```

Freezing rechecks raw results, scores, configuration, quiescence, task identity,
tooling, and every saved trial-file hash. It writes evidence suitable for later
promotion; it does not promote the task or edit any campaign control. The
`--check-only` option on `validate_controls.py` validates files and prints its
plan without starting Harbor or creating a control file.

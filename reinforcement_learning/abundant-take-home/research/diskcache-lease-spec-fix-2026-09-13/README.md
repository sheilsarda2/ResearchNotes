# DiskCache lease contract clarification

`research/task-revisions/diskcache-online-reshard-v2` copies all 50 files from
`candidates/diskcache-online-reshard`. Only the lease paragraph at instruction
line 32 changes. Tests, reference implementation, scoring, budgets, resources,
and all other instruction paragraphs are byte-identical. The validated revision
now runs as a separate 180-trial replacement; historical results remain unchanged.

The old wording says nonblocking contention "may raise" `ReshardBusyError`.
That leaves waiting for the lease as a plausible implementation choice. However,
`tests/test_online_reshard.py:399–418` holds another process's native transaction
open until the parent has observed `ReshardBusyError` from both a write and
`begin_reshard`. A blocking parent cannot reach the release message, so those
semantics are part of acceptance despite the permissive instruction wording.

The revised paragraph explicitly requires nonblocking acquisition by competing
instances/processes and `ReshardBusyError` before the contending operation. It
clarifies the held-transaction case, owner reentrancy, nested transactions, and
permitted serialization of threads on one instance. It specifies no lease file,
private helper, or migration algorithm and refers to no verifier-private path.
The existing reference's nonblocking file lease and reentrant owner behavior
already implement these semantics (`solution/changes.patch:208–233`).

The current `diskcache-online-reshard__tnYKGdt` result records
`VerifierTimeoutError` at 2026-09-13 23:29:19 UTC and exactly 23 completion dots.
Together with the next process-lease test's barrier, this strongly suggests a
24th-test deadlock. There is no preserved stack establishing the blocked call;
this is an inference, not a regrade or a claim that the submission would pass.

`origin.json` binds the original timeout evidence, both task manifests, and
`instruction.diff`. `source-manifest.json` and `revision-manifest.json` record
every file's SHA256, byte size, and mode. `preservation-check.json` records the
single-paragraph and other-file preservation checks from initial authoring.
The original task's pre-existing bytecode is retained because it is part of the
campaign's locked digest, giving 50 files in each manifest.

`harbor-controls-final/summary.json` records subsequent standard Harbor
validation: oracle reward 1 with all 45 native pytest cases passing, nop reward
0 with actual failed checks, no exceptions, and no model calls. The original
7,200-second agent and 900-second verifier limits, 2 CPUs and 4 GiB memory were
preserved. The freezer rechecked every control artifact and task file.

The old task's five counted outcomes remain historical. Its readiness hold and
main-campaign exclusion prevent further old-revision starts. The replacement
campaign is `candidates-diskcache-v2-efforts-20-20260913T234023Z`; preparation is
in `planned-cohort.json`, and the later coordinated promotion is recorded in
`../revision-promotion-2026-09-13/`. Main 1,800 plus three revision cohorts of 180
retains the total of 2,340 valid planned trials, sharing the original 12-slot cap.

`validate_controls.py` runs one standard Harbor 0.15.0 oracle control followed by
one nop control. It verifies all 50 frozen files, including the pre-campaign
bytecode, and compares all 36 supplied files directly with commit `c1ae968`.
The oracle must finish without an exception, receive reward 1, and produce
exactly 45 native pytest cases with zero failures, errors, or skips. The nop must
finish without an exception, receive reward 0, and contain a failed or errored
case. JUnit totals must agree with the individual cases. Both controls require
confirmed guard quiescence before grading; nop may have no agent executions.
Configuration, task checksum, runner helpers, and all trial artifact hashes are
bound into the proof.

The task retains its 7200-second agent and 900-second verifier budgets, 2 CPUs,
4096 MB memory, and 10240 MB storage. Controls run sequentially through the shared
admission pool with a local maximum of one active trial; they wait for shared
capacity. The local gate reserves 1536 MB during startup and 4096 MB globally.
These are admission checks, not task resource overrides. No model is selected.

After review, launch from the repository directory inside `keen_black`:

```sh
/home/vscode/.local/share/uv/tools/harbor/bin/python research/diskcache-lease-spec-fix-2026-09-13/validate_controls.py
```

Only after both controls pass, write a new promotion-ready selection:

```sh
/home/vscode/.local/share/uv/tools/harbor/bin/python research/diskcache-lease-spec-fix-2026-09-13/freeze_revision.py --controls research/diskcache-lease-spec-fix-2026-09-13/harbor-validation-TIMESTAMP/summary.json --output research/diskcache-lease-spec-fix-2026-09-13/validated-selection.json
```

The freezer rechecks exact task bytes, configuration, rewards, all 45 oracle
cases, cleanup timing, and saved artifact hashes. It does not promote the task
or edit campaign controls. Host syntax and `--check-only` validation passed;
the JUnit reader also parsed all 13 existing DiskCache XML artifacts (45 cases
each) without changing them. Those preflight checks preceded the successful
Harbor controls recorded above. Repeated validation creates a fresh timestamped
directory; pass that run's exact summary and a new output path to the freezer.

# Burn reader: remove incidental Debug requirements in hidden error assertions

The first completed v3 trial, Sonnet/medium
`rs-burn-store-pytorch-reader-v3__aLVwbse`, failed to compile seven hidden tests.
Each used `Result<PytorchReader, _>::expect_err`, which requires the successful
value to implement `Debug`. The supplied base reader does not implement that
trait, and the public interface in `instruction.md` does not require it. The
reference solution adds `#[derive(Debug)]`, so the original oracle check could
not expose this copied-test assumption.

The separate frozen v4 task changes only those seven calls to
`.err().expect(...)`. Each still requires an error, with the same failure
message and all subsequent error assertions. All 64 files remain present;
the other 63 files are byte-identical to v3. The previous valid `sys_info`
insertion in the absurd TAR count fixture remains intact. Instructions,
reference solution, test counts, eight score groups, resources and time
limits are unchanged.

The source anchors are `instruction.md:33`, baseline archive member
`crates/burn-store/src/pytorch/reader.rs:203`, reference
`solution/changes.patch:4620`, and hidden reader test lines 1340, 1350, 1369,
1454, 1506, 1649 and 1674. The affected tests cover big-endian ZIP, a scalar
top-level key, truncated legacy storage, an absurd TAR count, unknown ZIP
byte order, missing legacy endianness and big-endian legacy data.

`first-live-audit.json` binds the triggering raw artifacts. Independently
counted native and ATIF trajectories both contain 127 assistant turns and
127 tool calls with matching call IDs, order and timestamps. There are 127
native tool returns. All pre-verifier snapshot entries and final evidence
entries rehash correctly; the frozen 64-file task identity matches. All
three guard executions were quiescent, cleanup finished 18.410588 seconds
before verification, and the patched tool runtime matches the wrapper,
bootstrap and helper source hashes. No exception or censored usage was found.

That trial also retained a literal `/tests/` fixture path in an inline test,
which the instruction explicitly prohibits while providing a supported
`test_data_path` helper. This is a separate submission violation. The raw
reward remains zero. No hidden test ran because compilation failed, so this
trial does not establish the corrected TAR fixture's runtime outcome.

`validate_assertions.py`, `origin.json`, the source/revision manifests and
`assertion.diff` prove the precise seven-call change and retention of all
other bytes. `initial-validation.json` records that check. Standard Harbor
oracle/nop controls use the existing shared admission pool.
`validate_controls.py` requires oracle
reward 1 with all exact counts, nop reward 0, normal completion and guard
quiescence before verification. `freeze_revision.py` will produce the frozen
selection only after those controls pass. These files do not change live
campaign controls or promote a paid cohort.

Both `harbor-controls-final` and `harbor-controls-stable-001` completed normal
oracle=1/nop=0 controls, including all eight oracle groups and exact counts.
Both are **ineligible for promotion**: concurrent edits changed bound tooling
during each run. The first changed shared admission and the resource runner;
the second changed the incidents and interleaving helpers. Their task inputs
were unchanged, and each final tooling check correctly failed. The drift
reports retain these results without relabeling them as valid freezes.
Stable-001 also preserves every captured tooling source in a run-local
snapshot, with a manifest bound to its run identity.

The separate saved-submission diagnostic002 compiled all 108 hidden tests
and executed the exact seven affected tests: five passed, while two exposed
submission defects (accepting the absurd TAR count and omitting the specified
message for a scalar top-level key). This is a focused diagnostic, not a full
regrade. The earlier diagnostic001 is retained with its tooling-drift status.

`harbor-controls-immutable-001` completed through new immutable control tooling.
The original runner and 19 companion source files were copied byte-for-byte
into `immutable-tooling-v1`; all 12 files in the runner's import tree and three
file payloads resolve there. The installed Harbor package, actual working
directory, task path, shared admission pool, budgets and scoring are unchanged.
The captured candidate validator uses its ordinary repository-root logic via
a relative `research` link, verified against prior frozen evidence. No source
global is overridden. Bytecode writing is disabled so imports cannot add
files to the capture.

`immutable-execution-context.json` binds the source list, installed Harbor
paths, payload locations and import inspection. Each control writes a source
proof confirming every loaded local module came from the capture and all
captured bytes remained unchanged. The new control and freeze scripts verify
those proofs and the captured source tree; differences in the live scheduler
are recorded separately. The earlier bound tooling and invalid runs remain
unchanged.

The immutable oracle completed at 01:07:15 UTC with reward 1 and all eight
groups passing: 108 PyTorch unit tests, 52 safetensors unit tests, 20 integration
tests, 37 `pytorch-tests` cases, 62 fixture checks covering 428 tensor rows and
32 rejection cases. Nop completed at 01:10:20 UTC with reward 0 and normal
completion. Both source proofs contain all 11 expected local helper modules
plus the separately bound runner; their captured sources remained unchanged.
Agent cleanup preceded verification in both controls. The full control run
passed, and `freeze_revision_immutable.py` produced `frozen-task-manifest.json`.
`completion-proof.json` binds the final selection, controls, source proofs,
capture and focused diagnostic. All 36 supplied take-home files still match
the original commit. This proof prepares v4 for promotion; it does not itself
change live campaigns or historical rewards.

`promote_v4.py` subsequently activated
`candidates-burn-reader-v4-efforts-20-20260914T011100Z` at 01:11:27 UTC.
The replacement has 180 trials across the same three models, three efforts
and twenty attempts. The shared policy retains 117 cells and 2,340 total
targets. Only the nine retired Burn cells were replaced; the four old receipts
were archived and the new cells start at zero. Other cohorts retain their
progress. Old trial outputs remain byte-identical. The retired supervisor
was stopped only after its campaign became ineligible; no trial was signaled.

`promotion/` records the exact policy and user-plan transition. The temporary
validation priority restored all three original campaign controls at 01:08:28
before promotion; `validation-priority/` records those unchanged hashes.
Promotion checks restoration before writing, rechecks under the shared lock,
and checks the prepared paused-control hash again before enabling v4.
Nine regression tests cover the policy transformation and restoration gate.
`prepared-control.json` preserves the exact pre-activation control bytes.

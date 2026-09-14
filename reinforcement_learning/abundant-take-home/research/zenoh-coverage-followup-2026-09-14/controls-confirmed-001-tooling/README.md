# Additive continuation after separate oracle confirmation

These tools are a separate, single-use recovery path. They do not rewrite the
original control run or its failed continuation. Source preparation and pure
tests do not authorize a launch; the coordinator owns launch after independent
review and terminal prior handles.

The input is an explicit SHA-256 reference to
`../controls-confirmed-001/summary.json`. That proof must select the original
exception-free, validated nop and a separately completed passing oracle for
the unchanged final task checksum
`67f1fdca75fd66f4cbdd6e4729a582c9fbbdac428a0299eefc74104dffa59fab`.
It must bind each physical run and each separate revalidation identity. If
the original nop's validation failed, the proof requires either a third
`nop_confirmation` source run from `../harbor-nop-confirmation-001/`, or an
`independent_nop_validation` certificate from
`../nop-independent-validation-001/`. It selects the exact separately
validated nop record. The actual original control summary has observation
failures for both oracle and nop, so the two-source branch cannot admit it.
The original raw reward zeros, scores, and exact observation errors remain
included as `superseded_oracle` and (when replaced) `superseded_nop` evidence;
the combined proof does not claim those original validation records passed.

The independent-revalidation branch creates no new control run. It keeps
the original raw nop result path and hash, rehashes the whole original run,
recomputes the historical admission lifecycle from raw samples, and requires
a bound fresh shared-lock terminal observation, absence of that exact
historical runner identity, and absence of matching containers. The original
failed record and observation errors remain unchanged. Certificate validity
does not assert uninterrupted observation coverage or relabel the original
control summary as passed.

`recovery_common.py` validates the existing detailed raw control checks and
the additive lineage. It rechecks all six fixed timing-comparison cases,
their raw verdict logs, before/after source maps, cleanup, and the separate
reviewed decision permitting one full oracle confirmation. It requires the
old continuation to have finished unsuccessfully without launching a stage.
The selected oracle must supply its own normal-Harbor verifier image proof
and matching filtered Docker inspection. Observed storage remains honestly
unconfigured; this tooling does not invent a storage quota.

The recovery coordinator holds the original `../continuation.lock` and runs
the following children sequentially, with at most one active child:

1. One canonical `reviews-final-001` quality review, using Sonnet 5, high
   effort, Mini 2.4.6, the captured Harbor runner, and shared admission/local 1.
2. The existing audit helper, followed by validation of every one of the 11
   actual rubric outcomes and the reviewed task-copy/raw-evidence identities.
3. The existing six omission probes and two saved-success diagnostics.
4. The existing full paired-regrade input gate.
5. All nine existing saved-source full verifier regrades, with the selected
   oracle's exact image proof forwarded explicitly.

The quality wrapper uses the separate bounded read-only observation helper.
It atomically creates the one canonical output before starting its child.
Existing canonical outputs, stale audits, and prior alternate final-task
quality jobs prevent a new attempt. Failures stop progression. Neither tool
retries, selects another review output, signals existing controls, changes
task budgets, edits raw rewards, or counts diagnostics/regrades as model
trials. Already started children are reaped before an interrupted wrapper or
coordinator exits.

`source-manifest.json` binds all three runtime files, their tests, and the
listed local imported or invoked helpers, including the current packager used only as a read-only
evidence validator. Any drift rejects execution. `source-capture/` retains
those exact source bytes for inspection. Changes require separately reviewed
tooling; do not modify a manifest that has been used by a run.
The independent nop validator source is included in this roster. Its actual
certificate and terminal references are bound transitively by the combined
controls summary and rechecked by the gate before each stage.

Both entry points default to check-only. Supply the exact summary path and
its actual hash; never substitute an unreviewed or pending proof. After the
coordinator's review, its explicit `--run` is the only launch step:

```sh
python -B research/zenoh-coverage-followup-2026-09-14/controls-confirmed-001-tooling/continue_confirmed_validation.py \
  --controls-summary research/zenoh-coverage-followup-2026-09-14/controls-confirmed-001/summary.json \
  --controls-sha256 ACTUAL_REVIEWED_SUMMARY_SHA256
```

Run from the repository root. The example omits `--run`, so it
performs validation only. A missing proof is an expected closed gate while
confirmation is pending. The quality wrapper is an internal child of the
coordinator and should not be launched separately.

Successful completion means validation execution and provenance are complete.
Paired rewards remain separate from the historical nine model outcomes.
There is no task promotion, packaging, score relabeling, or submission here.

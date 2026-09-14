# Temporary holds and dispatch rounds

The scheduler previously included every unfinished cell in its round minimum,
including tasks held by the independent image-readiness gate. A held cell with
zero dispatches would therefore prevent all ready cells from starting their
second dispatch. This change makes explicit temporary holds part of the round
calculation without changing task selection, targets, counts, or receipts.

The overlay belongs at `state["interleaving"]["held_cells"]`:

```json
{
  "[\"task-name\",\"model-name\",\"high\"]": {
    "job": "exact-primary-campaign-name",
    "reason": "Verifier revision awaiting validation",
    "held_at": "UTC timestamp for the activation journal"
  }
}
```

Keys use the existing `cell_key(task, model, effort)` encoding. A hold applies
only when its key exists and its job matches the current cell. Additional audit
metadata is retained. Unknown or retired keys and holds bound to superseded jobs
do not affect admission. Malformed current hold metadata blocks admission with a
distinct reason. An absent or empty overlay preserves the original behavior.

Held cells cannot start a new primary dispatch and do not contribute to the
minimum. If every cell is held, new primary work waits. Removing a hold restores
the original count; that cell can catch up before cells with higher counts move
again. Existing reservations remain valid. Explicit repair jobs retain their
existing separate admission/accounting behavior; task-readiness controls remain
independent of this overlay.

Both the fast priority check and the final shared-lock admission check call the
same hold parser. The watchdog also uses it so a held task does not conceal an
eligible but idle worker. Selection refresh retains hold metadata; superseded
keys become harmless history.

Validation is recorded in `summary.json`: 57 combined unit tests, three selection
refresh regressions, and a disposable live-worker smoke with 16 passing
assertions and zero model calls. The smoke loads the previous scheduler, applies
the existing safe-point updater, proves active-task heartbeat and PID continuity,
then checks ready dispatch, all-held waiting, release/catch-up, and preserved
targets, history, and receipts. All 36 supplied take-home files still match
`c1ae968`.

The tests and smoke mutate only disposable fixture state. This evidence does not
claim activation on campaign workers; live activation and overlay publication
have their own journal and source bindings.

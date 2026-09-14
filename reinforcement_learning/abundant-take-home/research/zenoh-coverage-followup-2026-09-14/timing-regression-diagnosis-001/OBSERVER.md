# Separate read-only observer

`read_only_observer.py` is new observer tooling. It does not change the frozen
control harness, shared scheduling code, controls, or existing evidence. The
regressions use temporary files and fake child objects; they do not start
containers, trial runners, or models.

The shared configuration's existing `.lock` is opened read-only, with no symlink
traversal. A bounded nonblocking shared lock excludes the scheduling writers'
exclusive lock while exact configuration and state bytes are read. Missing files,
JSON decode failures, and lock contention receive brief retries, for two seconds
by default. It never creates a missing shared file, substitutes an empty state,
uses cached observations, or calls `SharedAdmission.locked`.

The bound applies to lock/retry waiting, not a hypothetical filesystem operation
that hangs inside the kernel. Ordinary local regular-file reads are expected.
Permission, schema, unsafe-path, and output errors remain explicit failures.

The caller creates its own new output directory and supplies absolute paths:

```python
def observe(*, terminal):
    return observer.observe_once(
        observations, control, child.pid,
        shared_control=shared_control,
        gap_path=read_gaps,
        terminal=terminal,
    )

observation = observer.wait_with_observation(child, observe)
assert observation['exit_code'] == 0
assert not observation['errors']
assert observation['terminal_sample_valid']
admission = original_helpers.validate_admission(observations, control)
```

The successful JSONL rows retain the existing `validate_admission` schema.
`terminal_sample` identifies a newly read sample after the child has exited.
The original admission validator still checks observed peak one, final zero,
and the unchanged global cap. A terminal sample alone does not prove those
conditions or a passing verifier. Normal controls/result/source/test checks
remain required.

Recovered retries are logged separately with timestamps, filenames, error type,
phase, and attempt number. They do not permanently fail a run. Exhausting a
bounded read is a validation error even if a later sample recovers. A failed final
read cannot reuse an earlier successful sample. Every already-started child is
reaped; the observer never retries, restarts, or signals it.

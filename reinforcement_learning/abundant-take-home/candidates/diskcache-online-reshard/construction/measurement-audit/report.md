Verifier independence audit, 2026-09-13.

- Removed module-specific time mocking at epoch 1000. TTL tests now use real deadlines several hours in the future/past and compare exact stored deadlines before and after migration. They verify touch, explicit expiry and invisibility of expired entries without a sleep or a patched clock.
- Mutation tests reserve an unfinished entry so implementations can cut over eagerly on the last budgeted entry. A valid eager-cutover variant passes the entire suite.
- Removed barriers tied to private migration.copied storage and to builtins.open(...).write. Process interruption uses mandatory topology publication boundaries and a public bounded-step acknowledgement barrier. Direct abort-to-idle is accepted without requiring an intermediate aborting phase.
- The partial-file recovery requirement remains unchanged. Current tests do not claim to inject a mid-payload failure for every legal copy mechanism; historical reference-only partial-write evidence remains in the initial construction logs.
- Corrected the capacity-preservation fixture to make the destination per-shard limit smaller than a copied payload, so an erroneous copy-time cull is observable.
- No golden source or public scope changed. The current feature suite has 34 new-feature cases plus 11 compatibility cases.

Fresh offline Docker results (full feature suite plus selected upstream compatibility checks):

| Variant | Tests | Failures | Errors | Reward |
| --- | ---: | ---: | ---: | ---: |
| baseline | 45 | 0 | 34 | 0 |
| eager-cutover | 45 | 0 | 0 | 1 |
| mutant-refresh-deadlines | 1 | 1 | 0 | targeted negative control |
| oracle | 45 | 0 | 0 | 1 |

Every valid variant has zero skips. Negative controls fail behavioral assertions, with no collection errors. Source variants, stdout and XML are stored alongside this report. Each fresh container applies the unchanged golden patch; a variant then replaces only its named package files before running the task test.sh. The ordinary nested-yield variant changes checkpoint.py and native worker.py together. No variant changes a grader or application fixture.

The full suites retain native Luigi scheduling / DiskCache integrations. Public contracts remain implementation-independent, and the original take-home files remain unchanged. No model runs or new horizon/headroom claims were made.

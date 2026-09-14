Verifier independence audit, 2026-09-13.

- Replaced os.open-only monkeypatching with actual POSIX mode-bit denial. A separate reader drops root credentials when necessary, so imported os.open aliases, builtins.open and pathlib readers encounter the same permission boundary.
- No public requirements or golden source changed.

Fresh offline Docker results (full feature suite plus selected upstream compatibility checks):

| Variant | Tests | Failures | Errors | Reward |
| --- | ---: | ---: | ---: | ---: |
| baseline | 57 | 0 | 48 | 0 |
| mutant-swallow-permission | 1 | 1 | 0 | targeted negative control |
| open-alias | 57 | 0 | 0 | 1 |
| oracle | 57 | 0 | 0 | 1 |
| pathlib-reader | 57 | 0 | 0 | 1 |

Every valid variant has zero skips. Negative controls fail behavioral assertions, with no collection errors. Source variants, stdout and XML are stored alongside this report. Each fresh container applies the unchanged golden patch; a variant then replaces only its named package files before running the task test.sh. The ordinary nested-yield variant changes checkpoint.py and native worker.py together. No variant changes a grader or application fixture.

The full suites retain native Luigi scheduling / DiskCache integrations. Public contracts remain implementation-independent, and the original take-home files remain unchanged. No model runs or new horizon/headroom claims were made.

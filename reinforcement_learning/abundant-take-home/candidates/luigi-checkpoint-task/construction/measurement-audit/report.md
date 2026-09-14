Verifier independence audit, 2026-09-13.

- Normalized ordinary nested yielded requirements to DynamicRequirements only within the verifier helper; neither class nor wrapper type is required from the implementation. The instruction explicitly confirms both forms.
- Replaced os.open-only monkeypatching with real filesystem permission denial in a separate reader process.
- Removed an assertion that a private completion-cache entry must be popped. The test still requires the missing child to be returned for scheduling and the real workflow to finish; the contract permits bypassing stale cached completion.
- No golden source changed. A representative alternate implementation yields ordinary nested structures and integrates them with native worker scheduling, including private parameters.

Fresh offline Docker results (full feature suite plus selected upstream compatibility checks):

| Variant | Tests | Failures | Errors | Reward |
| --- | ---: | ---: | ---: | ---: |
| baseline | 50 | 0 | 42 | 0 |
| mutant-swallow-permission | 1 | 1 | 0 | targeted negative control |
| open-alias | 50 | 0 | 0 | 1 |
| oracle | 50 | 0 | 0 | 1 |
| pathlib-reader | 50 | 0 | 0 | 1 |
| plain-nested-yields | 50 | 0 | 0 | 1 |

Every valid variant has zero skips. Negative controls fail behavioral assertions, with no collection errors. Source variants, stdout and XML are stored alongside this report. Each fresh container applies the unchanged golden patch; a variant then replaces only its named package files before running the task test.sh. The ordinary nested-yield variant changes checkpoint.py and native worker.py together. No variant changes a grader or application fixture.

The full suites retain native Luigi scheduling / DiskCache integrations. Public contracts remain implementation-independent, and the original take-home files remain unchanged. No model runs or new horizon/headroom claims were made.

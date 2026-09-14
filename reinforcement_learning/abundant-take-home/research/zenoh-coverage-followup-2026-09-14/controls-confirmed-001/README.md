# Combined validated controls

`summary.json` selects oracle reward 1 and no-op reward 0 for the exact final task checksum `67f1fdca75fd66f4cbdd6e4729a582c9fbbdac428a0299eefc74104dffa59fab`. This is an additive selection certificate, not a new execution or a rewritten historical run.

The original no-op is independently validated from its unchanged raw result and lifecycle evidence after its controller encountered an observation error. One later normal oracle confirmation is independently validated from its unchanged full passing result and actual image/cleanup proof after its controller encountered a private-tag collision. Both failed controller summaries, the first oracle's raw zero, all observation errors and all source-run references remain explicit.

The oracle passed every required group using the unchanged task's existing retry policy. The no-op is a successful negative control: its build failure causes the remaining groups to be marked failed; it does not prove those behaviors were executed by the no-op.

The separate quality review and saved-submission validations use this certificate's exact SHA-256 and retain their own outcomes. They are not additional sweep trials and do not alter historical scores.

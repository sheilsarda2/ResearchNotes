# Preserve agent input without exposing it in process arguments

A C++ Zenoh attempt issued `pkill -9 -f zenohd`. Both the agent launcher and
its process guard carried the task instructions, including that word, in their
arguments. This makes unintended self-termination a plausible explanation for
the observed exit 137; the exact signal target was not recorded.

The runtime adapter now uploads the exact task text as mini-swe-agent 2.4.6's
final `run.task` configuration and passes only a neutral private file path.
The built-in configuration and explicit model settings keep their original
precedence. Task instructions, model, effort, budgets, and verifier are unchanged.

`cli_smoke.py` exercises the published mini-swe-agent 2.4.6 CLI and YAML loader
with offline factories. `cli-smoke.json` binds the wheel and source hashes and
records eight passing checks: exact UTF-8 task input (including emoji and CRLF),
absence of task text from argv, model, effort, adaptive thinking, maximum tokens,
yolo mode, and immediate exit. Six runtime tests also pass, including the actual
Harbor adapter with fake execution and file upload. No model calls were made.

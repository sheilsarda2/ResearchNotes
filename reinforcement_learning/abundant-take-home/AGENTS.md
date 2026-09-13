# Preserve the supplied take-home materials

The user requires the original take-home files to remain unchanged. Do the
work using separate tooling and outputs; do not rewrite the documentation or
change the task framing. This rule applies in future sessions as well.

Treat all files supplied in the original take-home commit (`c1ae968`) as
read-only, including:

- `README.md` and `Abundant Take Home.pdf`
- `.gitignore`, `.env.example`, and `.devcontainer/**`
- Everything under `restaurant-weekly-cost-control-audit/`, including task
  instructions, configuration, input files, bundled skills, reference solution,
  and verifier tests
- `scripts/doctor.sh`

Add or edit our own scripts, benchmark configurations, monitor customizations,
and result artifacts in separate files. Apply environment customizations through
our own scripts without editing the supplied setup files. Keep original task
requirements, scoring, and resource limits intact. Do not add work notes to the
supplied README; maintain session instructions in this new `AGENTS.md`.

Before finishing changes, check that the supplied files still match their
original committed contents. Only an explicit subsequent user instruction may
override this restriction.

# Candidate research and validation

The user confirmed submission on September 14, 2026. The [final submission record](final-submission-2026-09-14/README.md) identifies the exact PDF and ZIP. The [commit history](commit-history-2026-09-14.md) maps the research, task packages, runtime fixes, evaluation and report to their commits.

The notes below describe the initial screening stage. Dated research files remain historical snapshots; the final report covers eleven completed task grids and selects three samples.

This directory contains AI-assisted working evidence for a ten-task screening pack. It is not the human-written client report requested by the Abundant brief.

The pack targets durable state changes in existing Python data and workflow tools. A candidate must have a pinned upstream baseline, an explicit behavioral contract, independent verification, a passing reference implementation, and a failing untouched baseline. Current-model headroom and agent-step horizon remain unmeasured until the user's model trials.

Research distinguishes:

- upstream functionality and public solutions already available;
- benchmark observations with their model, harness, task and verifier versions;
- novel task requirements proposed here;
- hypotheses about model failures, never presented as observed headroom.

Sources are checked as of their recorded UTC retrieval time. No finite search establishes that a newer or unpublished solution does not exist. The final source refresh and repository pins make the scope auditable.

The supplied take-home files remain unchanged. New runnable tasks live in `../candidates/`; retained submission tasks can later be selected into `../samples/`.

Validation logs belong in `validation/`. Upstream clones used during construction belong in the ignored `cache/` directory and must not be required by a packaged task.

## Working evidence

- [Handover: Rerun, Burn and Zenoh five-task expansion, September 13](handover-2026-09-13-rust-expansion.md) — checkpointed research; no new runnable Rust tasks yet.
- [Current shortlist and accepted Harbor runs](shortlist.json)
- [Huey impact and unmet-demand review](huey-demand-review.md)
- [Huey source audit and three candidates](huey-candidates.md)
- [sqlite-utils source audit and three candidates](sqlite-candidates.md)
- [Luigi/DiskCache source audit and three candidates](workflow-candidates.md)
- [Current benchmark results, corrections, and counterevidence](benchmark-freshness.md)
- [Independent freshness audit](independent-freshness-audit.md)
- [Independent measurement audit](independent-measurement-audit.md)
- [Runtime isolation and validation limits](runtime-validation.md)
- [Snapshot/restore provenance](../candidates/diskcache-snapshot-restore/provenance.json)

Five distinct worker agents contributed: three owned implementation/research streams, and two independently audited freshness and measurement validity. The runtime allows three workers alongside the coordinator, so they ran in staggered waves, with one implementation owner per task at a time.

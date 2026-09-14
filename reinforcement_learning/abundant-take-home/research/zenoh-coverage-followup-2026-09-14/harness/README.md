# Focused Zenoh follow-up harness

`focused_validation.py` defaults to static check-only. It launches no model calls and does not regrade or edit the frozen v3 task. Run the explicit `--run` form inside the existing Linux devcontainer with the shared Docker daemon:

```sh
python3 research/zenoh-coverage-followup-2026-09-14/harness/focused_validation.py --run \
  --image sha256:4db7103cce0ba57d4bbaabd98d0608e11e4f3b39d96806c40748618f74bc95dd \
  --source gold \
  --output research/zenoh-coverage-followup-2026-09-14/harness/gold-smoke-001
```

The output path must be new. The harness imports only the captured admission/recovery helpers from `../immutable-tooling-v1/files/scripts`, verifies their manifest and file hashes, joins the existing shared pool at 14 slots, and permits one focused case at a time through its local lock. It neither enables nor changes interleaving policy. The diagnostic name must remain unmanaged by that policy.

An admitted case has 4 CPUs, 8192 MiB memory and memory+swap limit, no external network, and one 3600-second deadline covering setup and all three cargo targets. The separate cleanup grace uses bounded commands. Exact image identity, cached gold patch, and cached original timestamp test hashes are checked. The four production `src` trees from `/opt/gold` replace the corresponding `/workspace/build` trees, preserving the warmed target directory. All three new targets run with `--no-fail-fast`, the exact requested features, and one test thread. Failures remain visible; the expected exploratory admin reply-stack failure must not be interpreted as a harness failure or hidden.

`--source pristine` uses `/opt/pristine`; `--source saved --saved-source <submission-root>` uses only the four collected source trees from an unchanged saved submission. An optional `--patch <file>` is constrained to those four source trees, checked before application, and recorded separately. It can support a separately versioned corrected reference or omission mutant. The harness never applies patches to host tasks or source submissions.

Each run preserves its input manifest, driver/test snapshots, source manifests, command logs, cargo stdout, exit status, resource inspection, and claimed/released lifecycle. Container removal and an empty exact-name Docker listing precede claim release. If absence cannot be proved, the claim remains visible for recovery. `result.json` records any input drift or cleanup failure. A nonzero cargo test result may be a completed diagnostic; inspect `case_exit_code` and `case/cargo.stdout.log`, not only the harness process exit code. No container-retention mode is provided.

Authoring validation is static only. The coordinator owns the first cached-image compile and all subsequent separately labelled reference, saved-source, and mutant runs.

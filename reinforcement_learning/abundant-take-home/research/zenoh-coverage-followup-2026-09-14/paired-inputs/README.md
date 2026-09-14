# Original Zenoh v3 inputs for future paired regrades

`manifest.json` freezes the nine earliest counted Zenoh v3 model/effort cells. Their original rewards are eight passes and one failure. This is a provenance manifest only: no model call, replay, new counted trial, or score change occurred.

Selection is independently computed from the exact campaign summary, plan, and 99-cell scope archived under `sources/`. Every summary terminal row is checked against its original raw result hash, cell, checksum, and finish time; counted rows are ordered by UTC finish time, then original trial path. Four infrastructure outcomes remain explicitly excluded from first-counted selection.

`task-inputs.json` binds every v3 task file to the pre-campaign frozen whole-directory digest. It separately lists the unchanged instruction, task configuration, Dockerfile, and upstream archive. All nine native trajectories contain the exact instruction in their first user prompt, and all nine delivered-instruction markers agree with its hash and length. The first-user-prompt hash is the same across the nine cells.

`trials/<trial-id>.json` contains hashes and byte sizes for every original raw trial file, original scores and step counts, and each permitted submitted source file. All 1,842 submitted files across the nine trials match their original pre-verifier snapshots. The full pre-verifier and post-verifier evidence snapshots also match the current raw artifacts. Paths in `raw_files` and `source_files` are relative to the record's original `trial`; `task_input_manifest` is relative to this paired-inputs directory. No raw trial is relocated or rewritten.

Historical identity limits are explicit. The five earlier trials XsLhxGQ, D3tzpaW, X54KUJt, 32sQasx, and YtCswNv lack the later tool-runtime preflight marker. All nine retain agent-Python runtime markers, but immutable original agent image IDs are not established by the inspected records. This evidence supports matching task files, delivered instructions, declared configurations, and submitted source; it does not prove byte-identical installed images or runtimes.

The final corrected revision must still prove equal agent-facing inputs and run the entire final verifier against each immutable submission. Any resulting paired outcomes must be separately named, preserve these original rewards, and remain outside the counted model sweep. This manifest does not certify the corrected grader, reference, or any paired outcome.

`build_manifest.py` reads originals and writes only this directory. It refuses to replace a completed manifest. It verified that all 36 supplied take-home files still match commit `c1ae968` and that the frozen presentation results remained unchanged.

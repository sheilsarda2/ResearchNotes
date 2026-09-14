# Select a separate collector snapshot

`--data-dir` selects the collector input directory when creating a new plan with `--plan-only`. It defaults to `research/takehome-presentation-2026-09-14/data`. An explicit selection must be that default directory or a descendant of `research/takehome-presentation-2026-09-14/data-snapshots/`, inside the workspace and without symlinks. No existing data directory or packaging plan is overwritten.

For a future completed snapshot, the command has this shape; the placeholder directory below is not a supplied or verified 99-cell snapshot:

```sh
python3 -B scripts/package-takehome-evidence.py --plan-only \
  --data-dir research/takehome-presentation-2026-09-14/data-snapshots/99-TIMESTAMP \
  --plan research/takehome-presentation-2026-09-14/packaging/plan-99.json
```

The selector does not change the fixed 99-cell scope, original first-counted raw-trial selection, source hashes, control gates, or optional corrected-revision gates. A directory named `99-*` can still be incomplete. Assembly continues to require actual verified 99-cell coverage.

The plan captures a `selected_data` manifest alongside its other hashed inputs. It records the repository-relative directory and results path, plus the exact relative paths, hashes, byte lengths and captured-document keys for `results.json` and every scope, campaign-summary and campaign-plan snapshot referenced by that file. References are resolved only within the selected directory. Shared bytes may have multiple relative aliases; each alias is retained. Missing files, inconsistent or drifting hashes, results/source collisions, path traversal, symlinks, case/Unicode destination collisions and file/ancestor collisions are rejected. Unused historical source files, caches and CSVs are not included by this selector; requested finalized presentation assets can still be supplied separately.

At assembly, the captured inputs are authoritative. The packager does not reopen the selected/default collector directory or load current results. It reconstructs `data-snapshot/results.json` and its referenced paths under the separate supporting-evidence bundle, using captured bytes, and verifies each copy. This keeps the relative `snapshot` references readable after export. The selection's original directory remains provenance metadata. Changing or removing that original directory after planning cannot silently replace the captured results; changing the captured input bytes is rejected.

Assembly rejects `--data-dir`, even when it repeats the directory already selected in the plan. Use the plan alone. Historical plans without `selected_data` continue to use their original captured inputs and do not acquire an inferred selector. The existing 82-cell plan remains blocked by the 99-cell assembly gate.

Once coverage and validation gates pass, an explicitly labelled review pack may be assembled with an empty `report/` directory, alongside presentation assistance. The final take-home report with human-written prose remains a separate user requirement; the packager neither authors it nor claims it is complete.

`test_data_snapshot_selection.py` exercises temporary synthetic snapshots only. The separate `final-revision-adapter-002/` proof preserves adapter-001, the 82-cell data/plans, the separate 94-cell draft data, and all supplied take-home files. No new 99-cell dataset, actual revision mapping, or review pack is created by these checks.

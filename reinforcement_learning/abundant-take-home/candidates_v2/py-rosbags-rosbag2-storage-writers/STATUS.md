# py-rosbags-rosbag2-storage-writers — status

**Stage:** drafted (no Docker build, no Harbor run yet; phase rule). Authored 2026-09-13.

## What the task is

Form B excision on **rosbags 0.11.5** (`47be3eed`, 2026-08-18, Apache-2.0). Both rosbag2 storage writer
backends are stubbed out (`McapWriter` in `src/rosbags/rosbag2/storage_mcap.py`, `Sqlite3Writer` in
`src/rosbags/rosbag2/storage_sqlite3.py`; 352 lines restored by `solution/changes.patch`). The `Writer`
front-end, both readers, `AnyReader` and `rosbags-convert` stay intact. The direct upstream writer tests
(`test_writer.py`, `test_roundtrip.py`, `test_write_*` in `test_storage_mcap.py`, 12 tests) are removed
from the agent tree and restored by the verifier from a pristine copy.

Why not Form A: the GitLab MR API shows one merged MR ever (upstream pushes to master directly); the 2026
git log has no feature-scale storage change (largest: generated lyrical typestore +1011 lines; pathlib
reader refactor +179/-23; IDL annotation parsing +159/-35; MCAP QoS fix +50/-10). Recorded in
`provenance.json.feature.mining_note`.

## Oracle / verifier design

Separate offline verifier (`tests/Dockerfile`, `tests/test.sh`), `[[artifacts]]` transfers only
`/workspace/repo/src/rosbags` (Harbor re-materializes it at the same path in the verifier container).
Groups, exact counts, all must pass for reward 1 (`/logs/verifier/score.json` carries per-group results):

| group | tests | oracle | no-op |
|---|---|---|---|
| upstream_feature (restored writer tests) | 12 | 12 | 0 |
| upstream_regression (rest of pristine suite) | 134 | 134 | 130 (4 errors: anyreader fixtures write bags) |
| hidden_mcap_differential | 47 | 47 | 0 |
| hidden_sqlite3_differential | 22 | 22 | 0 |
| fixture_read_regression | 8 | 8 | 8 |
| anticheat + build/import | — | pass | pass |

Cross-implementation checks: mcap-python 1.4.0 (`SeekingReader` indexed reads with topic/time filters,
`NonSeekingReader(validate_crcs=True)`, raw record walker for offsets/lengths/grouping), foxglove `mcap`
CLI v0.3.0 (`mcap doctor` + `mcap info` on 7 produced files incl. empty, schema-only, multi-chunk,
fixture rewrites), stdlib `sqlite3` (`PRAGMA table_info` parity with a ROS 2 rolling recorded schema-v4
bag, row-level content), rosbag2 fixture bags from `ros2/rosbag2@08780f9e` (mcap + sqlite3: `cdr_test`,
`talker`, `wbag`, `convert_a`, `bag_with_topics_and_service_events_and_action`) rewritten through the
writers with message-set, per-topic count and deserialized-field equality. Fixtures are vendored in
`tests/fixtures/` (936 KB, SHA256SUMS checked at image build); the agent gets only the `cdr_test` pair
under `/workspace/fixtures`.

Local dry run (host venv, `tests/test.sh` with env overrides): oracle reward 1 with exact counts; no-op
reward 0 (see `provenance.json.validation.local_dry_run`).

## Build plan and estimate

- Agent image: `python:3.12.11-slim-bookworm` (digest pinned) + apt `git sqlite3` + pinned wheels from
  `uv.lock` (apsw, lz4, numpy 2.2.6, ruamel.yaml, zstandard, typing_extensions, pytest 9.0.3, declinate
  0.0.6/ty 0.0.80 for `test_cli.py`, mcap 1.4.0) + editable install with
  `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ROSBAGS=0.11.5`. Estimated 3–5 min, ~450 MB.
- Verifier image: same + curl, `mcap` CLI download (14 MB, sha256 per arch), pristine tests, fixtures,
  hidden tests. Estimated 4–6 min, ~500 MB. Verifier run time on the host: ~5 s of pytest; expect < 2 min
  in the container.

## Next commands

```
cd candidates_v2/py-rosbags-rosbag2-storage-writers
docker build -t rosbags-task-env environment/
docker build -t rosbags-task-verifier tests/
# oracle: run solve.sh in env image, copy src/rosbags into a verifier container at /workspace/repo/src/rosbags, run /tests/test.sh
harbor run --task . --agent oracle -y      # expect reward 1, score.json all groups passed_group=true
harbor run --task . --agent nop -y         # expect reward 0, only fixture_read_regression passes
```
Then fill `provenance.json.build` (image sizes, minutes, host) and `.validation`, set funnel stage.

## Risks

1. **Contamination (C_public).** The excised code is public since 2025; a model may reproduce it from
   memory. Mitigation: behaviour-level differential checks; no headroom claim without a rollout gate.
2. **Upstream spec deviations baked into the oracle.** rosbags writes sentinel times `2**63-1`/`0` for
   message-less chunks/files (spec says 0) — stated explicitly as R30. rosbags' sqlite3 reader only
   accepts `ros2msg` definitions and rejects two fixtures (schema encoding `unknown`, mismatching RIHS01
   hashes in Go-converted `talker.db3`); hidden tests avoid the reader on those and use mcap-python instead.
3. **Build-time network dependencies.** `mcap` CLI from GitHub releases and wheels from PyPI; both pinned
   by version/sha256 but the download can fail transiently. Fallback: vendor the CLI binary into
   `tests/` (14 MB) if the build proves flaky.
4. **`test_cli_is_up_to_date`** depends on `declinate==0.0.6` output being byte-identical to the
   committed CLI; pinned to upstream's lock version (verified locally). If the container differs,
   deselect that single test and lower `upstream_regression` to 133.
5. **Resource sizing.** Everything is small; cpus=4/8 GB are convention values, not measured needs.

## Alternates considered

- Form A on 2026 commits: rejected (no feature-scale storage change; see above).
- Excise only the MCAP index/summary path (leave an unindexed writer): rejected as a half-state with a
  weaker oracle; the whole writer plus the sqlite3 backend gives two interacting formats.
- Generate fixtures with `ros:jazzy` + `ros2 bag record` in a build stage: unnecessary, ros2/rosbag2
  ships recorded resources (libmcap 0.8.0/1.4.0, sqlite3 schema v4); kept as a fallback if more
  message types are wanted.
- Excising the MCAP indexed *reader* path too (Form B, bigger horizon): possible follow-up task
  (`py-rosbags-mcap-indexed-reader`), verified by mcap-python-written chunked/indexed files.

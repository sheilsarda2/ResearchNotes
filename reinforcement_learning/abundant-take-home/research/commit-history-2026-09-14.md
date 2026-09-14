# Take-home work and commit history

The user confirmed submission on September 14, 2026. The [submission record](final-submission-2026-09-14/README.md) identifies the final PDF and ZIP by SHA-256.

This additive stack preserves the work completed during the take-home: 30 task commits, nine shared research/tooling/evidence commits, and a final index commit. Commit order reflects archival grouping; dated research and raw validation records retain their original contents. Earlier commits remain unchanged.

## Submitted samples

| Sample | Submitted task source |
| --- | --- |
| Rerun chunk optimizer | [rs-rerun-chunk-optimizer](../candidates_v2/rs-rerun-chunk-optimizer/) |
| Zenoh timestamps | [Corrected v4 validation task](zenoh-coverage-followup-2026-09-14/rs-zenoh-timestamp-instrumentation-v4-validation/) |
| Burn checkpoint reader | [PyTorch reader v4](task-revisions/rs-burn-store-pytorch-reader-v4/) |

The final PDF reports 99 first counted results across eleven tasks. The submission includes 27 raw trials for the three retained task cohorts. Historical Zenoh trials and corrected-verifier regrades are recorded separately in the existing evidence.

## Task commits

Each task commit includes its authored package, pinned source assets, verifier, reference solution and provenance, plus its available oracle/no-op validation jobs. Task names below describe the original candidate; later revisions are linked in the earlier history.

| Task | Package | Commit |
| --- | --- | --- |
| Burn scoped checkpoint remapping | [burn-scoped-checkpoint-remap](../candidates/burn-scoped-checkpoint-remap/) | `aceaa52` |
| DiskCache online resharding | [diskcache-online-reshard](../candidates/diskcache-online-reshard/) | `be23442` |
| DiskCache snapshot and restore | [diskcache-snapshot-restore](../candidates/diskcache-snapshot-restore/) | `a7ae081` |
| Huey dead-letter redrive | [huey-deadletter-redrive](../candidates/huey-deadletter-redrive/) | `bc3f05a` |
| Huey SQLite leases | [huey-sqlite-leases](../candidates/huey-sqlite-leases/) | `cf41102` |
| Huey SQLite outbox | [huey-sqlite-outbox](../candidates/huey-sqlite-outbox/) | `fae2b98` |
| Luigi checkpoint tasks | [luigi-checkpoint-task](../candidates/luigi-checkpoint-task/) | `cc81fea` |
| Luigi generation targets | [luigi-generation-target](../candidates/luigi-generation-target/) | `6e73c0d` |
| Object Store full-range responses | [object-store-full-range-response](../candidates/object-store-full-range-response/) | `a6b3c07` |
| RapidJSON schema property membership | [rapidjson-schema-property-membership](../candidates/rapidjson-schema-property-membership/) | `f752186` |
| Rerun selected-time export | [rerun-selected-time-export](../candidates/rerun-selected-time-export/) | `d9386e6` |
| sqlite-utils relational merge | [sqlite-utils-relational-merge](../candidates/sqlite-utils-relational-merge/) | `af60195` |
| sqlite-utils resumable import | [sqlite-utils-resumable-import](../candidates/sqlite-utils-resumable-import/) | `64b39af` |
| sqlite-utils schema planning | [sqlite-utils-schema-plan](../candidates/sqlite-utils-schema-plan/) | `3e44afd` |
| Zenoh queryable completeness | [zenoh-queryable-completeness](../candidates/zenoh-queryable-completeness/) | `20f26fe` |
| Foxglove C++ parameter handling | [cpp-foxglove-sdk-parameter-handler](../candidates_v2/cpp-foxglove-sdk-parameter-handler/) | `859f8cd` |
| MCAP C++ indexed reading | [cpp-mcap-indexed-reader](../candidates_v2/cpp-mcap-indexed-reader/) | `40eb820` |
| Rerun C++ FFI integration | [cpp-rerun-cpp-ffi-bridge](../candidates_v2/cpp-rerun-cpp-ffi-bridge/) | `3412141` |
| rosbag2 mixed-serialization playback | [cpp-rosbag2-mixed-serialization-playback](../candidates_v2/cpp-rosbag2-mixed-serialization-playback/) | `dd08a5d` |
| Zenoh C++ connectivity | [cpp-zenoh-cpp-connectivity-api](../candidates_v2/cpp-zenoh-cpp-connectivity-api/) | `a69a07b` |
| MCAP Python chunk and summary writing | [py-mcap-writer-chunk-summary](../candidates_v2/py-mcap-writer-chunk-summary/) | `5e23532` |
| ONNX reference RNN evaluation | [py-onnx-reference-rnn](../candidates_v2/py-onnx-reference-rnn/) | `b6d8a21` |
| Rosbags rosbag2 storage writers | [py-rosbags-rosbag2-storage-writers](../candidates_v2/py-rosbags-rosbag2-storage-writers/) | `57e811c` |
| Xarray Zarr chunk alignment | [py-xarray-zarr-align-chunks](../candidates_v2/py-xarray-zarr-align-chunks/) | `9bcb548` |
| Zarr cast and scale-offset codecs | [py-zarr-python-cast-value-scale-offset](../candidates_v2/py-zarr-python-cast-value-scale-offset/) | `df1fdcc` |
| Burn ONNX RNN runtime weights | [rs-burn-onnx-rnn-runtime-weights](../candidates_v2/rs-burn-onnx-rnn-runtime-weights/) | `53a36a4` |
| Burn PyTorch checkpoint reading | [rs-burn-store-pytorch-reader](../candidates_v2/rs-burn-store-pytorch-reader/) | `db06bc8` |
| MCAP Rust CLI recovery parity | [rs-mcap-cli-recover-parity](../candidates_v2/rs-mcap-cli-recover-parity/) | `04610a6` |
| Rerun chunk optimization | [rs-rerun-chunk-optimizer](../candidates_v2/rs-rerun-chunk-optimizer/) | `d84762c` |
| Zenoh timestamp instrumentation | [rs-zenoh-timestamp-instrumentation](../candidates_v2/rs-zenoh-timestamp-instrumentation/) | `444c059` |

The Huey SQLite leases and sqlite-utils schema planning commits also preserve their separate v2 verifier corrections and saved diagnostics. Burn, DiskCache and Zenoh repairs already committed during the campaign remain in the history below.

## Shared work in this stack

| Step | Commit |
| --- | --- |
| Preserve candidate construction and mining workflows | `9bfd562` |
| Preserve candidate discovery, demand audits and shortlist decisions | `f8dcd71` |
| Preserve model-effort launch configurations and agent setup | `5a27ded` |
| Prioritize missing first results across the benchmark sweep | `81ea5b0` |
| Cache task directory hashes during job locking and bind activation sources | `60f3f9c` |
| Group benchmark viewer results by explicit model effort | `9401f0f` |
| Preserve the restaurant benchmark evidence audit | `2510458` |
| Preserve candidate validation and shortlist quality reviews | `5a92c8d` |
| Preserve supplemental runtime and verifier diagnostic evidence | `073cf3b` |

The final commit adds this index, the [commit ledger](commit-archive-2026-09-14/commits.json), the [verification record](commit-archive-2026-09-14/verification.json), and the user-confirmed submission status.

## Earlier implementation and reporting history

| Step | Commit |
| --- | --- |
| Add effort, PST timestamps, and check pass rates to jobs viewer | `3cd2af7a` |
| Stop benchmark agent descendants before grading and bind trial evidence | `345b41f3` |
| Pin benchmark agent Python and recover pre-model startup failures | `8b761cd5` |
| Count completed verifier outcomes after guarded abnormal agent exits | `c1850eb0` |
| Fix Zenoh task contract and isolate benchmark cleanup failures | `231b7246` |
| Preserve benchmark runners waiting for shared capacity | `229ab2e8` |
| Bound Mini tool timeout cleanup and preserve interrupted-trial accounting | `0432954e` |
| Correct Burn count fixture and recover runtime inventory races | `92d88fc2` |
| Clarify DiskCache lease semantics and promote validated revisions | `98658da3` |
| Keep primary workers waiting for an interleaved dispatch round | `8cf7b269` |
| Persist interleaved sweep progress and contain scheduling incidents | `fecb5d70` |
| Fix Burn reader hidden Debug requirement and resume validated cohort | `74741b11` |
| Record live runtime audits and bounded Zenoh regression replay | `ea7b59dd` |
| Project runtime source hashes into benchmark evidence | `be00141e` |
| Flag incomplete usage after unanswered model queries | `f0320d73` |
| Keep held tasks from blocking sweep rounds | `1069bd88` |
| Add take-home readiness and evidence packaging tools | `384619d0` |
| Expand take-home reporting to all eleven completed task grids | `190b4359` |
| Revise take-home slides and add an executive summary | `31571b2f` |
| Move candidate selection into the executive summary | `4892d72a` |
| Package the final PDF report with the selected Harbor tasks | `d63f50d1` |

## Verification and scope

All 16,773 files in the 39 archive commits were checked against the reviewed source snapshots and their committed Git blobs. This includes 139 historical oracle/no-op jobs and 87 adjacent job logs. One incomplete historical validation job (`snapshot-oracle-v1`) has no trial configuration; its existing records are preserved.

All 36 supplied files still match `c1ae968`. The submitted PDF and ZIP retain their recorded hashes. Credential checks found only public example keys in upstream fixtures and ordinary text matches.

The remaining scheduler changes passed 53 offline tests: eight coverage-priority, thirteen interleaving, eight activation and twenty-four portable watchdog tests. Two watchdog tests require Linux `/proc` and `pidfd`, so they were excluded on macOS. Python, Bash and JavaScript syntax checks passed, along with isolated lock-cache checks. Viewer integration and live activation were not rerun during archival.

The editable `Results deck 091426.pptx`, timestamped intermediate slide exports, compiler output, upstream checkouts, generated verifier executables, runtime locks and live campaign-control changes stay local. The large submission ZIP stays local with its hash and build receipt committed. Earlier reviewed presentation exports already in Git remain in history.

## Inspecting the stack

Run `git log --oneline d63f50d..HEAD` to view this stack, or `git show <commit>` to inspect a step. The repository branch is `sw_rl`.

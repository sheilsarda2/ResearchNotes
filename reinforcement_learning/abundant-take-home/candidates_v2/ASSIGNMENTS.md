# candidates_v2 assignments

Fifteen primary tasks, five per language, plus alternates. Each is constructed by one agent following `CONVENTIONS.md`. Preference lists cite mined PRs from `mining/*.json`; the constructing agent picks the highest-preference item that survives inspection and records rejected alternates in `STATUS.md`. Task IDs are `<lang>-<repo>-<slug>`.

## Python

| ID | Repo | Form | Preference list | Oracle |
|---|---|---|---|---|
| py-zarr-python-* | zarr-developers/zarr-python | A | #3874 cast_value + scale_offset codecs (Apr 30; zarrs #409 implements the same codec, giving a cross-implementation oracle); #3925 get_ranges; #3679 memory store registry; #3781 structured dtypes; #3802 rectilinear chunks | zarrs (Rust) writes and reads the codec: byte-identical roundtrip both directions; PR tests; upstream codec suite |
| py-mcap-* | foxglove/mcap (python/mcap) | B | Excise the indexed-read path (summary, chunk index, message index, seeking) or the writer's chunk/index/summary emission | Conformance corpus expected JSON via the Python runner scripts; cross-check with Rust and Go readers in the verifier image |
| py-onnx-reference-* | onnx/onnx (onnx/reference) | B | Excise one operator family in the pure-Python reference evaluator: RNN family (RNN, LSTM, GRU), or Sequence ops, or Resize and GridSample | ONNX backend node test expected outputs; onnxruntime differential on the same models |
| py-rosbags-* | gitlab.com/ternaris/rosbags | A or B | Latest feature-scale merge requests touching rosbag2 MCAP or sqlite3 storage, or excise the MCAP writer index path | Fixtures recorded by rosbag2 (sqlite3 and mcap) shipped in the verifier image; mcap-python reads what rosbags writes |
| py-xarray-* | pydata/xarray | A | Newest feature-scale PR touching the zarr backend, encoding, or indexing with tests; consult `mining/pydata__xarray.json` when present, else mine with `scripts/mine_v2.py pydata/xarray` | PR tests plus zarr-python roundtrip of datasets written by the feature |

## Rust

| ID | Repo | Form | Preference list | Oracle |
|---|---|---|---|---|
| rs-rerun-* | rerun-io/rerun (store crates) | A | Newest store PR with tests among #12390 virtual ChunkStore partial query results, #12312 deep Chunk slicing, #12277 video out-of-order samples, #12557 gc prefetch; or any newer store/dataframe PR found in mining | Crate tests plus snapshot tests; `rerun rrd compare` where the feature changes encoded output |
| rs-zenoh-* | eclipse-zenoh/zenoh | A | #2620 timestamp instrumentation (Jul 21; zenoh-python #739 exposes the same API, giving a cross-language parity oracle); #2320 multiple client connections; #2397 wait-until-callback-drop | zenohd interop tests; parity with zenoh-python behavior; storage-manager and session test suites |
| rs-burn-onnx-* | tracel-ai/burn-onnx | A | #466 RNN weights as runtime inputs (Aug 21); #445 custom op hooks (Aug 12); #270 constrained Einsum | `onnx-official-tests` node corpus; onnxruntime differential on generated models |
| rs-mcap-cli-* | foxglove/mcap (rust/cli) | A | #1647 recover parity (Jun 1) optionally composed with #1646 cat parity; #1648 ros2 db3 conversion | Go `mcap` CLI as reference: identical output on the conformance corpus and on truncated or corrupted files |
| rs-burn-store-* | tracel-ai/burn (burn-store, burn-pack) | A | #5593 PyTorch reader hardening (Sep 10); #5349 burnpack streaming (Aug 20); #5064 burn-pack extraction (Jun 16) | PyTorch-produced fixtures (torch in verifier image) and safetensors reference; crate tests |

Rust alternates: zarrs #409 cast_value codec (zarr-python oracle, pairs with the Python zarr task); arrow-rs parquet #10141 or #9848 (pyarrow oracle); redb or fjall crash tests.

## C++

| ID | Repo | Form | Preference list | Oracle |
|---|---|---|---|---|
| cpp-rerun-cpp-* | rerun-io/rerun (rerun_cpp) | A or B | Newest rerun_cpp PR with tests (search PRs touching `rerun_cpp/src` since 2026-03); else excise a hand-written SDK component (recording stream batching, save/spawn, or a datatype builder set) | `docs/snippets/compare_snippet_output.py`: C++ snippet `.rrd` must match Python and Rust via `rerun rrd compare` |
| cpp-zenoh-cpp-* | eclipse-zenoh/zenoh-cpp | A | #750 connectivity API (Mar 13; parity with Rust #2301 and zenoh-python); else excise liveliness or querier wrappers | Network tests against zenohd; output parity with zenoh-python `session.info` |
| cpp-mcap-* | foxglove/mcap (cpp/mcap) | B | Excise the indexed reader (summary, chunk index, message index, seek) or the streamed writer's chunk/index/summary emission | Conformance corpus through the C++ runners; Rust and Go readers must read C++-written files |
| cpp-rosbag2-* | ros2/rosbag2 | A | Newest storage or MCAP plugin PR with tests since 2026-03 (mine with `scripts/mine_v2.py ros2/rosbag2` if `mining/ros2__rosbag2.json` is absent); alternate ros2/rmw_zenoh | rosbag2 gtests; differential playback between sqlite3 and mcap storage plugins |
| cpp-foxglove-sdk-* | foxglove/foxglove-sdk (cpp, c) | A | #1392 point cloud compression in C/C++ (Aug 10; Rust #1391 and Python #1393 are the reference implementations) | Rust SDK decodes and compares C++-produced compressed point clouds; MCAP outputs compared across SDKs |

C++ alternate and hard anchor: RocksDB commit-mined features with fault-injection tests, for example `#14448` sequential single deletes to range tombstones (Apr 4), `#14322` background SST validation on open (Mar 3), `#15086` snapshot version tracking (Sep 11), `#15104`/`#15117` format_version 8 (Aug).

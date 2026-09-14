# rosbag2: read and play bags whose topics use mixed serialization formats

You are working in a colcon workspace containing the `ros2/rosbag2` source tree (ROS 2 Rolling, C++20). Bags recorded by non-ROS tools regularly hold topics in more than one serialization format, for example CDR-encoded ROS topics next to a protobuf-encoded `foxglove.CompressedVideo` stream in the same MCAP file. Today `rosbag2_cpp::readers::SequentialReader::open()` rejects any bag whose topics do not all share one serialization format, so `ros2 bag play` refuses such a bag even when the user only wants the topics it can decode. Lift that restriction as specified below. The behaviour must be identical for the `sqlite3` and the `mcap` storage plugin.

## Workspace, build and test

- Source tree: `/workspace/ws/src/rosbag2` (rosbag2 0.34.0 at commit `0580af8a`, branch `rolling`). The ROS underlay is `/opt/ros/rolling` (Ubuntu 26.04, Python 3.14, GCC from the distro). The binary rosbag2 debs have been removed; the overlay is the only rosbag2 in the image.
- Warm build: `/workspace/ws/build` and `/workspace/ws/install` (merged install) already contain a full build of `lz4_cmake_module zstd_cmake_module mcap_vendor rosbag2_interfaces rosbag2_test_msgdefs rosbag2_test_common rosbag2_storage rosbag2_storage_sqlite3 rosbag2_storage_mcap rosbag2_storage_default_plugins rosbag2_cpp rosbag2_compression rosbag2_compression_zstd rosbag2_transport rosbag2_py ros2bag` with `BUILD_TESTING=ON`, so rebuilds are incremental.
- Build: `ws-build [package ...]` (defaults to the set above; runs `colcon build --merge-install` in a clean shell, 2 workers x `make -j2`; a full rebuild of the touched packages and their dependents took about 3 minutes on the validation host with 4 build jobs, single packages much less). Do not run `colcon build` in a shell that has the overlay sourced; `ws-build` handles that.
- Test: `ws-test <package> [test_executable] [gtest args]`, e.g. `ws-test rosbag2_cpp test_sequential_reader --gtest_filter='*Mixed*'`. Playback tests need the RMW; the image uses `rmw_fastrtps_cpp` (serialization format `"cdr"`) with localhost-only discovery. `ros2 bag play|record|info` from the overlay are on `PATH`.
- Every shell sources the underlay and the overlay (`/etc/ros_ws_env.sh`). There is no network.
- Build flags: default ament flags (no `-Werror`, sanitizers off). Linters (cpplint, uncrustify, cppcheck) are not run by the verifier, but the code should follow the repository's style.

### What is transferred for verification

Only the `src/` and `include/` directories of these six packages are transferred and evaluated:

```
rosbag2_storage  rosbag2_storage_sqlite3  rosbag2_storage_mcap  rosbag2_cpp  rosbag2_compression  rosbag2_transport
```

Everything else (`CMakeLists.txt`, `package.xml`, `test/` directories, `rosbag2_py`, `ros2bag`, all other packages) is restored from a pristine copy before the verifier rebuilds. Consequences: do not add new translation units (they would not be compiled); put new declarations in existing headers or new headers under an existing `include/` tree; keep `rosbag2_py` and `ros2bag` unchanged (the CLI behaviour must follow from the C++ changes alone). You may edit tests locally while developing; they are not transferred. The verifier compiles its own test sources against your headers, so the public names and signatures below must match exactly.

## Required public interface

All of the following are additions to existing public headers; existing declarations stay unless listed under "May change".

1. `rosbag2_storage::SerializedBagMessage` (`rosbag2_storage/serialized_bag_message.hpp`) gains a public member
   `std::string serialization_format;` (default-constructed, i.e. empty, meaning unknown). It is informational when writing: the serialization format of a topic is defined by the `TopicMetadata` passed to `create_topic()`.
2. `rosbag2_cpp::reader_interfaces::BaseReaderInterface` (`rosbag2_cpp/reader_interfaces/base_reader_interface.hpp`) gains a non-pure virtual
   `virtual std::vector<rosbag2_storage::TopicMetadata> get_undeliverable_topics() const;`
   whose default implementation returns an empty vector (a reader that delivers every topic). Test doubles derived from this interface override it.
3. `rosbag2_cpp::Reader` (`rosbag2_cpp/reader.hpp`) gains
   `std::vector<rosbag2_storage::TopicMetadata> get_undeliverable_topics() const;` forwarding to the implementation.
4. `rosbag2_cpp::readers::SequentialReader` (`rosbag2_cpp/readers/sequential_reader.hpp`) overrides `get_undeliverable_topics() const`. `rosbag2_compression::SequentialCompressionReader` inherits the behaviour.
5. `rosbag2_transport::ReadersManager` (`rosbag2_transport/readers_manager.hpp`) gains
   `[[nodiscard]] std::vector<rosbag2_storage::TopicMetadata> get_undeliverable_topics() const;`
   returning the concatenation of every managed reader's undeliverable topics, in the order the readers were provided; a topic present in several bags may appear more than once.

May change: the protected virtuals `SequentialReader::check_topics_serialization_formats(...)` and `SequentialReader::check_converter_serialization_format(...)` have no in-tree users and may be removed or kept.

## Behavioural contract

Let `requested` be `ConverterOptions::output_serialization_format` passed to `Reader::open(storage_options, converter_options)`. `Reader::open(uri)` passes default (empty) converter options. `ConverterOptions::input_serialization_format` is ignored.

- **C1. Messages are tagged.** Every message returned by `Reader::read_next()` (plain or compressed bag, converted or not) has `serialization_format` set to the format it is delivered in. Storage plugins set it when reading: the sqlite3 plugin from the topic's stored serialization format (including bags with the pre-Foxy schema), the mcap plugin from the channel's message encoding. If a storage plugin leaves it empty, the reader fills it from the topic's metadata (`TopicMetadata::serialization_format`, keyed by topic name). A converted message reports the converter's output format. The compression writer's compressed copy of a message keeps the original message's `serialization_format`, and messages decompressed by the compression reader are tagged the same way as uncompressed ones.
- **C2. No requested format: everything as stored.** With `requested` empty, a bag with any mix of serialization formats opens, `read_next()` delivers every message unconverted, and `get_undeliverable_topics()` is empty.
- **C3. Requested format matches at least one topic: no conversion, partial delivery.** If `requested` equals the stored format of at least one topic, `open()` succeeds and sets up no converter. Messages of topics stored in `requested` are returned as stored. Topics stored in another format are *undeliverable*: `get_undeliverable_topics()` returns exactly their `TopicMetadata` (in a uniform bag this list is empty), and `read_next()` throws `std::runtime_error` when the next message belongs to one of them instead of handing out data in an unexpected format. After `set_filter()` excludes the undeliverable topics (for example `StorageFilter::topics = {"/chatter"}`), the rest of the bag reads to the end without error.
- **C4. Requested format matches no topic.** If all topics share one stored format, a converter from that format to `requested` is set up as before (open throws `std::runtime_error` if the converter plugin does not exist; in this environment only the rmw `"cdr"` converter exists, so requesting `"cdr"` on a bag whose only topic is `"protobuf"` throws at open). If the topics have mixed formats, conversion is unsupported and `open()` throws `std::runtime_error`.
- **C5. `get_undeliverable_topics()`** throws `std::runtime_error` when the reader is not open; returns an empty vector when `requested` is empty or when a converter is in use; otherwise lists the topics not stored in `requested`. Both storage plugins yield the same result for the same content.
- **C6. Player resolution happens at construction.** `rosbag2_transport::Player` determines which selected topics are playable while it is being constructed, before any playback output or progress bar appears, using the readers' undeliverable topics (those not deliverable in the local rmw serialization format, `rmw_get_serialization_format()`).
  - A topic/service-event/action-interface topic that is undeliverable **and requested by name** in `PlayOptions` (`topics_to_filter`, `services_to_filter`, `actions_to_filter`) makes the constructor throw `std::runtime_error`; the message names every such topic and its serialization format. `ros2 bag play --topics <that topic>` therefore exits non-zero and its output contains the topic name.
  - An undeliverable topic that is only **implicitly** selected (play-everything default, or matching a regex) is excluded from playback and a warning is logged through the node's logger (`RCLCPP_WARN`) naming every excluded topic exactly once (even if recorded in several bags), together with the format; the rest of the bag plays. `ros2 bag play <bag>` exits zero, prints the excluded topic's name, and publishes every message of the playable topics.
  - If excluding the undeliverable topics leaves **no** playable topic selected, the constructor throws `std::runtime_error` naming the excluded topics instead of "succeeding" without publishing anything.
  - A topic the user **explicitly excluded** (`exclude_topics_to_filter`, `exclude_service_events_to_filter`, `exclude_actions_to_filter`, `exclude_regex_to_filter`) is neither warned about nor mentioned; a topic excluded by the automatic mechanism above is excluded through the same storage filter lists so storage plugins never deliver it. Bags with a uniform serialization format play exactly as before.
  - `ros2 bag play` (Python) is not modified; the exit code follows from `Player`'s constructor throwing (the rosbag2_py wrapper already propagates the exception).
- **C7. Rewrite/convert passes bytes through.** `rosbag2_transport::bag_rewrite` of a mixed-format bag with no output format requested writes every message of every selected topic to the output bag(s) unmodified, with each topic's serialization format preserved in the output metadata; filtering the output to the local-format topic yields a bag containing only that topic.
- **C8. Storage-plugin parity.** For identical content, the sqlite3 and mcap plugins must produce identical results for everything above: topic formats reported by `get_all_topics_and_types()`, `get_undeliverable_topics()`, the sequence of `(topic, receive timestamp, serialization_format, payload bytes)` returned by `read_next()`, which calls throw, and the `ros2 bag play` outcomes (exit status, whether the offending topic is named, and the messages a subscriber receives).
- **C9. Existing behaviour is preserved.** All existing tests of the six packages keep passing, in particular reader/writer/compression/topic-filter/readers-manager/rewrite suites. No timing-based behaviour is introduced.

## How the submission is verified

An offline verifier rebuilds the six packages plus `rosbag2_storage_default_plugins`, `rosbag2_compression_zstd`, `rosbag2_py` and `ros2bag` from your transferred `src/` and `include/` directories on a pristine tree, then runs:

1. gtest suites of the touched packages (the upstream suites for the reader, serialization converter, both storage plugins, playback and rewrite, extended for this feature) plus pristine regression suites (multifile reader, converter factory, sequential writer, storage-without-metadata, topic filters of both plugins, compression reader and writer, readers manager). Exact case counts are required; skips count as failures.
2. A storage-plugin differential (C8): synthetic bags with `/chatter` (`test_msgs/msg/BasicTypes`, `"cdr"`) and `/camera/video_compressed` (`foxglove.CompressedVideo`, `"protobuf"`, opaque payloads), plain and zstd-compressed (message and file mode), written and read with each plugin through the public C++ API; dumps must agree and satisfy C1-C5.
3. `ros2 bag play` on those bags (C6) with a subscriber counting the messages actually published, for both plugins.

Reward is 1 only if every group passes. Per-group results are written to `score.json` for attribution.

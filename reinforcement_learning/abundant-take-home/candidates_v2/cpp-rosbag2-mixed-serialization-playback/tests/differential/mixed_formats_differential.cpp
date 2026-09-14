// Storage-plugin differential harness for cpp-rosbag2-mixed-serialization-playback.
//
//   mixed_formats_differential <workdir> <storage_id>
//
// Writes synthetic bags with the given storage plugin under <workdir>/bags/<storage_id>/ and reads
// them back through the public rosbag2_cpp / rosbag2_compression / rosbag2_transport API, dumping
// what the reader hands out as JSON to <workdir>/<storage_id>.json. The verifier runs it once per
// storage plugin (sqlite3, mcap) and requires the two dumps to agree with each other and with the
// behavioural contract in instruction.md. Timestamps compared are receive timestamps only; the
// sqlite3 plugin does not persist send timestamps.
//
// Bags:
//   mixed          /chatter (test_msgs/msg/BasicTypes, local rmw format) and
//                  /camera/video_compressed (foxglove.CompressedVideo, "protobuf"), 5 messages each
//   uniform        /chatter only
//   only_proto     /camera/video_compressed only
//   mixed_msgzstd  mixed, written through SequentialCompressionWriter, zstd, MESSAGE mode
//   mixed_filezstd mixed, written through SequentialCompressionWriter, zstd, FILE mode
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "rclcpp/qos.hpp"
#include "rclcpp/serialization.hpp"
#include "rclcpp/serialized_message.hpp"
#include "rclcpp/time.hpp"
#include "rmw/rmw.h"

#include "rosbag2_compression/compression_options.hpp"
#include "rosbag2_compression/sequential_compression_writer.hpp"
#include "rosbag2_cpp/converter_options.hpp"
#include "rosbag2_cpp/reader.hpp"
#include "rosbag2_cpp/writer.hpp"
#include "rosbag2_storage/ros_helper.hpp"
#include "rosbag2_storage/serialized_bag_message.hpp"
#include "rosbag2_storage/storage_filter.hpp"
#include "rosbag2_storage/storage_options.hpp"
#include "rosbag2_storage/topic_metadata.hpp"
#include "rosbag2_transport/reader_writer_factory.hpp"

#include "test_msgs/msg/basic_types.hpp"

namespace fs = std::filesystem;

namespace
{
const std::string kChatter = "/chatter";
const std::string kProto = "/camera/video_compressed";
constexpr size_t kNumMessagesPerTopic = 5;
constexpr size_t kSerializedCapacity = 256;  // > CDR size of test_msgs/msg/BasicTypes (45 bytes)

// ------------------------------------------------------------------------------------ tiny JSON
std::string json_str(const std::string & s)
{
  std::ostringstream out;
  out << '"';
  for (unsigned char c : s) {
    switch (c) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (c < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c) << std::dec;
        } else {
          out << c;
        }
    }
  }
  out << '"';
  return out.str();
}

std::string json_list(const std::vector<std::string> & items)
{
  std::string out = "[";
  for (size_t i = 0; i < items.size(); ++i) {
    if (i) {out += ",";}
    out += items[i];
  }
  return out + "]";
}

std::string json_obj(const std::vector<std::pair<std::string, std::string>> & fields)
{
  std::string out = "{";
  for (size_t i = 0; i < fields.size(); ++i) {
    if (i) {out += ",";}
    out += json_str(fields[i].first) + ":" + fields[i].second;
  }
  return out + "}";
}

std::string hex(const rcutils_uint8_array_t & arr)
{
  static const char * digits = "0123456789abcdef";
  std::string out;
  out.reserve(arr.buffer_length * 2);
  for (size_t i = 0; i < arr.buffer_length; ++i) {
    out.push_back(digits[arr.buffer[i] >> 4]);
    out.push_back(digits[arr.buffer[i] & 0xf]);
  }
  return out;
}

std::string message_json(const rosbag2_storage::SerializedBagMessage & m)
{
  return json_obj({
      {"topic", json_str(m.topic_name)},
      {"recv", std::to_string(m.recv_timestamp)},
      {"format", json_str(m.serialization_format)},
      {"data_hex", json_str(m.serialized_data ? hex(*m.serialized_data) : std::string())},
    });
}

std::string names_json(const std::vector<rosbag2_storage::TopicMetadata> & topics)
{
  std::vector<std::string> names;
  for (const auto & t : topics) {names.push_back(json_str(t.name));}
  return json_list(names);
}

std::string topics_json(const std::vector<rosbag2_storage::TopicMetadata> & topics)
{
  std::map<std::string, std::string> sorted;
  for (const auto & t : topics) {sorted[t.name] = t.serialization_format;}
  std::vector<std::pair<std::string, std::string>> fields;
  for (const auto & [name, fmt] : sorted) {fields.emplace_back(name, json_str(fmt));}
  return json_obj(fields);
}

// ------------------------------------------------------------------------------------ writing
std::vector<uint8_t> fake_protobuf_payload(uint8_t index)
{
  // Minimal valid protobuf encoding of a foxglove.CompressedVideo: frame_id "cam0", 16 data
  // bytes, format "h264". The bytes must round-trip through storage unmodified.
  std::vector<uint8_t> payload = {0x12, 0x04, 'c', 'a', 'm', '0', 0x1a, 0x10};
  payload.insert(payload.end(), 16, index);
  const std::vector<uint8_t> format_field = {0x22, 0x04, 'h', '2', '6', '4'};
  payload.insert(payload.end(), format_field.begin(), format_field.end());
  return payload;
}

rclcpp::QoS bag_qos()
{
  // Reliable + transient local so a subscriber that joins while `ros2 bag play` is starting
  // still receives every message (the CLI check relies on this).
  return rclcpp::QoS(rclcpp::KeepLast(10)).reliable().transient_local();
}

void write_bag(
  const fs::path & uri, const std::string & storage_id, bool with_chatter, bool with_proto,
  const rosbag2_compression::CompressionOptions * compression)
{
  std::unique_ptr<rosbag2_cpp::Writer> writer;
  if (compression) {
    writer = std::make_unique<rosbag2_cpp::Writer>(
      std::make_unique<rosbag2_compression::SequentialCompressionWriter>(*compression));
  } else {
    writer = std::make_unique<rosbag2_cpp::Writer>();
  }
  rosbag2_storage::StorageOptions options;
  options.uri = uri.string();
  options.storage_id = storage_id;
  writer->open(options);

  const std::string rmw_format = rmw_get_serialization_format();
  if (with_chatter) {
    rosbag2_storage::TopicMetadata md;
    md.name = kChatter;
    md.type = "test_msgs/msg/BasicTypes";
    md.serialization_format = rmw_format;
    md.offered_qos_profiles = {bag_qos()};
    writer->create_topic(md);
  }
  if (with_proto) {
    rosbag2_storage::TopicMetadata md;
    md.name = kProto;
    md.type = "foxglove.CompressedVideo";
    md.serialization_format = "protobuf";
    md.offered_qos_profiles = {bag_qos()};
    writer->create_topic(md);
  }

  rclcpp::Serialization<test_msgs::msg::BasicTypes> serialization;
  for (size_t i = 0; i < kNumMessagesPerTopic; ++i) {
    const int64_t stamp_ns = 10000000 * static_cast<int64_t>(i);
    if (with_chatter) {
      test_msgs::msg::BasicTypes msg;
      msg.int32_value = static_cast<int32_t>(i);
      // Serialize into a pre-sized, zero-filled buffer. Fast-CDR does not write the alignment
      // padding bytes of the CDR stream, so serializing into a fresh allocation embeds heap garbage
      // in the payload and the byte-exact comparisons of this harness would be nondeterministic.
      auto serialized = std::make_shared<rclcpp::SerializedMessage>(kSerializedCapacity);
      std::memset(
        serialized->get_rcl_serialized_message().buffer, 0,
        serialized->get_rcl_serialized_message().buffer_capacity);
      serialization.serialize_message(&msg, serialized.get());
      // rmw_fastrtps reports buffer_capacity == buffer_length after serializing without touching
      // the allocation, so the precondition to check is only that no growth was needed.
      if (serialized->get_rcl_serialized_message().buffer_length > kSerializedCapacity) {
        throw std::runtime_error("serialized message outgrew the pre-zeroed buffer; raise kSerializedCapacity");
      }
      writer->write(serialized, kChatter, "test_msgs/msg/BasicTypes", rclcpp::Time(stamp_ns));
    }
    if (with_proto) {
      const auto payload = fake_protobuf_payload(static_cast<uint8_t>(i));
      auto message = std::make_shared<rosbag2_storage::SerializedBagMessage>();
      message->topic_name = kProto;
      message->recv_timestamp = stamp_ns + 1;
      message->send_timestamp = stamp_ns + 1;
      message->serialized_data =
        rosbag2_storage::make_serialized_message(payload.data(), payload.size());
      writer->write(message);
    }
  }
  writer->close();
}

// ------------------------------------------------------------------------------------ reading
struct ReadDump
{
  bool open_ok = false;
  std::string open_error;
  std::vector<rosbag2_storage::TopicMetadata> topics;
  std::vector<rosbag2_storage::TopicMetadata> undeliverable;
  std::vector<std::string> messages;  // json
  std::string read_error;

  std::string json(const std::vector<std::pair<std::string, std::string>> & extra = {}) const
  {
    std::vector<std::pair<std::string, std::string>> fields = {
      {"open_ok", open_ok ? "true" : "false"},
      {"open_error", json_str(open_error)},
      {"topics", topics_json(topics)},
      {"undeliverable", names_json(undeliverable)},
      {"messages", json_list(messages)},
      {"read_error", json_str(read_error)},
    };
    fields.insert(fields.end(), extra.begin(), extra.end());
    return json_obj(fields);
  }
};

// Opens with the given converter options, optionally filters, and reads everything.
ReadDump read_all(
  std::unique_ptr<rosbag2_cpp::Reader> reader,
  const rosbag2_storage::StorageOptions & storage_options,
  const rosbag2_cpp::ConverterOptions & converter_options,
  const std::vector<std::string> & topic_filter = {})
{
  ReadDump dump;
  try {
    reader->open(storage_options, converter_options);
    dump.open_ok = true;
  } catch (const std::exception & e) {
    dump.open_error = e.what();
    return dump;
  }
  try {
    dump.topics = reader->get_all_topics_and_types();
    dump.undeliverable = reader->get_undeliverable_topics();
    if (!topic_filter.empty()) {
      rosbag2_storage::StorageFilter filter;
      filter.topics = topic_filter;
      reader->set_filter(filter);
    }
    while (reader->has_next()) {
      dump.messages.push_back(message_json(*reader->read_next()));
    }
  } catch (const std::exception & e) {
    dump.read_error = e.what();
  }
  return dump;
}

// Mixed bag opened with the local rmw format requested: first message (chatter) is delivered,
// the second (protobuf) must make read_next() throw.
std::string requested_rmw_scenario(
  const rosbag2_storage::StorageOptions & storage_options, const std::string & rmw_format)
{
  ReadDump dump;
  std::string first_message = "null";
  bool second_read_throws = false;
  std::string second_read_error;
  try {
    rosbag2_cpp::Reader reader;
    reader.open(storage_options, {"", rmw_format});
    dump.open_ok = true;
    dump.topics = reader.get_all_topics_and_types();
    dump.undeliverable = reader.get_undeliverable_topics();
    if (reader.has_next()) {
      first_message = message_json(*reader.read_next());
    }
    if (reader.has_next()) {
      try {
        auto second = reader.read_next();
        second_read_error = "read_next returned a message on " + second->topic_name;
      } catch (const std::runtime_error & e) {
        second_read_throws = true;
        second_read_error = e.what();
      }
    }
  } catch (const std::exception & e) {
    if (!dump.open_ok) {dump.open_error = e.what();} else {dump.read_error = e.what();}
  }
  // Fresh reader, protobuf topic excluded via the storage filter: whole bag readable.
  auto filtered = read_all(
    std::make_unique<rosbag2_cpp::Reader>(), storage_options, {"", rmw_format}, {kChatter});
  return dump.json({
      {"first_message", first_message},
      {"second_read_throws", second_read_throws ? "true" : "false"},
      {"second_read_error", json_str(second_read_error)},
      {"filtered_open_ok", filtered.open_ok ? "true" : "false"},
      {"filtered_messages", json_list(filtered.messages)},
      {"filtered_read_error", json_str(filtered.read_error)},
    });
}

std::string open_throws_scenario(
  const rosbag2_storage::StorageOptions & storage_options, const std::string & requested_format)
{
  bool throws = false;
  std::string error;
  try {
    rosbag2_cpp::Reader reader;
    reader.open(storage_options, {"", requested_format});
  } catch (const std::runtime_error & e) {
    throws = true;
    error = e.what();
  }
  return json_obj({{"open_throws", throws ? "true" : "false"}, {"error", json_str(error)}});
}

rosbag2_storage::StorageOptions opts(const fs::path & uri, const std::string & storage_id)
{
  rosbag2_storage::StorageOptions o;
  o.uri = uri.string();
  o.storage_id = storage_id;
  return o;
}
}  // namespace

int main(int argc, char ** argv)
{
  if (argc != 3) {
    std::cerr << "usage: mixed_formats_differential <workdir> <storage_id>\n";
    return 2;
  }
  const fs::path workdir = argv[1];
  const std::string storage_id = argv[2];
  const fs::path bags = workdir / "bags" / storage_id;
  fs::create_directories(workdir / "bags");
  fs::remove_all(bags);
  fs::create_directories(bags);
  const std::string rmw_format = rmw_get_serialization_format();

  const fs::path mixed = bags / "mixed";
  const fs::path uniform = bags / "uniform";
  const fs::path only_proto = bags / "only_proto";
  const fs::path mixed_msgzstd = bags / "mixed_msgzstd";
  const fs::path mixed_filezstd = bags / "mixed_filezstd";

  try {
    write_bag(mixed, storage_id, true, true, nullptr);
    write_bag(uniform, storage_id, true, false, nullptr);
    write_bag(only_proto, storage_id, false, true, nullptr);
    rosbag2_compression::CompressionOptions msg_zstd;
    msg_zstd.compression_format = "zstd";
    msg_zstd.compression_mode = rosbag2_compression::CompressionMode::MESSAGE;
    write_bag(mixed_msgzstd, storage_id, true, true, &msg_zstd);
    rosbag2_compression::CompressionOptions file_zstd;
    file_zstd.compression_format = "zstd";
    file_zstd.compression_mode = rosbag2_compression::CompressionMode::FILE;
    write_bag(mixed_filezstd, storage_id, true, true, &file_zstd);
  } catch (const std::exception & e) {
    std::cerr << "writing bags failed: " << e.what() << "\n";
    std::ofstream(workdir / (storage_id + ".json")) << json_obj({
        {"storage_id", json_str(storage_id)}, {"rmw_format", json_str(rmw_format)},
        {"write_error", json_str(e.what())}});
    return 1;
  }

  std::vector<std::pair<std::string, std::string>> report = {
    {"storage_id", json_str(storage_id)},
    {"rmw_format", json_str(rmw_format)},
  };

  // No output format requested: everything as stored, every message tagged with its format.
  report.emplace_back("raw",
    read_all(std::make_unique<rosbag2_cpp::Reader>(), opts(mixed, storage_id), {"", ""}).json());
  // Local rmw format requested on a mixed bag.
  report.emplace_back("requested_rmw", requested_rmw_scenario(opts(mixed, storage_id), rmw_format));
  // A format none of the topics has, on a mixed bag: conversion impossible, open throws.
  report.emplace_back("requested_unknown", open_throws_scenario(opts(mixed, storage_id), "some_other_format"));
  // Uniform bag: unchanged behaviour.
  report.emplace_back("uniform_raw",
    read_all(std::make_unique<rosbag2_cpp::Reader>(), opts(uniform, storage_id), {"", ""}).json());
  report.emplace_back("uniform_requested_rmw",
    read_all(std::make_unique<rosbag2_cpp::Reader>(), opts(uniform, storage_id), {"", rmw_format}).json());
  // Bag with only a protobuf topic: readable raw; requesting the rmw format needs a
  // protobuf->rmw converter plugin, which does not exist here, so open throws.
  report.emplace_back("only_proto_raw",
    read_all(std::make_unique<rosbag2_cpp::Reader>(), opts(only_proto, storage_id), {"", ""}).json());
  report.emplace_back("only_proto_requested_rmw", open_throws_scenario(opts(only_proto, storage_id), rmw_format));
  // Compressed bags, read through the factory (picks the compression reader from the metadata).
  report.emplace_back("message_compressed",
    read_all(rosbag2_transport::ReaderWriterFactory::make_reader(opts(mixed_msgzstd, storage_id)),
      opts(mixed_msgzstd, storage_id), {"", ""}).json());
  report.emplace_back("file_compressed",
    read_all(rosbag2_transport::ReaderWriterFactory::make_reader(opts(mixed_filezstd, storage_id)),
      opts(mixed_filezstd, storage_id), {"", ""}).json());
  report.emplace_back("message_compressed_requested_rmw_filtered",
    read_all(rosbag2_transport::ReaderWriterFactory::make_reader(opts(mixed_msgzstd, storage_id)),
      opts(mixed_msgzstd, storage_id), {"", rmw_format}, {kChatter}).json());

  report.emplace_back("bags", json_obj({
      {"mixed", json_str(mixed.string())}, {"uniform", json_str(uniform.string())},
      {"only_proto", json_str(only_proto.string())}}));

  std::ofstream out(workdir / (storage_id + ".json"));
  out << json_obj(report) << "\n";
  std::cout << "wrote " << (workdir / (storage_id + ".json")).string() << "\n";
  return 0;
}

// Hidden verifier driver for the C++ MCAP indexed read path.
//
// Compiled against the submitted headers. Two modes:
//
//   indexed_probe summary  <file> <NoFallbackScan|AllowFallbackScan|ForceScan> [start end]...
//       open(); readSummary(method); dump status, indexes, statistics, and byteRange() for each
//       (start, end) pair given.
//
//   indexed_probe messages <file> <file|logtime|reverse> <start> <end>
//                          [--summary <method>] [--topics a,b,c]
//       open(); optionally readSummary(method); iterate readMessages(onProblem, options) and dump
//       every MessageView plus the distinct problem codes reported.
//
// All integers are emitted as JSON numbers (nlohmann handles uint64), payloads as hex strings.
#define MCAP_IMPLEMENTATION
#include <mcap/reader.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cstring>
#include <iostream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

using json = nlohmann::ordered_json;

static std::string StatusName(mcap::StatusCode code) {
  switch (code) {
    case mcap::StatusCode::Success: return "Success";
    case mcap::StatusCode::NotOpen: return "NotOpen";
    case mcap::StatusCode::InvalidSchemaId: return "InvalidSchemaId";
    case mcap::StatusCode::InvalidChannelId: return "InvalidChannelId";
    case mcap::StatusCode::FileTooSmall: return "FileTooSmall";
    case mcap::StatusCode::ReadFailed: return "ReadFailed";
    case mcap::StatusCode::MagicMismatch: return "MagicMismatch";
    case mcap::StatusCode::InvalidFile: return "InvalidFile";
    case mcap::StatusCode::InvalidRecord: return "InvalidRecord";
    case mcap::StatusCode::InvalidOpCode: return "InvalidOpCode";
    case mcap::StatusCode::InvalidChunkOffset: return "InvalidChunkOffset";
    case mcap::StatusCode::InvalidFooter: return "InvalidFooter";
    case mcap::StatusCode::DecompressionFailed: return "DecompressionFailed";
    case mcap::StatusCode::DecompressionSizeMismatch: return "DecompressionSizeMismatch";
    case mcap::StatusCode::UnrecognizedCompression: return "UnrecognizedCompression";
    case mcap::StatusCode::OpenFailed: return "OpenFailed";
    case mcap::StatusCode::MissingStatistics: return "MissingStatistics";
    case mcap::StatusCode::InvalidMessageReadOptions: return "InvalidMessageReadOptions";
    case mcap::StatusCode::NoMessageIndexesAvailable: return "NoMessageIndexesAvailable";
    case mcap::StatusCode::UnsupportedCompression: return "UnsupportedCompression";
    default: return "Code" + std::to_string(int(code));
  }
}

static std::string Hex(const std::byte* data, uint64_t size) {
  static const char* digits = "0123456789abcdef";
  std::string out;
  out.reserve(size * 2);
  for (uint64_t i = 0; i < size; ++i) {
    const auto b = uint8_t(data[i]);
    out.push_back(digits[b >> 4]);
    out.push_back(digits[b & 0x0f]);
  }
  return out;
}

static bool ParseMethod(const std::string& s, mcap::ReadSummaryMethod* out) {
  if (s == "NoFallbackScan") { *out = mcap::ReadSummaryMethod::NoFallbackScan; return true; }
  if (s == "AllowFallbackScan") { *out = mcap::ReadSummaryMethod::AllowFallbackScan; return true; }
  if (s == "ForceScan") { *out = mcap::ReadSummaryMethod::ForceScan; return true; }
  return false;
}

static json ChunkIndexJson(const mcap::ChunkIndex& ci) {
  json mio = json::object();
  std::vector<std::pair<uint16_t, uint64_t>> entries(ci.messageIndexOffsets.begin(),
                                                     ci.messageIndexOffsets.end());
  std::sort(entries.begin(), entries.end());
  for (const auto& [cid, off] : entries) {
    mio[std::to_string(cid)] = off;
  }
  return json{
    {"message_start_time", ci.messageStartTime},
    {"message_end_time", ci.messageEndTime},
    {"chunk_start_offset", ci.chunkStartOffset},
    {"chunk_length", ci.chunkLength},
    {"message_index_offsets", mio},
    {"message_index_length", ci.messageIndexLength},
    {"compression", ci.compression},
    {"compressed_size", ci.compressedSize},
    {"uncompressed_size", ci.uncompressedSize},
  };
}

static json StatisticsJson(const std::optional<mcap::Statistics>& stats) {
  if (!stats) {
    return nullptr;
  }
  json cmc = json::object();
  std::vector<std::pair<uint16_t, uint64_t>> entries(stats->channelMessageCounts.begin(),
                                                     stats->channelMessageCounts.end());
  std::sort(entries.begin(), entries.end());
  for (const auto& [cid, n] : entries) {
    cmc[std::to_string(cid)] = n;
  }
  return json{
    {"message_count", stats->messageCount},
    {"schema_count", stats->schemaCount},
    {"channel_count", stats->channelCount},
    {"attachment_count", stats->attachmentCount},
    {"metadata_count", stats->metadataCount},
    {"chunk_count", stats->chunkCount},
    {"message_start_time", stats->messageStartTime},
    {"message_end_time", stats->messageEndTime},
    {"channel_message_counts", cmc},
  };
}

static json SummaryState(mcap::McapReader& reader) {
  json out;
  const auto& footer = reader.footer();
  out["footer"] = footer ? json{{"summary_start", footer->summaryStart},
                                {"summary_offset_start", footer->summaryOffsetStart},
                                {"summary_crc", footer->summaryCrc}}
                         : json(nullptr);
  out["statistics"] = StatisticsJson(reader.statistics());
  json cis = json::array();
  for (const auto& ci : reader.chunkIndexes()) {
    cis.push_back(ChunkIndexJson(ci));
  }
  out["chunk_indexes"] = cis;

  std::vector<uint16_t> schemaIds;
  for (const auto& [id, ptr] : reader.schemas()) {
    (void)ptr;
    schemaIds.push_back(id);
  }
  std::sort(schemaIds.begin(), schemaIds.end());
  out["schemas"] = schemaIds;
  std::vector<uint16_t> channelIds;
  for (const auto& [id, ptr] : reader.channels()) {
    (void)ptr;
    channelIds.push_back(id);
  }
  std::sort(channelIds.begin(), channelIds.end());
  out["channels"] = channelIds;

  std::vector<mcap::AttachmentIndex> ais;
  for (const auto& [name, ai] : reader.attachmentIndexes()) {
    (void)name;
    ais.push_back(ai);
  }
  std::sort(ais.begin(), ais.end(), [](const auto& a, const auto& b) { return a.offset < b.offset; });
  json aisJson = json::array();
  for (const auto& ai : ais) {
    aisJson.push_back(json{{"name", ai.name}, {"offset", ai.offset}, {"length", ai.length},
                           {"log_time", ai.logTime}, {"create_time", ai.createTime},
                           {"data_size", ai.dataSize}, {"media_type", ai.mediaType}});
  }
  out["attachment_indexes"] = aisJson;

  std::vector<mcap::MetadataIndex> mis;
  for (const auto& [name, mi] : reader.metadataIndexes()) {
    (void)name;
    mis.push_back(mi);
  }
  std::sort(mis.begin(), mis.end(), [](const auto& a, const auto& b) { return a.offset < b.offset; });
  json misJson = json::array();
  for (const auto& mi : mis) {
    misJson.push_back(json{{"name", mi.name}, {"offset", mi.offset}, {"length", mi.length}});
  }
  out["metadata_indexes"] = misJson;
  return out;
}

static int RunSummary(int argc, char** argv) {
  if (argc < 4) {
    std::cerr << "usage: indexed_probe summary <file> <method> [start end]...\n";
    return 2;
  }
  mcap::ReadSummaryMethod method;
  if (!ParseMethod(argv[3], &method)) {
    std::cerr << "unknown method " << argv[3] << "\n";
    return 2;
  }
  json out;
  std::vector<std::string> problems;
  mcap::McapReader reader;
  const auto openStatus = reader.open(std::string_view(argv[2]));
  out["open_status"] = StatusName(openStatus.code);
  if (!openStatus.ok()) {
    std::cout << out.dump() << "\n";
    return 0;
  }
  const auto status = reader.readSummary(method, [&](const mcap::Status& s) {
    problems.push_back(StatusName(s.code));
  });
  out["status"] = StatusName(status.code);
  out["status_message"] = status.message;
  out["problems"] = problems;
  auto state = SummaryState(reader);
  for (auto it = state.begin(); it != state.end(); ++it) {
    out[it.key()] = it.value();
  }
  json ranges = json::array();
  for (int i = 4; i + 1 < argc; i += 2) {
    const uint64_t start = std::stoull(argv[i]);
    const uint64_t end = std::stoull(argv[i + 1]);
    const auto [a, b] = reader.byteRange(start, end);
    ranges.push_back(json{{"start", start}, {"end", end}, {"range", json::array({a, b})}});
  }
  out["byte_ranges"] = ranges;
  std::cout << out.dump() << "\n";
  return 0;
}

static int RunMessages(int argc, char** argv) {
  if (argc < 6) {
    std::cerr << "usage: indexed_probe messages <file> <file|logtime|reverse> <start> <end> "
                 "[--summary <method>] [--topics a,b]\n";
    return 2;
  }
  const std::string orderArg = argv[3];
  mcap::ReadMessageOptions options;
  if (orderArg == "file") {
    options.readOrder = mcap::ReadMessageOptions::ReadOrder::FileOrder;
  } else if (orderArg == "logtime") {
    options.readOrder = mcap::ReadMessageOptions::ReadOrder::LogTimeOrder;
  } else if (orderArg == "reverse") {
    options.readOrder = mcap::ReadMessageOptions::ReadOrder::ReverseLogTimeOrder;
  } else {
    std::cerr << "unknown order " << orderArg << "\n";
    return 2;
  }
  options.startTime = std::stoull(argv[4]);
  options.endTime = std::stoull(argv[5]);

  std::optional<mcap::ReadSummaryMethod> summaryMethod;
  std::optional<std::set<std::string>> topics;
  for (int i = 6; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--summary" && i + 1 < argc) {
      mcap::ReadSummaryMethod m;
      if (!ParseMethod(argv[++i], &m)) {
        std::cerr << "unknown method\n";
        return 2;
      }
      summaryMethod = m;
    } else if (arg == "--topics" && i + 1 < argc) {
      std::set<std::string> set;
      std::stringstream ss(argv[++i]);
      std::string item;
      while (std::getline(ss, item, ',')) {
        if (!item.empty()) {
          set.insert(item);
        }
      }
      topics = set;
    } else {
      std::cerr << "unknown argument " << arg << "\n";
      return 2;
    }
  }
  if (topics) {
    options.topicFilter = [topics](std::string_view topic) {
      return topics->count(std::string(topic)) > 0;
    };
  }

  json out;
  std::vector<std::string> problems;
  mcap::McapReader reader;
  const auto openStatus = reader.open(std::string_view(argv[2]));
  out["open_status"] = StatusName(openStatus.code);
  if (!openStatus.ok()) {
    std::cout << out.dump() << "\n";
    return 0;
  }
  if (summaryMethod) {
    const auto s = reader.readSummary(*summaryMethod, [](const mcap::Status&) {});
    out["summary_status"] = StatusName(s.code);
  } else {
    out["summary_status"] = nullptr;
  }

  json messages = json::array();
  const auto onProblem = [&](const mcap::Status& s) {
    problems.push_back(StatusName(s.code));
  };
  uint64_t count = 0;
  for (const auto& view : reader.readMessages(onProblem, options)) {
    if (++count > 100000) {
      problems.push_back("ProbeMessageLimitExceeded");
      break;
    }
    json m;
    m["channel_id"] = view.message.channelId;
    m["sequence"] = view.message.sequence;
    m["log_time"] = view.message.logTime;
    m["publish_time"] = view.message.publishTime;
    m["data_hex"] = Hex(view.message.data, view.message.dataSize);
    m["offset"] = view.messageOffset.offset;
    m["chunk_offset"] = view.messageOffset.chunkOffset ? json(*view.messageOffset.chunkOffset)
                                                       : json(nullptr);
    m["topic"] = view.channel ? json(view.channel->topic) : json(nullptr);
    m["channel_ptr_id"] = view.channel ? json(view.channel->id) : json(nullptr);
    m["schema_id"] = view.schema ? json(view.schema->id) : json(nullptr);
    m["channel_schema_id"] = view.channel ? json(view.channel->schemaId) : json(nullptr);
    messages.push_back(std::move(m));
  }
  out["messages"] = messages;
  std::set<std::string> distinct(problems.begin(), problems.end());
  out["problems"] = std::vector<std::string>(distinct.begin(), distinct.end());
  out["problem_count"] = problems.size();
  out["statistics_present"] = reader.statistics().has_value();
  out["chunk_index_count"] = reader.chunkIndexes().size();
  std::cout << out.dump() << "\n";
  return 0;
}

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "usage: indexed_probe <summary|messages> ...\n";
    return 2;
  }
  const std::string mode = argv[1];
  if (mode == "summary") {
    return RunSummary(argc, argv);
  }
  if (mode == "messages") {
    return RunMessages(argc, argv);
  }
  std::cerr << "unknown mode " << mode << "\n";
  return 2;
}

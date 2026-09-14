#pragma once

#include "types.hpp"
#include <algorithm>
#include <variant>

namespace mcap::internal {

// Helper for writing compile-time exhaustive variant visitors.
template <class>
inline constexpr bool always_false_v = false;

/**
 * @brief A job to read a specific message at offset `offset` from the decompressed chunk
 * stored in `chunkReaderIndex`. A timestamp is provided to order this job relative to other jobs.
 */
struct ReadMessageJob {
  Timestamp timestamp;
  RecordOffset offset;
  size_t chunkReaderIndex;
};

/**
 * @brief A job to decompress the chunk starting at `chunkStartOffset`. The message indices
 * starting directly after the chunk record and ending at `messageIndexEndOffset` will be used to
 * find specific messages within the chunk.
 */
struct DecompressChunkJob {
  Timestamp messageStartTime;
  Timestamp messageEndTime;
  ByteOffset chunkStartOffset;
  ByteOffset messageIndexEndOffset;
};

/**
 * @brief A union of jobs that an indexed MCAP reader executes.
 */
using ReadJob = std::variant<ReadMessageJob, DecompressChunkJob>;

/**
 * @brief A priority queue of jobs for an indexed MCAP reader to execute.
 *
 * NOT IMPLEMENTED: the ordering logic is missing. `push()` discards its argument, `pop()` returns
 * a default-constructed job and `len()` is always 0.
 */
struct ReadJobQueue {
private:
  bool reverse_ = false;
  std::vector<ReadJob> heap_;

public:
  explicit ReadJobQueue(bool reverse)
      : reverse_(reverse) {
    (void)reverse_;
  }

  void push(DecompressChunkJob&& decompressChunkJob) {
    (void)decompressChunkJob;
  }

  void push(ReadMessageJob&& readMessageJob) {
    (void)readMessageJob;
  }

  ReadJob pop() {
    return ReadJob{};
  }

  size_t len() const {
    return heap_.size();
  }
};

}  // namespace mcap::internal

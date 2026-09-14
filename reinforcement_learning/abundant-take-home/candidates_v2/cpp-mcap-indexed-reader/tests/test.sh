#!/bin/bash
# Verifier for cpp-mcap-indexed-reader.
#
# Clean room: the only thing taken from the agent environment is the transferred header directory
# (Harbor re-materializes the [[artifacts]] entry at its source path, /workspace/repo/cpp/mcap/include/mcap).
# Everything executed here -- upstream tests, conformance runners, the hidden probe, the corpus,
# the fixtures and the Go/Rust reference readers -- comes from the verifier image.
set -uo pipefail

SUBMISSION=${SUBMISSION:-/workspace/repo/cpp/mcap/include/mcap}
OUT=${OUT:-/logs/verifier}
PRISTINE=/opt/pristine/repo
VERIFY=/opt/verify

mkdir -p "$OUT"
echo 0 > "$OUT/reward.txt"
rm -rf "$VERIFY"
mkdir -p "$VERIFY/build"

# 1. Assemble the tree: pristine cpp/ (tests, runners, CMake files) with only the submitted header
#    directory dropped in. Nothing else from the agent is used.
cp -a "$PRISTINE/cpp" "$VERIFY/cpp"
rm -rf "$VERIFY/cpp/mcap/include/mcap"
if [ -d "$SUBMISSION" ]; then
  cp -a "$SUBMISSION" "$VERIFY/cpp/mcap/include/mcap"
else
  echo "submission directory $SUBMISSION not found" | tee "$OUT/build-error.log"
  mkdir -p "$VERIFY/cpp/mcap/include/mcap"
fi

# 2. Build every binary independently (so failures attribute) with the upstream warning set
#    (cpp/test/CMakeLists.txt) under clang 14, Debug, -Werror.
CXX=clang++
FLAGS=(-std=c++17 -g -O0 -Wall -Wextra -pedantic -Wshadow -Wpointer-arith -Werror
       -I"$VERIFY/cpp/mcap/include")
LIBS=(-llz4 -lzstd)
build() {
  local name=$1; shift
  timeout 900 "$CXX" "${FLAGS[@]}" "$@" -o "$VERIFY/build/$name" "${LIBS[@]}" \
    > "$OUT/build-$name.log" 2>&1
  echo $? > "$VERIFY/build/$name.rc"
}
build unit-tests "$VERIFY/cpp/test/unit_tests.cpp" &
build unit-tests-nocompress -DMCAP_COMPRESSION_NO_LZ4 -DMCAP_COMPRESSION_NO_ZSTD "$VERIFY/cpp/test/unit_tests.cpp" &
build indexed-reader-conformance "$VERIFY/cpp/test/indexed_reader_conformance.cpp" &
build streamed-reader-conformance "$VERIFY/cpp/test/streamed_reader_conformance.cpp" &
build streamed-writer-conformance "$VERIFY/cpp/test/streamed_writer_conformance.cpp" &
build indexed_probe /tests/harness/indexed_probe.cpp &
wait

python3 - "$VERIFY/build" > "$VERIFY/build/status.json" <<'PY'
import json, pathlib, sys
b = pathlib.Path(sys.argv[1])
status = {}
for rc in sorted(b.glob("*.rc")):
    status[rc.stem] = rc.read_text().strip() == "0"
print(json.dumps(status))
PY
cp "$VERIFY/build/status.json" "$OUT/build-status.json"

# 3. Run every check group; run_checks.py writes score.json and reward.txt.
#    Sibling checks: 9 cross-checked fixtures x 2 reference readers (Go: harness/go_indexed_reader.go
#    built against the pristine go/mcap; Rust: the crate's conformance_indexed_reader example).
python3 /tests/harness/run_checks.py \
  --bin-dir "$VERIFY/build" \
  --probe "$VERIFY/build/indexed_probe" \
  --corpus /tests/corpus \
  --fixtures /tests/fixtures \
  --submission "$VERIFY/cpp/mcap/include/mcap" \
  --out "$OUT" \
  --build-status "$VERIFY/build/status.json" \
  --go-reader /opt/ref/bin/go-indexed-reader \
  --rust-reader /opt/ref/bin/conformance_indexed_reader \
  --expected-sibling-checks 18 \
  | tee "$OUT/run_checks.log"

exit 0

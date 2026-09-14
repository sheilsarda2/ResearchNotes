#!/bin/bash
# Prepare the verifier's copy of the zenoh-cpp test tree (run once at verifier image build).
#
#  1. Install the hidden tests: the upstream PR's tests/universal/network/connectivity.cxx and
#     tests/CMakeLists.txt (extended with the verifier-only targets), the authored
#     interop_optional test and the parity probe.
#  2. Replace tests/run_with_router.sh with the offline wrapper (router started by test.sh).
#  3. Re-point session bootstrap in the upstream network tests at the local router:
#     Config::create_default() -> test_config() (client mode, scouting off). Test bodies are
#     untouched; connectivity.cxx builds its own explicit peer/router configs and is skipped.
set -euo pipefail
REPO="$1"
H="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

test -d "$REPO/tests/universal/network"
install -m 0644 "$H/connectivity.cxx" "$REPO/tests/universal/network/connectivity.cxx"
install -m 0644 "$H/tests_CMakeLists.txt" "$REPO/tests/CMakeLists.txt"
install -m 0644 "$H/zenoh_test_config.hxx" "$REPO/tests/zenoh_test_config.hxx"
mkdir -p "$REPO/tests/verifier"
install -m 0644 "$H/interop_optional.cxx" "$REPO/tests/verifier/interop_optional.cxx"
install -m 0644 "$H/parity_dump.cxx" "$REPO/tests/verifier/parity_dump.cxx"
install -m 0755 "$H/run_with_router.sh" "$REPO/tests/run_with_router.sh"

for f in "$REPO"/tests/universal/network/*.cxx "$REPO"/tests/zenohpico/network/*.cxx "$REPO"/tests/zenohc/shm_api.cxx; do
    case "$f" in *connectivity.cxx) continue ;; esac
    grep -q '^#include "zenoh.hxx"' "$f" || { echo "prepare_tests: $f has no zenoh.hxx include line" >&2; exit 1; }
    sed -i 's/Config::create_default()/test_config()/g' "$f"
    sed -i '0,/^#include "zenoh.hxx"/s//#include "zenoh.hxx"\n#include "zenoh_test_config.hxx"/' "$f"
    grep -q 'zenoh_test_config.hxx' "$f"
done
echo "prepare_tests: done"

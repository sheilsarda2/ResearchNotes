#!/bin/bash
# Reference solution: restore the excised indexed-read path from the upstream implementation.
set -euo pipefail
cd /workspace/repo
if git apply --check /solution/changes.patch 2>/dev/null; then
  git apply /solution/changes.patch
else
  patch -p1 --forward < /solution/changes.patch
fi
# Rebuild the C++ test binaries the same way the environment did (offline, cached conan packages).
cd /workspace/repo/cpp && ./build.sh --build-tests-only
./test/build/Debug/bin/unit-tests
./test/build/Debug/bin/unit-tests-nocompress

#!/bin/bash
set -euo pipefail
cd /workspace/repo
git apply /solution/changes.patch

#!/bin/bash
# Oracle: apply the upstream non-test diff of eclipse-zenoh/zenoh#2620 (merge 89ab32cb)
# on top of the task base a62451c0. The PR's own integration test is hidden and not
# part of this patch; the in-crate test adaptations (new struct fields) are included.
set -euo pipefail
cd /workspace/repo
git apply --check /solution/changes.patch
git apply /solution/changes.patch
# Sanity: the feature module must compile in the pre-warmed configuration.
cargo build --offline -p zenoh --features unstable,internal,test

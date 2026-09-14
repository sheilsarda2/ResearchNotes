#!/usr/bin/env bash
# Clean-room verifier entry point. Writes /logs/verifier/reward.txt (0 or 1) and
# /logs/verifier/score.json (per-group attribution). Never trusts anything under
# /workspace/repo except the transferred xarray/ package sources.
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /workspace/repo || exit 1
python /tests/verify.py 2>&1 | tee /logs/verifier/verify.log
status=${PIPESTATUS[0]}
echo "verify.py exit=${status}; reward=$(cat /logs/verifier/reward.txt)"
exit 0

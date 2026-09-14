#!/usr/bin/env bash
set -euo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
exec python3 /tests/verify.py

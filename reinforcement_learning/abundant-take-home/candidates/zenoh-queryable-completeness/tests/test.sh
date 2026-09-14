#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
cd /opt/check
python3 /tests/run-verifier.py

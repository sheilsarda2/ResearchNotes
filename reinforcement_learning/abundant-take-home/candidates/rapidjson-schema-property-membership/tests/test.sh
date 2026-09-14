#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
python3 /tests/verify.py

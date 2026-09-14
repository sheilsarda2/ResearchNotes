#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
cd /workspace/repo
export PYTHONPATH=/workspace/repo:/workspace/repo/test
python -m pytest -o addopts= -q /tests/test_generation_target.py \
  test/target_test.py::TargetTest \
  test/local_target_test.py::LocalTargetTest::test_exists \
  test/local_target_test.py::LocalTargetTest::test_copy \
  test/local_target_test.py::LocalTargetTest::test_move \
  test/local_target_test.py::LocalTargetTest::test_gzip_with_module \
  test/local_target_test.py::LocalTargetTest::test_bzip2 \
  test/local_target_test.py::LocalTargetTest::test_open_modes \
  --junitxml=/logs/verifier/results.xml -ra
status=$?
python - "$status" <<'PY'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
passed = False
try:
    suites = list(ET.parse('/logs/verifier/results.xml').getroot().iter('testsuite'))
    total = sum(int(s.get('tests', 0)) for s in suites)
    bad = sum(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped'))
    passed = sys.argv[1] == '0' and total >= 35 and bad == 0
except Exception:
    pass
Path('/logs/verifier/reward.txt').write_text('1\n' if passed else '0\n')
PY
exit 0

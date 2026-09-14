#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
echo 0 > /logs/verifier/reward.txt
cd /workspace/repo
export PYTHONPATH=/workspace/repo:/workspace/repo/tests:/tests
python -m pytest -o addopts= -q /tests/test_online_reshard.py \
 tests/test_fanout.py::test_set_get_delete \
 tests/test_fanout.py::test_incr \
 tests/test_fanout.py::test_decr \
 tests/test_fanout.py::test_read \
 tests/test_fanout.py::test_pop \
 tests/test_fanout.py::test_expire \
 tests/test_fanout.py::test_evict \
 tests/test_fanout.py::test_clear \
 tests/test_fanout.py::test_pickle \
 tests/test_fanout.py::test_memoize \
 --junitxml=/logs/verifier/results.xml -ra
status=$?
python - "$status" <<'PY'
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
passed = False
try:
    suites = list(ET.parse('/logs/verifier/results.xml').getroot().iter('testsuite'))
    count = sum(int(s.get('tests', 0)) for s in suites)
    bad = sum(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped'))
    passed = sys.argv[1] == '0' and count >= 40 and bad == 0
except Exception:
    pass
Path('/logs/verifier/reward.txt').write_text('1\n' if passed else '0\n')
PY
exit 0

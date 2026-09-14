"""Run protected integration tests against submitted Rust implementation, offline."""
import json
import os
import pathlib
import re
import subprocess
import time
import xml.etree.ElementTree as ET

LOGS = pathlib.Path('/logs/verifier')
EXPECTED = {
    'sorted_closed_interval_and_point_boundaries',
    'unsorted_duplicate_times_keep_every_matching_row',
    'sparse_empty_lists_and_secondary_timelines_stay_aligned',
    'multiple_chunks_entities_static_rows_and_absent_timeline',
    'disjoint_interior_gap_and_full_coverage',
    'fractional_selection_keeps_existing_floor_ceil_conversion',
    'store_information_and_blueprint_activation_survive_selection',
    'deterministic_varied_chunks_match_row_predicate',
    'clears',
    'clears_respect_index_order',
}
command = [
    'cargo', 'test', '--locked', '--offline', '-p', 're_entity_db',
    '--test', 'selected_time_export', '--test', 'clear',
    '--config', 'profile.dev.package."*".opt-level=0',
    '--', '--test-threads=1',
]
env = dict(os.environ, CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='2',
           CARGO_PROFILE_DEV_OPT_LEVEL='0', CARGO_PROFILE_DEV_DEBUG='0',
           RUSTUP_TOOLCHAIN='1.98.1')
start = time.monotonic()
try:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=850, env=env)
    output, status = result.stdout, result.returncode
except subprocess.TimeoutExpired as exc:
    output = (exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
    output += '\nVerifier build/test timeout.\n'
    status = 124
elapsed = time.monotonic() - start
(LOGS / 'cargo-test.log').write_text(output)
print(output, end='')
# The upstream logger can write between a test name and its final status.
# Bound each case by the next case/summary instead of requiring a single line.
cases = re.findall(r'^test (\w+) \.\.\. (.*?)(?=^test \w+ \.\.\. |^test result:|\Z)',
                   output, re.M | re.S)
passed_names = [name for name, detail in cases if detail.strip().splitlines()[-1:] == ['ok']]
summaries = re.findall(r'test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;', output)
ok = (status == 0 and len(passed_names) == len(EXPECTED) and set(passed_names) == EXPECTED
      and len(summaries) == 2 and sum(int(s[1]) for s in summaries) == len(EXPECTED)
      and all(s[0] == 'ok' and all(int(v) == 0 for v in s[2:]) for s in summaries))
diagnostics = dict(command=command, exit_code=status, elapsed_seconds=elapsed,
                   expected_tests=len(EXPECTED), observed_passed_tests=passed_names,
                   test_summaries=summaries, reward=int(ok))
(LOGS / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2) + '\n')
root = ET.Element('testsuite', name='rerun-selected-time-export', tests=str(len(EXPECTED)),
                  failures=str(sum(name not in passed_names for name in EXPECTED)), time=str(elapsed))
for name in sorted(EXPECTED):
    case = ET.SubElement(root, 'testcase', name=name, classname='rust.integration')
    if name not in passed_names:
        ET.SubElement(case, 'failure', message='Test did not pass; see cargo-test.log').text = output[-20000:]
if not ok and set(passed_names) == EXPECTED:
    ET.SubElement(root, 'error', message='Unexpected collection/status; see diagnostics.json')
ET.ElementTree(root).write(LOGS / 'results.xml', encoding='unicode')
(LOGS / 'reward.txt').write_text('1\n' if ok else '0\n')
print(json.dumps(diagnostics))

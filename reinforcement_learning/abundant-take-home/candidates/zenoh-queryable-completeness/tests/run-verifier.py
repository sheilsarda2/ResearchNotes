import json
import os
from pathlib import Path
import re
import subprocess
import time

logs = Path('/logs/verifier')
logs.mkdir(parents=True, exist_ok=True)
(logs / 'reward.txt').write_text('0\n')
env = os.environ.copy()
for name in ('RUSTFLAGS', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'CARGO_ENCODED_RUSTFLAGS', 'RUST_TEST_THREADS'):
    env.pop(name, None)
env.update(CARGO_HOME='/usr/local/cargo', CARGO_BUILD_JOBS='2', CARGO_PROFILE_DEV_DEBUG='0', CARGO_INCREMENTAL='0', RUSTUP_TOOLCHAIN='1.97.1')
base = ['cargo', '--offline', '--locked', '--manifest-path', '/opt/check/Cargo.toml']
# Rebuild the submitted implementation even when transferred source timestamps are old.
clean = subprocess.run(['cargo', 'clean', '--manifest-path', '/opt/check/Cargo.toml', '-p', 'zenoh'], env=env, text=True, capture_output=True)
(logs / 'clean.log').write_text(clean.stdout + clean.stderr)
checks = [
    ('completeness', ['cargo', 'test', *base[1:], '--test', 'completeness', '--', '--test-threads=1'], {
        'complete_then_incomplete', 'homogeneous_and_empty_controls',
        'incomplete_churn_preserves_complete_siblings', 'incomplete_then_complete',
        'undeclare_and_redeclare_complete_queryables', 'wildcard_same_expression_and_unrelated_key'}),
    ('upstream_queryable', ['cargo', 'test', *base[1:], '--test', 'upstream_queryable', 'test_queryable_same_session', '--', '--exact', '--test-threads=1'], {'test_queryable_same_session'}),
]
records = []
for label, cmd, expected in checks:
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd='/opt/check', env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=420)
        output, status = proc.stdout, proc.returncode
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b''
        output = partial.decode(errors='replace') if isinstance(partial, bytes) else partial
        output += '\nVERIFIER PROCESS TIMEOUT\n'
        status = 124
    (logs / (label + '.log')).write_text(output)
    print(output, flush=True)
    seen = {name: result for name, result in re.findall(r'^test ([A-Za-z0-9_:]+) \.\.\. (ok|FAILED|ignored)$', output, re.MULTILINE)}
    summary = re.search(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured;', output)
    passed = status == 0 and set(seen) == expected and all(value == 'ok' for value in seen.values()) and summary is not None and summary[1] == 'ok' and int(summary[2]) == len(expected) and all(int(summary[i]) == 0 for i in (3, 4, 5))
    records.append({'check': label, 'exit_code': status, 'seconds': time.monotonic() - started, 'expected_tests': sorted(expected), 'observed_tests': seen, 'passed': passed})
passed = clean.returncode == 0 and all(record['passed'] for record in records)
diagnostics = {'reward': int(passed), 'clean_exit_code': clean.returncode, 'checks': records}
(logs / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2) + '\n')
(logs / 'reward.txt').write_text('1\n' if passed else '0\n')
print(json.dumps(diagnostics), flush=True)

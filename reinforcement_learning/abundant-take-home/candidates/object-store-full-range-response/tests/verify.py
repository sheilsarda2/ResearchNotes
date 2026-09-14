"""Run protected Rust integration tests against the transferred src implementation."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

root = Path('/workspace/repo')
logs = Path('/logs/verifier')
logs.mkdir(parents=True, exist_ok=True)
(logs / 'reward.txt').write_text('0\n')
# This fresh container's manifest, lockfile and integration tests are never
# transferred from the agent. Reinstate the independent target after transfer.
shutil.copyfile('/tests/harbor_full_range_response.rs', root / 'tests/harbor_full_range_response.rs')
env = dict(os.environ, CARGO_NET_OFFLINE='true', RUSTUP_TOOLCHAIN='1.98.1', CARGO_BUILD_JOBS='2')
# Force submitted source to compile rather than trusting cached timestamps.
clean = subprocess.run(['cargo', 'clean', '-p', 'object_store'], cwd=root, env=env, capture_output=True, text=True)
(logs / 'clean.log').write_text(clean.stdout + clean.stderr)
command = ['cargo', 'test', '--offline', '--locked', '--features', 'http', '--test', 'harbor_full_range_response', '--test', 'get_range_file', '--', '--test-threads=1']
started = time.monotonic()
try:
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=780)
    output = result.stdout + result.stderr
    code = result.returncode
except subprocess.TimeoutExpired as error:
    output = str(error)
    code = 124
(logs / 'cargo-tests.log').write_text(output)
# Both protected targets must collect all expected tests, with no ignored tests.
summaries = re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out', output)
expected = sorted([(3, 0, 0, 0, 0), (11, 0, 0, 0, 0)])
observed = sorted([tuple(map(int, values)) for values in summaries])
success = clean.returncode == 0 and code == 0 and observed == expected
record = {'command': command, 'exit_code': code, 'clean_exit_code': clean.returncode, 'seconds': time.monotonic() - started, 'expected_summaries': expected, 'observed_summaries': observed, 'reward': int(success)}
(logs / 'diagnostics.json').write_text(json.dumps(record, indent=2) + '\n')
(logs / 'reward.txt').write_text(f'{int(success)}\n')
print(output)
print(json.dumps(record))
raise SystemExit(0 if success else 1)

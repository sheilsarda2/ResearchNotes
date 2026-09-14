"""Fixed three runs per source; unchanged upstream test and source-bound results."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

OUT = Path('/tmp/timing-output')
BUILD = Path('/workspace/build')
ROOTS = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src', 'zenoh/src', 'zenoh-ext/src')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(root):
    result = {}
    for directory in ROOTS:
        for p in sorted((root / directory).rglob('*')):
            assert not p.is_symlink()
            if p.is_file():
                result[str(p.relative_to(root))] = digest(p)
    return result


def dump(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')


def main():
    OUT.mkdir()
    request = json.loads(Path('/tmp/timing-inputs.json').read_text())
    assert request['repeats'] == 3 and request['variant_order'] == ['untouched_gold', 'corrected_reference']
    assert digest(BUILD / 'zenoh/tests/routing.rs') == request['routing_sha256']
    assert digest('/opt/pristine/zenoh/tests/routing.rs') == request['routing_sha256']
    assert sources(Path('/opt/gold')) == request['gold_source_files']
    assert sources(Path('/tmp/corrected-source')) == request['corrected_source_files']
    result = {'kind': 'unchanged_timing_test_comparison', 'model_calls': 0, 'full_regrade': False,
              'reward': None, 'fixed_repeats_per_source': 3, 'cases': [], 'complete': False}
    command = ['cargo', 'test', '--offline', '-p', 'zenoh', '-p', 'zenoh-codec',
               '--features', 'zenoh/test,zenoh/unstable,zenoh/internal', '--test', 'routing']
    env = dict(os.environ, CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='4')
    for variant in request['variant_order']:
        root = Path('/opt/gold' if variant == 'untouched_gold' else '/tmp/corrected-source')
        expected = request['gold_source_files' if variant == 'untouched_gold' else 'corrected_source_files']
        for directory in ROOTS:
            shutil.rmtree(BUILD / directory)
            shutil.copytree(root / directory, BUILD / directory)
        assert sources(BUILD) == expected
        for relative in expected:
            os.utime(BUILD / relative, None)
        dump(variant + '-source-before.json', sources(BUILD))
        with (OUT / (variant + '-compile.log')).open('x') as log:
            compiled = subprocess.run([*command, '--no-run'], cwd=BUILD, env=env,
                                      stdout=log, stderr=subprocess.STDOUT, timeout=1500)
        assert compiled.returncode == 0, 'Comparison must compile before a runtime outcome'
        for repeat in range(1, 4):
            name = f'{variant}-{repeat}.log'
            argv = [*command, 'scouting_delay_regression', '--', '--exact', '--test-threads=1']
            started = datetime.now(timezone.utc).isoformat()
            begin = time.monotonic()
            with (OUT / name).open('x') as log:
                completed = subprocess.run(argv, cwd=BUILD, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=300)
            text = (OUT / name).read_text()
            assert 'running 1 test' in text and 'error[E' not in text
            passed = 'test scouting_delay_regression ... ok' in text
            failed = 'test scouting_delay_regression ... FAILED' in text
            assert passed != failed and completed.returncode == (0 if passed else 101)
            if failed:
                assert 'expected <400ms' in text, 'A different failure needs investigation'
            elapsed = re.search(r'zenoh.open\(\) took ([0-9.]+)(ns|µs|us|ms|s), expected <400ms', text)
            result['cases'].append({'variant': variant, 'repeat': repeat, 'started_at': started,
                'wall_seconds': time.monotonic() - begin, 'command': argv,
                'returncode': completed.returncode, 'test_passed': passed,
                'reported_open_ms': float(elapsed.group(1))*{'ns':1e-6,'µs':.001,'us':.001,'ms':1,'s':1000}[elapsed.group(2)] if elapsed else None,
                'passing_open_ms_upper_bound': 400 if passed else None,
                'log': name, 'log_sha256': digest(OUT / name)})
            dump('comparison.json', result)
        assert sources(BUILD) == expected and digest(BUILD / 'zenoh/tests/routing.rs') == request['routing_sha256']
        dump(variant + '-source-after.json', sources(BUILD))
    result.update(complete=True, finished_at=datetime.now(timezone.utc).isoformat(), tests_unchanged=True)
    dump('comparison.json', result)


if __name__ == '__main__':
    main()

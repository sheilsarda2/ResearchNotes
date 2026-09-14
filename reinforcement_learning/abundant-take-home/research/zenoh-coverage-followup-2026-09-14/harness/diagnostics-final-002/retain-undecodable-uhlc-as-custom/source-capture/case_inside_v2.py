#!/usr/bin/env python3
"""Container-side focused case. Invoked only by focused_validation.py under a claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

SRC_DIRS = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src', 'zenoh/src', 'zenoh-ext/src')
OUT = Path('/tmp/focused-output')


def now():
    return datetime.now(timezone.utc).isoformat()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources(root):
    result = {}
    for directory in SRC_DIRS:
        path = Path(root) / directory
        assert path.is_dir() and not path.is_symlink(), str(path)
        for file in sorted(path.rglob('*')):
            assert not file.is_symlink(), str(file)
            if file.is_file():
                result[file.relative_to(root).as_posix()] = digest(file)
    assert result
    return result


def main():
    OUT.mkdir(exist_ok=False)
    request = json.loads(Path('/tmp/focused-request.json').read_text())
    record = {'started_at': now(), 'kind': 'focused_no_model_diagnostic', 'full_regrade': False}
    try:
        for path, expected in request['image_files'].items():
            assert digest(path) == expected, 'Cached image source mismatch: ' + path
        gold = sources('/opt/gold')
        assert gold == request['expected_gold_sources'], 'Cached gold differs from reconstructed original reference'
        write('gold-source-manifest.json', gold)
        pristine = sources('/opt/pristine')
        write('pristine-source-manifest.json', pristine)
        root = {'gold': '/opt/gold', 'pristine': '/opt/pristine', 'saved': '/tmp/focused-source'}[request['source']]
        original = sources(root)
        if request['source'] == 'saved':
            assert original == request['saved_sources'], 'Saved source copy mismatch'
        write('selected-source-manifest.json', original)
        for directory in SRC_DIRS:
            dest = Path('/workspace/build') / directory
            shutil.rmtree(dest)
            shutil.copytree(Path(root) / directory, dest)
        assert sources('/workspace/build') == original, 'Source overlay mismatch'
        if request['patch_sha256']:
            assert digest('/tmp/focused.patch') == request['patch_sha256']
            for mode in (['--check'], []):
                with (OUT / ('patch-check.log' if mode else 'patch-apply.log')).open('w') as log:
                    subprocess.run(['git', 'apply', *mode, '/tmp/focused.patch'], cwd='/workspace/build',
                                   stdout=log, stderr=subprocess.STDOUT, check=True)
        effective = sources('/workspace/build')
        write('effective-source-manifest.json', effective)
        # Preserve the warmed target directory, but invalidate old source fingerprints.
        for name in effective:
            os.utime(Path('/workspace/build') / name, None)
        for name, expected in request['tests'].items():
            source = Path('/tmp/focused-tests') / name
            assert digest(source) == expected, 'Test copy mismatch: ' + name
            destination = Path('/workspace/build/zenoh/tests') / name
            shutil.copyfile(source, destination)
            assert digest(destination) == expected
        command = ['cargo', 'test', '--offline', '-p', 'zenoh', '--features',
                   'zenoh/test,zenoh/unstable,zenoh/internal', '--no-fail-fast']
        for name in request['tests']:
            command += ['--test', Path(name).stem]
        command += ['--', '--test-threads=1', '--nocapture']
        record.update(cargo_started_at=now(), command=command)
        write('case.json', record)
        env = dict(os.environ, CARGO_NET_OFFLINE='true', CARGO_BUILD_JOBS='4')
        with (OUT / 'cargo.stdout.log').open('w') as log:
            completed = subprocess.run(command, cwd='/workspace/build', env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
        record.update(cargo_finished_at=now(), exit_code=completed.returncode,
                      source_unchanged=(sources('/workspace/build') == effective),
                      tests_unchanged=all(digest(Path('/workspace/build/zenoh/tests') / n) == h
                                          for n, h in request['tests'].items()))
        assert record['source_unchanged'] and record['tests_unchanged'], 'Inputs mutated during cargo'
        return completed.returncode
    except Exception as error:
        record.update(error_type=type(error).__name__, error=str(error), exit_code=125)
        return 125
    finally:
        record['finished_at'] = now()
        write('case.json', record)


if __name__ == '__main__':
    sys.exit(main())

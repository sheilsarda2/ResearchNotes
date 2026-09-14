#!/usr/bin/env python3
"""Run the entire pinned verifier against one copied original submission."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

OUT = Path('/tmp/paired-output')
SOURCE = Path('/workspace/repo')
ROOTS = ('commons/zenoh-protocol/src', 'commons/zenoh-codec/src', 'zenoh/src', 'zenoh-ext/src')


def hashes(root):
    assert stat.S_ISDIR(root.lstat().st_mode)
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        assert stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Nonregular input'
        if stat.S_ISREG(mode):
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def sources():
    return {directory + '/' + name: value for directory in ROOTS
            for name, value in hashes(SOURCE / directory).items()}


def tests():
    return {'tests/' + name: value for name, value in hashes(Path('/tests')).items()}


def write(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def main():
    OUT.mkdir(exist_ok=False)
    request = json.loads(Path('/tmp/paired-request.json').read_text())
    result = {'kind': 'paired_verifier_process_result', 'started_at_epoch': time.time(),
              'full_regrade': True, 'model_calls': 0, 'verifier_exit_code': None,
              'timed_out': False, 'workload_timeout_seconds': 3600}
    child = None
    try:
        before_source, before_tests = sources(), tests()
        write('source-before.json', before_source)
        write('tests-before.json', before_tests)
        assert before_source == request['source_file_sha256'], 'Original source copy differs'
        assert before_tests == request['test_file_sha256'], 'Image /tests differs from final task'
        logs = Path('/logs/verifier')
        logs.mkdir(parents=True, exist_ok=True)
        assert not any(logs.iterdir()), 'Refuse existing verifier output'
        result['verifier_started_at_epoch'] = time.time()
        result['deadline_epoch'] = result['verifier_started_at_epoch'] + 3600
        write('raw-result.json', result)
        with (OUT / 'verifier-process.log').open('xb') as stream:
            child = subprocess.Popen(['/bin/bash', '/tests/test.sh'], cwd='/workspace/build',
                                     stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                result['verifier_exit_code'] = child.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                result['timed_out'] = True
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                result['verifier_exit_code'] = child.returncode
        result['verifier_finished_at_epoch'] = time.time()
        after_source, after_tests = sources(), tests()
        write('source-after.json', after_source)
        write('tests-after.json', after_tests)
        result['source_files_unchanged'] = before_source == after_source
        result['test_files_unchanged'] = before_tests == after_tests
        assert result['source_files_unchanged'] and result['test_files_unchanged'], 'Input changed during verifier'
        return 124 if result['timed_out'] else 0
    except BaseException as error:
        result.update(error_type=type(error).__name__, error=str(error))
        return 125
    finally:
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        result['finished_at_epoch'] = time.time()
        write('raw-result.json', result)


if __name__ == '__main__':
    raise SystemExit(main())

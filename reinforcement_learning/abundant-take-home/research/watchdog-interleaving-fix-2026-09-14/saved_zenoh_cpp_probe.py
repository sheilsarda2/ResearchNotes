#!/usr/bin/env python3
"""Run unchanged saved headers against an unchanged, pinned verifier regression."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

OUT = Path('/tmp/zenoh-cpp-diagnostic')
REPO = Path('/work/repo')
TARGET = 'test_advanced_pub_sub_zenohpico'
END = time.monotonic() + float(os.environ['DIAGNOSTIC_REMAINING_SEC'])


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def run(label, command, timeout):
    allowed = min(timeout, END - time.monotonic() - 10)
    if allowed <= 0:
        raise TimeoutError('Overall diagnostic bound exhausted')
    started = time.time()
    with (OUT / (label + '.log')).open('w') as log:
        process = subprocess.Popen(command, cwd=REPO / 'build', stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=allowed)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            code, timed_out = process.returncode, True
    row = dict(label=label, command=command, started_at_epoch=started,
               finished_at_epoch=time.time(), return_code=code, timed_out=timed_out)
    write(label + '.json', row)
    print(json.dumps(row), flush=True)
    return row


def main():
    OUT.mkdir(exist_ok=True)
    expected = json.loads(Path('/tmp/zenoh-cpp-diagnostic-input.json').read_text())
    headers = Path('/workspace/repo/include')
    assert tree(headers) == expected['headers'], 'Saved headers changed before overlay'
    task_tests = tree(Path('/tests'))
    assert task_tests == expected['tests'], 'Pinned verifier tests differ from frozen task'
    dependencies = {}
    for root in (Path('/opt/zenoh/include'), Path('/opt/zenoh/lib'), Path('/opt/zenoh/lib64')):
        if root.exists():
            dependencies.update({str(root / name): value for name, value in tree(root).items()})
    dependencies['/opt/zenoh/bin/zenohd'] = sha(Path('/opt/zenoh/bin/zenohd'))
    write('input-image-proof.json', dict(tests=task_tests, dependencies=dependencies,
                                       pristine_tests=tree(Path('/opt/pristine/tests')),
                                       pristine_headers=tree(Path('/opt/pristine/include'))))
    for name in ('include', 'tests'):
        shutil.rmtree(REPO / name)
    shutil.copytree('/opt/pristine/tests', REPO / 'tests')
    shutil.copytree(headers, REPO / 'include')
    assert tree(REPO / 'include') == expected['headers']
    write('overlaid-headers-before.json', tree(REPO / 'include'))
    # These are exactly the configure/build operations of the original verifier.
    configured = run('configure', ['cmake', '-S', str(REPO), '-B', str(REPO / 'build')], 600)
    if configured['return_code'] != 0:
        return 2
    built = run('build', ['cmake', '--build', str(REPO / 'build'), '--target', 'tests', '-j4', '--', '-k'], 1500)
    if built['return_code'] != 0:
        return 2
    listed = run('registered', ['ctest', '-N'], 30)
    import re
    names = re.findall(r'^\s*Test\s*#\d+:\s*(\S+)', (OUT / 'registered.log').read_text(), re.M)
    assert listed['return_code'] == 0
    assert sorted(names) == sorted(Path('/tests/hidden/expected_tests.txt').read_text().split())
    assert len(names) == 32 and names[9] == TARGET
    write('registered-tests.json', names)
    router_log = (OUT / 'router.log').open('w')
    router = subprocess.Popen(['/opt/zenoh/bin/zenohd', '-l', 'tcp/127.0.0.1:27447',
                               '--no-multicast-scouting', '--cfg', 'scouting/gossip/enabled:false'],
                              stdout=router_log, stderr=subprocess.STDOUT, start_new_session=True)
    results = []
    try:
        ready_deadline = min(time.monotonic() + 60, END)
        while time.monotonic() < ready_deadline:
            if router.poll() is not None:
                raise RuntimeError('Router exited before readiness')
            try:
                socket.create_connection(('127.0.0.1', 27447), timeout=1).close()
                break
            except OSError:
                time.sleep(.2)
        else:
            raise TimeoutError('Router readiness timed out')
        for iteration in range(1, 4):
            label = f'isolated-{iteration}'
            results.append(run(label, ['ctest', '-j1', '-V', '--timeout', '300',
                                      '--output-junit', str(OUT / (label + '.xml')),
                                      '-R', '^' + TARGET + '$'], 320))
            if results[-1]['return_code'] != 0:
                break
        if all(row['return_code'] == 0 for row in results):
            results.append(run('suite-prefix', ['ctest', '-j1', '-V', '--timeout', '300',
                                               '--output-junit', str(OUT / 'suite-prefix.xml'),
                                               '-I', '1,10'], 700))
        # A debugger run is separate diagnostic evidence and never replaces a test result.
        if any(row['return_code'] != 0 for row in results) and END - time.monotonic() > 350:
            results.append(run('debugger', ['gdb', '--batch', '-ex', 'set pagination off',
                                           '-ex', 'run', '-ex', 'thread apply all bt', '--args',
                                           str(REPO / 'build/tests/advanced_pub_sub_zenohpico'),
                                           'tcp/127.0.0.1:27447'], 320))
        write('outcomes.json', dict(results=results, router_alive=router.poll() is None,
                                   headers_after=tree(REPO / 'include'), tests_after=tree(REPO / 'tests')))
        assert tree(REPO / 'include') == expected['headers'], 'Submitted headers changed during replay'
        assert tree(REPO / 'tests') == tree(Path('/opt/pristine/tests')), 'Verifier test sources changed'
        assert tree(Path('/tests')) == expected['tests'], 'Hidden verifier sources changed'
        assert all(sha(Path(name)) == value for name, value in dependencies.items()), 'Dependency bytes changed'
        write('inputs-unchanged.json', dict(headers=True, tests=True, dependencies=True))
        return 0
    finally:
        if router.poll() is None:
            os.killpg(router.pid, signal.SIGTERM)
            try:
                router.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(router.pid, signal.SIGKILL)
                router.wait(timeout=5)
        router_log.close()


if __name__ == '__main__':
    raise SystemExit(main())

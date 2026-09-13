#!/usr/bin/env python3
"""Model-free Linux/Docker regression for Mini's post-timeout pipe drain.

Run from the development container with --docker, --output NEW_DIRECTORY, and
--upstream-local pointing to the installed Mini 2.4.6 environments/local.py or
its preserved proof copy. Only this harness's disposable container is managed.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import types
import unittest
import uuid
from unittest.mock import patch


BASE_IMAGE = 'sha256:a76d6fd31b9397504d4355faa5c014dcfca43249d6a2ebeb65be824a40de3261'
TOOL_TIMEOUT = 0.4
ROOT = None

PAYLOAD = r'''import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
if len(sys.argv) > 3:
    root.joinpath('holder.json').write_text(json.dumps({
        'pid': os.getpid(), 'start_ticks': pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
        'pgrp': os.getpgrp(), 'session': os.getsid(0)}))
    if mode == 'partial-utf8':
        os.write(1, b'partial-' + bytes([0xe2, 0x82]))
    elif mode not in ('quiet', 'normal-background'):
        print('holder-ready-' + chr(9731), flush=True)
        time.sleep(.55)
        print('late-output', flush=True)
    time.sleep(120)
else:
    options = {} if mode == 'same-group' else {'process_group': 0}
    if mode == 'normal-background':
        options.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.Popen([sys.executable, '-u', __file__, str(root), mode, 'holder'], **options)
    until = time.monotonic() + 2
    while not root.joinpath('holder.json').exists() and time.monotonic() < until:
        time.sleep(.005)
    if mode not in ('quiet', 'partial-utf8'):
        print('parent-ready', flush=True)
    if mode != 'normal-background':
        time.sleep(120)
'''


def load_patch():
    path = ROOT / 'source/benchmark_mini_tool_cleanup.py'
    spec = importlib.util.spec_from_file_location('benchmark_mini_tool_cleanup', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_upstream():
    """Execute the exact reviewed function, without constructing any model."""
    path = ROOT / 'source/mini_local.py'
    text = path.read_text()
    node = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == '_run')
    source = ''.join(text.splitlines(True)[node.lineno - 1:node.end_lineno])
    module = load_patch()
    assert hashlib.sha256(source.encode()).hexdigest() == module.REVIEWED_RUN_SHA256
    scope = {'os': os, 'signal': signal, 'subprocess': subprocess}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope['_run']


def alive(record):
    path = Path('/proc') / str(record['pid']) / 'stat'
    try:
        fields = path.read_text().rsplit(')', 1)[1].split()
        return fields[19] == record['start_ticks'] and fields[0] != 'Z'
    except FileNotFoundError:
        return False


def worker(case, folder):
    module = load_patch()
    run = load_upstream() if case == 'old-hang' else module.bounded_run
    mode = {'old-hang': 'detached', 'fixed-timeout': 'detached',
            'fixed-quiet': 'quiet', 'fixed-partial-utf8': 'partial-utf8',
            'fixed-same-group': 'same-group',
            'fixed-normal-background': 'normal-background'}.get(case)
    if mode:
        command = f'{shlex.quote(sys.executable)} -u {shlex.quote(str(folder / "payload.py"))} {shlex.quote(str(folder))} {mode}'
    elif case == 'fixed-nonzero':
        command = "printf 'normal-out\\n'; printf 'normal-err\\n' >&2; exit 17"
    else:
        command = "printf 'normal-out\\n'; printf 'normal-err\\n' >&2"
    started = time.monotonic()
    row = {'case': case, 'tool_timeout_sec': TOOL_TIMEOUT, 'drain_bound_sec': module.POST_KILL_DRAIN_SEC}
    try:
        result = run(command, str(folder), dict(os.environ), TOOL_TIMEOUT)
        row.update(exception=None, returncode=result.returncode, stdout=result.stdout)
    except subprocess.TimeoutExpired as error:
        row.update(exception=type(error).__name__, timeout=error.timeout,
                   original_command_preserved=error.cmd == command, stdout=error.output,
                   output_type=type(error.output).__name__)
    # Exercise cleanup/destruction after the exception leaves its handler; an
    # implicit Popen wait must not reintroduce the hang after bounded_run returns.
    gc.collect()
    row['elapsed_including_gc_sec'] = time.monotonic() - started
    holder = folder / 'holder.json'
    if holder.exists():
        row['holder_alive_before_worker_exit'] = alive(json.loads(holder.read_text()))
    row['owned_zombie_children'] = []
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) == os.getpid() and fields[0] == 'Z':
                row['owned_zombie_children'].append(int(path.parent.name))
        except (OSError, ValueError):
            pass
    (folder / 'worker-result.json').write_text(json.dumps(row, indent=2) + '\n')


class DrainTests(unittest.TestCase):
    def execute_case(self, case):
        folder = ROOT / 'cases' / case
        folder.mkdir(parents=True)
        (folder / 'payload.py').write_text(PAYLOAD)
        phase = folder / 'phase'
        phase.mkdir()
        command = f'{shlex.quote(sys.executable)} {shlex.quote(__file__)} --worker {case} --output {shlex.quote(str(ROOT))}'
        seconds = 2.5 if case == 'old-hang' else 5
        process = subprocess.run(
            [sys.executable, str(ROOT / 'source/benchmark_process_guard.py'), 'run',
             '--phase', str(phase), '--execution', 'test', '--deadline', str(time.time() + seconds),
             '--command', command], capture_output=True, text=True, timeout=20,
        )
        (folder / 'guard-stdout.txt').write_text(process.stdout)
        (folder / 'guard-stderr.txt').write_text(process.stderr)
        closed = subprocess.run(
            [sys.executable, str(ROOT / 'source/benchmark_process_guard.py'), 'close', '--phase', str(phase)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        closure = json.loads(closed.stdout)
        (folder / 'closed.json').write_text(json.dumps(closure, indent=2) + '\n')
        self.assertTrue(closure['quiescent'])
        self.assertTrue(all(record['quiescent'] for record in closure['executions']))
        holder = folder / 'holder.json'
        if holder.exists():
            self.assertFalse(alive(json.loads(holder.read_text())), 'outer guard must quiesce escaped holders')
        # A grading boundary is permitted only after the existing outer guard
        # has completed. This does not equate a returned tool timeout with safe
        # trial-wide quiescence.
        boundary = {'outer_guard_quiescent': closure['quiescent'],
                    'no_live_holder': not holder.exists() or not alive(json.loads(holder.read_text())),
                    'checked_after_close_at': time.time()}
        (folder / 'grading-boundary.json').write_text(json.dumps(boundary, indent=2) + '\n')
        path = folder / 'worker-result.json'
        return process, json.loads(path.read_text()) if path.exists() else None, closure

    def test_original_hangs_until_outer_guard_deadline(self):
        process, result, closure = self.execute_case('old-hang')
        self.assertEqual(process.returncode, 124)
        self.assertIsNone(result)
        self.assertEqual(closure['executions'][0]['reason'], 'deadline')
        self.assertTrue((ROOT / 'cases/old-hang/holder.json').is_file())

    def test_fixed_preserves_partial_output_without_duplicates_and_leaves_other_groups(self):
        process, result, _ = self.execute_case('fixed-timeout')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result['exception'], 'TimeoutExpired')
        self.assertEqual(result['timeout'], TOOL_TIMEOUT)
        self.assertTrue(result['original_command_preserved'])
        self.assertEqual(result['output_type'], 'str')
        self.assertEqual(result['stdout'].count('parent-ready\n'), 1)
        self.assertEqual(result['stdout'].count('holder-ready-\u2603\n'), 1)
        self.assertEqual(result['stdout'].count('late-output\n'), 1)
        self.assertTrue(result['holder_alive_before_worker_exit'])
        self.assertLess(result['elapsed_including_gc_sec'], TOOL_TIMEOUT + result['drain_bound_sec'] + 0.7)
        self.assertFalse(result['owned_zombie_children'])

    def test_no_output_timeout_returns_empty_text(self):
        process, result, _ = self.execute_case('fixed-quiet')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result['exception'], 'TimeoutExpired')
        self.assertEqual(result['stdout'], '')

    def test_partial_utf8_is_preserved_with_original_replacement_decoding(self):
        process, result, _ = self.execute_case('fixed-partial-utf8')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result['stdout'], 'partial-\ufffd')
        self.assertEqual(result['exception'], 'TimeoutExpired')

    def test_existing_same_group_timeout_keeps_fast_cleanup(self):
        process, result, _ = self.execute_case('fixed-same-group')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(result['exception'], 'TimeoutExpired')
        self.assertLess(result['elapsed_including_gc_sec'], TOOL_TIMEOUT + 0.7)
        self.assertFalse(result['holder_alive_before_worker_exit'])
        self.assertFalse(result['owned_zombie_children'])

    def test_normal_background_service_is_not_killed_by_tool_wrapper(self):
        process, result, _ = self.execute_case('fixed-normal-background')
        self.assertEqual(process.returncode, 0)
        self.assertIsNone(result['exception'])
        self.assertEqual(result['returncode'], 0)
        self.assertTrue(result['holder_alive_before_worker_exit'])
        self.assertEqual(result['stdout'], 'parent-ready\n')

    def test_normal_command_keeps_stdout_stderr_and_exit_status(self):
        for case, code in [('fixed-normal', 0), ('fixed-nonzero', 17)]:
            with self.subTest(case=case):
                process, result, _ = self.execute_case(case)
                self.assertEqual(process.returncode, 0)
                self.assertIsNone(result['exception'])
                self.assertEqual(result['returncode'], code)
                self.assertEqual(result['stdout'], 'normal-out\nnormal-err\n')

    def test_install_is_checked_idempotent_and_preserves_environment_configuration(self):
        module = load_patch()
        local = types.ModuleType('minisweagent.environments.local')
        local._run = load_upstream()
        config_sentinel = object()
        local.LocalEnvironmentConfig = config_sentinel
        environments = types.ModuleType('minisweagent.environments')
        environments.local = local
        package = types.ModuleType('minisweagent')
        package.environments = environments
        with patch.dict(sys.modules, {'minisweagent': package, 'minisweagent.environments': environments,
                                      'minisweagent.environments.local': local}), \
                patch.object(module.importlib.metadata, 'version', return_value='2.4.6'):
            first = module.install()
            self.assertEqual(module.install(), first)
            self.assertTrue(first['installed'])
            self.assertEqual(first['package_version'], '2.4.6')
            self.assertIs(local._run, module.bounded_run)
            self.assertIs(local.LocalEnvironmentConfig, config_sentinel)
            local._run = lambda *args: None
            with self.assertRaisesRegex(RuntimeError, 'Unsupported Mini'):
                module.install()

    def test_recorded_upstream_default_tool_timeout_is_unchanged(self):
        tree = ast.parse((ROOT / 'source/mini_local.py').read_text())
        config = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == 'LocalEnvironmentConfig')
        timeout = next(node for node in config.body if isinstance(node, ast.AnnAssign)
                       and node.target.id == 'timeout')
        self.assertEqual(ast.literal_eval(timeout.value), 30)


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true')
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upstream-local', type=Path)
    args = parser.parse_args()
    ROOT = args.output.resolve()
    if args.worker:
        worker(args.worker, ROOT / 'cases' / args.worker)
        return 0
    if args.inside:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DrainTests))
        hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (ROOT / 'source').glob('*.py')}
        (ROOT / 'test-summary.json').write_text(json.dumps({
            'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors), 'model_calls': 0,
            'tool_timeout_in_fixture_sec': TOOL_TIMEOUT, 'source_sha256': hashes,
            'python_version': sys.version.split()[0],
            'upstream_default_tool_timeout_sec': 30,
            'created_at': datetime.now(timezone.utc).isoformat()}, indent=2) + '\n')
        return 0 if result.wasSuccessful() else 1
    if not args.docker or args.upstream_local is None:
        parser.error('--docker and --upstream-local are required for the disposable regression')
    if ROOT.exists():
        parser.error('--output must be fresh; evidence is never overwritten')
    source = ROOT / 'source'
    source.mkdir(parents=True)
    scripts = Path(__file__).resolve().parents[1]
    inputs = [(scripts / 'benchmark_mini_tool_cleanup.py', 'benchmark_mini_tool_cleanup.py'),
              (scripts / 'benchmark_process_guard.py', 'benchmark_process_guard.py'),
              (Path(__file__), 'test_mini_tool_cleanup.py'), (args.upstream_local, 'mini_local.py')]
    for original, name in inputs:
        (source / name).write_bytes(original.read_bytes())
    hashes = {name: hashlib.sha256(original.read_bytes()).hexdigest() for original, name in inputs}
    name = 'benchmark-mini-drain-' + uuid.uuid4().hex[:10]
    command = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--cpus', '1', '--memory', '256m',
               '--volume', f'{ROOT}:/proof', '--workdir', '/proof', BASE_IMAGE,
               'python', '/proof/source/test_mini_tool_cleanup.py', '--inside', '--output', '/proof']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    finally:
        # Only this randomly named fixture container is eligible for cleanup.
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=20)
    (ROOT / 'docker-stdout.txt').write_text(result.stdout)
    (ROOT / 'docker-stderr.txt').write_text(result.stderr)
    stable = all(hashlib.sha256(original.read_bytes()).hexdigest() == hashes[name] for original, name in inputs)
    summary = json.loads((ROOT / 'test-summary.json').read_text())
    summary.update(source_files_unchanged=stable, base_image=BASE_IMAGE,
                   container_exit_code=result.returncode, passed=summary['passed'] and stable and result.returncode == 0)
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(result.stderr)
    print(json.dumps(summary, indent=2))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

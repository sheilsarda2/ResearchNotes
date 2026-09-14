#!/usr/bin/env python3
"""Compile corrected hidden tests against an unchanged saved Burn submission.

Read-only inspection is the default. Run with --apply inside keen_black using
Harbor's Python to reserve one shared slot and execute the offline diagnostic.
This diagnostic compiles the whole library test target, verifies all 108 hidden
PyTorch test names, and exercises the seven corrected error assertions. It does
not run a full regrade: the historical anti-cheat failure and reward remain
unchanged. The submission receives no Debug implementation or source edits.
No model is called.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / 'research/task-revisions/rs-burn-store-pytorch-reader-v4'
ORIGINAL = ROOT / 'research/task-revisions/rs-burn-store-pytorch-reader-v3'
TRIAL = ROOT / 'jobs/candidates-burn-reader-v3-efforts-20-20260913T232621Z/rs-burn-store-pytorch-reader-v3__aLVwbse'
SUBMISSION = TRIAL / 'artifacts/submission/burn-store/src'
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGE = 'sha256:b4b34ba9c6e8f9efde1a2f35e2d011c92b17f7aaea8b887488105b5e6ca75ca6'
TESTS = tuple('pytorch::tests::reader::' + name for name in (
    'test_big_endian_file_is_refused',
    'test_top_level_key_that_is_not_a_dict',
    'test_truncated_legacy_file_fails_at_open',
    'test_tar_absurd_storage_count_is_an_error',
    'test_unrecognized_byteorder_is_an_error',
    'test_legacy_sys_info_without_little_endian_is_refused',
    'test_legacy_big_endian_file_is_refused',
))
LIMITS = {'cpus': 4, 'memory_mb': 8192, 'network_mode': 'none', 'timeout_sec': 3600}
RAW = ('result.json', 'config.json', 'benchmark-evidence.json', 'benchmark-snapshot.json',
       'benchmark-deadline.json', 'verifier/score.json', 'verifier/reward.txt',
       'verifier/build.log', 'verifier/build_errors.txt', 'verifier/anticheat_hits.txt')
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission

COMMAND = r'''set -euo pipefail
cd /workspace/repo
SUB=/workspace/repo/crates/burn-store/src
OUT=/tmp/burn-saved-diagnostic
mkdir -p "$OUT"
rm -rf "$SUB/pytorch/tests" "$SUB/safetensors/tests"
cp -a /tests/hidden "$SUB/pytorch/tests"
cp -a /opt/pristine/crates/burn-store/src/safetensors/tests "$SUB/safetensors/tests"
# This submission already wires in both test modules; do not edit model sources.
grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/pytorch/mod.rs"
grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/safetensors/mod.rs"
find "$SUB" -type f -exec touch {} +
export CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never RUST_BACKTRACE=1
cargo clean --offline -p burn-store
set +e
cargo test --offline --locked -p burn-store --lib --no-run > "$OUT/compile.log" 2>&1
code=$?
printf '%s\n' "$code" > "$OUT/compile.exit"
if [ "$code" -ne 0 ]; then exit "$code"; fi
cargo test --offline --locked -p burn-store --lib -- pytorch::tests:: --list --format terse > "$OUT/list.log" 2>&1
code=$?
printf '%s\n' "$code" > "$OUT/list.exit"
if [ "$code" -ne 0 ]; then exit "$code"; fi
for test in __TARGET_TESTS__; do
    cargo test --offline --locked -p burn-store --lib -- "$test" --exact --test-threads=1 > "$OUT/${test##*::}.log" 2>&1
    printf '%s\n' "$?" > "$OUT/${test##*::}.exit"
done
exit 0
'''
COMMAND = COMMAND.replace('__TARGET_TESTS__', ' '.join(TESTS))


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(path):
    files = [p for p in sorted(path.rglob('*')) if p.is_file()]
    if not files or any(p.is_symlink() or not p.resolve().is_relative_to(path.resolve()) for p in files):
        raise RuntimeError('Missing or nonlocal diagnostic input')
    return {str(p.relative_to(path)): digest(p) for p in files}


def write(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def snapshot():
    origin = json.loads((BASE / 'origin.json').read_text())
    if origin['trigger_trial'] != str(TRIAL.relative_to(ROOT)):
        raise RuntimeError('Revision is not bound to the selected historical trial')
    for label, folder in (('source', ORIGINAL), ('revision', TASK)):
        manifest_path = BASE / f'{label}-manifest.json'
        if digest(manifest_path) != origin[f'{label}_manifest_sha256']:
            raise RuntimeError('Revision provenance manifest changed')
        expected = {name: item['sha256'] for name, item in json.loads(manifest_path.read_text())['files'].items()}
        if hashes(folder) != expected:
            raise RuntimeError('Frozen source or corrected task differs from its manifest')
    result = json.loads((TRIAL / 'result.json').read_text())
    if result.get('exception_info') or result['task_name'] != ORIGINAL.name or not result.get('finished_at'):
        raise RuntimeError('Expected a completed historical Burn trial')
    errors = (TRIAL / 'verifier/build_errors.txt').read_text()
    if errors.count("error[E0277]: `PytorchReader` doesn't implement `Debug`") != 7:
        raise RuntimeError('Historical build failure does not match the diagnosed Debug bound')
    score = json.loads((TRIAL / 'verifier/score.json').read_text())
    if score['reward'] != 0 or score['groups']['anticheat']['ok'] is not False:
        raise RuntimeError('Expected the independent historical anti-cheat failure')
    return {'task_file_sha256': hashes(TASK), 'source_task_file_sha256': hashes(ORIGINAL),
            'submission_file_sha256': hashes(SUBMISSION),
            'historical_file_sha256': {name: digest(TRIAL / name) for name in RAW},
            'provenance_sha256': {name: digest(BASE / name) for name in (
                'origin.json', 'source-manifest.json', 'revision-manifest.json', 'assertion.diff')},
            'shared_control_sha256': digest(SHARED), 'runner_sha256': digest(Path(__file__)),
            'admission_helper_sha256': digest(ROOT / 'scripts/benchmark_shared_admission.py'),
            'memory_helper_sha256': digest(ROOT / 'scripts/benchmark_recovery.py')}


def classify_one(name, code, text):
    passed = code == 0 and f'test {name} ... ok' in text and '1 passed; 0 failed;' in text
    failed = code == 101 and f'test {name} ... FAILED' in text and '0 passed; 1 failed;' in text
    return {'exit_code': code, 'passed': passed, 'assertion_failed': failed,
            'exactly_one_test_executed': passed or failed}


def classify(output):
    logs = output / 'logs'
    compile_code = int((logs / 'compile.exit').read_text())
    compiled = compile_code == 0
    names = []
    cases = {}
    list_code = None
    if compiled:
        list_code = int((logs / 'list.exit').read_text())
        names = re.findall(r'^(pytorch::tests::[^\s]+): test$',
                           (logs / 'list.log').read_text(), flags=re.MULTILINE)
    if compiled and list_code == 0:
        for name in TESTS:
            stem = name.rsplit('::', 1)[1]
            code = int((logs / f'{stem}.exit').read_text())
            cases[name] = classify_one(name, code, (logs / f'{stem}.log').read_text())
    exercised = len(cases) == 7 and all(c['exactly_one_test_executed'] for c in cases.values())
    compiled_all_hidden = (compiled and list_code == 0 and len(names) == len(set(names)) == 108
                           and set(TESTS).issubset(names))
    return {'diagnostic_completed': compiled_all_hidden and exercised,
            'whole_lib_test_target_compiled': compiled, 'compile_exit_code': compile_code,
            'all_108_hidden_pytorch_tests_compiled': compiled_all_hidden,
            'hidden_pytorch_test_names': names, 'targeted_assertions': cases,
            'all_seven_error_assertions_exercised': exercised,
            'all_seven_error_assertions_passed': exercised and all(c['passed'] for c in cases.values()),
            'historical_anticheat_failure_unchanged': True,
            'full_regrade_performed': False, 'reward': None}


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=60, **kwargs)


def container_source_hashes(name):
    command = ('import hashlib,json; from pathlib import Path; '
               'p=Path("/workspace/repo/crates/burn-store/src"); '
               'print(json.dumps({str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() '
               'for f in sorted(p.rglob("*")) if f.is_file()}))')
    return json.loads(run(['docker', 'exec', name, 'python3', '-c', command], capture_output=True).stdout)


def production_sources(values):
    return {name: value for name, value in values.items()
            if not name.startswith(('pytorch/tests/', 'safetensors/tests/'))}


def container_missing(result, name):
    """Accept only Docker's complete not-found error for our exact container."""
    pattern = (r'(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*'
               + re.escape(name))
    return (result.returncode != 0 and result.stdout.strip() in {'', '[]'}
            and re.fullmatch(pattern, result.stderr.strip(), flags=re.IGNORECASE) is not None)


def stop_own_container(name):
    """Release the shared slot only after our named container is proved stopped."""
    inspected = subprocess.run(['docker', 'inspect', '--format', '{{json .State.Running}}', name],
                               capture_output=True, text=True, timeout=30)
    if inspected.returncode != 0:
        return container_missing(inspected, name)
    if inspected.stdout.strip() == 'false':
        return True
    run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checked = subprocess.run(['docker', 'inspect', '--format', '{{json .State.Running}}', name],
                             capture_output=True, text=True, timeout=30)
    return ((checked.returncode == 0 and checked.stdout.strip() == 'false') or
            container_missing(checked, name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-wait-sec', type=int, default=21600)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        test = TESTS[0]
        passed = f'test {test} ... ok\n1 passed; 0 failed;'
        failed = f'test {test} ... FAILED\n0 passed; 1 failed;'
        assert classify_one(test, 0, passed)['passed']
        assert classify_one(test, 101, failed)['assertion_failed']
        assert not classify_one(test, 0, failed)['exactly_one_test_executed']
        assert not classify_one(test, 101, passed)['exactly_one_test_executed']
        assert not classify_one(test, 0, '0 passed; 0 failed;')['exactly_one_test_executed']
        assert not classify_one(TESTS[1], 0, passed)['exactly_one_test_executed']
        name = 'burn-reader-saved-fixture'
        missing = subprocess.CompletedProcess([], 1, '', f'error: no such object: {name}\n')
        assert container_missing(missing, name)
        assert container_missing(subprocess.CompletedProcess([], 1, '[]\n',
            f'Error response from daemon: No such container: {name}\n'), name)
        for error in (f'error: no such object: {name}-other',
                      f'Cannot connect to daemon: no such object: {name}',
                      f'error: no such object: {name}\npermission denied',
                      f'permission denied while inspecting {name}',
                      f'context deadline exceeded: {name}'):
            assert not container_missing(subprocess.CompletedProcess([], 1, '', error), name)
        assert not container_missing(subprocess.CompletedProcess([], 0, '', missing.stderr), name)
        assert not container_missing(subprocess.CompletedProcess([], 1, 'true', missing.stderr), name)
        from unittest.mock import patch
        with patch.object(subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, 'true\n', ''), missing]), \
                patch(__name__ + '.run') as remove:
            assert stop_own_container(name)
            assert remove.call_args.args[0] == ['docker', 'rm', '-f', name]
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, '', 'Cannot connect to the Docker daemon')), patch(__name__ + '.run') as remove:
            assert not stop_own_container(name)
            remove.assert_not_called()
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            logs = output / 'logs'
            logs.mkdir()
            (logs / 'compile.exit').write_text('101\n')
            assert not classify(output)['diagnostic_completed']
            (logs / 'compile.exit').write_text('0\n')
            (logs / 'list.exit').write_text('0\n')
            names = list(TESTS) + [f'pytorch::tests::other_{i}' for i in range(101)]
            (logs / 'list.log').write_text('\n'.join(n + ': test' for n in names))
            for name in TESTS:
                stem = name.rsplit('::', 1)[1]
                (logs / f'{stem}.exit').write_text('0\n')
                (logs / f'{stem}.log').write_text(f'test {name} ... ok\n1 passed; 0 failed;')
            assert classify(output)['diagnostic_completed']
            assert classify(output)['all_seven_error_assertions_passed']
            (logs / 'list.log').write_text('\n'.join(n + ': test' for n in names[:-1]))
            assert not classify(output)['diagnostic_completed']
            (logs / 'list.log').write_text('\n'.join(n + ': test' for n in names[:-1] + [names[0]]))
            assert not classify(output)['diagnostic_completed']
            (logs / 'list.log').write_text('\n'.join(n + ': test' for n in names))
            stem = TESTS[0].rsplit('::', 1)[1]
            (logs / f'{stem}.exit').write_text('101\n')
            (logs / f'{stem}.log').write_text(failed)
            assert classify(output)['diagnostic_completed']
            assert not classify(output)['all_seven_error_assertions_passed']
        print('Thirteen outcome and eleven cleanup checks passed; no Docker or model calls.')
        return 0
    if args.max_wait_sec < 1 or (args.apply and args.output is None):
        parser.error('--apply requires a fresh --output and a positive bounded wait')
    inputs = snapshot()
    record = {'kind': 'focused_saved_submission_diagnostic', 'case': 'aLVwbse',
              'status': 'inspected', 'model_calls': 0, 'task': str(TASK),
              'source_trial': str(TRIAL), 'image_id': IMAGE, 'limits': LIMITS,
              'targeted_tests': TESTS, 'expected_hidden_pytorch_tests': 108,
              'command': COMMAND, **inputs, 'full_regrade_performed': False, 'reward': None}
    if not args.apply:
        print(json.dumps(record, indent=2))
        return 0
    output = args.output.resolve()
    if not output.is_relative_to(BASE) or output == BASE or output.exists():
        parser.error('--output must be a new directory below this diagnostic directory')
    lock = (BASE / 'saved-submission-diagnostic.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    output.mkdir()
    record.update(status='waiting_for_shared_capacity', started_at=now())
    status_path = output / 'result.json'
    config = {'max_active': 1, 'startup_reserve_mb': 1536, 'reserve_mb': 4096,
              'shared_pool': str(SHARED), 'paused': False}
    write(output / 'control.json', config)
    admission = SharedAdmission(SHARED, output / 'control.json')
    name = f'burn-reader-saved-alvwbse-{os.getpid()}'
    record['container_name'] = name
    acquired = False

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        deadline = time.monotonic() + args.max_wait_sec
        while time.monotonic() < deadline:
            if snapshot() != inputs:
                raise RuntimeError('Diagnostic inputs changed before admission')
            reason = admission.try_acquire(name, memory_snapshot(), config)
            record.update(updated_at=now(), admission_wait_reason=reason)
            write(status_path, record)
            if reason is None:
                acquired = True
                break
            time.sleep(2)
        else:
            raise RuntimeError('Shared admission wait expired without starting a container')
        run(['docker', 'create', '--name', name, '--cpus', '4', '--memory', '8192m',
             '--memory-swap', '8192m', '--network', 'none', IMAGE, 'sleep', 'infinity'],
            stdout=subprocess.DEVNULL)
        run(['docker', 'start', name], stdout=subprocess.DEVNULL)
        run(['docker', 'exec', name, 'bash', '-c',
             'rm -rf /workspace/repo/crates/burn-store/src; mkdir -p /workspace/repo/crates/burn-store/src'])
        run(['docker', 'cp', str(SUBMISSION) + '/.', name + ':/workspace/repo/crates/burn-store/src'])
        copied = container_source_hashes(name)
        if copied != inputs['submission_file_sha256']:
            raise RuntimeError('Container submission does not match the saved source bytes')
        record['container_submission_copy_verified'] = True
        run(['docker', 'cp', str(TASK / 'tests') + '/.', name + ':/tests'])
        record.update(status='running', verifier_started_at=now())
        write(status_path, record)
        with (output / 'targeted-test.log').open('w') as log:
            result = subprocess.run(['docker', 'exec', name, 'timeout', '-k', '30', '3600',
                                     'bash', '-c', COMMAND], stdout=log, stderr=subprocess.STDOUT, timeout=3670)
        run(['docker', 'cp', name + ':/tmp/burn-saved-diagnostic', str(output / 'logs')])
        record.update(classify(output))
        observed = production_sources(container_source_hashes(name))
        record['container_production_source_sha256'] = observed
        if observed != production_sources(inputs['submission_file_sha256']):
            raise RuntimeError('Production sources changed during the focused diagnostic')
        record.update(status='complete', verifier_exit_code=result.returncode,
                      verifier_finished_at=now(), diagnostic_log_sha256=digest(output / 'targeted-test.log'),
                      diagnostic_output_sha256=hashes(output / 'logs'))
        if result.returncode != 0 or not record['diagnostic_completed']:
            record.update(status='error', error_type='DiagnosticIncomplete')
    except BaseException as error:
        record.update(status='error', error_type=type(error).__name__)
    finally:
        if acquired:
            try:
                stopped = stop_own_container(name)
            except Exception:
                stopped = False
            record['container_stopped_confirmed'] = stopped
            if stopped:
                admission.release(name)
                with admission.locked() as (_, state):
                    record['shared_claim_released'] = name not in state['participants'][admission.key]['trials']
            else:
                record.update(status='error', shared_claim_retained=True)
        try:
            record['inputs_unchanged'] = snapshot() == inputs
        except Exception:
            record['inputs_unchanged'] = False
        if not record['inputs_unchanged']:
            record.update(status='error', error_type='InputDrift')
        record.update(finished_at=now(), updated_at=now())
        write(status_path, record)
    return 0 if record['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

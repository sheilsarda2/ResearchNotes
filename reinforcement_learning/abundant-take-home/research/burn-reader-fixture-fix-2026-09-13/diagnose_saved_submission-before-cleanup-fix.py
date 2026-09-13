#!/usr/bin/env python3
"""Replay one corrected hidden test against an unchanged saved Burn submission.

Read-only inspection is the default. Run with --apply inside keen_black using
Harbor's Python to reserve one shared slot and execute the offline diagnostic.
The saved model implementation is expected to STILL FAIL: its count guard uses
InvalidFormat, although the unchanged assertion requires Pickle. This replay
tests that the corrected fixture reaches that guard. It never regrades or
rewrites the original trial, and never calls a model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / 'research/task-revisions/rs-burn-store-pytorch-reader-v3'
ORIGINAL = ROOT / 'candidates_v2/rs-burn-store-pytorch-reader'
TRIAL = ROOT / 'jobs/candidates-all14-efforts-20-20260913T183301Z/rs-burn-store-pytorch-reader__dVXYtU8'
SUBMISSION = TRIAL / 'artifacts/submission/burn-store/src'
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGE = 'sha256:b4b34ba9c6e8f9efde1a2f35e2d011c92b17f7aaea8b887488105b5e6ca75ca6'
TEST = 'pytorch::tests::reader::test_tar_absurd_storage_count_is_an_error'
EXPECTED_ERROR = 'archive member declares 1099511627776 entries, more than its 11 bytes could hold'
LIMITS = {'cpus': 4, 'memory_mb': 8192, 'network_mode': 'none', 'timeout_sec': 3600}
RAW = ('result.json', 'config.json', 'benchmark-evidence.json', 'benchmark-snapshot.json',
       'benchmark-deadline.json', 'verifier/score.json', 'verifier/reward.txt',
       'verifier/pytorch_unit.log', 'verifier/pytorch_unit_failures.txt')
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission

COMMAND = r'''set -euo pipefail
cd /workspace/repo
SUB=/workspace/repo/crates/burn-store/src
rm -rf "$SUB/pytorch/tests" "$SUB/safetensors/tests"
cp -a /tests/hidden "$SUB/pytorch/tests"
cp -a /opt/pristine/crates/burn-store/src/safetensors/tests "$SUB/safetensors/tests"
# This submission already wires in both test modules; do not edit model sources.
grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/pytorch/mod.rs"
grep -qE '^\s*(pub(\(crate\))?\s+)?mod\s+tests\s*;' "$SUB/safetensors/mod.rs"
find "$SUB" -type f -exec touch {} +
export CARGO_NET_OFFLINE=true CARGO_TERM_COLOR=never RUST_BACKTRACE=1
cargo clean --offline -p burn-store
exec cargo test --offline --locked -p burn-store --lib -- \
    pytorch::tests::reader::test_tar_absurd_storage_count_is_an_error \
    --exact --test-threads=1
'''


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
    if "TAR archive has no 'sys_info' member" not in (TRIAL / 'verifier/pytorch_unit.log').read_text():
        raise RuntimeError('Historical targeted failure does not match the diagnosed fixture issue')
    return {'task_file_sha256': hashes(TASK), 'source_task_file_sha256': hashes(ORIGINAL),
            'submission_file_sha256': hashes(SUBMISSION),
            'historical_file_sha256': {name: digest(TRIAL / name) for name in RAW},
            'provenance_sha256': {name: digest(BASE / name) for name in (
                'origin.json', 'source-manifest.json', 'revision-manifest.json', 'fixture.diff')},
            'shared_control_sha256': digest(SHARED), 'runner_sha256': digest(Path(__file__))}


def classify(code, text):
    passed = code == 0 and f'test {TEST} ... ok' in text and '1 passed; 0 failed;' in text
    reached = (code == 101 and f'test {TEST} ... FAILED' in text
               and EXPECTED_ERROR in text and "TAR archive has no 'sys_info' member" not in text
               and '0 passed; 1 failed;' in text)
    return {'diagnostic_pass': passed, 'expected_remaining_model_defect_observed': reached,
            'fixture_reaches_count_guard': reached, 'full_regrade_performed': False, 'reward': None}


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


def stop_own_container(name):
    """Release the shared slot only after our named container is proved stopped."""
    inspected = subprocess.run(['docker', 'inspect', '--format', '{{json .State.Running}}', name],
                               capture_output=True, text=True, timeout=30)
    if inspected.returncode != 0:
        if 'No such object' in inspected.stderr or 'No such container' in inspected.stderr:
            return True
        return False
    if inspected.stdout.strip() == 'false':
        return True
    run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checked = subprocess.run(['docker', 'inspect', '--format', '{{json .State.Running}}', name],
                             capture_output=True, text=True, timeout=30)
    return ((checked.returncode == 0 and checked.stdout.strip() == 'false') or
            (checked.returncode != 0 and ('No such object' in checked.stderr or 'No such container' in checked.stderr)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max-wait-sec', type=int, default=21600)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        log = f'test {TEST} ... FAILED\n{EXPECTED_ERROR}\n0 passed; 1 failed;'
        assert classify(101, log)['expected_remaining_model_defect_observed']
        assert not classify(101, log)['diagnostic_pass']
        assert not classify(0, log)['expected_remaining_model_defect_observed']
        assert not classify(101, log + "TAR archive has no 'sys_info' member")['fixture_reaches_count_guard']
        assert not classify(0, '0 passed; 0 failed;')['diagnostic_pass']
        assert classify(0, f'test {TEST} ... ok\n1 passed; 0 failed;')['diagnostic_pass']
        print('Six pure outcome-classification checks passed; no Docker or model calls.')
        return 0
    if args.max_wait_sec < 1 or (args.apply and args.output is None):
        parser.error('--apply requires a fresh --output and a positive bounded wait')
    inputs = snapshot()
    record = {'kind': 'focused_saved_submission_diagnostic', 'case': 'dVXYtU8',
              'status': 'inspected', 'model_calls': 0, 'task': str(TASK),
              'source_trial': str(TRIAL), 'image_id': IMAGE, 'limits': LIMITS,
              'targeted_test': TEST, 'expected_remaining_error': EXPECTED_ERROR,
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
    name = f'burn-reader-saved-dvxytu8-{os.getpid()}'
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
        record.update(classify(result.returncode, (output / 'targeted-test.log').read_text()))
        observed = production_sources(container_source_hashes(name))
        record['container_production_source_sha256'] = observed
        if observed != production_sources(inputs['submission_file_sha256']):
            raise RuntimeError('Production sources changed during the focused diagnostic')
        record.update(status='complete', verifier_exit_code=result.returncode,
                      verifier_finished_at=now(), diagnostic_log_sha256=digest(output / 'targeted-test.log'))
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

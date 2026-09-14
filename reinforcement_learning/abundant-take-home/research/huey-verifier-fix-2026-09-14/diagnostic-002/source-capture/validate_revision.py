"""Offline Huey verifier diagnostics under the existing shared admission gate.

Default is read-only input validation. --apply creates fresh evidence only.
Each container uses 2 CPUs, 4 GiB, no network and the pinned verifier image.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
ORIGINAL = ROOT / 'candidates/huey-sqlite-leases'
REVISION = ROOT / 'research/task-revisions/huey-sqlite-leases-v2'
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGE = 'sha256:43fa880b4ac5b716019174b443c7f4b70925e9f885de705e6d66b76bba1f9927'
TRIALS = ROOT / 'jobs/candidates-all14-efforts-20-20260913T183301Z'
CASES = [('reference', None), ('saved-direct-sql', 'GN5kRT6'),
         ('saved-cleared-token', 'mrj9rUA'), ('broken-transfer-atomicity', None)]
BROKEN_TESTS = ['test_due_batch_late_error_and_scheduler_integration',
                'test_kill_mid_due_transfer_restores_whole_batch']
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_shared_admission import SharedAdmission
from benchmark_recovery import memory_snapshot


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    assert path.is_dir() and not any(p.is_symlink() for p in path.rglob('*'))
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def inputs():
    original = tree(ORIGINAL)
    assert original == json.loads((BASE / 'original-task-files.json').read_text())
    revision = tree(REVISION)
    assert set(original) == set(revision)
    changed = [k for k in original if original[k] != revision[k]]
    assert changed == ['tests/test_leases.py'], changed
    saved = {}
    for _, suffix in CASES:
        if suffix is None:
            continue
        trial = TRIALS / ('huey-sqlite-leases__' + suffix)
        saved[suffix] = dict(submission=tree(trial / 'artifacts/submission/huey'),
                             result_sha256=sha(trial / 'result.json'),
                             snapshot_sha256=sha(trial / 'benchmark-snapshot.json'))
    tools = [Path(__file__), BASE / 'broken_atomicity.py', BASE / 'check_probe.py',
             *(ROOT / 'scripts' / p for p in ['benchmark_shared_admission.py',
                  'benchmark_interleaving.py', 'benchmark_recovery.py'])]
    return dict(original=original, revision=revision, saved=saved,
                tools={str(p.relative_to(ROOT)): sha(p) for p in tools})


def run(args, *, timeout=30, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=timeout, **kwargs)


def absent(result, name):
    return result.returncode != 0 and result.stdout.strip() in ('', '[]') and re.fullmatch(
        r'(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*' + re.escape(name),
        result.stderr.strip(), re.I) is not None


def remove(name):
    command = ['docker', 'inspect', '--format', '{{json .State.Running}}', name]
    first = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if absent(first, name):
        return True
    if first.returncode != 0:
        return False
    run(['docker', 'rm', '-f', name], timeout=10, stdout=subprocess.DEVNULL)
    last = subprocess.run(command, capture_output=True, text=True, timeout=10)
    return absent(last, name)


def validate_case(label, suffix, out, name, frozen):
    out.mkdir()
    record = dict(case=label, started_at=now(), image=IMAGE, model_calls=0)
    write(out / 'result.json', record)
    run(['docker', 'create', '--name', name, '--cpus', '2', '--memory', '4096m',
         '--memory-swap', '4096m', '--network', 'none', IMAGE,
         'timeout', '-k', '5', '1100', 'sleep', 'infinity'], stdout=subprocess.DEVNULL)
    run(['docker', 'start', name], stdout=subprocess.DEVNULL)
    run(['docker', 'cp', str(REVISION / 'tests') + '/.', name + ':/tests'], timeout=60)
    if suffix is None:
        run(['docker', 'cp', str(ORIGINAL / 'solution'), name + ':/solution'])
        with (out / 'prepare.log').open('w') as log:
            run(['docker', 'exec', name, 'bash', '/solution/solve.sh'],
                timeout=60, stdout=log, stderr=subprocess.STDOUT)
    else:
        source = TRIALS / ('huey-sqlite-leases__' + suffix) / 'artifacts/submission/huey'
        run(['docker', 'exec', name, 'rm', '-rf', '/workspace/repo/huey'])
        run(['docker', 'cp', str(source), name + ':/workspace/repo/huey'], timeout=60)
        # The saved runtime is never patched. The original verifier restores only tests.
        script = ('from pathlib import Path; import hashlib,json; p=Path("/workspace/repo/huey"); '
                  'print(json.dumps({str(q.relative_to(p)):hashlib.sha256(q.read_bytes()).hexdigest() '
                  'for q in sorted(p.rglob("*")) if q.is_file()}))')
        observed = json.loads(run(['docker', 'exec', name, 'python', '-B', '-c', script],
                                  capture_output=True).stdout)
        assert observed == frozen['saved'][suffix]['submission'], 'Copied source bytes differ'
        record['saved_source_bytes_verified'] = True
    command = ['docker', 'exec', name, 'timeout', '-k', '5', '900', 'bash', '/tests/test.sh']
    if label == 'broken-transfer-atomicity':
        run(['docker', 'cp', str(BASE / 'broken_atomicity.py'), name + ':/tmp/broken_atomicity.py'])
        run(['docker', 'exec', name, 'bash', '-c',
             'rm -rf huey/tests && cp -a /opt/pristine-huey-tests huey/tests && mkdir -p /logs/verifier'])
        command = ['docker', 'exec', '-e', 'PYTHONPATH=/tmp:/workspace/repo',
                   '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1', name, 'timeout', '-k', '5', '900',
                   'python', '-B', '-m', 'pytest', '-q', '-c', '/dev/null', '--confcutdir=/tests',
                   '-p', 'broken_atomicity', *['/tests/test_leases.py::' + t for t in BROKEN_TESTS],
                   '--junitxml=/logs/verifier/results.xml']
    with (out / 'run.log').open('w') as log:
        executed = subprocess.run(command, text=True, timeout=930, stdout=log, stderr=subprocess.STDOUT)
    record['verifier_command_exit_code'] = executed.returncode
    run(['docker', 'cp', name + ':/logs/verifier', str(out / 'verifier')], timeout=30)
    xml = ET.parse(out / 'verifier/results.xml')
    cases = list(xml.getroot().iter('testcase'))
    failed = [t.get('name') for t in cases if t.find('failure') is not None or t.find('error') is not None]
    skipped = [t.get('name') for t in cases if t.find('skipped') is not None]
    expected_count = 2 if label == 'broken-transfer-atomicity' else 193
    if label == 'broken-transfer-atomicity':
        passed = executed.returncode == 1 and set(failed) == set(BROKEN_TESTS)
        # Require failure at the rollback assertions, after each failpoint was reached.
        errors = [t.find('failure').text for t in cases if t.find('failure') is not None]
        passed = passed and len(errors) == 2 and all('assert h.pending_count() == 0' in e
                    or 'assert fresh.pending_count() == 0' in e for e in errors)
    else:
        diagnostics = json.loads((out / 'verifier/diagnostics.json').read_text())
        passed = executed.returncode == 0 and diagnostics['reward'] == 1 and not failed
    record.update(finished_at=now(), collected=len(cases), failed_tests=failed, skipped_tests=skipped,
                  expected_outcome_observed=passed and len(cases) == expected_count and not skipped,
                  verifier_files=tree(out / 'verifier'))
    write(out / 'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    frozen = inputs()
    if not args.apply:
        print(json.dumps(dict(checked=True, changed_files=['tests/test_leases.py'],
                              image=IMAGE, model_calls=0, cases=CASES)))
        return 0
    out = args.output.resolve() if args.output else None
    if out is None or not out.is_relative_to(BASE) or out.exists() or out == BASE:
        parser.error('--output must be a new child directory of the evidence folder')
    out.mkdir()
    write(out / 'inputs.json', frozen)
    capture = out / 'source-capture'; capture.mkdir()
    for path in frozen['tools']:
        (capture / Path(path).name).write_bytes((ROOT / path).read_bytes())
    config = dict(max_active=1, startup_reserve_mb=768, reserve_mb=4096, shared_pool=str(SHARED))
    write(out / 'control.json', config)
    admission = SharedAdmission(SHARED, out / 'control.json')
    claim = 'huey-verifier-v2-diagnostic-' + str(os.getpid())
    acquired = False
    record = dict(status='waiting', started_at=now(), model_calls=0,
                  pid=os.getpid(), process_identity=admission.identity, claim=claim,
                  limits=dict(cpus=2, memory_mb=4096, network='none', verifier_timeout_sec=900), cases=[])
    active_name = None
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        until = time.monotonic() + 21600
        while time.monotonic() < until:
            assert inputs() == frozen, 'Input drift before admission'
            reason = admission.try_acquire(claim, memory_snapshot(), config)
            acquired = reason is None
            record.update(updated_at=now(), wait_reason=reason)
            write(out / 'result.json', record)
            if acquired:
                break
            time.sleep(2)
        else:
            raise TimeoutError('No shared capacity within six hours')
        assert run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'],
                   capture_output=True).stdout.strip() == IMAGE
        record.update(status='running', admitted_at=now())
        write(out / 'result.json', record)
        for index, (label, suffix) in enumerate(CASES):
            assert inputs() == frozen, 'Input drift before case'
            active_name = claim + '-' + str(index)
            case = validate_case(label, suffix, out / label, active_name, frozen)
            assert remove(active_name), 'Container removal not proven'
            active_name = None
            record['cases'].append(case)
            write(out / 'result.json', record)
            assert case['expected_outcome_observed'], label + ' did not have expected outcome'
        record['status'] = 'complete'
    except BaseException as error:
        record.update(status='error', error_type=type(error).__name__, error=str(error)[:500])
    finally:
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            with admission.locked() as (_, state):
                acquired = acquired or claim in state['participants'][admission.key]['trials']
        except Exception:
            record['claim_lookup_error'] = True
        removed = active_name is None
        if active_name:
            try:
                removed = remove(active_name)
            except Exception:
                removed = False
        record['all_containers_removed'] = removed
        if acquired and removed:
            admission.release(claim)
            with admission.locked() as (_, state):
                record['claim_released'] = claim not in state['participants'][admission.key]['trials']
                record['claim_released_at'] = now()
        elif acquired:
            record.update(status='error', claim_retained=True)
        record['inputs_unchanged'] = inputs() == frozen
        if not record['inputs_unchanged']:
            record['status'] = 'invalid_input_drift'
        record['finished_at'] = now()
        write(out / 'result.json', record)
    print(json.dumps({k: record.get(k) for k in ['status', 'error_type', 'error',
                      'all_containers_removed', 'claim_released', 'inputs_unchanged']}))
    return 0 if record['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

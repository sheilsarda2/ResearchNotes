#!/usr/bin/env python3
"""Bounded saved-source replay, default read-only; --apply requires restored priority."""
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
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
TASK = ROOT / 'candidates_v2/cpp-zenoh-cpp-connectivity-api'
TRIAL = ROOT / 'jobs/candidates-all14-efforts-20-20260913T183301Z/cpp-zenoh-cpp-connectivity-api__4gQVPQy'
SOURCE = TRIAL / 'artifacts/submission/include'
AUDIT = BASE / 'zenoh-cpp-4gqvpqy-regression-crash-audit.json'
AUDIT_SHA = '26afe7d629f3d0deff49b5ed7b8f60744d257a761b6740f989319589f7561b64'
RESTORATION = ROOT / 'research/burn-reader-debug-fix-2026-09-13/validation-priority/restoration.json'
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGES = ROOT / 'jobs/candidates-v2-top9-efforts-256-20260913T181833Z.images.json'
IMAGE = 'sha256:96e7c245fdf05b8a5a9ebcf8d93dbec977331377394a507f4384faa6b1e73c39'
PROBE = BASE / 'saved_zenoh_cpp_probe_v2.py'
WORK_DEADLINE = None
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission
import benchmark_interleaving as interleaving


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    temporary = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def hashes(root):
    files = [p for p in sorted(root.rglob('*')) if p.is_file()]
    if not files or any(p.is_symlink() for p in root.rglob('*')):
        raise RuntimeError('Missing input tree or symlink')
    return {str(p.relative_to(root)): sha(p) for p in files}


def restored():
    return RESTORATION.exists() and json.loads(RESTORATION.read_text()).get('passed') is True


def snapshot():
    if sha(AUDIT) != AUDIT_SHA:
        raise RuntimeError('Bound audit changed')
    audit = json.loads(AUDIT.read_text())
    for path, expected in audit['bound_artifact_sha256'].items():
        if sha(ROOT / path) != expected:
            raise RuntimeError('Historical trial artifact changed')
    expected = {name.removeprefix('artifacts/submission/include/'): row['sha256']
                for name, row in audit['saved_source_snapshot']['entries'].items() if row['kind'] == 'file'}
    if hashes(SOURCE) != expected or len(expected) != 64:
        raise RuntimeError('Saved 64-header source changed')
    from harbor.models.task.task import Task
    from harbor.publisher.packager import Packager
    if Task(TASK).checksum != audit['task_identity']['checksum']:
        raise RuntimeError('Task checksum changed')
    if Packager.compute_content_hash(TASK)[0] != audit['task_identity']['packager_digest']:
        raise RuntimeError('Task content changed')
    images = json.loads(IMAGES.read_text())['images']
    if not any(x['task'] == TASK.name and x['phase'] == 'verifier' and x['id'] == IMAGE for x in images):
        raise RuntimeError('Pinned original verifier image missing from prewarm manifest')
    tools = [Path(__file__), PROBE, *(ROOT / 'scripts' / name for name in
             ('benchmark_shared_admission.py', 'benchmark_interleaving.py', 'benchmark_recovery.py'))]
    return dict(headers=expected, tests=hashes(TASK / 'tests'), task=hashes(TASK),
                raw= audit['bound_artifact_sha256'], tool_sha256={str(p.relative_to(ROOT)): sha(p) for p in tools})


def command(args, *, cleanup=False, timeout=60, **kwargs):
    if WORK_DEADLINE is not None and not cleanup:
        timeout = min(timeout, WORK_DEADLINE - time.monotonic())
        if timeout <= 0:
            raise TimeoutError('Diagnostic work deadline reached')
    return subprocess.run(args, check=True, text=True, timeout=timeout, **kwargs)


def missing(result, name):
    pattern = r'(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*' + re.escape(name)
    return result.returncode != 0 and result.stdout.strip() in ('', '[]') and re.fullmatch(
        pattern, result.stderr.strip(), re.I) is not None


def remove_own(name):
    args = ['docker', 'inspect', '--format', '{{json .State.Running}}', name]
    before = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if missing(before, name):
        return True
    if before.returncode != 0:
        return False
    command(['docker', 'rm', '-f', name], cleanup=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    after = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return missing(after, name)


def stop_own(name):
    args = ['docker', 'inspect', '--format', '{{json .State.Running}}', name]
    before = subprocess.run(args, capture_output=True, text=True, timeout=10)
    if missing(before, name) or before.returncode == 0 and before.stdout.strip() == 'false':
        return True
    if before.returncode != 0:
        return False
    command(['docker', 'kill', name], cleanup=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    after = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return missing(after, name) or after.returncode == 0 and after.stdout.strip() == 'false'


def owns_claim(admission, name):
    with admission.locked() as (_, state):
        return name in state['participants'][admission.key]['trials']


def classify(logs):
    if not (logs / 'outcomes.json').exists():
        return dict(completed=False, reason='No final probe outcomes')
    out = json.loads((logs / 'outcomes.json').read_text())
    tests = {}
    for xml in sorted(logs.glob('*.xml')):
        root = ET.parse(xml).getroot()
        cases = list(root.iter('testcase'))
        tests[xml.stem] = dict(count=len(cases), names=[x.get('name') for x in cases], failed=[x.get('name') for x in cases
                                if x.find('failure') is not None or x.find('error') is not None],
                                runnable=all(x.find('skipped') is None and x.get('status') ==
                                    ('fail' if x.find('failure') is not None or x.find('error') is not None else 'run')
                                    for x in cases))
    isolated = sorted(name for name in tests if name.startswith('isolated-'))
    target = 'test_advanced_pub_sub_zenohpico'
    segfaults = []
    for label, result in tests.items():
        log = logs / (label + '.log')
        if target in result['failed'] and log.exists() and re.search(
                re.escape(target) + r'[^\n]*(?:SegFault|SEGFAULT|SIGSEGV)', log.read_text(errors='replace')):
            segfaults.append(log.name)
    debugger = logs / 'debugger.log'
    debugger_crash = debugger.exists() and re.search(
        r'^(?:Program|Thread[^\n]*) received signal SIGSEGV', debugger.read_text(errors='replace'), re.M) is not None
    exercised = (1 <= len(isolated) <= 3 and isolated == [f'isolated-{i}' for i in range(1, len(isolated) + 1)]
                 and all(tests[name]['names'] == [target] and tests[name]['runnable'] for name in isolated))
    if exercised and not any(tests[name]['failed'] for name in isolated):
        registered = json.loads((logs / 'registered-tests.json').read_text())
        exercised = (len(isolated) == 3 and tests.get('suite-prefix', {}).get('names') == registered[:10]
                     and tests['suite-prefix']['runnable'])
    rows = [row for row in out.get('results', []) if row.get('label') != 'debugger']
    expected_labels = isolated + (['suite-prefix'] if 'suite-prefix' in tests else [])
    exercised = (exercised and [row.get('label') for row in rows] == expected_labels
                 and set(tests) == set(expected_labels) and all(
                     row.get('timed_out') is False and row.get('return_code') ==
                     (8 if tests[row['label']]['failed'] else 0) for row in rows))
    return dict(completed=(logs / 'inputs-unchanged.json').exists() and exercised,
                expected_tests_exercised=exercised, tests=tests,
                regression_crash_reproduced=bool(segfaults), segfault_logs=segfaults,
                debugger_target_sigsegv_observed=debugger_crash,
                router_alive=out['router_alive'], probe_results=out['results'])


def main():
    global WORK_DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-wait-sec', type=int, default=21600)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        name = 'zenoh-diagnostic'
        assert missing(subprocess.CompletedProcess([], 1, '', 'Error response from daemon: No such container: ' + name), name)
        for msg in ('permission denied', 'Cannot connect to Docker daemon', 'no such container: ' + name + '-other'):
            assert not missing(subprocess.CompletedProcess([], 1, '', msg), name)
        with TemporaryDirectory() as directory:
            p = Path(directory) / 'restoration.json'
            with patch.dict(globals(), RESTORATION=p):
                assert not restored()
                p.write_text('{"passed": false}')
                assert not restored()
                p.write_text('{"passed": true}')
                assert restored()
            assert not classify(Path(directory))['completed']
        print('Eight gate/cleanup/outcome checks passed; no Docker or model calls.')
        return 0
    inputs = snapshot()
    record = dict(status='checked', model_calls=0, full_regrade_performed=False, reward=None,
                  trial=str(TRIAL.relative_to(ROOT)), image=IMAGE, limits=dict(cpus=4, memory_mb=8192,
                  network='none', runtime_sec=1800), restoration_passed=restored(), source_files=64)
    if not args.apply:
        record['tool_sha256'] = inputs['tool_sha256']
        print(json.dumps(record, indent=2))
        return 0
    if not restored():
        raise RuntimeError('Burn validation priority has not been restored; no admission attempted')
    if args.output is None or args.max_wait_sec < 1:
        parser.error('--apply needs a fresh --output and positive wait limit')
    output = args.output.resolve()
    if not output.is_relative_to(BASE) or output.exists() or output == BASE:
        parser.error('Output must be a new child directory of the evidence folder')
    lock = (BASE / 'zenoh-cpp-saved-replay.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    output.mkdir()
    write(output / 'inputs.json', inputs)
    (output / 'source-capture').mkdir()
    for name in inputs['tool_sha256']:
        (output / 'source-capture' / Path(name).name).write_bytes((ROOT / name).read_bytes())
    record.update(started_at=now(), restoration_sha256=sha(RESTORATION), status='waiting_for_shared_capacity')
    status = output / 'result.json'
    config = dict(max_active=1, startup_reserve_mb=1536, reserve_mb=4096, shared_pool=str(SHARED), paused=False)
    write(output / 'control.json', config)
    name = f'zenoh-cpp-saved-4gqvpqy-{os.getpid()}'
    record['container_name'] = name
    admission = SharedAdmission(SHARED, output / 'control.json')
    acquired = False
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        wait_end = time.monotonic() + args.max_wait_sec
        while time.monotonic() < wait_end:
            if not restored() or sha(RESTORATION) != record['restoration_sha256'] or snapshot() != inputs:
                raise RuntimeError('Diagnostic gate or inputs changed before admission')
            with admission.locked() as (_, state):
                if interleaving.managed(name, state):
                    raise RuntimeError('Diagnostic unexpectedly registered in managed interleaving policy')
            reason = admission.try_acquire(name, memory_snapshot(), config)
            # Record ownership before fallible output I/O; finally also checks the
            # shared state in case a signal/error interrupted the acquire return.
            acquired = reason is None
            record.update(updated_at=now(), wait_reason=reason)
            write(status, record)
            if acquired:
                break
            time.sleep(2)
        else:
            raise TimeoutError('Shared-capacity wait ended without launching')
        admitted = time.monotonic()
        end = admitted + 1800
        WORK_DEADLINE = end - 150
        record.update(status='running', admitted_at=now(), workload_limit_sec=1650,
                      cleanup_reserved_sec=150)
        write(status, record)
        image = command(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], capture_output=True).stdout.strip()
        if image != IMAGE:
            raise RuntimeError('Pinned verifier image mismatch')
        command(['docker', 'create', '--name', name, '--cpus', '4', '--memory', '8192m', '--memory-swap',
                 '8192m', '--network', 'none', IMAGE, 'sleep', 'infinity'], stdout=subprocess.DEVNULL)
        command(['docker', 'start', name], stdout=subprocess.DEVNULL)
        command(['docker', 'exec', name, 'mkdir', '-p', '/workspace/repo/include'])
        command(['docker', 'cp', str(SOURCE) + '/.', name + ':/workspace/repo/include'])
        command(['docker', 'cp', str(PROBE), name + ':/tmp/zenoh-cpp-diagnostic-probe.py'])
        command(['docker', 'cp', str(output / 'inputs.json'), name + ':/tmp/zenoh-cpp-diagnostic-input.json'])
        remaining = max(1, int(WORK_DEADLINE - time.monotonic() - 5))
        with (output / 'probe.log').open('w') as log:
            process = subprocess.Popen(['docker', 'exec', '-e', f'DIAGNOSTIC_REMAINING_SEC={remaining}', name,
                       'timeout', '-k', '5', str(remaining), 'python3', '/tmp/zenoh-cpp-diagnostic-probe.py'],
                       stdout=log, stderr=subprocess.STDOUT)
            while process.poll() is None and time.monotonic() < WORK_DEADLINE:
                time.sleep(1)
            if process.poll() is None:
                raise TimeoutError('1650-second work deadline reached; 150 seconds reserved for cleanup')
            record['probe_exit_code'] = process.returncode
        command(['docker', 'cp', name + ':/tmp/zenoh-cpp-diagnostic', str(output / 'logs')])
        record.update(classify(output / 'logs'))
        record.update(status='complete' if record['completed'] and process.returncode == 0 else 'error',
                      log_sha256=hashes(output / 'logs'))
    except BaseException as error:
        record.update(status='error', error_type=type(error).__name__, error=str(error)[:500])
    finally:
        # Signals are deferred only during our bounded teardown, never for paid trials.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            acquired = acquired or owns_claim(admission, name)
        except Exception:
            record['claim_state_check_error'] = True
        if acquired:
            stopped = False
            try:
                stopped = stop_own(name)
            except Exception:
                record['initial_stop_error'] = True
            record['workload_stopped_before_log_collection'] = stopped
            if stopped and not (output / 'logs').exists():
                try:
                    command(['docker', 'cp', name + ':/tmp/zenoh-cpp-diagnostic', str(output / 'partial-logs')],
                            cleanup=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
            # A transient inspection/kill error must not skip the independent
            # exact-container removal attempt. Never release an unproved claim.
            try:
                removed = remove_own(name)
            except Exception:
                removed = False
            record['container_removed_confirmed'] = removed
            if removed:
                admission.release(name)
                with admission.locked() as (_, state):
                    record['shared_claim_released'] = name not in state['participants'][admission.key]['trials']
                    record['unmanaged_after'] = not interleaving.managed(name, state)
            else:
                record.update(status='error', shared_claim_retained=True)
            record['elapsed_admission_to_cleanup_sec'] = time.monotonic() - admitted if 'admitted' in locals() else None
            if record['elapsed_admission_to_cleanup_sec'] is not None and record['elapsed_admission_to_cleanup_sec'] > 1800:
                record.update(status='invalid', error_type='DiagnosticBoundExceeded')
        try:
            record['inputs_unchanged'] = snapshot() == inputs
        except Exception:
            record['inputs_unchanged'] = False
        if not record['inputs_unchanged']:
            record.update(status='invalid', error_type='InputDrift')
        record.update(finished_at=now(), updated_at=now())
        write(status, record)
    return 0 if record['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

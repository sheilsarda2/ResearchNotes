"""Offline SQLite schema-plan verifier diagnostics under the existing shared admission gate.

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
import tarfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
ORIGINAL = ROOT / 'candidates/sqlite-utils-schema-plan'
REVISION = ROOT / 'research/task-revisions/sqlite-utils-schema-plan-v2'
SHARED = ROOT / 'jobs/candidate-campaigns-shared.control.json'
IMAGE = 'sha256:9e08f5ad5384ac44441442a2eb70a24acb6fae5a092077d878f13d3ca2ca2862'
TRIALS = ROOT / 'jobs/candidates-all14-efforts-20-20260913T183301Z'
CASES = [('reference', None), ('nop', None), ('saved-sonnet-max', 'GnfPeSr')]
NUL_TEST = 'test_default_values_are_literals[a\\x00b]'
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
    assert changed == ['tests/test_schema_plan.py'], changed
    before = (ORIGINAL / 'tests/test_schema_plan.py').read_bytes()
    deleted = b"{'t':{'rename':{'v':''}}},"
    assert before.count(deleted) == 1
    assert (REVISION / 'tests/test_schema_plan.py').read_bytes() == before.replace(deleted, b'')
    saved = {}
    for _, suffix in CASES:
        if suffix is None:
            continue
        trial = TRIALS / ('sqlite-utils-schema-plan__' + suffix)
        saved[suffix] = dict(submission=tree(trial / 'artifacts/submission/sqlite_utils'),
                             result_sha256=sha(trial / 'result.json'),
                             snapshot_sha256=sha(trial / 'benchmark-snapshot.json'))
    tools = [Path(__file__),
             *(ROOT / 'scripts' / p for p in ['benchmark_shared_admission.py',
                  'benchmark_interleaving.py', 'benchmark_recovery.py'])]
    baseline = {}
    with tarfile.open(ORIGINAL / 'tests/image-source/upstream.tar.gz') as archive:
        for member in archive.getmembers():
            name = member.name.removeprefix('./')
            if member.isfile() and name.startswith('sqlite_utils/'):
                relative = name.removeprefix('sqlite_utils/')
                if '__pycache__' not in Path(relative).parts and not relative.endswith('.pyc'):
                    baseline[relative] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    assert baseline and '__init__.py' in baseline
    return dict(original=original, revision=revision, saved=saved, baseline_runtime=baseline,
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
    # Verify the cached image starts from the pinned package, before any mutation.
    script = ('from pathlib import Path; import hashlib,json; p=Path("/workspace/repo/sqlite_utils"); '
              'print(json.dumps({str(q.relative_to(p)):hashlib.sha256(q.read_bytes()).hexdigest() '
              'for q in sorted(p.rglob("*")) if q.is_file() and "__pycache__" not in q.parts and q.suffix != ".pyc"}))')
    observed = json.loads(run(['docker', 'exec', name, 'python', '-B', '-c', script],
                              capture_output=True).stdout)
    assert observed == frozen['baseline_runtime'], 'Cached baseline package differs from pinned archive'
    record['baseline_source_bytes_verified'] = True
    if label == 'reference':
        # Read-only contract check in the same SQLite runtime; only an in-memory DB.
        check = 'import sqlite3,json\nc=sqlite3.connect(":memory:")\nc.execute("CREATE TABLE t(v TEXT)")\nc.execute("INSERT INTO t VALUES(?)", ["kept"])\nc.execute(\'ALTER TABLE t RENAME COLUMN v TO ""\')\nnames=[r[1] for r in c.execute("PRAGMA table_info(t)")]\nassert names == [""] and c.execute("SELECT * FROM t").fetchall() == [("kept",)]\nprint(json.dumps(dict(sqlite_version=sqlite3.sqlite_version, empty_identifier_accepted=True, value_preserved=True)))\n'
        checked = json.loads(run(['docker', 'exec', name, 'python', '-B', '-c', check],
                                 capture_output=True).stdout)
        write(out / 'sqlite-contract-check.json', checked)

    if label == 'reference':
        run(['docker', 'cp', str(ORIGINAL / 'solution'), name + ':/solution'])
        with (out / 'prepare.log').open('w') as log:
            run(['docker', 'exec', name, 'bash', '/solution/solve.sh'],
                timeout=60, stdout=log, stderr=subprocess.STDOUT)
    elif suffix:
        source = TRIALS / ('sqlite-utils-schema-plan__' + suffix) / 'artifacts/submission/sqlite_utils'
        run(['docker', 'exec', name, 'rm', '-rf', '/workspace/repo/sqlite_utils'])
        run(['docker', 'cp', str(source), name + ':/workspace/repo/sqlite_utils'], timeout=60)
        observed = json.loads(run(['docker', 'exec', name, 'python', '-B', '-c', script],
                                  capture_output=True).stdout)
        assert observed == frozen['saved'][suffix]['submission'], 'Copied source bytes differ'
        record['saved_source_bytes_verified'] = True
    else:
        # Baseline remains untouched; confirm the new API is absent.
        check = 'from sqlite_utils import Database; assert not hasattr(Database, "transform_schema")'
        run(['docker', 'exec', name, 'python', '-B', '-c', check])
        record['new_api_absent'] = True
    command = ['docker', 'exec', name, 'timeout', '-k', '5', '900', 'bash', '/tests/test.sh']
    with (out / 'run.log').open('w') as log:
        executed = subprocess.run(command, text=True, timeout=930, stdout=log, stderr=subprocess.STDOUT)
    record['verifier_command_exit_code'] = executed.returncode
    run(['docker', 'cp', name + ':/logs/verifier', str(out / 'verifier')], timeout=30)
    xml = ET.parse(out / 'verifier/results.xml')
    cases = list(xml.getroot().iter('testcase'))
    failed = [t.get('name') for t in cases if t.find('failure') is not None or t.find('error') is not None]
    skipped = [t.get('name') for t in cases if t.find('skipped') is not None]
    errors = [t.get('name') for t in cases if t.find('error') is not None]
    diagnostics = json.loads((out / 'verifier/diagnostics.json').read_text())
    outcomes = diagnostics['outcomes']
    common = (len(cases) == diagnostics['collected'] == 331 and not skipped and not errors
              and not outcomes['skipped'] and not outcomes['xfail']
              and outcomes['failed'] == len(failed)
              and outcomes['passed'] + outcomes['failed'] == 331)
    if label == 'reference':
        passed = (executed.returncode == diagnostics['exit_code'] == 0
                  and diagnostics['reward'] == 1 and not failed)
    elif label == 'nop':
        failures = [t.find('failure').text or '' for t in cases if t.find('failure') is not None]
        passed = (executed.returncode == diagnostics['exit_code'] == 1
                  and diagnostics['reward'] == 0 and len(failed) == 118 and outcomes['passed'] == 213
                  and any('Database.transform_schema is missing' in text for text in failures))
    else:
        failures = [t.find('failure').text or '' for t in cases if t.find('failure') is not None]
        passed = (executed.returncode == diagnostics['exit_code'] == 1
                  and diagnostics['reward'] == 0 and failed == [NUL_TEST]
                  and len(failures) == 1 and 'query contains a null character' in failures[0])
    reward_file = int((out / 'verifier/reward.txt').read_text().strip())
    record.update(finished_at=now(), collected=len(cases), passed=outcomes['passed'],
                  failed_tests=failed, skipped_tests=skipped, reward=diagnostics['reward'],
                  expected_outcome_observed=passed and common and reward_file == diagnostics['reward'],
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
        print(json.dumps(dict(checked=True, changed_files=['tests/test_schema_plan.py'],
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
    (capture / 'test_schema_plan.py').write_bytes((REVISION / 'tests/test_schema_plan.py').read_bytes())
    config = dict(max_active=1, startup_reserve_mb=768, reserve_mb=4096, shared_pool=str(SHARED))
    write(out / 'control.json', config)
    admission = SharedAdmission(SHARED, out / 'control.json')
    claim = 'sqlite-verifier-v2-diagnostic-' + str(os.getpid())
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

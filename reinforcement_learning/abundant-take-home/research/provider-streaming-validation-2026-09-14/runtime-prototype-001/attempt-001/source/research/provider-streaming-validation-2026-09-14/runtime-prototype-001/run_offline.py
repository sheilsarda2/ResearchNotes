"""One normally admitted, network-none disposable lifecycle proof; never a model run."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = HERE.parents[2]
ENV = BASE / 'runtime-environment-001'
ENV_PROOF_SHA = '3a3d5a28a5c950e1315a62c3aa1f1e46ea9c8e380db04c15c5057409c68a8f2b'
IMAGE = 'sha256:a76d6fd31b9397504d4355faa5c014dcfca43249d6a2ebeb65be824a40de3261'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for part in iter(lambda: source.read(1024 * 1024), b''):
            value.update(part)
    return value.hexdigest()


def dump(path, value):
    temporary = Path(path).with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def preflight():
    manifest = json.loads((HERE / 'source-manifest.json').read_text())
    for name, expected in manifest['source_file_sha256'].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError('Prototype or unchanged runtime source drift')
    if digest(ENV / 'environment-proof.json') != ENV_PROOF_SHA:
        raise RuntimeError('Captured environment proof changed')
    proof = json.loads((ENV / 'environment-proof.json').read_text())
    assert proof['passed'] and proof['cached_python31211_image']['id'] == IMAGE
    for name, expected in [('site-packages.tar', proof['tar_sha256']), ('file-manifest.json', proof['file_manifest_sha256']),
                           ('mini-swe-agent', proof['console_entry_sha256'])]:
        if digest(ENV / 'fixture' / name) != expected:
            raise RuntimeError('Captured package fixture drift')
    for path, expected in proof['copied_admission_source_sha256'].items():
        assert digest(ENV / 'tooling' / Path(path).name) == expected
    return dict(kind='offline_runtime_preflight', model_calls=0, environment_proof_sha256=ENV_PROOF_SHA,
                source_manifest_sha256=digest(HERE / 'source-manifest.json'), image=IMAGE,
                source_file_sha256=manifest['source_file_sha256'], limits=dict(cpu=4, memory_mb=8192,
                network='none', shared_cap=14, local_cap=1, admitted_wall_seconds=600))


def run(output, host_root, inputs):
    if output.parent != HERE or not output.name.startswith('attempt-') or output.exists():
        raise ValueError('Require a fresh attempt-* directory beside this runner')
    output.mkdir()
    source = output / 'source'
    case_output = output / 'output'
    case_output.mkdir()
    for name in inputs['source_file_sha256']:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    control = output / 'control.json'
    pool_path = ROOT / 'jobs/candidate-campaigns-shared.control.json'
    dump(control, dict(shared_pool=str(pool_path), max_active=1))
    dump(output / 'inputs.json', inputs)
    sys.path.insert(0, str(ENV / 'tooling'))
    from benchmark_shared_admission import SharedAdmission, process_identity
    from benchmark_recovery import memory_snapshot
    assert json.loads(pool_path.read_text())['max_active'] == 14
    name = 'streaming-offline-' + uuid.uuid4().hex[:16]
    result = dict(kind='offline_runtime_container_proof', status='waiting', started_at=now(),
                  passed=False, model_calls=0, counted_sweep_trials=0, container_name=name,
                  runner_pid=os.getpid(), runner_identity=process_identity(os.getpid()), inputs=inputs)
    dump(output / 'result.json', result)
    pool = SharedAdmission(pool_path, control)
    acquired = False
    created = False
    deadline = None
    sequence = 0

    def command(label, argv, *, timeout=30, check=True):
        nonlocal sequence
        sequence += 1
        if deadline is not None:
            timeout = min(timeout, max(.1, deadline - time.monotonic() - 45))
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        (output / (str(sequence).zfill(2) + '-' + label + '.log')).write_text(completed.stdout + completed.stderr)
        if check and completed.returncode:
            raise RuntimeError(label + ' failed with exit ' + str(completed.returncode))
        return completed

    def container_absent():
        completed = subprocess.run(['docker', 'container', 'inspect', name, '--format', '{{.Id}}'], capture_output=True, text=True, timeout=10)
        if completed.returncode == 0:
            return False
        if 'No such container' in completed.stderr or 'No such object' in completed.stderr:
            return True
        raise RuntimeError('Container absence is unknown')

    def stop(signum, frame):
        raise KeyboardInterrupt('Offline runner termination requested')

    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            reason = pool.try_acquire(name, memory_snapshot(), {'startup_reserve_mb': 1024})
            if reason is None:
                acquired = True
                break
            result.update(wait_reason=reason, heartbeat_at=now())
            dump(output / 'result.json', result)
            print(json.dumps(dict(status='waiting', reason=reason)), flush=True)
            time.sleep(10)
        deadline = time.monotonic() + 600
        result.update(status='admitted', admitted_at=now(), heartbeat_at=now())
        dump(output / 'result.json', result)
        # Only a narrow captured source scaffold is mounted. The original
        # workspace, .env, tasks and raw jobs are not exposed to the fixture.
        def host(path):
            return str(host_root / path.relative_to(ROOT))
        command('create', ['docker', 'create', '--name', name, '--label', 'streaming-offline-proof=' + name,
                          '--network', 'none', '--cpus', '4', '--memory', '8g',
                          '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'LITELLM_LOCAL_MODEL_COST_MAP=True',
                          '--workdir', '/tmp', '--mount', 'type=bind,src=' + host(source) + ',dst=/workspace,readonly',
                          '--mount', 'type=bind,src=' + host(ENV / 'fixture') + ',dst=/fixture,readonly',
                          '--mount', 'type=bind,src=' + host(case_output) + ',dst=/output', IMAGE, 'sleep', 'infinity'])
        created = True
        command('start', ['docker', 'start', name])
        inspect = json.loads(command('limits', ['docker', 'inspect', name, '--format',
                    '{"Id":{{json .Id}},"Image":{{json .Image}},"HostConfig":{{json .HostConfig}},"Mounts":{{json .Mounts}}}']).stdout)
        host_config = inspect['HostConfig']
        assert inspect['Image'] == IMAGE and host_config['NanoCpus'] == 4000000000
        assert host_config['Memory'] == 8192 * 1024 ** 2 and host_config['NetworkMode'] == 'none'
        assert {r['Destination']: r['RW'] for r in inspect['Mounts']} == {'/workspace': False, '/fixture': False, '/output': True}
        result['observed_limits'] = dict(cpu=4, memory_mb=8192, network='none', storage_opt=host_config.get('StorageOpt'))
        script_root = '/workspace/' + HERE.relative_to(ROOT).as_posix()
        command('prepare', ['docker', 'exec', name, 'python3', '-B', script_root + '/prepare_inside.py', '--proof', '/output/prepare.json'], timeout=90)
        actual = json.loads((case_output / 'prepare.json').read_text())
        assert actual['every_package_hash_verified'] and actual['package_files'] == 13188
        command('lifecycle', ['docker', 'exec', name, '/opt/mini/bin/python', '-B', script_root + '/offline_lifecycle.py',
                              '--output', '/output/cases', '--mini-python', '/opt/mini/bin/python'], timeout=430)
        summary = json.loads((case_output / 'cases/summary.json').read_text())
        result.update(lifecycle_summary_sha256=digest(case_output / 'cases/summary.json'), lifecycle_passed=summary['passed'])
        assert summary['passed'] and summary['model_calls'] == summary['external_requests'] == 0
        assert all(digest(source / name) == expected for name, expected in inputs['source_file_sha256'].items())
        result.update(status='complete', passed=True, captured_sources_unchanged=True)
    except BaseException as error:
        result.update(status='failed', error_type=type(error).__name__, error=str(error))
    finally:
        cleanup_errors = []
        # Reconcile ownership even if acquisition or Docker create failed just
        # after writing external state; never release while existence is unknown.
        try:
            with pool.locked() as (_, state):
                acquired = name in state['participants'].get(pool.key, {}).get('trials', {})
        except BaseException as error:
            cleanup_errors.append('claim_lookup_' + type(error).__name__)
        try:
            if not container_absent():
                try:
                    subprocess.run(['docker', 'kill', name], capture_output=True, timeout=10)
                except BaseException as error:
                    cleanup_errors.append('kill_' + type(error).__name__)
                try:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True, check=True, timeout=10)
                except BaseException as error:
                    cleanup_errors.append('remove_' + type(error).__name__)
            absent = container_absent()
            result['container_absent'] = absent
            if absent:
                if acquired:
                    pool.release(name)
                with pool.locked() as (_, state):
                    result['claim_absent'] = name not in state['participants'].get(pool.key, {}).get('trials', {})
        except BaseException as error:
            cleanup_errors.append('cleanup_' + type(error).__name__)
        result.update(cleanup_errors=cleanup_errors, finished_at=now())
        result['passed'] = bool(result['passed'] and result.get('container_absent') and result.get('claim_absent') and not cleanup_errors)
        dump(output / 'result.json', result)
        print(json.dumps({k: result.get(k) for k in ('status', 'passed', 'error_type', 'error', 'container_absent', 'claim_absent')}), flush=True)
    return 0 if result['passed'] else 1


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--host-root', type=Path)
    args = parser.parse_args()
    inputs = preflight()
    if args.check_only:
        print(json.dumps({key: value for key, value in inputs.items() if key != 'source_file_sha256'}, indent=2))
        return
    if args.output is None or args.host_root is None:
        parser.error('--run requires --output and --host-root')
    raise SystemExit(run(args.output.resolve(), args.host_root.resolve(), inputs))


if __name__ == '__main__':
    main()

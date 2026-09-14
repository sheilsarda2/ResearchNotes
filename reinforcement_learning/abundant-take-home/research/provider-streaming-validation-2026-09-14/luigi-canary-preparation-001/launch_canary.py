"""Prepare/check, or explicitly run one NEW standalone transport canary.

No campaign plan adoption, restart, retries of jobs, or existing worker changes.
"""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CAPTURE = HERE / 'captured/files'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_map(path):
    result = {}
    for item in sorted(Path(path).rglob('*')):
        if item.is_symlink():
            raise RuntimeError('Symlink in identity-bound source tree')
        if item.is_file():
            result[item.relative_to(path).as_posix()] = digest(item)
    return result


def write_new(path, value):
    with Path(path).open('x') as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write('\n')


def comparable_config(config):
    value = copy.deepcopy(config)
    # Harbor models these exception names as sets; serialization order varies
    # by process. Preserve members exactly without requiring incidental order.
    for field in ('exclude_exceptions', 'include_exceptions'):
        if isinstance(value.get('retry', {}).get(field), list):
            value['retry'][field] = sorted(value['retry'][field])
    return value


def certificate_preflight(expected_sha=None, *, installed=True):
    certificate_path = HERE / 'preliminary-transport-certificate.json'
    if expected_sha is not None and digest(certificate_path) != expected_sha:
        raise RuntimeError('Reviewed transport certificate changed')
    cert = json.loads(certificate_path.read_text())
    for reference in cert['bound_inputs'].values():
        if digest(ROOT / reference['path']) != reference['sha256']:
            raise RuntimeError('Canary input/proof/launcher drift')
    capture = json.loads((HERE / 'captured/manifest.json').read_text())
    if file_map(CAPTURE) != capture['source_file_sha256']:
        raise RuntimeError('Captured runtime tree drift')
    task = ROOT / cert['task']['path']
    if file_map(task) != cert['task']['file_sha256']:
        raise RuntimeError('Canary task changed')
    config = json.loads((HERE / 'job.config.json').read_text())
    control = json.loads((HERE / 'control.json').read_text())
    if (config['job_name'] != cert['job_name'] or config['n_attempts'] != 1 or config['n_concurrent_trials'] != 1
            or config['retry']['max_retries'] != 0 or len(config['tasks']) != 1 or len(config['agents']) != 1):
        raise RuntimeError('Canary is not exactly one predeclared attempt')
    if control['max_active'] != 1 or control['paused'] or control['shared_pool'] != cert['shared_control']:
        raise RuntimeError('Canary admission control changed')
    if os.environ.get('MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT', '10') != '10':
        raise RuntimeError('Unexpected inherited Mini retry override')
    # A custom wrapper is intentionally not registered as a runnable campaign
    # job: standard active_pid cannot recognize its argv while it is active.
    for plan_path in (ROOT / 'jobs').glob('*.plan.json'):
        plan = json.loads(plan_path.read_text())
        if any(job.get('name') == cert['job_name'] for job in plan.get('jobs', [])):
            raise RuntimeError('Canary must stay outside runnable campaign plans')
    if installed:
        from harbor.models.job.config import JobConfig
        from harbor.models.task.task import Task
        validated = JobConfig.model_validate(config)
        if comparable_config(json.loads(validated.model_dump_json())) != comparable_config(config):
            raise RuntimeError('JobConfig does not round-trip exactly')
        if Task(task).checksum != cert['task']['harbor_task_checksum']:
            raise RuntimeError('Harbor task checksum changed')
        spec = importlib.util.find_spec('harbor')
        package = Path(spec.origin).parent
        expected = json.loads((HERE / 'installed-harbor-identity.json').read_text())
        actual = {p.relative_to(package).as_posix(): digest(p) for p in package.rglob('*.py')}
        if actual != expected['python_file_sha256']:
            raise RuntimeError('Installed Harbor source changed')
        if json.loads(Path(cert['shared_control']).read_text()).get('max_active') != 14:
            raise RuntimeError('Reviewed shared concurrency cap changed')
    return cert


def run(cert, certificate_sha):
    if digest(HERE / 'preliminary-transport-certificate.json') != certificate_sha:
        raise RuntimeError('Certificate changed before launch')
    job_path = ROOT / cert['raw_job_path']
    if job_path.exists() or (HERE / 'run-001').exists():
        raise RuntimeError('Canary is once-only: existing raw job or run record')
    record = HERE / 'run-001'
    lock = (HERE / '.launch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if job_path.exists() or record.exists():
        raise RuntimeError('Duplicate canary launch')
    record.mkdir()
    fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    identity = dict(schema_version=1, kind='standalone_streaming_canary_runner', status='starting',
                    pid=os.getpid(), process_start_identity=fields[19], started_at=now(),
                    certificate_sha256=certificate_sha, job=cert['job_name'], raw_job_path=cert['raw_job_path'],
                    counted_sweep_trials=0, campaign_adoption='not_automatic')
    write_new(record / 'run-identity.json', identity)
    try:
        os.chdir(ROOT)
        # Same credential transport as run-candidate-screen.py. Never print or
        # persist credentials, headers, or a full environment.
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env')
        if os.environ.get('MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT', '10') != '10':
            raise RuntimeError('Unexpected Mini retry override after credential loading')
        os.environ['ANTHROPIC_API_KEY'] = os.environ['TAKE_HOME_TOKEN']
        os.environ['HARBOR_ADMISSION_CONTROL'] = str(HERE / 'control.json')
        os.environ['HARBOR_INTERLEAVE_TASKS'] = '1'
        if any(name.startswith('benchmark_') for name in sys.modules):
            raise RuntimeError('Uncaptured benchmark module already imported')
        if any(Path(path or '.').resolve() == ROOT / 'scripts' for path in sys.path):
            raise RuntimeError('Live scripts path must not shadow the capture')
        scripts = CAPTURE / 'scripts'
        prototype = CAPTURE / 'research/provider-streaming-validation-2026-09-14/runtime-prototype-002'
        sys.path.insert(0, str(scripts))
        sys.path.insert(0, str(prototype))
        source = scripts / 'harbor-resource-runner.py'
        spec = importlib.util.spec_from_file_location('captured_standard_resource_runner', source)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        original_install = runner.install_agent_runtime

        def install_opted_in_agent_runtime():
            original_install()
            import streaming_runtime
            streaming_runtime.install()

        runner.install_agent_runtime = install_opted_in_agent_runtime
        sys.argv = [str(source), 'run', '-c', str(HERE / 'job.config.json'), '-y']
        runner.main()
        identity['status'] = 'runner_returned'
    except BaseException as error:
        identity.update(status='runner_exited', exit_type=type(error).__name__)
        if isinstance(error, SystemExit):
            identity['exit_code'] = error.code
        raise
    finally:
        imports = {}
        inspection_errors = []
        for name, module in sorted(sys.modules.items()):
            if name.startswith('benchmark_') or name in ('streaming_runtime', 'captured_standard_resource_runner'):
                try:
                    path = Path(module.__file__).resolve()
                    if not path.is_relative_to(CAPTURE):
                        raise RuntimeError('Uncaptured runtime import')
                    imports[name] = dict(path=str(path.relative_to(ROOT)), sha256=digest(path))
                except BaseException as error:
                    inspection_errors.append(dict(module=name, error_type=type(error).__name__))
        identity.update(finished_at=now(), loaded_local_modules=imports,
                        runtime_inspection_errors=inspection_errors,
                        captured_source_files_unchanged=file_map(CAPTURE) == json.loads((HERE / 'captured/manifest.json').read_text())['source_file_sha256'],
                        task_files_unchanged=file_map(ROOT / cert['task']['path']) == cert['task']['file_sha256'],
                        eligibility='pending independent terminal review, regardless of reward')
        write_new(record / 'runner-outcome.json', identity)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--run', action='store_true')
    parser.add_argument('--certificate-sha256')
    args = parser.parse_args()
    if args.run and not args.certificate_sha256:
        parser.error('--run requires the exact reviewed --certificate-sha256')
    cert = certificate_preflight(args.certificate_sha256)
    if args.check_only:
        print(json.dumps(dict(prepared_only=True, model_calls=0, job=cert['job_name'],
                             certificate_sha256=digest(HERE / 'preliminary-transport-certificate.json'),
                             task_checksum=cert['task']['harbor_task_checksum'], count=1), indent=2))
        return
    run(cert, args.certificate_sha256)


if __name__ == '__main__':
    main()

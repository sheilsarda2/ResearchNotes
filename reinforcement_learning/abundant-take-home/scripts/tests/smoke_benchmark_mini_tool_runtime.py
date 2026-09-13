"""Offline Mini CLI smoke using read-only installed packages in a disposable VM container."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission

IMAGE = 'sha256:a76d6fd31b9397504d4355faa5c014dcfca43249d6a2ebeb65be824a40de3261'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    control = args.output / 'control.json'
    pool_path = ROOT / 'jobs/candidate-campaigns-shared.control.json'
    control.write_text(json.dumps({'shared_pool': str(pool_path), 'max_active': 1}) + '\n')
    pool = SharedAdmission(pool_path, control)
    name = 'mini-tool-cli-smoke-' + uuid.uuid4().hex[:10]
    created = False
    acquired = False
    snapshot = None
    result = {'model_calls': 0, 'image_id': IMAGE, 'network': 'none', 'passed': False}
    original = args.packages / 'minisweagent/environments/local.py'

    def command(label, argv):
        completed = subprocess.run(argv, text=True, capture_output=True, timeout=60)
        (args.output / (label + '.stdout.txt')).write_text(completed.stdout)
        (args.output / (label + '.stderr.txt')).write_text(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'{label} failed with exit {completed.returncode}')
        return completed.stdout

    try:
        while reason := pool.try_acquire(name, memory_snapshot(), {'startup_reserve_mb': 1024}):
            print('Waiting for shared admission: ' + reason, flush=True)
            time.sleep(5)
        acquired = True
        before = hashlib.sha256(original.read_bytes()).hexdigest()
        snapshot = tempfile.TemporaryDirectory(prefix='mini-tool-cli-packages-')
        # OCI cannot bind a /proc/PID/root magic link. Copy only package code;
        # never copy the live process environment or user configuration.
        package_copy = Path(snapshot.name) / 'site-packages'
        shutil.copytree(args.packages, package_copy, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
        command('create', ['docker', 'create', '--name', name, '--network', 'none', '--cpus', '1',
                          '--memory', '1g', '--env', 'PYTHONDONTWRITEBYTECODE=1',
                          '--mount', f'type=bind,src={package_copy},dst=/opt/mini/lib/python3.12/site-packages,readonly',
                          IMAGE, 'sleep', 'infinity'])
        created = True
        command('start', ['docker', 'start', name])
        command('venv', ['docker', 'exec', name, 'python3', '-m', 'venv', '--without-pip', '/opt/mini'])
        entry = Path(snapshot.name) / 'mini-swe-agent'
        entry.write_bytes((args.packages.parents[2] / 'bin/mini-swe-agent').read_bytes())
        command('entry', ['docker', 'cp', str(entry), name + ':/opt/mini/bin/mini-swe-agent'])
        command('private', ['docker', 'exec', name, 'install', '-d', '-m', '700', '/tmp/benchmark-private'])
        sources = {'b.py': 'benchmark_mini_tool_bootstrap.py', 'c.py': 'benchmark_mini_tool_cleanup.py'}
        result['source_sha256'] = {}
        for remote, local in sources.items():
            source = ROOT / 'scripts' / local
            result['source_sha256'][local] = hashlib.sha256(source.read_bytes()).hexdigest()
            command('upload-' + remote, ['docker', 'cp', str(source), name + ':/tmp/benchmark-private/' + remote])
        interpreter = '/opt/mini/bin/python'
        bootstrap = '/tmp/benchmark-private/b.py'
        check = command('preflight', ['docker', 'exec', name, interpreter, bootstrap, '--benchmark-preflight'])
        observed = json.loads(next(line.split('=', 1)[1] for line in check.splitlines()
                                   if line.startswith('BENCHMARK_MINI_TOOL_RUNTIME=')))
        assert observed['installed'] and observed['python'] == '3.12.11' and observed['package_version'] == '2.4.6'
        result['preflight'] = observed
        help_output = command('actual-cli-help', ['docker', 'exec', name, interpreter, bootstrap, '--help'])
        assert 'BENCHMARK_MINI_TOOL_RUNTIME=' in help_output and 'Run mini-SWE-agent' in help_output
        safety = command('isolation', ['docker', 'exec', name, interpreter, '-c',
                                      'import os,json; print(json.dumps({"api_key_present": any(k.endswith("_API_KEY") and v for k,v in os.environ.items()), "pythonpath_present": "PYTHONPATH" in os.environ}))'])
        result['isolation'] = json.loads(safety)
        assert not result['isolation']['api_key_present'] and not result['isolation']['pythonpath_present']
        result['original_local_module_sha256'] = before
        assert hashlib.sha256(original.read_bytes()).hexdigest() == before
        result.update(original_package_file_unchanged=True, actual_cli_help_passed=True, passed=True)
    finally:
        try:
            if created:
                subprocess.run(['docker', 'rm', '--force', name], check=True, stdout=subprocess.DEVNULL)
        finally:
            try:
                if snapshot is not None:
                    snapshot.cleanup()
            finally:
                if acquired:
                    pool.release(name)
                (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()

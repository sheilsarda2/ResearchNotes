#!/usr/bin/env python3
"""No-model Docker smoke for the pinned mini-swe-agent Python runtime.

Run with the devcontainer's Harbor interpreter and PYTHONPATH=scripts:
  python scripts/tests/test_benchmark_agent_runtime.py --docker --output NEW_DIR

Uses the cached, frozen Foxglove task image. Only disposable containers created
by this harness are removed. No agent.run(), trial, verifier, or model is run.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import time
import uuid


IMAGE = 'sha256:c1bc2b2f9f3d218562a00260f5cd402c9e410e48b933b3fdfb4f228e46379c98'
TASK = 'candidates_v2/cpp-foxglove-sdk-parameter-handler'
PROBE = r'''import importlib.metadata, json, os, platform, sys, traceback
data = {'python': platform.python_version(), 'executable': sys.executable,
        'packages': {name: importlib.metadata.version(name)
                     for name in ('mini-swe-agent', 'litellm', 'typing-extensions')},
        'api_key_present': any(k.endswith('_API_KEY') and v for k, v in os.environ.items())}
try:
    from typing import NotRequired
    data['typing_not_required_imported'] = True
except ImportError:
    data['typing_not_required_imported'] = False
try:
    import litellm
    from minisweagent.models.litellm_model import LitellmModel
    data['model_module_imported'] = True
except Exception as error:
    data.update(model_module_imported=False, error_type=type(error).__name__,
                error=str(error), traceback=traceback.format_exc())
print('RUNTIME_SMOKE_JSON=' + json.dumps(data, sort_keys=True))
'''


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hashes(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob('*')) if path.is_file()}


async def command(*args: str, timeout: float = 1800) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    return process.returncode, stdout.decode(errors='replace'), stderr.decode(errors='replace')


class DockerExecEnvironment:
    """Small exec adapter: actual Harbor installation, credential-free Docker.

    The runtime hook wraps MiniSweAgent.install(), so exercising it needs only
    the public exec contract. Installing through that method catches both shell
    rewriting regressions and package compatibility on the real task base.
    """

    def __init__(self, container: str, output: Path):
        self.container = container
        self.output = output
        self.calls = 0

    async def exec(self, command, user=None, env=None, cwd=None, timeout_sec=None, **kwargs):
        from harbor.environments.base import ExecResult
        if kwargs:
            raise RuntimeError('Unexpected environment.exec options: ' + ','.join(kwargs))
        variables = dict(env or {})
        if any(key.endswith('_API_KEY') for key in variables):
            raise RuntimeError('No API credentials are permitted in this smoke')
        variables['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
        args = ['docker', 'exec', '-i']
        if user is not None:
            args += ['--user', str(user)]
        if cwd is not None:
            args += ['--workdir', str(cwd)]
        for key, value in variables.items():
            args += ['--env', f'{key}={value}']
        args += [self.container, '/bin/bash', '-lc', command]
        self.calls += 1
        before = time.monotonic()
        code, stdout, stderr = await globals()['command'](
            *args, timeout=timeout_sec or 1800)
        prefix = self.output / f'install-exec-{self.calls:02d}'
        prefix.with_suffix('.stdout.txt').write_text(stdout)
        prefix.with_suffix('.stderr.txt').write_text(stderr)
        prefix.with_suffix('.json').write_text(json.dumps({
            'return_code': code, 'elapsed_sec': time.monotonic() - before,
            'user': user, 'cwd': cwd, 'environment_variable_names': sorted(variables),
            'command': command,
        }, indent=2) + '\n')
        return ExecResult(return_code=code, stdout=stdout, stderr=stderr)


async def run_case(output: Path, mode: str, image: str) -> dict:
    from harbor.agents.installed.mini_swe_agent import MiniSweAgent

    folder = output / mode
    folder.mkdir()
    container = 'agent-runtime-smoke-' + mode + '-' + uuid.uuid4().hex[:8]
    start = now()
    code, stdout, stderr = await command(
        'docker', 'run', '--detach', '--name', container,
        '--cpus', '1', '--memory', '1g', '--network', 'bridge',
        '--entrypoint', '/bin/sleep', image, 'infinity')
    if code:
        raise RuntimeError('Disposable container failed to start: ' + stderr)
    row = {'mode': mode, 'started_at': start, 'container_name': container,
           'image_id': image, 'model_calls': 0}
    try:
        environment = DockerExecEnvironment(container, folder)
        baseline = await environment.exec(
            'python3 -c "import platform; print(platform.python_version())"')
        row['task_system_python_before'] = baseline.stdout.strip()
        agent = MiniSweAgent(logs_dir=folder / 'agent', version='2.4.6')
        await agent.install(environment)
        row['installation_completed'] = True
        # Neither import probe nor any model client can use the network here.
        code, _, stderr = await command('docker', 'network', 'disconnect', 'bridge', container)
        if code:
            raise RuntimeError('Could not isolate completed installation: ' + stderr)
        probe = await environment.exec(
            'set -euo pipefail; source "$HOME/.local/bin/env"; '
            '"$(uv tool dir)/mini-swe-agent/bin/python" -c ' + shlex.quote(PROBE))
        for line in probe.stdout.splitlines():
            if line.startswith('RUNTIME_SMOKE_JSON='):
                row['runtime'] = json.loads(line.split('=', 1)[1])
        if 'runtime' not in row:
            raise RuntimeError('Import probe produced no structured evidence')
        baseline_after = await environment.exec(
            'python3 -c "import platform; print(platform.python_version())"')
        row['task_system_python_after'] = baseline_after.stdout.strip()
        info = row['runtime']
        checks = {
            'task_python_preserved': row['task_system_python_before'] == row['task_system_python_after'],
            'task_python_is_310': row['task_system_python_before'].startswith('3.10.'),
            'no_api_keys_present': info['api_key_present'] is False,
            'agent_version_preserved': info['packages']['mini-swe-agent'] == '2.4.6',
        }
        if mode == 'original':
            checks.update({
                'old_agent_runtime_is_310': info['python'].startswith('3.10.'),
                'reproduced_notrequired_failure': info.get('error_type') == 'ImportError'
                    and 'NotRequired' in info.get('error', ''),
                'model_module_import_failed': info['model_module_imported'] is False,
            })
        else:
            metadata_path = folder / 'agent' / 'benchmark-agent-runtime.json'
            metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
            row['preflight_metadata'] = metadata
            checks.update({
                'pinned_runtime_selected': info['python'] == '3.12.11',
                'typing_notrequired_imported': info['typing_not_required_imported'] is True,
                'model_module_import_succeeded': info['model_module_imported'] is True,
                'setup_preflight_recorded': metadata.get('preflight_passed') is True,
                'setup_preflight_version_confirmed': metadata.get('python') == '3.12.11',
            })
        row['checks'] = checks
        row['passed'] = all(checks.values())
    except Exception as error:
        row.update(passed=False, harness_error_type=type(error).__name__, harness_error=str(error))
    finally:
        code, _, stderr = await command('docker', 'rm', '--force', container)
        row['container_removed'] = code == 0
        if code:
            row.update(passed=False, cleanup_error=stderr)
        row['finished_at'] = now()
        (folder / 'summary.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


async def main_async(args) -> int:
    repository = Path(__file__).resolve().parents[2]
    if args.output.exists():
        raise SystemExit('--output must be a fresh directory')
    args.output.mkdir(parents=True)
    candidate_before = hashes(repository / TASK)
    source_names = ['scripts/benchmark_agent_runtime.py',
                    'scripts/tests/test_benchmark_agent_runtime.py']
    source_hashes = {}
    for name in source_names:
        data = (repository / name).read_bytes()
        source_hashes[name] = hashlib.sha256(data).hexdigest()
        target = args.output / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    inspect_code, inspect_out, inspect_err = await command(
        'docker', 'image', 'inspect', args.image,
        '--format', '{{.Id}}')
    if inspect_code:
        raise RuntimeError('Exact cached image is required: ' + inspect_err)
    image_id = inspect_out.strip()
    rows = []
    row = await run_case(args.output, 'original', image_id)
    rows.append(row)
    print(json.dumps({'mode': 'original', 'passed': row['passed'],
                      'runtime': row.get('runtime', {})}), flush=True)
    from benchmark_agent_runtime import install
    install()
    row = await run_case(args.output, 'fixed', image_id)
    rows.append(row)
    print(json.dumps({'mode': 'fixed', 'passed': row['passed'],
                      'runtime': row.get('runtime', {})}), flush=True)
    unchanged = all(hashlib.sha256((repository / name).read_bytes()).hexdigest() == value
                    for name, value in source_hashes.items())
    task_unchanged = candidate_before == hashes(repository / TASK)
    summary = {'created_at': now(), 'model_calls': 0,
               'task': TASK, 'image_id': image_id,
               'cases': rows, 'candidate_files_unchanged': task_unchanged,
               'source_files_unchanged': unchanged, 'source_sha256': source_hashes,
               'candidate_sha256': candidate_before,
               'passed': all(row['passed'] for row in rows) and unchanged and task_unchanged}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return 0 if summary['passed'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', action='store_true')
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.docker:
        parser.error('--docker is required; no model calls are made')
    return asyncio.run(main_async(args))


if __name__ == '__main__':
    raise SystemExit(main())

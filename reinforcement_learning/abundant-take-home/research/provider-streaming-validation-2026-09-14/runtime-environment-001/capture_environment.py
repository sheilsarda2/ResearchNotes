"""Capture only installed code from a known stopped container; never launch it."""
from __future__ import annotations
import email
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
CONTAINER = '54fc9cccca16155cc88f4f4af6f8cbebbfaacee8c2122c0fc5cf9ee42b9dd806'
IMAGE = 'sha256:a76d6fd31b9397504d4355faa5c014dcfca43249d6a2ebeb65be824a40de3261'
PACKAGES = '/root/.local/share/uv/tools/mini-swe-agent/lib/python3.12/site-packages'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')

def inspect_stopped():
    template = '{"id":{{json .Id}},"image":{{json .Image}},"status":{{json .State.Status}},"pid":{{.State.Pid}},"finished_at":{{json .State.FinishedAt}}}'
    value = json.loads(subprocess.check_output(['docker', 'inspect', '--format', template, CONTAINER], text=True))
    assert value['id'] == CONTAINER and value['status'] == 'exited' and value['pid'] == 0, value
    return value

def read_file(path):
    data = subprocess.check_output(['docker', 'cp', CONTAINER + ':' + path, '-'])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        files = [m for m in archive if m.isfile()]
        assert len(files) == 1
        return archive.extractfile(files[0]).read()

def main():
    fixture = BASE / 'fixture'
    fixture.mkdir(exist_ok=False)
    before = inspect_stopped()
    # Only selected non-secret image metadata is retained, never Config.Env.
    template = '{"id":{{json .Id}},"architecture":{{json .Architecture}},"os":{{json .Os}},"created":{{json .Created}},"layers":{{json .RootFS.Layers}}}'
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', '--format', template, IMAGE], text=True))
    assert image['id'] == IMAGE and (image['os'], image['architecture']) == ('linux', 'arm64')
    expected_path = BASE.parent / 'source-identity.json'
    execution_path = BASE.parent / 'execution-identity.json'
    expected = json.loads(expected_path.read_text())
    execution = json.loads(execution_path.read_text())
    files = {}; metadata = {}; wheels = {}; byte_count = 0; skipped = 0
    proc = subprocess.Popen(['docker', 'cp', CONTAINER + ':' + PACKAGES, '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode='r|*') as source, tarfile.open(fixture / 'site-packages.tar', 'w') as target:
            for member in source:
                parts = PurePosixPath(member.name).parts
                assert parts and parts[0] == 'site-packages' and '..' not in parts and not member.name.startswith('/')
                assert member.isdir() or member.isfile(), 'Nonregular package entry: ' + member.name
                if member.isdir():
                    continue
                if '__pycache__' in parts or member.name.endswith('.pyc'):
                    skipped += 1
                    continue
                rel = '/'.join(parts[1:])
                data = source.extractfile(member).read()
                assert rel not in files, 'Duplicate package path'
                files[rel] = {'sha256': sha(data), 'bytes': len(data)}
                byte_count += len(data)
                normalized = tarfile.TarInfo('site-packages/' + rel)
                normalized.size = len(data); normalized.mode = member.mode & 0o777
                target.addfile(normalized, io.BytesIO(data))
                if rel.endswith('.dist-info/METADATA'):
                    msg = email.message_from_bytes(data)
                    key = msg['Name'].lower().replace('_', '-')
                    assert key not in metadata
                    metadata[key] = msg['Version']
                if rel.endswith('.dist-info/WHEEL'):
                    wheels[rel] = [line[5:] for line in data.decode().splitlines() if line.startswith('Tag: ')]
        assert proc.wait(timeout=10) == 0, proc.stderr.read().decode()
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()
    assert {k: files[k]['sha256'] for k in expected['file_sha256']} == expected['file_sha256']
    litellm = {k[len('litellm/'):]: v['sha256'] for k, v in files.items() if k.startswith('litellm/') and k.endswith(('.py', '.json'))}
    assert litellm == execution['litellm_file_sha256']
    assert metadata == {k.lower().replace('_', '-'): v for k, v in expected['packages'].items()}
    entry = read_file('/root/.local/share/uv/tools/mini-swe-agent/bin/mini-swe-agent')
    config = read_file('/root/.local/share/uv/tools/mini-swe-agent/pyvenv.cfg')
    (fixture / 'mini-swe-agent').write_bytes(entry)
    (fixture / 'source-pyvenv.cfg').write_bytes(config)
    assert inspect_stopped() == before
    write(fixture / 'file-manifest.json', files)
    tooling = BASE / 'tooling'; tooling.mkdir(exist_ok=False)
    source_hashes = {}
    for name in ['benchmark_shared_admission.py', 'benchmark_interleaving.py', 'benchmark_recovery.py']:
        path = REPO / 'scripts' / name
        data = path.read_bytes(); (tooling / name).write_bytes(data)
        source_hashes[str(path.relative_to(REPO))] = sha(data)
    runtime_hashes = {str((REPO / 'scripts' / name).relative_to(REPO)): sha((REPO / 'scripts' / name).read_bytes())
                      for name in ['benchmark_mini_tool_bootstrap.py', 'benchmark_mini_tool_cleanup.py', 'benchmark_process_guard.py', 'benchmark_deadline.py', 'benchmark_agent_runtime.py']}
    tar_hash = hashlib.file_digest((fixture / 'site-packages.tar').open('rb'), 'sha256').hexdigest()
    proof = {'schema_version': 1, 'kind': 'stopped_agent_package_fixture', 'passed': True,
             'captured_at': datetime.now(timezone.utc).isoformat(), 'source_container': before,
             'cached_python31211_image': image, 'source_interpreter': '3.12.3',
             'target_interpreter': '3.12.11', 'target_bootstrap_execution_pending': True,
             'source_identity': {'path': str(expected_path.relative_to(REPO)), 'sha256': sha(expected_path.read_bytes())},
             'execution_identity': {'path': str(execution_path.relative_to(REPO)), 'sha256': sha(execution_path.read_bytes())},
             'mini_platformdirs_source_files_equal': len(expected['file_sha256']), 'litellm_source_files_equal': len(litellm),
             'all_dependency_versions_equal': True, 'packages': metadata, 'wheel_tags': wheels,
             'package_file_count': len(files), 'package_file_bytes': byte_count, 'excluded_bytecode_files': skipped,
             'tar_sha256': tar_hash, 'file_manifest_sha256': sha((fixture / 'file-manifest.json').read_bytes()),
             'console_entry_sha256': sha(entry), 'source_pyvenv_sha256': sha(config),
             'copied_admission_source_sha256': source_hashes, 'runtime_source_sha256': runtime_hashes,
             'active_trial_access': False, 'container_starts': 0, 'package_installs': 0, 'model_calls': 0}
    write(BASE / 'environment-proof.json', proof)
    print(json.dumps({k: proof[k] for k in ['passed', 'package_file_count', 'package_file_bytes', 'tar_sha256', 'all_dependency_versions_equal']}))

if __name__ == '__main__':
    main()

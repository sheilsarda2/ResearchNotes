"""Retain the exact normal oracle verifier image for later paired regrades.

Only adds a private image tag. It does not alter a running container or its files.
Filtered inspection deliberately excludes environment variables.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def maybe_pin(job, output, checksum):
    provisional = output / 'verifier-image-provisional.json'
    if provisional.exists():
        return
    trials = [path.parent for path in job.glob('*/config.json')]
    if not trials:
        return
    assert len(trials) == 1, 'Expected one oracle trial'
    trial = trials[0]
    config = json.loads((trial / 'config.json').read_text())
    assert config['agent']['name'] == 'oracle'
    project = trial.name.lower() + '__verifier__trial'
    query = subprocess.run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project],
                           text=True, capture_output=True, timeout=10, check=True)
    ids = query.stdout.split()
    if not ids:
        return
    assert len(ids) == 1, 'Expected one verifier container'
    inspection = subprocess.run(['docker', 'inspect', ids[0]], text=True, capture_output=True, timeout=10)
    if inspection.returncode:
        return  # Container ended between discovery and inspection; keep observing.
    raw = json.loads(inspection.stdout)[0]
    assert raw['Config']['Labels']['com.docker.compose.project'] == project
    assert raw['State']['Running']
    host = {name: raw['HostConfig'].get(name) for name in
            ('NanoCpus', 'Memory', 'MemorySwap', 'NetworkMode', 'StorageOpt')}
    assert host['NanoCpus'] == 4000000000 and host['Memory'] == 8192 * 1024 * 1024
    assert host['NetworkMode'] == 'none'
    filtered = {'Id': raw['Id'], 'Image': raw['Image'], 'Name': raw['Name'],
                'Config.Labels': raw['Config']['Labels'], 'HostConfig': host,
                'observed_at': datetime.now(timezone.utc).isoformat()}
    raw_path = output / 'oracle-verifier-docker-inspection.json'
    dump(raw_path, filtered)
    tag = 'zenoh-coverage-followup-final-verifier:' + checksum[:12]
    prior = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                           text=True, capture_output=True, timeout=10)
    if prior.returncode == 0:
        assert prior.stdout.strip() == raw['Image'], 'Private tag already identifies a different image'
    else:
        subprocess.run(['docker', 'image', 'tag', raw['Image'], tag], check=True, timeout=10)
    dump(provisional, {'kind': 'normal_harbor_oracle_verifier_image_pending_result',
                      'verifier_image_id': raw['Image'], 'private_tag': tag,
                      'oracle_trial_path': str(trial), 'final_task_checksum': checksum,
                      'observed_host_config': host,
                      'raw_docker_inspection_path': str(raw_path),
                      'raw_docker_inspection_sha256': digest(raw_path),
                      'helper_sha256': digest(__file__), 'model_calls': 0})


def finalize(output, result_path, root):
    provisional = json.loads((output / 'verifier-image-provisional.json').read_text())
    result = json.loads(result_path.read_text())
    assert str(result_path.parent) == provisional['oracle_trial_path']
    assert result['task_checksum'] == provisional['final_task_checksum']
    assert result['agent_info']['name'] == 'oracle' and result['exception_info'] is None
    assert result['verifier_result']['rewards']['reward'] == 1
    raw_path = Path(provisional['raw_docker_inspection_path'])
    assert digest(raw_path) == provisional['raw_docker_inspection_sha256']
    actual = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                                      provisional['private_tag']], text=True, timeout=10).strip()
    assert actual == provisional['verifier_image_id']
    proof = dict(provisional, kind='normal_harbor_oracle_verifier_image',
                 oracle_result_path=str(result_path.relative_to(root)),
                 oracle_result_sha256=digest(result_path),
                 raw_docker_inspection_path=str(raw_path.relative_to(root)),
                 oracle_trial_path=str(result_path.parent.relative_to(root)),
                 passed=True)
    path = output / 'verifier-image-proof.json'
    dump(path, proof)
    return {'path': str(path.relative_to(root)), 'sha256': digest(path),
            'verifier_image_id': actual, 'private_tag': provisional['private_tag']}

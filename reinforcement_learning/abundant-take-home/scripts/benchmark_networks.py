"""Use small, isolated trial subnets without restarting Docker or changing tasks."""
import fcntl
import ipaddress
import json
from pathlib import Path
import subprocess


def reservation_state(control_path):
    control_path = Path(control_path)
    control = json.loads(control_path.read_text())
    owner = Path(control['shared_pool']) if control.get('shared_pool') else control_path
    return owner.with_suffix('.subnets.json')


def choose_subnet(pool, used):
    occupied = [ipaddress.ip_network(value) for value in used]
    for subnet in ipaddress.ip_network(pool).subnets(new_prefix=24):
        if not any(subnet.overlaps(other) for other in occupied if other.version == subnet.version):
            return str(subnet)
    raise RuntimeError('Benchmark network address pool exhausted')


def allocate(control_path, session_id):
    control_path = Path(control_path)
    pool = json.loads(control_path.read_text())['network_pool_cidr']
    state = reservation_state(control_path)
    with state.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(state.read_text()) if state.exists() else {}
        if session_id in saved:
            return saved[session_id]
        ids = subprocess.check_output(['docker', 'network', 'ls', '-q'], text=True, timeout=15).split()
        networks = json.loads(subprocess.check_output(['docker', 'network', 'inspect', *ids], text=True, timeout=15)) if ids else []
        occupied = [item['Subnet'] for network in networks for item in network['IPAM'].get('Config') or [] if item.get('Subnet')]
        subnet = choose_subnet(pool, [*saved.values(), *occupied])
        saved[session_id] = subnet
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved, indent=2) + '\n')
        temporary.replace(state)
        return subnet


def release(control_path, session_id):
    """Recycle completed trial reservations; Docker still protects live subnets."""
    state = reservation_state(control_path)
    if not state.exists():
        return
    with state.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(state.read_text())
        if session_id not in saved:
            return
        del saved[session_id]
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved, indent=2) + '\n')
        temporary.replace(state)


def install(control_path):
    from harbor.environments.docker.docker import DockerEnvironment
    control_path = Path(control_path)
    if not json.loads(control_path.read_text()).get('network_pool_cidr'):
        return
    if getattr(DockerEnvironment, '_benchmark_subnets_installed', False):
        return
    original = DockerEnvironment._write_resources_compose_file

    def write_with_subnet(environment):
        path = original(environment)
        # Preserve offline verification and tasks with their own Compose topology.
        if path is not None and not environment._network_disabled and not environment._uses_compose:
            data = json.loads(path.read_text())
            assert 'networks' not in data, 'Unexpected existing resource network configuration'
            data['networks'] = {'default': {'ipam': {'config': [
                {'subnet': allocate(control_path, environment.session_id)}]}}}
            path.write_text(json.dumps(data, indent=2) + '\n')
        return path

    DockerEnvironment._write_resources_compose_file = write_with_subnet
    DockerEnvironment._benchmark_subnets_installed = True

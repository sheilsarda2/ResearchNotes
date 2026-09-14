"""Resource checks and evidence-preserving recovery for our benchmark harness."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess


def memory_snapshot():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        values[key] = int(value.split()[0]) // 1024
    total = values['MemTotal']
    available = values['MemAvailable']
    # A devcontainer can impose an additional limit inside the Docker VM.
    cgroup = Path('/sys/fs/cgroup')
    try:
        limit = (cgroup / 'memory.max').read_text().strip()
        if limit != 'max':
            limit_mb = int(limit) // 1024**2
            current = int((cgroup / 'memory.current').read_text()) // 1024**2
            stats = dict(line.split() for line in (cgroup / 'memory.stat').read_text().splitlines())
            reclaimable = int(stats.get('inactive_file', 0)) // 1024**2
            total = min(total, limit_mb)
            available = min(available, max(0, limit_mb - current + reclaimable))
    except (OSError, ValueError):
        pass
    pressure = 0.0
    try:
        for line in Path('/proc/pressure/memory').read_text().splitlines():
            if line.startswith('full '):
                pressure = float(line.split()[1].split('=')[1])
    except OSError:
        pass
    return {'total_mb': total, 'available_mb': available,
            'swap_used_mb': values['SwapTotal'] - values['SwapFree'],
            'memory_pressure_pct': pressure}


def memory_block_reason(control, snapshot):
    if snapshot['total_mb'] < control.get('min_total_mb', 0):
        return 'Docker memory allocation'
    if snapshot['available_mb'] < control.get('reserve_mb', 1536) + control.get('startup_reserve_mb', 256):
        return 'memory reserve'
    if snapshot['memory_pressure_pct'] > control.get('max_memory_pressure_pct', 1.0):
        return 'memory pressure'
    return None


def job_containers(directory):
    """Inspect only state and mount metadata; container environments hold keys."""
    ids = subprocess.check_output(['docker', 'ps', '-aq'], text=True, timeout=20).split()
    if not ids:
        return []
    template = '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State}},"mounts":{{json .Mounts}}}'
    output = subprocess.check_output(['docker', 'inspect', '--format', template, *ids], text=True, timeout=30)
    owned = []
    for line in output.splitlines():
        container = json.loads(line)
        if any(Path(mount.get('Source', '/')).is_relative_to(directory.resolve())
               for mount in container['mounts']):
            owned.append(container)
    return owned


def prepare_resume(directory):
    """Call only with the job runner stopped, before Harbor removes partial dirs."""
    partials = [path for path in directory.iterdir()
                if path.is_dir() and not (path / 'result.json').exists()]
    containers = job_containers(directory)
    if not partials and not containers:
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    archive = directory.parent / 'recovery' / directory.name / stamp
    archive.mkdir(parents=True)
    (archive / 'containers.json').write_text(json.dumps(containers, indent=2) + '\n')
    for container in containers:
        if container['state']['Running']:
            subprocess.run(['docker', 'stop', '--time', '10', container['id']],
                           check=True, capture_output=True, timeout=30)
    for path in partials:
        shutil.copytree(path, archive / path.name)
    # Retain any workbook that had not yet been copied out by Harbor. Keep the
    # stopped containers too; never prune evidence or touch another job's work.
    workbooks = []
    for container in containers:
        destination = archive / 'container-workbooks' / container['id'][:12]
        destination.mkdir(parents=True)
        result = subprocess.run(['docker', 'cp', container['id'] + ':/root/audit_report.xlsx',
                                 str(destination / 'audit_report.xlsx')],
                                capture_output=True, timeout=30)
        if result.returncode == 0:
            workbooks.append(container['id'])
    (archive / 'manifest.json').write_text(json.dumps({
        'created_at': stamp, 'job': directory.name,
        'partial_trial_directories': [path.name for path in partials],
        'containers': len(containers), 'recovered_workbooks': workbooks,
        'note': 'Interrupted work retained separately; it is not a scored trial.',
    }, indent=2) + '\n')
    return archive

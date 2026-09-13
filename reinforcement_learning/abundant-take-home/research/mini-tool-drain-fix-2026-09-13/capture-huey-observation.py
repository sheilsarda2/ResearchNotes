"""Read-only, argv-free observation of the identified live Mini pipe hang."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path.cwd()
TRIAL = 'jobs/candidates-all14-efforts-20-20260913T183301Z/huey-sqlite-leases__LAUZTSQ'
CONTAINER = '56682fe89d90'
OUT = ROOT / 'research/mini-tool-drain-fix-2026-09-13'
PHASE = '/tmp/benchmark-guard-f8ef43be86a940c79081289d4f1e9f3d'
CODE = r'''
import hashlib, inspect, json, os
from pathlib import Path
from minisweagent.environments import local
expected = {1776: '2199885', 1783: '2199888', 2096: '2272250',
            2101: '2273353', 2102: '2273356', 2103: '2273356'}
processes = []
for pid, identity in expected.items():
    path = Path('/proc') / str(pid)
    fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
    assert fields[19] == identity and fields[0] != 'Z'
    fds = {}
    for fd in (path / 'fd').iterdir():
        try:
            target = os.readlink(fd)
            if target.startswith('pipe:'):
                fds[fd.name] = target
        except FileNotFoundError:
            pass
    processes.append(dict(pid=pid, start_ticks=identity, state=fields[0],
                          parent_pid=int(fields[1]), cpu_ticks=[fields[11], fields[12]],
                          wait_channel=(path / 'wchan').read_text(), pipes=fds))
assert processes[1]['pipes']['4'] == 'pipe:[3938292]'
assert all(p['pipes']['1'] == p['pipes']['2'] == 'pipe:[3938292]' for p in processes[2:])
print(json.dumps(dict(processes=processes,
    mini_local_sha256=hashlib.sha256(Path(local.__file__).read_bytes()).hexdigest(),
    mini_run_sha256=hashlib.sha256(inspect.getsource(local._run).encode()).hexdigest(),
    tool_timeout_sec=local.LocalEnvironmentConfig().timeout)))
'''


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


mounts = json.loads(subprocess.check_output(
    ['docker', 'inspect', '--format', '{{json .Mounts}}', CONTAINER], text=True))
assert any(m['Source'] == str(ROOT / TRIAL / 'agent') for m in mounts)
snapshots = []
started = time.monotonic()
for index in range(6):
    if index:
        time.sleep(30)
    data = json.loads(subprocess.check_output(
        ['docker', 'exec', CONTAINER, '/root/.local/share/uv/tools/mini-swe-agent/bin/python',
         '-c', CODE], text=True).splitlines()[-1])
    data['observed_at'] = datetime.now(timezone.utc).isoformat()
    trace = json.loads((ROOT / TRIAL / 'agent/mini-swe-agent.trajectory.json').read_text())
    data['recorded_turns'] = sum(m['role'] == 'assistant' for m in trace['messages'])
    assert data['recorded_turns'] == 39
    snapshots.append(data)
    (OUT / 'live-observations.json').write_text(json.dumps(snapshots, indent=2) + '\n')
    print(json.dumps({'sample': index, 'observed_at': data['observed_at'],
                      'recorded_turns': data['recorded_turns']}), flush=True)
assert all(s['processes'][1]['cpu_ticks'] == snapshots[0]['processes'][1]['cpu_ticks']
           for s in snapshots)
files = {name: digest(ROOT / TRIAL / name) for name in [
    'config.json', 'benchmark-runtime.json', 'agent/benchmark-agent-runtime.json']}
observation = dict(schema_version=1, kind='mini_tool_pipe_drain_hang', trial=TRIAL,
    observed_at=snapshots[-1]['observed_at'],
    agent={'pid': 1783, 'host_pid': 16981, 'start_ticks': '2199888'},
    guard={'pid': 1776, 'host_pid': 16974, 'start_ticks': '2199885', 'phase': PHASE},
    pid_namespace='trial_container', container=CONTAINER,
    mini_local_sha256=data['mini_local_sha256'], mini_run_sha256=data['mini_run_sha256'],
    tool_timeout_sec=data['tool_timeout_sec'], stalled_for_sec=time.monotonic()-started,
    pending_step=40, recorded_turns=39,
    pipe={'inode': 3938292, 'agent_read_fd': 4, 'holder_pids': [2096, 2101, 2102, 2103]},
    files=files, observations={'path': str((OUT / 'live-observations.json').relative_to(ROOT)),
                             'sha256': digest(OUT / 'live-observations.json')})
path = OUT / 'incident-observation.json'
with path.open('x') as output:
    output.write(json.dumps(observation, indent=2) + '\n')
print(json.dumps({'observation': str(path.relative_to(ROOT)), 'sha256': digest(path)}))

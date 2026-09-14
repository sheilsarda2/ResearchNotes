"""Capture filtered resource evidence from only this canary's Docker events."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import time

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / 'run-001/container-lifecycle-v2'
PROJECT = 'luigi-generation-target__rigdbsv'
SAFE_ACTIONS = ('create', 'start', 'die', 'destroy', 'oom')
UNTIL = 1789383906.6793282


def now():
    return datetime.now(timezone.utc).isoformat()


def owner_live():
    try:
        fields = Path('/proc/76965/stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == '5940603'
    except (OSError, IndexError):
        return False


def main():
    OUTPUT.mkdir()
    identity = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]
    command = ['docker', 'events', '--filter', 'type=container', '--filter',
               'label=com.docker.compose.project='+PROJECT, '--format', '{{json .}}']
    for action in SAFE_ACTIONS:
        command.extend(['--filter', 'event='+action])
    with (OUTPUT / 'identity.json').open('x') as stream:
        json.dump(dict(pid=os.getpid(), identity=identity, at=now(), read_only=True,
                       source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       command=command, model_calls=0, workload_signals=0), stream, indent=2)
    with (OUTPUT / 'event-client-stderr.log').open('x') as err:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=err,
                                 stdin=subprocess.DEVNULL)
        buffer = b''
        count = 0
        errors = []
        try:
            while owner_live() and time.time() < UNTIL:
                if child.poll() is not None:
                    errors.append(dict(stage='event_client_exit', returncode=child.returncode))
                    break
                if not select.select([child.stdout], [], [], 1)[0]:
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    continue
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    event = json.loads(line)
                    actor = event.get('Actor', {})
                    if actor.get('Attributes', {}).get('com.docker.compose.project') != PROJECT:
                        raise RuntimeError('Unexpected Docker project event')
                    container = actor['ID']
                    action = event.get('Action')
                    # Exec event action strings can contain a complete command.
                    # Never record those, even if the daemon ignores its filters.
                    if action not in SAFE_ACTIONS:
                        raise RuntimeError('Unexpected non-lifecycle Docker event')
                    record = dict(at=now(), id=container, action=action, time_nano=event.get('timeNano'))
                    if action in ('create', 'start'):
                        try:
                            result = subprocess.run(['docker', 'inspect', container], check=True,
                                                    capture_output=True, timeout=5)
                            raw = json.loads(result.stdout)[0]
                            if raw['Config']['Labels'].get('com.docker.compose.project') != PROJECT:
                                raise RuntimeError('Unexpected inspected project')
                            record['inspection'] = dict(
                                id=raw['Id'], name=raw['Name'], image=raw['Image'],
                                labels={key: raw['Config']['Labels'].get(key) for key in
                                        ('com.docker.compose.project', 'com.docker.compose.service')},
                                host_config={key: raw['HostConfig'].get(key) for key in
                                             ('NanoCpus', 'Memory', 'MemorySwap', 'StorageOpt', 'NetworkMode')},
                                state={key: raw['State'].get(key) for key in
                                       ('Status', 'Running', 'Pid', 'StartedAt', 'FinishedAt', 'ExitCode', 'OOMKilled')})
                        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                            record['inspection_error_type'] = type(error).__name__
                    with (OUTPUT / 'events.jsonl').open('a') as stream:
                        stream.write(json.dumps(record, sort_keys=True)+'\n')
                    count += 1
        finally:
            # Only our read-only Docker event client is stopped; no trial signal.
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
            with (OUTPUT / 'result.json').open('x') as stream:
                json.dump(dict(at=now(), events=count, errors=errors,
                               owner_live=owner_live(), observer_bound_reached=time.time() >= UNTIL,
                               event_client_reaped=child.poll() is not None, workload_signals=0), stream, indent=2)


if __name__ == '__main__':
    main()

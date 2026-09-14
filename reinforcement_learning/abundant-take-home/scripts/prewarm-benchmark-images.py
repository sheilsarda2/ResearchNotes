#!/usr/bin/env python3
"""Build unchanged task Dockerfiles serially before admitting their model trials."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import tomllib

from benchmark_recovery import memory_snapshot
from benchmark_shared_admission import SharedAdmission


def dump(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n'); temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]; os.chdir(root)
    args.output.mkdir(parents=True, exist_ok=True)
    control = json.loads(args.control.read_text())
    pool = SharedAdmission(control['shared_pool'], args.control)
    readiness_path = Path(control['image_readiness'])
    state = json.loads(readiness_path.read_text()) if readiness_path.exists() else {'ready_tasks': [], 'images': [], 'failures': []}
    # Warm a small Python task first so model work can begin while the compiled
    # environments build. This changes scheduling, never task contents.
    tasks = json.loads(args.manifest.read_text())['tasks']
    tasks.sort(key=lambda t: (t['id'] != 'py-rosbags-rosbag2-storage-writers',))
    for task in tasks:
        name = task['id']
        if name in state['ready_tasks']:
            continue
        claim = f'prewarm-{name}'
        while pool.try_acquire(claim, memory_snapshot(), {'startup_reserve_mb': 8192}):
            time.sleep(5)
        try:
            source = root/task['path']; cfg = tomllib.loads((source/'task.toml').read_text())
            for phase, folder in (('agent', 'environment'), ('verifier', 'tests')):
                tag = f'candidate-v2-prewarm-{name}:{phase}'
                logfile = args.output/f'{name}-{phase}.log'
                print(f'Building {name} {phase}', flush=True)
                timeout = cfg['environment']['build_timeout_sec']
                try:
                    with logfile.open('ab') as out:
                        result = subprocess.run(['docker', 'build', '--progress=plain', '-t', tag, str(source/folder)],
                                                stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
                    assert result.returncode == 0, f'Image build exit {result.returncode}'
                except (AssertionError, subprocess.TimeoutExpired) as error:
                    state['failures'].append({'task': name, 'phase': phase, 'error': str(error), 'log': str(logfile)})
                    dump(readiness_path, state)
                    c = json.loads(args.control.read_text()); c.update(paused=True, pause_reason=f'Image build failed: {name} {phase}')
                    dump(args.control, c)
                    raise
                image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
                state['images'].append({'task': name, 'phase': phase, 'tag': tag, 'id': image_id})
            state['ready_tasks'].append(name)
            state['updated_at'] = datetime.now(timezone.utc).isoformat()
            dump(readiness_path, state)
            print(f'Ready for full trials: {name}', flush=True)
        finally:
            pool.release(claim)
    print('All selected task images ready.', flush=True)


if __name__ == '__main__':
    main()

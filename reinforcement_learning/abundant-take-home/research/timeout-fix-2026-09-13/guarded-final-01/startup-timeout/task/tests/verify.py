import hashlib, json, pathlib, time
root = pathlib.Path('/workspace/deadline-evidence')
output = pathlib.Path('/logs/verifier')
output.mkdir(parents=True, exist_ok=True)
def snapshot():
    found = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.rglob('*')) if p.is_file()}
    found.update({'agent/' + p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(pathlib.Path('/logs/agent').glob('*-trajectory.json'))})
    return found
started = time.time_ns()
before = snapshot()
time.sleep(2.0)
after = snapshot()
late = sorted(root.glob('*.late'))
ready = sorted(p.name for p in root.glob('*.ready'))
expected_ready = ['child.ready', 'daemon.ready', 'new-session.ready', 'parent.ready']
checks = {'startup_timeout_spawned_no_writer': ready == [],
          'no_delayed_writes': not late,
          'artifact_stable_during_verifier': before == after}
output.joinpath('deadline-checks.json').write_text(json.dumps({
    'verifier_started_unix_ns': started, 'before': before, 'after': after,
    'late_files': [p.name for p in late], 'ready_files': ready,
    'checks': checks}, indent=2) + '\n')
output.joinpath('reward.txt').write_text(str(int(all(checks.values()))))

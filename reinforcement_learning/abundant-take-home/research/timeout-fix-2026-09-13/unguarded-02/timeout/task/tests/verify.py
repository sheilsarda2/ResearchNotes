import hashlib, json, pathlib, time
root = pathlib.Path('/workspace/deadline-evidence')
output = pathlib.Path('/logs/verifier')
output.mkdir(parents=True, exist_ok=True)
def snapshot():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}
started = time.time_ns()
before = snapshot()
time.sleep(2.0)
after = snapshot()
late = sorted(root.glob('*.late'))
ready = sorted(p.name for p in root.glob('*.ready'))
expected_ready = ['child.ready', 'daemon.ready', 'new-session.ready', 'parent.ready']
checks = {'all_writer_types_started': ready == expected_ready,
          'no_delayed_writes': not late,
          'artifact_stable_during_verifier': before == after}
output.joinpath('deadline-checks.json').write_text(json.dumps({
    'verifier_started_unix_ns': started, 'before': before, 'after': after,
    'late_files': [p.name for p in late], 'ready_files': ready,
    'checks': checks}, indent=2) + '\n')
output.joinpath('reward.txt').write_text(str(int(all(checks.values()))))

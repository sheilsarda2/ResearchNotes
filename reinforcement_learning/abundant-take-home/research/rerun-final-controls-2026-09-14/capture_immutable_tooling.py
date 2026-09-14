"""Capture byte-identical control tooling without changing the live scheduler."""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
CAPTURE = BASE / 'immutable-tooling-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not CAPTURE.exists(), 'Never overwrite a tooling capture'
    paths = sorted((ROOT / 'scripts').glob('benchmark_*.py'))
    paths += [ROOT / 'scripts/harbor-resource-runner.py', ROOT / 'scripts/candidate-bench.py']
    initial = {str(path.relative_to(ROOT)): digest(path) for path in paths}
    target = CAPTURE / 'files'
    for path in paths:
        destination = target / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        assert digest(destination) == initial[str(path.relative_to(ROOT))]
        destination.chmod(0o444)
    assert initial == {str(path.relative_to(ROOT)): digest(path) for path in paths}

    # The runner does not need a repository mirror. Only candidate-bench's
    # read-only verification uses ROOT; preserve its ordinary path semantics.
    link = target / 'research'
    link.symlink_to(os.path.relpath(ROOT / 'research', target), target_is_directory=True)
    assert link.resolve() == ROOT / 'research'

    dependencies = {}
    for path in paths:
        name = str(path.relative_to(ROOT))
        imports = set()
        for node in ast.walk(ast.parse((target / name).read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split('.')[0])
            elif isinstance(node, ast.Import):
                imports.update(alias.name.split('.')[0] for alias in node.names)
        own = sorted(module for module in imports if module.startswith('benchmark_'))
        assert all('scripts/' + module + '.py' in initial for module in own)
        dependencies[name] = own
    closure, pending = set(), ['scripts/harbor-resource-runner.py']
    while pending:
        name = pending.pop()
        if name in closure:
            continue
        closure.add(name)
        pending.extend('scripts/' + module + '.py' for module in dependencies[name])
    assert 'scripts/benchmark_incidents.py' not in closure
    assert 'scripts/benchmark_trial_intervention.py' not in closure
    # These helpers are read as payload bytes rather than imported on the host.
    payloads = ['scripts/benchmark_process_guard.py',
                'scripts/benchmark_mini_tool_bootstrap.py',
                'scripts/benchmark_mini_tool_cleanup.py']
    assert all(name in initial for name in payloads)
    manifest = {
        'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
        'source_root': str(ROOT), 'files_root': str(target.relative_to(ROOT)),
        'source_sha256': initial, 'copied_bytes_match_sources_at_capture': True,
        'read_only_file_mode': '0o444', 'static_local_dependencies': dependencies,
        'runner_transitive_imports': sorted(closure), 'file_payloads': payloads,
        'research_link': {'path': 'research', 'target': os.readlink(link),
                          'resolved_target': str(ROOT / 'research'),
                          'scope': 'captured candidate-bench read-only validation'},
        'repository_root_overrides': False,
        'runtime_uses_captured_sibling_files': True, 'model_calls': 0,
    }
    (CAPTURE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (CAPTURE / 'manifest.json').chmod(0o444)
    print(json.dumps({'capture': str(CAPTURE.relative_to(ROOT)),
                      'manifest_sha256': digest(CAPTURE / 'manifest.json'),
                      'source_files': len(initial), 'runner_import_files': len(closure)}, indent=2))


if __name__ == '__main__':
    main()

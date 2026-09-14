"""Execute an unchanged captured Harbor runner and audit its local imports."""
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import sys

sys.dont_write_bytecode = True

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
CAPTURE = BASE / 'immutable-tooling-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    args = sys.argv[1:]
    assert len(args) > 3 and args[0] == '--proof' and args[2] == '--'
    proof_path = Path(args[1]).resolve()
    assert proof_path.is_relative_to(BASE) and not proof_path.exists()
    manifest = json.loads((CAPTURE / 'manifest.json').read_text())
    tree = (CAPTURE / 'files').resolve()
    scripts = tree / 'scripts'
    assert (tree / 'research').resolve() == ROOT / 'research'
    assert not any(name.startswith('benchmark_') for name in sys.modules)
    for name, expected in manifest['source_sha256'].items():
        assert digest(tree / name) == expected
    # The wrapper starts in this research folder, never in the live scripts
    # directory. All transitive local imports therefore resolve to this tree.
    assert not any(Path(path or '.').resolve() == ROOT / 'scripts' for path in sys.path)
    sys.path.insert(0, str(scripts))
    runner = scripts / 'harbor-resource-runner.py'
    sys.argv = [str(runner), *args[3:]]
    proof = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
             'cwd': str(Path.cwd()), 'runner': str(runner.relative_to(ROOT)),
             'runner_sha256': digest(runner),
             'capture_manifest_sha256': digest(CAPTURE / 'manifest.json'),
             'repository_root_overrides': False, 'model_calls': 0,
             'inspection_only': args[3:] == ['--inspect-only']}
    try:
        runpy.run_path(str(runner), run_name=(
            'captured_runner_inspection' if proof['inspection_only'] else '__main__'))
    finally:
        imports = {}
        for name, module in sorted(sys.modules.items()):
            if not name.startswith('benchmark_'):
                continue
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(scripts), f'Uncaptured import: {name}'
            relative = str(path.relative_to(tree))
            actual = digest(path)
            assert manifest['source_sha256'][relative] == actual
            imports[name] = {'path': str(path.relative_to(ROOT)), 'sha256': actual}
        assert all(digest(tree / name) == expected for name, expected in manifest['source_sha256'].items())
        assert (tree / 'research').resolve() == ROOT / 'research'
        proof.update(finished_at=datetime.now(timezone.utc).isoformat(),
                     captured_tree_unchanged=True, all_local_imports_captured=True,
                     loaded_local_modules=imports,
                     installed_harbor={
                         'version': version('harbor'),
                         'job_module': str(Path(sys.modules['harbor.job'].__file__).resolve()),
                         'cli_module': str(Path(sys.modules['harbor.cli.main'].__file__).resolve()),
                     })
        with proof_path.open('x') as output:
            json.dump(proof, output, indent=2)
            output.write('\n')


if __name__ == '__main__':
    main()

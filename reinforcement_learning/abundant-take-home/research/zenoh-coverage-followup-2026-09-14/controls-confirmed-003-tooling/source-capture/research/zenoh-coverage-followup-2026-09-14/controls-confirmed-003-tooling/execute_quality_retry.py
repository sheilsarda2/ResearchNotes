"""Run an authorized model-based Harbor quality review using captured tooling."""
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import shutil
import sys

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
ROOT = BASE.parents[1]
CAPTURE = BASE / 'immutable-tooling-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(folder):
    return {str(p.relative_to(folder)): digest(p) for p in sorted(folder.rglob('*')) if p.is_file()}


def main():
    args = sys.argv[1:]
    assert len(args) > 3 and args[0] == '--proof-dir' and args[2] == '--'
    proof_dir = Path(args[1]).resolve()
    assert proof_dir.is_relative_to(BASE) and not proof_dir.exists()
    proof_dir.mkdir()
    manifest = json.loads((CAPTURE / 'manifest.json').read_text())
    tree = (CAPTURE / 'files').resolve(); scripts = tree / 'scripts'
    assert (tree / 'research').resolve() == ROOT / 'research'
    assert not any(name.startswith('benchmark_') for name in sys.modules)
    for name, expected in manifest['source_sha256'].items():
        assert digest(tree / name) == expected
    assert not any(Path(path or '.').resolve() == ROOT / 'scripts' for path in sys.path)
    sys.path.insert(0, str(scripts))
    from harbor.analyze import checker
    original_assemble = checker.assemble_check_task
    wrapper_records = []

    def assemble_and_capture(*pargs, **kwargs):
        original_task = Path(kwargs['task_dir']).resolve()
        expected = hashes(original_task)
        wrapper = original_assemble(*pargs, **kwargs)
        copied = proof_dir / 'generated-wrapper'
        assert not copied.exists(), 'One quality review per process'
        shutil.copytree(wrapper, copied)
        assert hashes(original_task) == expected
        assert hashes(copied / 'environment/task') == expected
        from harbor.models.task.task import Task
        record = {'original_task': str(original_task.relative_to(ROOT)),
                  'original_task_checksum': Task(original_task).checksum,
                  'original_task_file_sha256': expected,
                  'ephemeral_wrapper_path': str(wrapper),
                  'wrapper_checksum': Task(wrapper).checksum,
                  'captured_wrapper': str(copied.relative_to(ROOT)),
                  'captured_wrapper_file_sha256': hashes(copied),
                  'original_copy_byte_identical': True}
        import recovery_common as retry_gate
        retry_gate.validate_regenerated_wrapper(record)
        wrapper_records.append(record)
        (proof_dir / 'wrapper-inputs.json').write_text(json.dumps(record, indent=2) + '\n')
        return wrapper

    checker.assemble_check_task = assemble_and_capture
    source_files = []
    for folder in [checker.PROMPTS_DIR, checker.CHECK_TASK_TEMPLATE_DIR]:
        source_files.extend(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    import harbor.analyze.models as models
    source_files += [Path(checker.__file__), Path(models.__file__), models.load_rubric.__globals__['DEFAULT_RUBRIC_PATH']]
    (proof_dir / 'rubric.json').write_text(models.load_rubric().model_dump_json(indent=2) + '\n')
    for p in Path(models.__file__).parent.rglob('*rubric*'):
        if p.is_file(): source_files.append(p)
    source_hashes = {str(p): digest(p) for p in sorted(set(source_files))}
    runner = scripts / 'harbor-resource-runner.py'
    sys.argv = [str(runner), *args[3:]]
    inspection = args[3:] == ['--inspect-only']
    proof = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
             'model_calls': not inspection, 'model_execution_requested': not inspection,
             'inspection_only': inspection,
             'purpose': 'Required task-quality review; separate from scored benchmark campaign',
             'runner_sha256': digest(runner),
             'capture_manifest_sha256': digest(CAPTURE / 'manifest.json'),
             'quality_review_source_sha256': source_hashes,
             'wrapper_capture_hook_sha256': digest(Path(__file__)),
             'installed_harbor': version('harbor')}
    try:
        runpy.run_path(str(runner), run_name=('captured_check_inspection' if inspection else '__main__'))
    finally:
        imports = {}
        for name, module in sorted(sys.modules.items()):
            if not name.startswith('benchmark_'): continue
            p = Path(module.__file__).resolve()
            assert p.is_relative_to(scripts), f'Uncaptured import: {name}'
            relative = str(p.relative_to(tree)); assert digest(p) == manifest['source_sha256'][relative]
            imports[name] = {'path': str(p.relative_to(ROOT)), 'sha256': digest(p)}
        assert all(digest(tree / name) == expected for name, expected in manifest['source_sha256'].items())
        assert all(hashes(ROOT / row['original_task']) == row['original_task_file_sha256'] for row in wrapper_records)
        proof.update(finished_at=datetime.now(timezone.utc).isoformat(),
                     all_local_imports_captured=True, captured_tree_unchanged=True,
                     loaded_local_modules=imports, original_tasks_unchanged=True,
                     captured_wrapper_count=len(wrapper_records))
        (proof_dir / 'runtime-source-proof.json').write_text(json.dumps(proof, indent=2) + '\n')


if __name__ == '__main__': main()

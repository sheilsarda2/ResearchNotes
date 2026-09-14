"""Read-only adapter checks and synthetic tests; write a fresh local proof only."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SCRIPT = ROOT / 'scripts/package-takehome-evidence.py'
spec = importlib.util.spec_from_file_location('packager', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    proof_path = HERE / 'verification.json'
    assert not proof_path.exists(), 'Preserve this proof; use a new evidence directory for a new version.'
    files = [SCRIPT, HERE / 'verify_adapter.py', HERE.parent / 'test_packager.py',
             HERE.parent / 'test_final_revision_mapping.py', HERE.parent / 'FINAL_REVISION_MAPPING.md']
    source_hashes = {str(f.relative_to(ROOT)): digest(f) for f in files}
    expected = {
        'research/takehome-presentation-2026-09-14/data/results.json': '9dabba6f5ff251f8a79b06bc875be76c51a5bdf0481d5be058bba03941390d7a',
        'research/takehome-presentation-2026-09-14/packaging/plan-82-expanded.json': '222fbc46901e83d962753658e2bee89e72ad965024600f0ed9d80f59f7932c17',
        'research/takehome-presentation-2026-09-14/packaging/plan-82-expanded-verification.json': '73bf24ad35bcbb7da484067305efdc734f1bb1026538bfdd14efc9bd58ce99b6',
    }
    assert all(digest(ROOT / path) == value for path, value in expected.items())
    preservation_before = p.original_materials()
    tests = {}
    for name in ('test_packager.py', 'test_final_revision_mapping.py'):
        run = subprocess.run([sys.executable, '-B', str(HERE.parent / name)], cwd=ROOT,
                             capture_output=True, timeout=60)
        log = HERE / (name + '.log')
        with log.open('xb') as stream:
            stream.write(run.stdout + run.stderr)
        assert run.returncode == 0, name + ' failed; inspect its separate log.'
        tests[name] = dict(exit_code=run.returncode, log=str(log.relative_to(ROOT)), sha256=digest(log))
    old_plan = json.loads((HERE.parent / 'plan-82-expanded.json').read_bytes())
    try:
        p.verify_plan(old_plan)
    except ValueError as error:
        gate = str(error)
        assert gate == '99-case packaging gate is incomplete (82/99); --plan-only is available', gate
    else:
        raise AssertionError('The old partial plan unexpectedly passed the assembly gate')
    base = ROOT / 'research/zenoh-coverage-followup-2026-09-14'
    manifest_path, lineage_path = base / 'task-manifest.json', base / 'revision.json'
    task = json.loads(manifest_path.read_bytes())
    lineage = json.loads(lineage_path.read_bytes())
    old_files, new_files = p.file_hashes(lineage['parent_task']), p.file_hashes(task['task'])
    assert old_files == lineage['parent_file_sha256']
    assert new_files == lineage['revision_file_sha256'] == task['task_file_sha256']
    visible = lambda files: {n: h for n, h in files.items() if n in ('instruction.md', 'task.toml') or n.startswith('environment/')}
    assert visible(old_files) == visible(new_files)
    # This checks real original inputs against their captured campaign summary.
    # It is deliberately not a refreshed 99-cell dataset or an actual revision mapping.
    input_path = base / 'paired-inputs/manifest.json'
    inputs = json.loads(input_path.read_bytes())
    snapshots = {}
    for key, entry in inputs['source_snapshots'].items():
        path = ROOT / entry['snapshot']
        assert digest(path) == entry['sha256']
        snapshots[key] = json.loads(path.read_bytes())
    first = {}
    for row in snapshots['summary']['trials']:
        if row['task'] != p.ZENOH_HISTORY or row['status'] not in p.COUNTED:
            continue
        key = p.cell(row)
        if key not in first or (p.timestamp(row['finished_at']), row['trial']) < (
                p.timestamp(first[key]['finished_at']), first[key]['trial']):
            first[key] = row
    rows = [dict(r, artifacts={r['trial'] + '/result.json': dict(sha256=r['result_sha256'])}) for r in first.values()]
    definition = next(row for row in snapshots['plan']['tasks'] if row['id'] == p.ZENOH_HISTORY)
    records, _ = p.verify_original_pair_inputs(dict(path=str(input_path.relative_to(ROOT)), sha256=digest(input_path)),
                                               rows, definition, set())
    for source, expected_hash in source_hashes.items():
        assert digest(ROOT / source) == expected_hash, 'Adapter source changed during verification'
        destination = HERE / 'source-capture' / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write((ROOT / source).read_bytes())
        assert digest(destination) == expected_hash
    assert all(digest(ROOT / path) == value for path, value in expected.items())
    proof = dict(schema_version=1, checked_at=datetime.now(timezone.utc).isoformat(),
        passed=True, kind='packaging_adapter_local_verification', model_calls=0, runtime_launches=0,
        final_mapping_created=False, pack_assembled=False, frozen_data_refreshed=False,
        source_sha256=source_hashes, tests=tests, original_plan_assembly_rejection=gate,
        preserved_snapshot_sha256=expected, preservation_before=preservation_before,
        preservation_after=p.original_materials(),
        final_task_static_check=dict(path=task['task'], manifest_sha256=digest(manifest_path),
            lineage_sha256=digest(lineage_path), agent_facing_inputs_unchanged=True,
            runtime_controls_or_quality_not_claimed=True),
        original_input_schema_check=dict(manifest_sha256=digest(input_path), original_trials=len(records),
            saved_source_files=sum(len(r['sources']) for r in records.values()),
            original_rewards={r['trial']: r['reward'] for r in rows},
            basis='Captured campaign summary; does not replace the frozen 99-cell collector gate.'),
        limitations=['Actual corrected controls, quality audit and nine paired regrades must pass before a real mapping.',
            'Original image/runtime limitations are retained; no unrecorded immutable agent image identity is inferred.',
            'Observed unconfigured storage quota remains null with Docker evidence.',
            'The take-home still requires the user human-written report.'])
    with proof_path.open('x') as stream:
        stream.write(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(proof=str(proof_path.relative_to(ROOT)), sha256=digest(proof_path),
                         source_sha256=source_hashes, originals_checked=36, tests_passed=True), indent=2))


if __name__ == '__main__':
    main()

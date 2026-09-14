"""Verify separate-data selection without overwriting datasets or assembling a pack."""
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
    assert not proof_path.exists(), 'Preserve this proof; use a new evidence directory for another version.'
    sources = [SCRIPT, HERE / 'verify_adapter.py', HERE.parent / 'test_packager.py',
               HERE.parent / 'test_final_revision_mapping.py', HERE.parent / 'test_data_snapshot_selection.py',
               HERE.parent / 'DATA_SNAPSHOT_SELECTION.md']
    hashes = {str(f.relative_to(ROOT)): digest(f) for f in sources}
    selected = p.BASE / 'data-snapshots/94-20260914T0620'
    preserved = [p.BASE / 'data', selected, HERE.parent / 'final-revision-adapter-001',
                 HERE.parent / 'plan-82.inputs', HERE.parent / 'plan-82-expanded.inputs']
    before = {str(path.relative_to(ROOT)): p.file_hashes(path) for path in preserved}
    prior_files = {name: digest(HERE.parent / name) for name in
                   ('plan-82.json', 'plan-82-expanded.json', 'plan-82-expanded-verification.json')}
    assert prior_files['plan-82-expanded.json'] == '222fbc46901e83d962753658e2bee89e72ad965024600f0ed9d80f59f7932c17'
    assert digest(p.BASE / 'data/results.json') == '9dabba6f5ff251f8a79b06bc875be76c51a5bdf0481d5be058bba03941390d7a'
    assert digest(HERE.parent / 'final-revision-adapter-001/verification.json') == 'a55a7684419e318f08b032793be6597870f5d7e1083dd15bb751c6518850f79b'
    originals_before = p.original_materials()
    tests = {}
    for name in ('test_packager.py', 'test_final_revision_mapping.py', 'test_data_snapshot_selection.py'):
        run = subprocess.run([sys.executable, '-B', str(HERE.parent / name)], cwd=ROOT,
                             capture_output=True, timeout=60)
        log = HERE / (name + '.log')
        with log.open('xb') as stream:
            stream.write(run.stdout + run.stderr)
        assert run.returncode == 0, name + ' failed; inspect its log.'
        tests[name] = dict(exit_code=run.returncode, log=str(log.relative_to(ROOT)), sha256=digest(log))
    # A new plan-only proof using an existing separate snapshot; no new data is collected.
    plan_path = HERE / 'plan-94-selection-proof.json'
    assert not plan_path.exists()
    plan = p.make_plan(plan_path, data_dir=selected)
    with plan_path.open('x') as stream:
        stream.write(json.dumps(plan, indent=2, sort_keys=True) + '\n')
    assert plan['coverage']['covered'] == 94 and plan['coverage']['ready'] is False
    assert plan['selected_data']['directory'] == str(selected.relative_to(ROOT))
    assert plan['selected_data']['files']['results.json']['sha256'] == digest(selected / 'results.json')
    assert len([e for e in plan['entries'] if e['kind'] == 'raw_trial']) == 27
    assert plan['retained'] == p.RETAINED and plan['final_revision_evidence'] == []
    rejections = {}
    for path, count in [(HERE.parent / 'plan-82-expanded.json', 82), (plan_path, 94)]:
        try:
            p.verify_plan(json.loads(path.read_bytes()))
        except ValueError as error:
            message = str(error)
            assert message == f'99-case packaging gate is incomplete ({count}/99); --plan-only is available', message
            rejections[str(path.relative_to(ROOT))] = message
        else:
            raise AssertionError('An incomplete snapshot unexpectedly passed the assembly gate')
    for source, expected in hashes.items():
        assert digest(ROOT / source) == expected, 'Adapter source changed during verification'
        destination = HERE / 'source-capture' / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as stream:
            stream.write((ROOT / source).read_bytes())
        assert digest(destination) == expected
    after = {str(path.relative_to(ROOT)): p.file_hashes(path) for path in preserved}
    assert before == after
    assert all(digest(HERE.parent / name) == value for name, value in prior_files.items())
    proof = dict(schema_version=1, checked_at=datetime.now(timezone.utc).isoformat(),
        kind='packaging_data_snapshot_selector_local_verification', passed=True,
        model_calls=0, runtime_launches=0, new_data_snapshot_created=False, final_mapping_created=False,
        pack_assembled=False, source_sha256=hashes, tests=tests,
        plan_only=dict(path=str(plan_path.relative_to(ROOT)), sha256=digest(plan_path),
            coverage=plan['coverage'], selected_data=plan['selected_data'], raw_trials=27,
            report_supplied=False, basis='Existing separate94 snapshot; default82 data was not refreshed.'),
        assembly_gate_rejections=rejections, preserved_directory_file_sha256=before,
        preserved_prior_plan_sha256=prior_files, all_preserved_directories_unchanged=True,
        original_materials_before=originals_before, original_materials_after=p.original_materials(),
        note='A review pack may have an empty report directory once99 and validation gates pass; final human-written report remains separate.')
    with proof_path.open('x') as stream:
        stream.write(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(proof=str(proof_path.relative_to(ROOT)), sha256=digest(proof_path),
        packager_sha256=hashes[str(SCRIPT.relative_to(ROOT))], plan=str(plan_path.relative_to(ROOT)),
        tests_passed=True, original_files_checked=36, assembled=False), indent=2))


if __name__ == '__main__':
    main()

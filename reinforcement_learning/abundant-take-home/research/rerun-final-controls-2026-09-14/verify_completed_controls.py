"""Verify saved controls, accepting the task's expected no-op early-build schema.

This does not launch Harbor or change any raw artifact. The original execution
validator remains unchanged; its expected score-group names are parameterized
only for a no-op whose verified early compile failure emits no test groups.
"""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rerun_control_validation', BASE/'validate_controls_immutable.py')
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


def main():
    output = BASE/'harbor-controls-001'
    identity = controls.read(output/'run-identity.json')
    original_summary = controls.read(output/'summary.json')
    assert original_summary['inputs_unchanged'] and original_summary['tooling_unchanged']
    assert controls.frozen_inputs() == identity['inputs']
    assert controls.tooling_hashes() == identity['tooling_sha256']
    assert original_summary['controls']['oracle']['passed'] is True
    prior_nop = original_summary['controls']['nop']
    assert prior_nop['exit_code'] == 0 and prior_nop['error_type'] == 'AssertionError'
    summary = {'schema_version':1, 'verified_at':datetime.now(timezone.utc).isoformat(),
               'passed':False, 'model_calls':0, 'new_docker_runs':0,
               'task_checksum':identity['task_checksum'],
               'original_execution_summary':str((output/'summary.json').relative_to(controls.ROOT)),
               'original_execution_summary_sha256':controls.digest(output/'summary.json'),
               'run_identity_sha256':controls.digest(output/'run-identity.json'),
               'verification_source_sha256':controls.digest(Path(__file__)),
               'correction':'The expected no-op fails at cargo test --no-run and emits empty tests/groups. The original wrapper mistakenly required populated groups for both agents. Raw outcomes and execution sources are unchanged.',
               'controls':{}}
    for agent in ('oracle','nop'):
        job = output/'jobs'/original_summary['controls'][agent]['job']
        paths = list(job.glob('*/result.json')); assert len(paths) == 1
        expected_groups = controls.GROUPS
        if agent == 'nop':
            score = controls.read(paths[0].parent/'verifier/score.json')
            assert score['anticheat'] == {'passed':True,'hits':0}
            assert score['build'] == {'passed':False,'exit':101}
            assert score['tests'] == score['groups'] == {}
            assert score['failure_reason'] == 'cargo test --no-run failed (exit 101)'
            controls.GROUPS = []
        try:
            verified = controls.validate_result(paths[0],agent,identity['task_checksum'],identity['tooling_sha256'])
        finally:
            controls.GROUPS = expected_groups
        verified['admission_lifecycle'] = controls.validate_admission(output/(agent+'-admission-observations.jsonl'),output/'control.json')
        summary['controls'][agent] = verified
    summary['inputs_unchanged'] = controls.frozen_inputs() == identity['inputs']
    summary['tooling_unchanged'] = controls.tooling_hashes() == identity['tooling_sha256']
    summary['passed'] = summary['inputs_unchanged'] and summary['tooling_unchanged'] and all(row['passed'] for row in summary['controls'].values())
    target = BASE/'final-validation.json'; assert not target.exists()
    target.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'path':str(target.relative_to(controls.ROOT)),'sha256':controls.digest(target),'passed':summary['passed'],'model_calls':0}))


if __name__ == '__main__': main()

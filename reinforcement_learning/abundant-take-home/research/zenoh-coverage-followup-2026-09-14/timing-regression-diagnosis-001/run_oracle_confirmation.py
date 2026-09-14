"""One new, unchanged-task oracle control, gated on reviewed timing evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
sys.path.insert(0, str(BASE))
import validate_controls_immutable as original
from pin_control_verifier_image import maybe_pin, finalize
import read_only_observer as observer


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def ref(path):
    return {'path':str(path.relative_to(ROOT)), 'sha256':digest(path)}


def tools():
    return {**original.tooling_hashes(), **{str(p.relative_to(ROOT)):digest(p) for p in
        (Path(__file__), HERE / 'read_only_observer.py')}}



def validate_timing_cases(result, case_root):
    comparison = read(case_root / 'comparison.json')
    assert comparison['complete'] and comparison['fixed_repeats_per_source'] == 3
    assert result['test_outcomes'] == comparison['cases']
    rows = comparison['cases']
    assert len(rows) == 6 and {(r['variant'],r['repeat']) for r in rows} == {
        (v,n) for v in ('untouched_gold','corrected_reference') for n in (1,2,3)}
    for row in rows:
        assert Path(row['log']).name == row['log']
        path = case_root / row['log']
        assert digest(path) == row['log_sha256']
        text = path.read_text()
        assert 'running 1 test' in text and 'error[E' not in text
        passed = 'test scouting_delay_regression ... ok' in text
        failed = 'test scouting_delay_regression ... FAILED' in text
        assert passed != failed and type(row['test_passed']) is bool
        assert row['test_passed'] == passed and row['returncode'] == (0 if passed else 101)
        if failed:
            assert 'expected <400ms' in text
    return rows


def gate(decision):
    from importlib.metadata import version
    from harbor.models.task.task import Task
    assert version('harbor') == '0.15.0'
    assert Task(original.TASK).checksum == read(BASE / 'task-manifest.json')['harbor_task_checksum']
    value = read(decision)
    assert value['kind'] == 'reviewed_timing_comparison_decision'
    assert value['single_full_oracle_confirmation_authorized'] is True and value['model_calls'] == 0
    comparison_path = ROOT / value['comparison_result']['path']
    assert digest(comparison_path) == value['comparison_result']['sha256']
    result = read(comparison_path)
    assert result['complete'] and result['container_absent'] and result['claim_absent'] and result['inputs_unchanged']
    assert result['cleanup_errors']==[] and result['model_calls']==0 and result['finished_at']
    comparison_inputs = ROOT / value['comparison_inputs']['path']
    assert comparison_inputs == comparison_path.parent / 'inputs.json'
    assert digest(comparison_inputs) == value['comparison_inputs']['sha256']
    compared = read(comparison_inputs)
    assert compared['task_checksum'] == read(BASE / 'task-manifest.json')['harbor_task_checksum']
    assert compared['variant_order'] == ['untouched_gold', 'corrected_reference'] and compared['repeats'] == 3
    validate_timing_cases(result, comparison_path.parent / 'case')
    for variant,key in [('untouched_gold','gold_source_files'),('corrected_reference','corrected_source_files')]:
        for phase in ('before','after'):
            assert read(comparison_path.parent / 'case' / (variant + '-source-' + phase + '.json')) == compared[key]
    assert len(result['test_outcomes']) == 6
    assert all(len([r for r in result['test_outcomes'] if r['variant']==v]) == 3
               for v in ('untouched_gold','corrected_reference'))
    assert any(r['test_passed'] for r in result['test_outcomes'] if r['variant']=='corrected_reference'), 'No evidence corrected source can meet unchanged timing contract'
    assert value['interpretation'] and value['raw_original_oracle_preserved'] is True
    old = read(BASE / 'harbor-controls-final/summary.json')
    continuation = read(BASE / 'continuation-001/summary.json')
    assert old.get('finished_at') and continuation.get('finished_at') and not continuation['stages']
    assert not (BASE / 'reviews-final-001').exists()
    inputs = original.frozen_inputs()
    manifest = read(BASE / 'task-manifest.json')
    assert value['task_checksum'] == manifest['harbor_task_checksum']
    return {'decision':ref(decision), 'comparison_result':ref(comparison_path),'comparison_inputs':ref(comparison_inputs),
            'original_controls_summary':ref(BASE/'harbor-controls-final/summary.json'),
            'prior_continuation':ref(BASE/'continuation-001/summary.json'),
            'inputs':inputs, 'tooling_sha256':tools()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--decision', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    before = gate(args.decision.resolve())
    output = BASE / 'harbor-oracle-confirmation-001'
    assert not output.exists(), 'Never repeat a full control from this helper'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name, command = original.command('oracle', output/'jobs', stamp)
    if not args.run:
        print(json.dumps({'check_only':True,'passed':True,'launches':0,'command':command,'evidence':before},indent=2))
        return
    output.mkdir()
    (output/'jobs').mkdir()
    control = output/'control.json'
    original.dump(control, original.admission_control())
    identity = {'schema_version':1,'harbor_version':'0.15.0','started_at':original.now(),'task':str(original.TASK.relative_to(ROOT)),
        'task_checksum':read(BASE/'task-manifest.json')['harbor_task_checksum'],
        'inputs':before['inputs'],'tooling_sha256':before['tooling_sha256'],
        'original_budgets':original.original_budgets(),'control_sha256':digest(control),'model_calls':0,
        'execution_source_tree':str((original.CAPTURE/'files').relative_to(ROOT)),
        'confirmation_basis':before, 'live_source_differences_at_start':original.live_source_differences()}
    original.dump(output/'run-identity.json', identity)
    summary = {'schema_version':1,'started_at':original.now(),'passed':False,'model_calls':0,
               'kind':'single_full_oracle_confirmation','run_identity':str((output/'run-identity.json').relative_to(ROOT)),
               'run_identity_sha256':digest(output/'run-identity.json'),'controls':{}}
    original.dump(output/'summary.json',summary)
    record = {'job':name,'command':command,'model_calls':0,'passed':False}
    started = time.monotonic()
    env = dict(os.environ,HARBOR_ADMISSION_CONTROL=str(control),PYTHONDONTWRITEBYTECODE='1')
    print(original.now(),'Starting one reviewed full oracle confirmation',name,flush=True)
    try:
        with (output/'oracle.log').open('x') as log:
            child = subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            observations = output/'oracle-admission-observations.jsonl'
            gaps = output/'oracle-observation-gaps.jsonl'
            def observe(terminal=False):
                row = observer.observe_once(observations,control,child.pid,
                    shared_control=Path(original.admission_control()['shared_pool']),gap_path=gaps,terminal=terminal)
                maybe_pin(output/'jobs'/name,output,identity['task_checksum'])
                return row
            record['observation'] = observer.wait_with_observation(child,observe)
            record['exit_code'] = child.returncode
            assert record['observation']['terminal_sample_valid'] and not record['observation']['errors']
        record['admission_lifecycle'] = original.validate_admission(observations,control)
        assert child.returncode == 0
        paths = list((output/'jobs'/name).glob('*/result.json'))
        assert len(paths) == 1
        record.update(original.validate_result(paths[0],'oracle',identity['task_checksum'],before['tooling_sha256']))
        record['verifier_image_proof'] = finalize(output,paths[0],ROOT)
        record['observation_gaps'] = ref(gaps) if gaps.exists() else None
    except BaseException as error:
        record.update(error_type=type(error).__name__,error=str(error))
    record['wall_seconds'] = time.monotonic()-started
    summary['controls']['oracle'] = record
    summary.update(finished_at=original.now(),inputs_unchanged=original.frozen_inputs()==before['inputs'],
                   tooling_unchanged=tools()==before['tooling_sha256'],decision_unchanged=gate(args.decision.resolve())==before)
    summary['passed'] = record['passed'] and summary['inputs_unchanged'] and summary['tooling_unchanged'] and summary['decision_unchanged']
    original.dump(output/'summary.json',summary)
    print(json.dumps({'summary':str((output/'summary.json').relative_to(ROOT)),'passed':summary['passed']}),flush=True)
    if not summary['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

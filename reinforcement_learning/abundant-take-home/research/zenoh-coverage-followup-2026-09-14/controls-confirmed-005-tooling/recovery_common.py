"""Bounded recovery after optional Docker StorageOpt inspection failed."""
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
spec = importlib.util.spec_from_file_location('frozen_recovery004_common', BASE / 'controls-confirmed-004-tooling/recovery_common.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
require, path, digest, read = prior.require, prior.path, prior.digest, prior.read
reference, bound = prior.reference, prior.bound
CONTROLS_SHA = prior.CONTROLS_SHA
DIAGNOSTICS = prior.DIAGNOSTICS
PAIRED = BASE / 'paired-regrades/run-002'
OUTPUT = BASE / 'continuation-confirmed-005'
OLD_PAIRED = BASE / 'paired-regrades/run-001'
OLD_OUTPUT = BASE / 'continuation-confirmed-004'
REPAIR = BASE / 'paired-storage-inspection-fix-001'
combined_controls, quality_gate, diagnostics_gate = prior.combined_controls, prior.quality_gate, prior.diagnostics_gate


def frozen_sources():
    prior.frozen_sources()
    manifest = read(HERE / 'source-manifest.json')
    require(manifest['kind'] == 'full_regrade_optional_storage_recovery_tooling'
            and manifest['launches'] == 0, 'Unexpected 005 source manifest')
    required = {str((HERE / name).relative_to(ROOT)) for name in
                ('recovery_common.py', 'continue_confirmed_validation.py', 'test_recovery.py',
                 'README.md', 'prior-attempt-preservation.json')}
    required.update(str(target.relative_to(ROOT)) for target in
                    (BASE / 'paired-regrades/run_paired_regrades_v2.py',
                     BASE / 'paired-regrades/case_inside.py', REPAIR / 'verification.json'))
    require(required <= set(manifest['source_sha256']), 'Incomplete 005 source manifest')
    for filename, expected in manifest['source_sha256'].items():
        require(digest(filename) == expected, '005 source/input drift: ' + filename)
    preserved = read(HERE / 'prior-attempt-preservation.json')
    for directory, expected in preserved['trees'].items():
        require(prior.tree_files(directory) == expected, 'Historical tree drift: ' + directory)
    return reference(HERE / 'source-manifest.json')


def validate_prior_documents(continuation, paired, case, inspection_error):
    stages = continuation['stages']
    require(continuation['passed'] is False and continuation['finished_at']
            and continuation['error_type'] == 'ValueError'
            and continuation['error'] == 'paired-full-regrades failed; no later stage or retry is authorized'
            and set(stages) == {'focused-diagnostics', 'paired-file-gate', 'paired-full-regrades'}
            and all(stages[name]['exit_code'] == code and stages[name]['finished_at']
                    for name, code in [('focused-diagnostics', 0), ('paired-file-gate', 0), ('paired-full-regrades', 1)])
            and continuation['controls_summary']['sha256'] == CONTROLS_SHA,
            'Only exact terminal 004 inspection failure is reusable')
    require(paired['finished_at'] and paired['validation_passed'] is False
            and paired['all_regrades_complete'] is False and len(paired['regrades']) == 1
            and paired['model_calls'] == paired['counted_sweep_trials'] == 0
            and paired['controls_summary_sha256'] == CONTROLS_SHA,
            'Prior full regrade must have stopped on its first case')
    require(case['status'] == 'error' and case['finished_at'] and not case['validation_passed']
            and case['reward'] is None and case['groups'] == {}
            and case['original_source_unchanged'] is True
            and case['error_type'] == 'ValueError' and case['error'].startswith('inspect-limits failed;')
            and case['model_calls'] == 0 and case['counted_sweep_trial'] is False,
            'Prior case must be an unscored pre-verifier inspection error')
    require(case['cleanup'] == dict(claim_absent=True, container_absent=True,
                errors=['collect-output:1', 'collect-verifier:1'], stopped_before_collection=True),
            'Prior cleanup must preserve the missing-output collection errors and proven absence')
    events = [event['event'] for event in case['lifecycle']]
    require('claimed' in events and 'released' in events and 'full-verifier-start' not in events,
            'Unexpected prior verifier execution or unreleased claim')
    require('map has no entry for key "StorageOpt"' in inspection_error,
            'Unexpected inspection failure')
    require(paired['regrades'][0]['original_trial_path'] == case['original_trial_path']
            and paired['stopped_on_incomplete_regrade'] == case['original_trial_path'],
            'Failed case identity differs from prior summary')


def absence_gate(continuation, case):
    require(Path('/proc/self/stat').exists(), 'Check/run from existing Linux Harbor host')
    pids = [continuation['pid'], *(row['pid'] for row in continuation['stages'].values())]
    require(all(isinstance(pid, int) and pid > 1 and not Path('/proc', str(pid)).exists() for pid in pids),
            'Prior controller/child PID still present; do not overlap')
    name = case['container_name']
    raw = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'],
                                  text=True, timeout=15)
    require(not raw.strip(), 'Prior replay container still present')
    pool = ROOT / 'jobs/candidate-campaigns-shared.control.json'
    with pool.with_suffix('.lock').open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        state = read(pool.with_suffix('.state.json'))
    participants = state['participants']
    control = str((OLD_PAIRED / '01-D3tzpaW/control.json').resolve())
    require(case['admission_key'] not in participants
            and not any(row.get('control') == control or name in row.get('trials', {})
                        for row in participants.values()), 'Prior replay admission ownership remains')
    return dict(prior_pids_absent=pids, container_absent=name,
                participant_absent=case['admission_key'])


def prior_failure_gate():
    continuation = read(OLD_OUTPUT / 'summary.json')
    paired = read(OLD_PAIRED / 'summary.json')
    case = read(OLD_PAIRED / '01-D3tzpaW/result.json')
    error = (OLD_PAIRED / '01-D3tzpaW/artifacts/03-inspect-limits.log').read_text()
    validate_prior_documents(continuation, paired, case, error)
    return dict(continuation=reference(OLD_OUTPUT / 'summary.json'),
                failed_paired=reference(OLD_PAIRED / 'summary.json'),
                failed_case=reference(OLD_PAIRED / '01-D3tzpaW/result.json'),
                failure_phase='optional_docker_field_before_source_copy_and_verifier',
                absence=absence_gate(continuation, case))


def repair_preflight_gate():
    proof = read(REPAIR / 'verification.json')
    require(proof['kind'] == 'paired_optional_storage_inspection_repair_verification'
            and proof['passed'] is True
            and proof['model_calls'] == proof['containers_started'] == proof['shared_claims_acquired'] == 0
            and proof['new_control_runs'] == proof['new_verifier_runs'] == 0
            and all(value is True for value in proof['assertions'].values()),
            'Optional Docker inspection repair requires passing offline proof')
    # Exact proof, test sources/logs, Docker fixtures and harness bytes are frozen
    # in the externally reviewed source manifest before this gate can run.
    return reference(REPAIR / 'verification.json')


def fresh_outputs():
    for target in (OUTPUT, PAIRED):
        require(not target.exists() and not target.is_symlink(), '005 refuses existing output: ' + str(target))
    require(not (BASE / 'reviews-final-003').exists(), 'No third quality attempt is authorized')


def paired_gate(controls_sha256):
    value = read(PAIRED / 'summary.json')
    require(value['validation_passed'] is True and value['all_regrades_complete'] is True
            and len(value['regrades']) == 9 and all(row['validation_passed'] for row in value['regrades'])
            and value['model_calls'] == value['counted_sweep_trials'] == 0
            and value['controls_summary_sha256'] == controls_sha256
            and value['diagnostics_summary_sha256'] == digest(DIAGNOSTICS / 'summary.json'),
            'All nine full regrades must complete; paired rewards remain separate')
    return reference(PAIRED / 'summary.json')

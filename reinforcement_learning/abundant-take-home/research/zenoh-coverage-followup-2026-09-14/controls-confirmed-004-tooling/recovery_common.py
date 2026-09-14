"""Additive zero-model gates after the exact 003 pre-admission Git failure."""
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
spec = importlib.util.spec_from_file_location('frozen_recovery003_common', BASE / 'controls-confirmed-003-tooling/recovery_common.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
require, path, digest, read = prior.require, prior.path, prior.digest, prior.read
reference, bound, module = prior.reference, prior.bound, prior.module
TASK, TASK_CHECKSUM = prior.TASK, prior.TASK_CHECKSUM
REVIEW_OUTPUT, AUDIT = prior.REVIEW_OUTPUT, prior.AUDIT
DIAGNOSTICS = BASE / 'harness/diagnostics-final-002'
PAIRED = BASE / 'paired-regrades/run-001'
OUTPUT = BASE / 'continuation-confirmed-004'
OLD_DIAGNOSTICS = BASE / 'harness/diagnostics-final'
CONTROLS_SHA = 'f8ea89b1137235d6ccdc8ae950bbc2121213c1df14eb553c00d0991735c9ceac'
QUALITY_SHA = '86fbf131221b3780b0a521507c8f51044d5da7ec5600205ae64dcc4d3e23ba42'
AUDIT_SHA = '77e53523e12d62d8eacc1037979208e7dfed61506ceee916e39f08b6e1e1181c'
PRIOR_SHA = '796ccdbf62d61f2712384eb28cae9525dd49d0e15d4f7338828f0d10dfd050f1'
FAILED_SHA = '62971200caa4eab0e646d3071323806cfb667296cc35ae41721624c6904c3489'
PREFLIGHT_SHA = '55c7dc148350d033348960c2dbb7735bb6bea3b173bd837fc86c7e02a40caf8e'
ERROR_TEXT = 'Focused validation stopped: ValueError: Patch may change only the four collected src directories\n'
SOURCE_NAMES = (
    'controls-confirmed-004-tooling/recovery_common.py',
    'controls-confirmed-004-tooling/continue_confirmed_validation.py',
    'controls-confirmed-004-tooling/test_recovery.py',
    'controls-confirmed-004-tooling/README.md',
    'controls-confirmed-004-tooling/prior-attempt-preservation.json',
    'controls-confirmed-003-tooling/source-manifest.json',
    'harness/focused_validation_v3.py', 'run_followup_diagnostics_v2.py',
    'diagnostic-path-preflight-fix-001/actual-preflight.json',
    'diagnostic-path-preflight-fix-001/verification.json',
    'diagnostic-path-preflight-fix-001/diagnosis.json',
    'diagnostic-path-preflight-fix-001/test_patch_inspection.py')


def tree_files(root):
    root = path(root)
    require(root.is_dir(), 'Missing preserved tree')
    result = {}
    for item in sorted(root.rglob('*')):
        require(not item.is_symlink(), 'Symlink in preserved tree')
        if item.is_file():
            result[str(item.relative_to(ROOT))] = digest(item)
    return result


def frozen_sources():
    prior.frozen_sources()
    manifest = read(HERE / 'source-manifest.json')
    require(manifest['kind'] == 'zero_model_preflight_recovery_tooling' and manifest['launches'] == 0,
            'Unexpected 004 source manifest')
    require(manifest['source_sha256'] == {str((BASE / n).relative_to(ROOT)): digest(BASE / n)
                                         for n in SOURCE_NAMES}, '004 source drift')
    preserved = read(HERE / 'prior-attempt-preservation.json')
    for directory, expected in preserved['trees'].items():
        require(tree_files(directory) == expected, 'Historical tree drift: ' + directory)
    for filename, expected in preserved['files'].items():
        require(digest(filename) == expected, 'Historical input drift: ' + filename)
    return reference(HERE / 'source-manifest.json')


def exact(value, sha):
    return bound(dict(path=str(value), sha256=sha))


def expected_cases():
    mutants = read(BASE / 'mutants/manifest.json')['mutants']
    saved = read(BASE / 'saved-source-audit/full-source-preflight.json')['trials']
    require(len(mutants) == 6 and len(saved) == 2, 'All six omission and two saved cases are required')
    result = {r['id']: 'single_omission_probe' for r in mutants}
    result.update({Path(r['trial']).name.split('__')[-1]: 'saved_success_diagnostic' for r in saved})
    require(len(result) == 8, 'Duplicate diagnostic identities')
    return result


def validate_prior_documents(continuation, diagnostics, error_text, entries):
    stages = continuation['stages']
    require(continuation['passed'] is False and continuation['finished_at']
            and continuation['error_type'] == 'ValueError'
            and continuation['error'] == 'focused-diagnostics failed; no later stage or retry is authorized'
            and set(stages) == {'quality-review', 'quality-evidence-audit', 'focused-diagnostics'}
            and all(stages[n]['exit_code'] == code and stages[n]['finished_at']
                    for n, code in [('quality-review', 0), ('quality-evidence-audit', 0), ('focused-diagnostics', 1)])
            and continuation['controls_summary']['sha256'] == CONTROLS_SHA,
            'Only terminal 003 after passed quality and first focused preflight failure is reusable')
    require(continuation['quality_gate'] == dict(summary=reference(REVIEW_OUTPUT / 'summary.json'), audit=reference(AUDIT)),
            '003 must bind the exact completed quality evidence')
    require(diagnostics['passed'] is False and diagnostics['cases'] == []
            and diagnostics['model_calls'] == diagnostics['counted_sweep_trials'] == 0
            and diagnostics['controls_summary_sha256'] == CONTROLS_SHA
            and diagnostics['harness_sha256'] == digest(BASE / 'harness/focused_validation_v2.py')
            and diagnostics['coordinator_sha256'] == digest(BASE / 'run_followup_diagnostics.py'),
            'Prior focused run must have failed before any case')
    require(entries == {'summary.json', 'omit-zero-config-rejection.controller.log'}
            and error_text == ERROR_TEXT, 'Unknown prior error, result, claim or case output')


def prior_failure_gate():
    continuation = exact(BASE / 'continuation-confirmed-003/summary.json', PRIOR_SHA)
    diagnostics = exact(OLD_DIAGNOSTICS / 'summary.json', FAILED_SHA)
    entries = {p.name for p in OLD_DIAGNOSTICS.iterdir()}
    validate_prior_documents(continuation, diagnostics,
                            (OLD_DIAGNOSTICS / 'omit-zero-config-rejection.controller.log').read_text(), entries)
    # These are historical Linux PIDs; absence must be checked in their original host namespace.
    require(Path('/proc/self/stat').exists(), 'Check/run from the existing Linux Harbor host')
    pids = [continuation['pid'], *(row['pid'] for row in continuation['stages'].values())]
    require(all(isinstance(pid, int) and pid > 1 and not Path('/proc', str(pid)).exists() for pid in pids),
            'A prior controller or child PID is still present; do not overlap')
    return dict(continuation=reference(BASE / 'continuation-confirmed-003/summary.json'),
                failed_diagnostics=reference(OLD_DIAGNOSTICS / 'summary.json'),
                prior_pids_absent=pids, failure_phase='capture_before_output_or_admission')


def repair_preflight_gate():
    proof = exact(BASE / 'diagnostic-path-preflight-fix-001/actual-preflight.json', PREFLIGHT_SHA)
    require(proof['passed'] is True and proof['model_calls'] == proof['admission_claims'] == proof['container_launches'] == 0
            and proof['task_checksum'] == TASK_CHECKSUM and proof['controls']['sha256'] == CONTROLS_SHA
            and proof['quality_summary']['sha256'] == QUALITY_SHA and proof['quality_audit']['sha256'] == AUDIT_SHA,
            'Repair preflight must preserve controls, task, quality and zero execution')
    for value in proof.values():
        if isinstance(value, dict) and set(value) == {'path', 'sha256'}:
            require(digest(value['path']) == value['sha256'], 'Repair preflight input drift')
    rows = proof['cases']
    require(len(rows) == 8 and {row['name'] for row in rows} == set(expected_cases()), 'All eight exact preflights required')
    for row in rows:
        require(row['returncode'] == 0 and row['check_only'] is True and bound(row['output'])['check_only'] is True
                and digest(row['stderr']['path']) == row['stderr']['sha256']
                and path(row['stderr']['path']).read_bytes() == b'', 'Failed or drifted repair case preflight')
    return reference(BASE / 'diagnostic-path-preflight-fix-001/actual-preflight.json')


def combined_controls(controls_path, controls_sha256):
    require(controls_sha256 == CONTROLS_SHA, '004 must reuse exact confirmed controls')
    return prior.combined_controls(controls_path, controls_sha256)


def quality_gate(gate):
    summary = exact(REVIEW_OUTPUT / 'summary.json', QUALITY_SHA)
    review = exact(AUDIT, AUDIT_SHA)
    require(summary['passed'] is True and summary['controls_summary'] == gate['controls_reference']
            and set(summary['reviews']) == {TASK.name}, 'One matching completed quality review is required')
    row = summary['reviews'][TASK.name]
    require(review['check_report'] == row['check_report']
            and review['check_report_sha256'] == row['check_report_sha256'] == digest(row['check_report'])
            and review['raw_trial'] == str(path(row['result']).parent.relative_to(ROOT))
            and review['original_task_checksum'] == row['original_task_checksum'] == TASK_CHECKSUM,
            'Quality audit does not describe the exact completed review')
    validator = module('confirmed004_quality_gate', ROOT / 'scripts/package-takehome-evidence.py')
    validator.verify_revision_quality(reference(AUDIT), gate['task'], set())
    return dict(summary=reference(REVIEW_OUTPUT / 'summary.json'), audit=reference(AUDIT))


def fresh_outputs():
    for target in (OUTPUT, DIAGNOSTICS, PAIRED):
        require(not target.exists() and not target.is_symlink(), '004 refuses existing output: ' + str(target))
    require(not (BASE / 'reviews-final-003').exists(), 'No third quality attempt is authorized')


def diagnostics_gate(controls_sha256):
    value = read(DIAGNOSTICS / 'summary.json')
    rows = value['cases']
    require(value['passed'] is True and value['model_calls'] == value['counted_sweep_trials'] == 0
            and value['controls_summary_sha256'] == controls_sha256
            and value['harness_sha256'] == digest(BASE / 'harness/focused_validation_v3.py')
            and value['coordinator_sha256'] == digest(BASE / 'run_followup_diagnostics_v2.py')
            and value['mutant_manifest_sha256'] == digest(BASE / 'mutants/manifest.json')
            and value['saved_source_preflight_sha256'] == digest(BASE / 'saved-source-audit/full-source-preflight.json')
            and len(rows) == 8 and {r['name']: r['kind'] for r in rows} == expected_cases(),
            'All six exact omission probes and both saved diagnostics must complete with the repaired harness')
    return reference(DIAGNOSTICS / 'summary.json')


def paired_gate(controls_sha256):
    value = read(PAIRED / 'summary.json')
    require(value['validation_passed'] is True and value['all_regrades_complete'] is True
            and len(value['regrades']) == 9 and value['model_calls'] == value['counted_sweep_trials'] == 0
            and value['controls_summary_sha256'] == controls_sha256
            and value['diagnostics_summary_sha256'] == digest(DIAGNOSTICS / 'summary.json'),
            'All nine full regrades must complete; paired rewards remain separate')
    return reference(PAIRED / 'summary.json')

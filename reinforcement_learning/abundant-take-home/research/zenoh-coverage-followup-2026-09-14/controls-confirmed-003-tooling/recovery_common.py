"""Read-only gates for an additive, single-use Zenoh control recovery."""
import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
BASE = HERE.parent
ROOT = BASE.parents[1]
TASK_CHECKSUM = '67f1fdca75fd66f4cbdd6e4729a582c9fbbdac428a0299eefc74104dffa59fab'
TASK_MANIFEST_SHA = 'b31cdea49802b2b2c24da18ae1b032671f9fbc8f406764534352d425ef160e3c'
TASK = BASE / 'rs-zenoh-timestamp-instrumentation-v4-validation'
REVIEW_OUTPUT = BASE / 'reviews-final-002'
AUDIT = BASE / 'completed-review-audits-002' / (TASK.name + '.json')
DIAGNOSTICS = BASE / 'harness/diagnostics-final'
PAIRED = BASE / 'paired-regrades/run-001'
OUTPUT = BASE / 'continuation-confirmed-003'
OBSERVER = BASE / 'timing-regression-diagnosis-001/read_only_observer.py'
SOURCE_NAMES = (
    'controls-confirmed-003-tooling/recovery_common.py',
    'controls-confirmed-003-tooling/continue_confirmed_validation.py',
    'controls-confirmed-003-tooling/run_confirmed_quality_review.py',
    'controls-confirmed-003-tooling/test_recovery.py',
    'controls-confirmed-003-tooling/audit_final_quality_review.py',
    'controls-confirmed-003-tooling/execute_quality_retry.py',
    'controls-confirmed-003-tooling/test_quality_retry.py',
    'controls-confirmed-003-tooling/prior-attempt-preservation.json',
    'quality-output-path-diagnosis-001/diagnosis.json',
    'quality-output-path-diagnosis-001/retry-authorization.json',
    'run_final_quality_review.py', 'execute_quality_check.py', 'audit_final_quality_review.py',
    'continue_validation.py', 'validate_controls_immutable.py', 'run_followup_diagnostics.py',
    'harness/focused_validation_v2.py', 'harness/case_inside_v2.py',
    'paired-regrades/run_paired_regrades.py', 'paired-regrades/case_inside.py',
    'timing-regression-diagnosis-001/read_only_observer.py',
    'timing-regression-diagnosis-001/validate_existing_nop.py',
    'oracle-image-rebinding-001/capture_image.py',
    'oracle-image-rebinding-001/validate_existing_oracle.py',
    'task-manifest.json', 'revision.json', 'paired-inputs/manifest.json',
    'mutants/manifest.json', 'saved-source-audit/full-source-preflight.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def path(value):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    require(candidate.is_relative_to(ROOT) and '..' not in candidate.parts
            and candidate.resolve() == candidate, 'Unsafe or symlinked evidence path')
    return candidate


def digest(value):
    return hashlib.sha256(path(value).read_bytes()).hexdigest()


def read(value):
    return json.loads(path(value).read_bytes())


def reference(value):
    target = path(value)
    return dict(path=str(target.relative_to(ROOT)), sha256=digest(target))


def bound(reference):
    require(digest(reference['path']) == reference['sha256'], 'Bound evidence hash drift: ' + reference['path'])
    return read(reference['path'])


def module(name, source):
    spec = importlib.util.spec_from_file_location(name, path(source))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def source_paths():
    return [BASE / name for name in SOURCE_NAMES] + [ROOT / 'scripts/package-takehome-evidence.py']


def frozen_sources():
    manifest = read(HERE / 'source-manifest.json')
    require(manifest['kind'] == 'confirmed_controls_recovery_tooling' and manifest['launches'] == 0,
            'Unexpected recovery source manifest')
    actual = {str(p.relative_to(ROOT)): digest(p) for p in source_paths()}
    require(actual == manifest['source_sha256'], 'Recovery/frozen helper source drift')
    return reference(HERE / 'source-manifest.json')


RETRY_AUTH_SHA = 'c0481e80e14f454d074965457c184b10cb9844e634179ef449e684e3c484471d'
DIAGNOSIS_SHA = 'f1b6af69aecc1c8d3d833e557d08678cf1053cbf3895560d9fce997f6b7b0744'


def prior_review_exception():
    """Allow only the explicitly reviewed first output-placement failure."""
    authorization_path = BASE / 'quality-output-path-diagnosis-001/retry-authorization.json'
    auth = bound(dict(path=str(authorization_path), sha256=RETRY_AUTH_SHA))
    diagnosis_path = BASE / 'quality-output-path-diagnosis-001/diagnosis.json'
    require(auth['diagnosis'] == dict(path=str(diagnosis_path.relative_to(ROOT)), sha256=DIAGNOSIS_SHA),
            'Retry must bind the reviewed first-attempt diagnosis')
    diagnosis = bound(auth['diagnosis'])
    old = BASE / 'reviews-final-001'
    previous = BASE / 'continuation-confirmed-002'
    require(auth['kind'] == 'reviewed_single_quality_output_retry'
            and auth['new_review_attempts'] == 1 and auth['max_total_reviewer_attempts'] == 2
            and auth['automatic_retries'] == 0 and auth['final_task_checksum'] == TASK_CHECKSUM
            and auth['model'] == 'anthropic/claude-sonnet-5' and auth['reasoning_effort'] == 'high'
            and auth['mini_version'] == '2.4.6' and auth['n_concurrent'] == auth['n_attempts'] == 1
            and auth['shared_max_active'] == 14 and auth['local_max_active_overall'] == 1
            and all(auth[key] is True for key in ('task_bytes_unchanged', 'rubric_unchanged',
                                                 'generated_instruction_unchanged', 'review_resources_unchanged'))
            and auth['outputs'] == dict(review=str(REVIEW_OUTPUT.relative_to(ROOT)),
                audit=str(AUDIT.parent.relative_to(ROOT)), continuation=str(OUTPUT.relative_to(ROOT)))
            and path(auth['prior_review']) == old and path(auth['prior_continuation']) == previous,
            'Only one unchanged second review is authorized; no third review or changed settings')
    require(diagnosis['kind'] == 'reviewer_output_path_error_diagnostic'
            and diagnosis['official_review_valid'] is False and diagnosis['official_reward'] == 0
            and diagnosis['official_criteria'] == {} and diagnosis['agent_exception'] is None
            and diagnosis['expected_output_path'] == '/app/check-result.json'
            and diagnosis['actual_tool_write_path'] == '/app/task/check-result.json'
            and diagnosis['tool_returncode'] == 0 and diagnosis['new_model_calls'] == diagnosis['new_control_runs'] == 0,
            'Only the diagnosed output-placement failure permits this second review')
    for value in diagnosis.values():
        if isinstance(value, dict) and set(value) == {'path', 'sha256'}:
            require(digest(value['path']) == value['sha256'], 'Prior diagnosis input drift')
    require(path(diagnosis['review_summary']['path']) == old / 'summary.json'
            and path(diagnosis['stopped_continuation']['path']) == previous / 'summary.json',
            'Prior review/continuation identities differ')
    review = bound(diagnosis['review_summary'])
    continuation = bound(diagnosis['stopped_continuation'])
    require(review['passed'] is False and review['finished_at'] and set(review['reviews']) == {TASK.name}
            and review['controls_summary'] == continuation['controls_summary'] == auth['controls_summary']
            and continuation['passed'] is False and continuation['finished_at']
            and set(continuation['stages']) == {'quality-review'}
            and continuation['stages']['quality-review']['exit_code'] == 1
            and continuation['stages']['quality-review']['finished_at']
            and continuation['error_type'] == 'ValueError'
            and continuation['error'] == 'quality-review failed; no later stage or retry is authorized',
            'Prior continuation must be terminal with only its failed quality stage')
    row = review['reviews'][TASK.name]
    require(row['passed'] is False and row['finished_at'] and row['exit_code'] == 1
            and row['attempts'] == 1 and row['model'] == auth['model'] and row['effort'] == auth['reasoning_effort']
            and row['observation_errors'] == [] and row['observation']['errors'] == []
            and row['observation']['exit_code'] == 1 and row['observation']['terminal_sample_valid'] is True
            and row['error_type'] == 'AssertionError' and row['error'] == 'Review execution/terminal observation failed',
            'Unexpected first-review execution or observation failure cannot authorize a retry')
    raw = bound(diagnosis['raw_result'])
    raw_root = path(diagnosis['raw_trial'])
    require(raw_root.is_relative_to(old / 'jobs') and path(diagnosis['raw_result']['path']) == raw_root / 'result.json'
            and raw['finished_at'] and raw['exception_info'] is None
            and raw['verifier_result']['rewards']['reward'] == 0
            and raw['agent_info']['name'] == 'mini-swe-agent' and raw['agent_info']['version'] == '2.4.6',
            'Prior raw zero/exception-free reviewer identity must be preserved')
    official = bound(diagnosis['official_report'])
    require(len(official['results']) == 1 and official['results'][0]['task_name'] == TASK.name
            and official['results'][0]['checks'] == {}
            and 'missing result file: check-result.json' in official['results'][0]['error'],
            'Missing official report is the sole permitted prior failure; diagnostic judgments are not a report')
    artifacts = bound(diagnosis['artifact_manifest'])
    expected_artifact = [item for item in artifacts if item['source'] == '/app/check-result.json']
    require(len(expected_artifact) == 1 and expected_artifact[0]['status'] == 'failed'
            and not (raw_root / 'artifacts/check-result.json').exists()
            and not (BASE / 'completed-review-audits' / (TASK.name + '.json')).exists(),
            'The first missing output must remain missing and unofficial')
    preservation = read(HERE / 'prior-attempt-preservation.json')
    require(preservation['kind'] == 'unchanged_failed_first_quality_attempt'
            and set(preservation['source_trees']) == {str(old.relative_to(ROOT)), str(previous.relative_to(ROOT))},
            'Prior-attempt preservation scope differs')
    for folder, expected in preservation['source_trees'].items():
        root = path(folder); items = sorted(root.rglob('*'))
        require(not any(item.is_symlink() for item in items), 'Prior run contains symlinked evidence')
        actual = {str(item.relative_to(root)): digest(item) for item in items if item.is_file()}
        require(expected and actual == expected, 'Prior review/continuation tree drift')
    old_wrapper = read(old / (TASK.name + '-proof/wrapper-inputs.json'))
    require(old_wrapper['original_task_checksum'] == TASK_CHECKSUM
            and old_wrapper['original_copy_byte_identical'] is True
            and raw['task_checksum'] == old_wrapper['wrapper_checksum'], 'First generated-wrapper identity differs')
    return dict(authorization=reference(authorization_path), diagnosis=reference(diagnosis_path),
                prior_review=old, wrapper=old_wrapper, controls_summary=auth['controls_summary'])


def validate_regenerated_wrapper(record):
    prior = prior_review_exception()['wrapper']
    require(all(record[key] == prior[key] for key in ('original_task', 'original_task_checksum',
            'original_task_file_sha256', 'wrapper_checksum', 'captured_wrapper_file_sha256'))
            and record['original_copy_byte_identical'] is True,
            'Newly generated quality wrapper differs; no model launch is authorized')
    return True


def fresh_outputs(*, include_coordinator=True):
    targets = [REVIEW_OUTPUT, AUDIT, DIAGNOSTICS, PAIRED]
    if include_coordinator:
        targets.append(OUTPUT)
    require(not any(p.exists() for p in targets), 'An existing review/audit/diagnostic/regrade stage cannot be retried')
    prior = prior_review_exception()
    # Exactly the hash-bound first failure is permitted; any other prior attempt blocks.
    for directory in BASE.glob('reviews-*'):
        if directory == prior['prior_review']:
            continue
        require(not any(directory.glob('jobs/' + TASK.name + '-quality-*')), 'A prior final-task quality job already exists')
        for config in directory.glob('jobs/*/*/config.json'):
            raw = read(config)
            if TASK.name in str(raw.get('task', {}).get('path', '')) or any(TASK.name in part for part in config.parts):
                raise ValueError('A prior final-task quality attempt already exists')


def validate_comparison(comparison, superseded_result):
    result = bound(comparison['result'])
    inputs = bound(comparison['inputs'])
    decision = bound(comparison['decision'])
    require(result['complete'] is True and result['model_calls'] == 0 and result['finished_at']
            and result['container_absent'] is True and result['claim_absent'] is True
            and result['inputs_unchanged'] is True and result['cleanup_errors'] == [],
            'Timing comparison execution or cleanup is incomplete')
    root = path(comparison['result']['path']).parent
    require(path(comparison['inputs']['path']) == root / 'inputs.json'
            and inputs['kind'] == 'fixed_unchanged_timing_comparison'
            and inputs['task_checksum'] == TASK_CHECKSUM and inputs['task_manifest_sha256'] == TASK_MANIFEST_SHA
            and inputs['model_calls'] == 0 and inputs['full_regrade'] is False
            and inputs['variant_order'] == ['untouched_gold', 'corrected_reference'] and inputs['repeats'] == 3
            and dict(path=inputs['failed_oracle_result'], sha256=inputs['failed_oracle_result_sha256']) == superseded_result,
            'Timing comparison used other inputs or omitted the original oracle')
    raw = read(root / 'case/comparison.json')
    rows = raw['cases']
    require(raw['complete'] is True and raw['fixed_repeats_per_source'] == 3
            and raw['model_calls'] == 0 and raw['full_regrade'] is False and raw['reward'] is None
            and raw['tests_unchanged'] is True and rows == result['test_outcomes']
            and len(rows) == 6 and {(r['variant'], r['repeat']) for r in rows}
            == {(variant, repeat) for variant in inputs['variant_order'] for repeat in (1, 2, 3)},
            'Require six distinct completed timing comparison cases')
    for row in rows:
        require(Path(row['log']).name == row['log'], 'Unsafe timing comparison log path')
        log = root / 'case' / row['log']
        require(digest(log) == row['log_sha256'], 'Timing comparison raw log drift')
        content = log.read_text()
        passed = 'test scouting_delay_regression ... ok' in content
        failed = 'test scouting_delay_regression ... FAILED' in content
        require('running 1 test' in content and 'error[E' not in content and passed != failed
                and type(row['test_passed']) is bool and row['test_passed'] == passed
                and row['returncode'] == (0 if passed else 101)
                and (passed or 'expected <400ms' in content), 'Timing comparison raw verdict differs')
    for variant, key in [('untouched_gold', 'gold_source_files'), ('corrected_reference', 'corrected_source_files')]:
        require(inputs[key] and all(read(root / 'case' / (variant + '-source-' + phase + '.json')) == inputs[key]
                                   for phase in ('before', 'after')), 'Timing comparison source bytes changed')
    require(any(r['test_passed'] for r in rows if r['variant'] == 'corrected_reference'),
            'No corrected-source pass supports the single confirmation')
    require(decision['kind'] == 'reviewed_timing_comparison_decision' and decision['task_checksum'] == TASK_CHECKSUM
            and decision['single_full_oracle_confirmation_authorized'] is True and decision['model_calls'] == 0
            and decision['raw_original_oracle_preserved'] is True and decision['interpretation']
            and decision['comparison_result'] == comparison['result'] and decision['comparison_inputs'] == comparison['inputs'],
            'Single-oracle confirmation lacks a bound reviewed comparison decision')


def preserved_failure(agent, superseded, original_ref, original):
    raw = bound(superseded['result'])
    score = bound(superseded['score'])
    row = original['controls'][agent]
    original_job = path(original_ref['summary']['path']).parent / 'jobs' / row['job']
    result_path = path(superseded['result']['path'])
    require(row['passed'] is False and result_path.is_relative_to(original_job) and result_path.name == 'result.json'
            and path(superseded['score']['path']) == result_path.parent / 'verifier/score.json',
            'Superseded raw outcome is not the original failed ' + agent)
    require(raw['task_checksum'] == TASK_CHECKSUM and raw['config']['agent']['name'] == agent
            and raw['finished_at'] and raw['exception_info'] is None
            and raw['verifier_result']['rewards']['reward'] == score['reward'] == superseded['reward'] == 0
            and superseded['exception'] is None, 'Original reward-zero result was relabelled: ' + agent)
    require(superseded['observation_errors'] == row['observation_errors']
            and superseded['observation_errors'] and superseded['reason'],
            'Original observation error was omitted: ' + agent)


def independent_nop(certificate, identity, original_ref, original, superseded):
    source_ref = {key: original_ref[key] for key in ('summary', 'run_identity')}
    require(certificate['kind'] == 'independent_original_nop_validation'
            and certificate['execution_kind'] == 'independent_revalidation_of_existing_normal_nop'
            and certificate['new_control_runs'] == 0
            and identity['kind'] == 'independent_original_nop_revalidation'
            and identity['new_control_runs'] == identity['model_calls'] == 0
            and certificate['source_run'] == identity['source_run'] == source_ref
            and certificate['original_nop_record'] == original['controls']['nop']
            and certificate['original_observation_errors'] == original['controls']['nop']['observation_errors'],
            'Independent nop validation must preserve its failed original record and execution identity')
    nop = certificate['controls']['nop']
    require(dict(path=nop['result'], sha256=nop['result_sha256']) == superseded['result'],
            'Independent nop validation cannot substitute another raw execution')
    independent_run(certificate, original_ref, nop, 'nop', 'nop-independent-validation-001')


def independent_run(certificate, original_ref, record, agent, certificate_directory):
    old_folder = path(original_ref['summary']['path']).parent
    before = certificate['original_run_file_sha256_before']
    require(before == certificate['original_run_file_sha256_after'] and before,
            'Independent validation changed original run evidence')
    old_items = sorted(old_folder.rglob('*'))
    require(not any(item.is_symlink() for item in old_items), 'Original run evidence contains a symlink')
    actual = {str(item.relative_to(old_folder)): digest(item) for item in old_items if item.is_file()}
    require(actual == before, 'Original run evidence changed after independent validation')
    lifecycle = record['admission_lifecycle']
    observations_path = path(lifecycle['path'])
    require(observations_path == old_folder / (agent + '-admission-observations.jsonl')
            and digest(observations_path) == lifecycle['sha256'], 'Independent admission log differs')
    observations = [json.loads(line) for line in observations_path.read_text().splitlines()]
    claims = [sum(len(p['trials']) for p in row['own_participants'].values()) for row in observations]
    require(len(observations) == lifecycle['sample_count'] and max(claims) == lifecycle['peak_own_claims'] == 1
            and claims[-1] == lifecycle['final_own_claims'] == 0
            and lifecycle['passed'] is lifecycle['shared_cap_respected'] is True
            and all(row['total_claims'] <= row['shared_max_active'] == 14 for row in observations),
            'Independent admission lifecycle cannot be established from raw samples')
    terminal_ref = certificate['terminal_absence']
    require(path(terminal_ref['path']) == BASE / certificate_directory / 'terminal-absence.json',
            'Independent validation requires its own terminal absence proof')
    terminal = bound(terminal_ref)
    sample = bound(terminal['shared_observation'])
    historical = terminal['historical_runner']
    expected_key = str(historical['pid']) + ':' + historical['start_ticks']
    seen_keys = {key for row in observations for key in row['own_participants']}
    raw = bound(dict(path=record['result'], sha256=record['result_sha256']))
    require(terminal['kind'] == 'fresh_locked_terminal_absence' and sample == terminal['terminal_sample']
            and sample['terminal_sample'] is True and sample['own_participants'] == {}
            and sample['runner_pid'] == historical['pid']
            and historical['participant_key'] == expected_key and seen_keys == {expected_key}
            and sample['total_claims'] <= sample['shared_max_active'] == 14
            and terminal['original_runner_absent'] is True
            and terminal['observed_runner_start_ticks'] != historical['start_ticks']
            and terminal['matching_containers'] == [] and terminal['container_absent'] is True
            and terminal['claim_absent'] is True
            and terminal['trial_name_match'] == Path(record['result']).parent.name.lower()
            and datetime.fromisoformat(raw['finished_at'].replace('Z', '+00:00'))
                <= datetime.fromisoformat(sample['at'].replace('Z', '+00:00'))
                <= datetime.fromisoformat(certificate['finished_at'].replace('Z', '+00:00')),
            'Independent validation lacks bound fresh runner/container/claim absence')


def independent_oracle(certificate, identity, source_ref, source, task):
    expected_source = {key: source_ref[key] for key in ('summary', 'run_identity')}
    old_record = source['controls']['oracle']
    errors = old_record['observation']['errors']
    require(source['kind'] == 'single_full_oracle_confirmation' and source['decision_unchanged'] is True
            and source['passed'] is False and old_record['passed'] is False
            and old_record['exit_code'] == 0 and old_record['error_type'] == 'AssertionError' and old_record['error'] == ''
            and old_record['observation']['exit_code'] == 0
            and old_record['observation']['terminal_sample_valid'] is True
            and old_record['observation']['terminal_sample_at']
            and certificate['kind'] == 'independent_normal_oracle_confirmation_validation'
            and certificate['execution_kind'] == 'independent_revalidation_of_existing_normal_oracle_confirmation'
            and certificate['new_control_runs'] == 0
            and identity['kind'] == 'independent_normal_oracle_confirmation_revalidation'
            and identity['new_control_runs'] == identity['model_calls'] == 0
            and certificate['source_run'] == identity['source_run'] == expected_source
            and certificate['original_control_record'] == old_record
            and certificate['original_observation_errors'] == errors and errors
            and all(error['error_type'] == 'AssertionError'
                    and error['error'] == 'Private tag already identifies a different image'
                    and error['terminal'] is False and error['at'] for error in errors),
            'Independent oracle must preserve its exact image-pin callback errors; unexpected errors require review')
    record = certificate['controls']['oracle']
    result_path = path(record['result'])
    expected_job = path(source_ref['summary']['path']).parent / 'jobs' / old_record['job']
    raw = bound(dict(path=record['result'], sha256=record['result_sha256']))
    score = read(result_path.parent / 'verifier/score.json')
    require(result_path.is_relative_to(expected_job) and result_path.name == 'result.json'
            and raw['task_checksum'] == TASK_CHECKSUM and raw['config']['agent']['name'] == 'oracle'
            and raw['finished_at'] and raw['exception_info'] is None
            and raw['verifier_result']['rewards']['reward'] == score['reward'] == record['reward'] == 1,
            'Independent oracle requires the same normal confirmation raw reward one with no exception')
    independent_run(certificate, source_ref, record, 'oracle', 'oracle-independent-validation-001')
    controller = bound(identity['original_controller'])
    absence = certificate['original_controller_absence']
    require(controller['kind'] == 'observed_existing_oracle_confirmation_controller'
            and controller['run_identity_sha256'] == source_ref['run_identity']['sha256']
            and absence['controller'] == identity['original_controller']
            and absence['absent'] is True and absence['observed_start_ticks'] != controller['start_ticks']
            and absence['at'], 'Independent oracle lacks original controller absence evidence')
    capture_ref = certificate['image_capture']
    require(path(capture_ref['path']) == BASE / 'oracle-image-rebinding-001/capture.json',
            'Independent oracle requires its distinct actual image capture')
    capture = bound(capture_ref)
    image = bound(record['verifier_image_proof'])
    require(capture['kind'] == 'actual_normal_oracle_confirmation_image_capture'
            and capture['passed'] is True and capture['model_calls'] == capture['new_control_runs'] == 0
            and capture['task_checksum'] == TASK_CHECKSUM
            and capture['task_manifest'] == reference(BASE / 'task-manifest.json')
            and capture['run_identity'] == source_ref['run_identity']
            and path(capture['oracle_trial']) == result_path.parent
            and capture['verifier_image_id'] == image['verifier_image_id']
            and capture['private_tag'] == image['private_tag'] != capture['prior_private_tag']
            and capture['prior_tag_unchanged'] is True
            and capture['prior_image_id'] != capture['verifier_image_id']
            and capture['filtered_inspection'] == dict(path=image['raw_docker_inspection_path'],
                                                       sha256=image['raw_docker_inspection_sha256']),
            'Independent oracle image capture must bind the actual new image without repointing the old tag')
    tests = bound(capture['actual_test_files'])
    require(tests == {name: checksum for name, checksum in task['task_file_sha256'].items()
                      if name.startswith('tests/')}, 'Captured normal oracle image contains different task tests')
    layers = bound(capture['image_layers'])
    require(layers['images'] == dict(original=capture['prior_image_id'], confirmation=capture['verifier_image_id'])
            and layers['equal_rootfs_layers'] is True
            and layers['rootfs_layers']['original'] == layers['rootfs_layers']['confirmation'],
            'Confirmation image filesystem identity differs from the captured baseline')
    require(digest(capture['helper']['path']) == capture['helper']['sha256'], 'Actual-image capture helper changed')


def combined_controls(controls_path, controls_sha256):
    controls_path = path(controls_path)
    require(controls_path == BASE / 'controls-confirmed-001/summary.json', 'Use the explicit additive combined-control proof')
    ref = dict(path=str(controls_path.relative_to(ROOT)), sha256=controls_sha256)
    controls = bound(ref)
    task_ref = reference(BASE / 'task-manifest.json')
    require(task_ref['sha256'] == TASK_MANIFEST_SHA, 'Final task manifest changed')
    task = bound(task_ref)
    require(task['harbor_task_checksum'] == TASK_CHECKSUM and task['task'] == str(TASK.relative_to(ROOT)), 'Final task identity changed')
    task_files = {}
    for item in sorted(TASK.rglob('*')):
        require(not item.is_symlink(), 'Final task contains a symlink')
        if item.is_file():
            task_files[str(item.relative_to(TASK))] = digest(item)
    require(task_files == task['task_file_sha256'], 'Final task bytes changed')
    verifier = module('confirmed_controls_packager_validator', ROOT / 'scripts/package-takehome-evidence.py')
    verifier.verify_revision_controls(ref, task, task_ref, set())
    runs = controls['source_runs']
    kinds = {r['kind'] for r in runs}
    require(len(runs) == len(kinds) and {'original_controls', 'oracle_confirmation'} <= kinds
            and kinds <= {'original_controls', 'oracle_confirmation', 'nop_confirmation',
                          'independent_nop_validation', 'independent_oracle_validation'}
            and not {'nop_confirmation', 'independent_nop_validation'} <= kinds,
            'Both distinct original and confirmation control runs are required; optional nop confirmation must be distinct')
    sources = {}
    for row in runs:
        folder = BASE / {'original_controls': 'harbor-controls-final',
                         'oracle_confirmation': 'harbor-oracle-confirmation-001',
                         'nop_confirmation': 'harbor-nop-confirmation-001',
                         'independent_nop_validation': 'nop-independent-validation-001',
                         'independent_oracle_validation': 'oracle-independent-validation-001'}[row['kind']]
        require(path(row['summary']['path']) == folder / 'summary.json'
                and path(row['run_identity']['path']) == folder / 'run-identity.json',
                'Source run must use its original evidence directory')
        run = bound(row['summary'])
        identity = bound(row['run_identity'])
        require(run['finished_at'] and run['model_calls'] == 0 and run['inputs_unchanged'] is True
                and run['tooling_unchanged'] is True, 'Source control run is not terminal and unchanged')
        require(row['run_identity'] == dict(path=run['run_identity'], sha256=run['run_identity_sha256']),
                'Source control identity reference differs')
        require(identity['task'] == task['task'] and identity['task_checksum'] == TASK_CHECKSUM
                and identity['inputs']['task_file_sha256'] == task['task_file_sha256']
                and identity['inputs']['task_manifest_sha256'] == TASK_MANIFEST_SHA, 'Source run used another task')
        sources[row['kind']] = (row, run)
    original_ref, original = sources['original_controls']
    confirmation_ref, confirmation = sources['oracle_confirmation']
    require(original['passed'] is False and original['controls']['oracle']['passed'] is False,
            'Original failed oracle must remain failed')
    selected_oracle = confirmation['controls']['oracle']
    if confirmation['passed'] is False:
        require(selected_oracle['passed'] is False and 'independent_oracle_validation' in sources,
                'Failed confirmation wrapper requires explicit independent raw oracle validation')
        oracle_ref, oracle_certificate = sources['independent_oracle_validation']
        require(oracle_certificate['passed'] is True and oracle_certificate['controls']['oracle']['passed'] is True,
                'Independent confirmation oracle validation did not pass')
        independent_oracle(oracle_certificate, bound(oracle_ref['run_identity']), confirmation_ref, confirmation, task)
        selected_oracle = oracle_certificate['controls']['oracle']
    else:
        require(confirmation['passed'] is True and selected_oracle['passed'] is True
                and 'independent_oracle_validation' not in sources, 'Replacement oracle control did not pass')
    selected_nop = original['controls']['nop']
    if selected_nop['passed'] is False:
        nop_kind = 'independent_nop_validation' if 'independent_nop_validation' in sources else 'nop_confirmation'
        require(nop_kind in sources and 'superseded_nop' in controls,
                'Failed original nop requires a separate validated normal nop and preserved failure evidence')
        nop_confirmation = sources[nop_kind][1]
        require(nop_confirmation['passed'] is True and nop_confirmation['controls']['nop']['passed'] is True,
                'Replacement nop control did not pass')
        selected_nop = nop_confirmation['controls']['nop']
        preserved_failure('nop', controls['superseded_nop'], original_ref, original)
        if nop_kind == 'independent_nop_validation':
            independent_nop(nop_confirmation, bound(sources[nop_kind][0]['run_identity']),
                            original_ref, original, controls['superseded_nop'])
    else:
        require(selected_nop['passed'] is True and not {'nop_confirmation', 'independent_nop_validation'} & kinds
                and 'superseded_nop' not in controls,
                'A passed original nop does not authorize another nop selection')
    require(controls['controls']['oracle'] == selected_oracle
            and controls['controls']['nop'] == selected_nop, 'Combined control rows must be exact source-run records')
    superseded = controls['superseded_oracle']
    preserved_failure('oracle', superseded, original_ref, original)
    prior = controls['prior_continuation']
    prior_summary = bound(prior['summary'])
    require(path(prior['summary']['path']) == BASE / 'continuation-001/summary.json'
            and prior_summary['finished_at'] == prior['finished_at'] and prior['finished_at']
            and prior_summary['passed'] is prior['passed'] is False
            and prior_summary['stages'] == prior['stages'] == {}, 'Prior continuation must terminate before launching any stage')
    require(all(digest(name) == expected for name, expected in prior_summary['source_sha256'].items()),
            'A frozen prior continuation helper changed')
    comparison = controls['timing_comparison']
    validate_comparison(comparison, superseded['result'])
    oracle = controls['controls']['oracle']
    image = bound(oracle['verifier_image_proof'])
    require(image['passed'] is True and image['kind'] == 'normal_harbor_oracle_verifier_image'
            and image['oracle_result_path'] == oracle['result'] and image['oracle_result_sha256'] == oracle['result_sha256']
            and image['final_task_checksum'] == TASK_CHECKSUM, 'Use the replacement oracle own verifier-image proof')
    inspection = bound(dict(path=image['raw_docker_inspection_path'], sha256=image['raw_docker_inspection_sha256']))
    require(inspection['Image'] == image['verifier_image_id'] and inspection['HostConfig'] == image['observed_host_config']
            and inspection['Config.Labels']['com.docker.compose.project']
            == Path(oracle['result']).parent.name.lower() + '__verifier__trial', 'Replacement oracle image inspection differs')
    host = image['observed_host_config']
    require(host['NanoCpus'] == 4000000000 and host['Memory'] == 8192 * 1024 * 1024
            and host['NetworkMode'] == 'none' and type(host['MemorySwap']) is int
            and host['StorageOpt'] in (None, {})
            and re.fullmatch(r'sha256:[0-9a-f]{64}', image['verifier_image_id']),
            'Replacement oracle resources changed or image identity is incomplete')
    return dict(controls_reference=ref, task=task, task_manifest=task_ref,
                image_proof=oracle['verifier_image_proof'], verifier_image_id=image['verifier_image_id'])

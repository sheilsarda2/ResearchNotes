"""Isolated recovery-gate and sequencing regressions; never run real children.

Only the established packager control validator is stubbed for compact schema
fixtures. Its comprehensive artifact checks have their own existing tests.
"""
import copy
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


c = load('recovery_common_tests', 'recovery_common.py')
with patch.dict(sys.modules, {'recovery_common': c}):
    coordinator = load('confirmed_coordinator_tests', 'continue_confirmed_validation.py')
    quality = load('confirmed_quality_tests', 'run_confirmed_quality_review.py')


class FakeChild:
    def __init__(self, pid, code):
        self.pid = pid
        self.code = code
        self.returncode = None
        self.waited = False

    def poll(self):
        return self.returncode

    def wait(self):
        self.waited = True
        self.returncode = self.code
        return self.code


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'research/followup'
        self.here = self.base / 'controls-confirmed-001-tooling'
        self.here.mkdir(parents=True)
        task = self.base / 'rs-zenoh-timestamp-instrumentation-v4-validation'
        values = dict(ROOT=self.root, BASE=self.base, HERE=self.here, TASK=task,
            REVIEW_OUTPUT=self.base / 'reviews-final-001', AUDIT=self.base / 'completed-review-audits' / (task.name + '.json'),
            DIAGNOSTICS=self.base / 'harness/diagnostics-final', PAIRED=self.base / 'paired-regrades/run-001',
            OUTPUT=self.base / 'continuation-confirmed-001', OBSERVER=self.base / 'observer.py', SOURCE_NAMES=('frozen.py',))
        self.patches = [patch.object(c, key, value) for key, value in values.items()]
        # The new first-review exception has separate real-artifact regressions.
        # Keep these existing control/sequence fixtures independent of that input.
        self.patches.append(patch.object(c, 'prior_review_exception', return_value={
            'prior_review': self.base / 'reviews-authorized-first'}))
        for current in self.patches:
            current.start()
        self.write(self.base / 'frozen.py', b'unchanged fixture source\n')
        self.write(self.root / 'scripts/package-takehome-evidence.py', b'fixture validator source\n')
        self.write(task / 'instruction.md', b'fixture instruction\n')
        task_ref = self.write(self.base / 'task-manifest.json', dict(task=str(task.relative_to(self.root)),
            harbor_task_checksum=c.TASK_CHECKSUM, task_file_sha256={'instruction.md':c.digest(task / 'instruction.md')}))
        self.patches.append(patch.object(c, 'TASK_MANIFEST_SHA', task_ref['sha256']))
        self.patches[-1].start()
        self.task = c.read(task_ref['path'])
        self.write(self.here / 'source-manifest.json', dict(kind='confirmed_controls_recovery_tooling', launches=0,
            source_sha256={str(p.relative_to(self.root)):c.digest(p) for p in c.source_paths()}))
        self.controls_path = self.base / 'controls-confirmed-001/summary.json'
        self.controls = self.controls_fixture(task_ref)
        self.seal()
        self.validation_calls = []
        self.actual_module = c.module
        def module(name, path):
            if name == 'confirmed_controls_packager_validator':
                def validate(reference, task, task_reference, supporting):
                    self.validation_calls.append(reference)
                    value = c.bound(reference)
                    c.require(value['passed'] is True and set(value['controls']) == {'oracle','nop'}, 'Fixture control proof pending')
                return types.SimpleNamespace(verify_revision_controls=validate)
            return self.actual_module(name, path)
        self.patches.append(patch.object(c, 'module', side_effect=module))
        self.patches[-1].start()

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        self.temp.cleanup()

    def write(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True)+'\n').encode())
        return c.reference(path)

    def seal(self):
        self.ref = self.write(self.controls_path, self.controls)

    def controls_fixture(self, task_ref):
        old = self.base / 'harbor-controls-final'
        new = self.base / 'harbor-oracle-confirmation-001'
        identity = dict(task=self.task['task'], task_checksum=c.TASK_CHECKSUM,
            inputs=dict(task_file_sha256=self.task['task_file_sha256'], task_manifest_sha256=task_ref['sha256']))
        old_identity = self.write(old / 'run-identity.json', identity)
        new_identity = self.write(new / 'run-identity.json', identity)
        old_result = self.write(old / 'jobs/old-oracle/oldtrial/result.json', dict(task_checksum=c.TASK_CHECKSUM,
            config=dict(agent=dict(name='oracle')), finished_at='2026-09-14T01:00:00Z', exception_info=None,
            verifier_result=dict(rewards=dict(reward=0))))
        old_score = self.write(old / 'jobs/old-oracle/oldtrial/verifier/score.json', dict(reward=0))
        new_result = self.write(new / 'jobs/new-oracle/newtrial/result.json', dict(fixture='new oracle result'))
        host = dict(NanoCpus=4000000000, Memory=8192*1024*1024, MemorySwap=16384*1024*1024,
                    NetworkMode='none', StorageOpt=None)
        inspected = self.write(new / 'inspection.json', dict(Image='sha256:'+'b'*64, HostConfig=host,
            **{'Config.Labels': {'com.docker.compose.project':'newtrial__verifier__trial'}}))
        image = self.write(new / 'verifier-image-proof.json', dict(passed=True, kind='normal_harbor_oracle_verifier_image',
            oracle_result_path=new_result['path'], oracle_result_sha256=new_result['sha256'],
            final_task_checksum=c.TASK_CHECKSUM, verifier_image_id='sha256:'+'b'*64, observed_host_config=host,
            raw_docker_inspection_path=inspected['path'], raw_docker_inspection_sha256=inspected['sha256']))
        old_oracle = dict(job='old-oracle', passed=False, observation_errors=[dict(error_type='FileNotFoundError',error='fixture missing state')])
        nop = dict(passed=True, kind='fixture original nop')
        oracle = dict(passed=True, result=new_result['path'], result_sha256=new_result['sha256'], verifier_image_proof=image)
        common = dict(finished_at='2026-09-14T02:00:00Z', model_calls=0, inputs_unchanged=True, tooling_unchanged=True)
        old_ref = self.write(old / 'summary.json', dict(common, passed=False, controls=dict(oracle=old_oracle,nop=nop),
            run_identity=old_identity['path'], run_identity_sha256=old_identity['sha256']))
        new_ref = self.write(new / 'summary.json', dict(common, passed=True, controls=dict(oracle=oracle),
            run_identity=new_identity['path'], run_identity_sha256=new_identity['sha256']))
        previous = dict(finished_at='2026-09-14T02:00:01Z', passed=False, stages={},
            source_sha256={str((self.base/'frozen.py').relative_to(self.root)):c.digest(self.base/'frozen.py')})
        prior_ref = self.write(self.base / 'continuation-001/summary.json', previous)
        comparison_root = self.base / 'comparison'
        source_map = {'zenoh/src/session.rs': 'a'*64}
        rows = []
        for variant in ('untouched_gold', 'corrected_reference'):
            for repeat in (1, 2, 3):
                passed = variant == 'corrected_reference' or repeat == 3
                content = 'running 1 test\ntest scouting_delay_regression ... ' + ('ok\n' if passed else 'FAILED\nexpected <400ms\n')
                log = self.write(comparison_root / 'case' / (variant + '-' + str(repeat) + '.log'), content.encode())
                rows.append(dict(variant=variant,repeat=repeat,log=Path(log['path']).name,
                    log_sha256=log['sha256'],test_passed=passed,returncode=0 if passed else 101))
            for phase in ('before', 'after'):
                self.write(comparison_root / 'case' / (variant + '-source-' + phase + '.json'), source_map)
        self.write(comparison_root / 'case/comparison.json',dict(cases=rows,complete=True,
            fixed_repeats_per_source=3,model_calls=0,full_regrade=False,reward=None,tests_unchanged=True))
        comparison = self.write(comparison_root / 'result.json',dict(complete=True,model_calls=0,
            finished_at='2026-09-14T02:00:00Z',container_absent=True,claim_absent=True,inputs_unchanged=True,
            cleanup_errors=[],test_outcomes=rows))
        inputs = self.write(comparison_root / 'inputs.json',dict(kind='fixed_unchanged_timing_comparison',
            task_checksum=c.TASK_CHECKSUM,task_manifest_sha256=task_ref['sha256'],model_calls=0,full_regrade=False,
            variant_order=['untouched_gold','corrected_reference'],repeats=3,
            failed_oracle_result=old_result['path'],failed_oracle_result_sha256=old_result['sha256'],
            gold_source_files=source_map,corrected_source_files=source_map))
        decision = self.write(self.base / 'comparison/decision.json', dict(kind='reviewed_timing_comparison_decision',
            task_checksum=c.TASK_CHECKSUM, single_full_oracle_confirmation_authorized=True,model_calls=0,
            raw_original_oracle_preserved=True, interpretation='Synthetic mixed timing result justifies one confirmation only.', comparison_result=comparison,comparison_inputs=inputs))
        return dict(passed=True, controls=dict(oracle=oracle,nop=nop), source_runs=[
            dict(kind='original_controls',summary=old_ref,run_identity=old_identity),
            dict(kind='oracle_confirmation',summary=new_ref,run_identity=new_identity)],
            superseded_oracle=dict(result=old_result,score=old_score,reward=0,exception=None,
                observation_errors=old_oracle['observation_errors'],reason='Preserve genuine timing failure and observer error'),
            prior_continuation=dict(summary=prior_ref,finished_at=previous['finished_at'],passed=False,stages={}),
            timing_comparison=dict(result=comparison,inputs=inputs,decision=decision))

    def gate(self):
        return c.combined_controls(self.controls_path, self.ref['sha256'])

    def failed_nop_fixture(self, *, confirmation=True):
        source = next(row for row in self.controls['source_runs'] if row['kind'] == 'original_controls')
        old = c.bound(source['summary'])
        old['controls']['nop'] = dict(passed=False, job='old-nop', observation_errors=[
            dict(error_type='FileNotFoundError', error='fixture nop observer failure')])
        source['summary'] = self.write(c.path(source['summary']['path']), old)
        raw = self.write(self.base/'harbor-controls-final/jobs/old-nop/oldnop/result.json',
            dict(task_checksum=c.TASK_CHECKSUM, config=dict(agent=dict(name='nop')),
                 finished_at='2026-09-14T02:00:00Z', exception_info=None,
                 verifier_result=dict(rewards=dict(reward=0))))
        score = self.write(c.path(raw['path']).parent/'verifier/score.json', dict(reward=0))
        self.controls['superseded_nop'] = dict(result=raw, score=score, reward=0, exception=None,
            observation_errors=old['controls']['nop']['observation_errors'], reason='Preserve failed original nop observer proof')
        if confirmation:
            folder = self.base/'harbor-nop-confirmation-001'
            identity = self.write(folder/'run-identity.json', c.bound(source['run_identity']))
            result = self.write(folder/'jobs/new-nop/newnop/result.json', dict(fixture='new normal nop'))
            row = dict(passed=True, result=result['path'], result_sha256=result['sha256'], reward=0)
            summary = self.write(folder/'summary.json', dict(passed=True, finished_at='2026-09-14T04:00:00Z',
                model_calls=0, inputs_unchanged=True, tooling_unchanged=True, controls=dict(nop=row),
                run_identity=identity['path'], run_identity_sha256=identity['sha256']))
            self.controls['source_runs'].append(dict(kind='nop_confirmation', summary=summary, run_identity=identity))
            self.controls['controls']['nop'] = row
        self.seal()

    def test_failed_original_nop_cannot_launch_without_separate_confirmation(self):
        self.failed_nop_fixture(confirmation=False)
        with patch.object(coordinator.subprocess, 'Popen') as popen, self.assertRaisesRegex(ValueError, 'separate validated normal nop'):
            coordinator.run_once(self.controls_path, self.ref['sha256'])
        popen.assert_not_called()
        self.assertFalse(c.OUTPUT.exists())

    def test_both_original_failures_preserved_with_separate_normal_controls(self):
        self.failed_nop_fixture()
        gate = self.gate()
        self.assertEqual(gate['controls_reference'], self.ref)
        original = c.bound(self.controls['source_runs'][0]['summary'])
        self.assertFalse(original['controls']['oracle']['passed'])
        self.assertFalse(original['controls']['nop']['passed'])
        self.assertEqual(c.bound(self.controls['superseded_nop']['result'])['verifier_result']['rewards']['reward'], 0)
        self.assertEqual(len(self.controls['source_runs']), 3)

    def test_new_nop_selection_cannot_omit_original_observer_error(self):
        self.failed_nop_fixture()
        self.controls['superseded_nop']['observation_errors'] = []
        self.seal()
        with self.assertRaisesRegex(ValueError, 'observation error.*nop'):
            self.gate()

    def test_new_nop_summary_must_have_passed_its_own_checks(self):
        self.failed_nop_fixture()
        source = self.controls['source_runs'][-1]
        summary = c.bound(source['summary']); summary['passed'] = False
        source['summary'] = self.write(c.path(source['summary']['path']), summary)
        self.seal()
        with self.assertRaisesRegex(ValueError, 'Replacement nop control did not pass'):
            self.gate()

    def test_new_nop_selected_row_must_match_source_run(self):
        self.failed_nop_fixture()
        self.controls['controls']['nop']['unbound_field'] = 'changed'
        self.seal()
        with self.assertRaisesRegex(ValueError, 'exact source-run records'):
            self.gate()

    def test_quality_import_failure_records_terminal_and_never_retries(self):
        original_module = c.module
        def broken_import(name, source):
            if name == 'confirmed_quality_legacy_validation':
                raise ImportError('synthetic helper import failure')
            return original_module(name, source)
        with patch.object(c, 'module', side_effect=broken_import), patch.object(quality.subprocess, 'Popen') as popen:
            self.assertEqual(quality.run_once(self.controls_path, self.ref['sha256']), 1)
            popen.assert_not_called()
        record = c.read(c.REVIEW_OUTPUT/'summary.json')
        self.assertFalse(record['passed']); self.assertTrue(record['finished_at'])
        self.assertEqual(record['reviews'][c.TASK.name]['error_type'], 'ImportError')
        with patch.object(quality.subprocess, 'Popen') as popen, self.assertRaises(ValueError):
            quality.run_once(self.controls_path, self.ref['sha256'])
        popen.assert_not_called()

    def independent_nop_fixture(self):
        self.failed_nop_fixture(confirmation=False)
        source = self.controls['source_runs'][0]
        source_ref = {key:source[key] for key in ('summary','run_identity')}
        original = c.bound(source['summary'])
        old = c.path(source['summary']['path']).parent
        folder = self.base/'nop-independent-validation-001'
        raw = self.controls['superseded_nop']['result']
        observations = [dict(at='2026-09-14T02:00:00Z',total_claims=1,shared_max_active=14,
            own_participants={'123:456':dict(trials={'trial':{}})}),
            dict(at='2026-09-14T02:01:00Z',total_claims=0,shared_max_active=14,own_participants={})]
        log = self.write(old/'nop-admission-observations.jsonl',
            ''.join(json.dumps(row)+'\n' for row in observations).encode())
        lifecycle = dict(log, sample_count=2, peak_own_claims=1, final_own_claims=0,
                         shared_cap_respected=True, global_cap=14, passed=True)
        nop = dict(passed=True, result=raw['path'], result_sha256=raw['sha256'], reward=0, admission_lifecycle=lifecycle)
        sample = dict(at='2026-09-14T03:00:00Z',terminal_sample=True,own_participants={},runner_pid=123,
                      total_claims=0,shared_max_active=14)
        sample_ref = self.write(folder/'terminal-observation.jsonl', sample)
        terminal_ref = self.write(folder/'terminal-absence.json',dict(kind='fresh_locked_terminal_absence',
            shared_observation=sample_ref, terminal_sample=sample, historical_runner=dict(pid=123,start_ticks='456',participant_key='123:456'),
            observed_runner_start_ticks=None, original_runner_absent=True, matching_containers=[],
            container_absent=True, claim_absent=True, trial_name_match='oldnop'))
        identity = c.bound(source['run_identity'])
        identity.update(kind='independent_original_nop_revalidation',new_control_runs=0,model_calls=0,source_run=source_ref)
        identity_ref = self.write(folder/'run-identity.json',identity)
        files = {str(item.relative_to(old)):c.digest(item) for item in old.rglob('*') if item.is_file()}
        certificate = dict(kind='independent_original_nop_validation',execution_kind='independent_revalidation_of_existing_normal_nop',
            passed=True,finished_at='2026-09-14T04:00:00Z',model_calls=0,new_control_runs=0,inputs_unchanged=True,tooling_unchanged=True,
            run_identity=identity_ref['path'],run_identity_sha256=identity_ref['sha256'],source_run=source_ref,
            controls=dict(nop=nop),original_nop_record=original['controls']['nop'],
            original_observation_errors=original['controls']['nop']['observation_errors'],
            original_run_file_sha256_before=files,original_run_file_sha256_after=files,terminal_absence=terminal_ref)
        summary_ref = self.write(folder/'summary.json',certificate)
        self.controls['source_runs'].append(dict(kind='independent_nop_validation',summary=summary_ref,run_identity=identity_ref))
        self.controls['controls']['nop'] = nop
        self.seal()

    def test_independent_nop_reuses_exact_original_raw_execution_with_fresh_absence(self):
        self.independent_nop_fixture()
        self.gate()
        self.assertEqual(self.controls['controls']['nop']['result'],self.controls['superseded_nop']['result']['path'])
        certificate = c.bound(self.controls['source_runs'][-1]['summary'])
        self.assertEqual(certificate['new_control_runs'],0)
        self.assertFalse(certificate['original_nop_record']['passed'])

    def test_independent_nop_rejects_raw_evidence_drift(self):
        self.independent_nop_fixture()
        self.write(self.base/'harbor-controls-final/unexpected-new-file',b'drift')
        with self.assertRaisesRegex(ValueError,'Original run evidence changed'):
            self.gate()

    def test_independent_nop_rejects_changed_original_failed_record(self):
        self.independent_nop_fixture()
        source = self.controls['source_runs'][-1]; certificate=c.bound(source['summary'])
        certificate['original_nop_record']['passed']=True
        source['summary']=self.write(c.path(source['summary']['path']),certificate);self.seal()
        with self.assertRaisesRegex(ValueError,'preserve its failed original'):
            self.gate()

    def test_independent_nop_rejects_claimed_terminal_absence_with_live_claim(self):
        self.independent_nop_fixture()
        source=self.controls['source_runs'][-1];certificate=c.bound(source['summary'])
        terminal=c.bound(certificate['terminal_absence']);sample=c.bound(terminal['shared_observation'])
        sample['own_participants']={'123:456':dict(trials={'trial':{}})}
        terminal['shared_observation']=self.write(c.path(terminal['shared_observation']['path']),sample)
        terminal['terminal_sample']=sample
        certificate['terminal_absence']=self.write(c.path(certificate['terminal_absence']['path']),terminal)
        source['summary']=self.write(c.path(source['summary']['path']),certificate);self.seal()
        with self.assertRaisesRegex(ValueError,'fresh runner/container/claim absence'):
            self.gate()

    def independent_oracle_fixture(self):
        self.independent_nop_fixture()
        source=self.controls['source_runs'][1];summary=c.bound(source['summary'])
        old=c.path(source['summary']['path']).parent;folder=self.base/'oracle-independent-validation-001'
        capture_folder=self.base/'oracle-image-rebinding-001'
        selected=copy.deepcopy(summary['controls']['oracle'])
        result=self.write(c.path(selected['result']),dict(task_checksum=c.TASK_CHECKSUM,
            config=dict(agent=dict(name='oracle')),finished_at='2026-09-14T02:00:00Z',exception_info=None,
            verifier_result=dict(rewards=dict(reward=1))))
        self.write(c.path(result['path']).parent/'verifier/score.json',dict(reward=1))
        selected.update(result_sha256=result['sha256'],reward=1)
        errors=[dict(at='2026-09-14T01:59:00Z',error_type='AssertionError',
                     error='Private tag already identifies a different image',terminal=False)]
        old_record=dict(passed=False,job='new-oracle',exit_code=0,error_type='AssertionError',error='',
            observation=dict(exit_code=0,terminal_sample_valid=True,terminal_sample_at='2026-09-14T02:01:00Z',errors=errors))
        summary.update(kind='single_full_oracle_confirmation',passed=False,decision_unchanged=True,controls=dict(oracle=old_record))
        source['summary']=self.write(c.path(source['summary']['path']),summary)
        source_ref={key:source[key] for key in ('summary','run_identity')}
        samples=[dict(at='2026-09-14T01:59:00Z',runner_pid=666,total_claims=1,shared_max_active=14,
                      own_participants={'666:777':dict(trials={'newtrial':{}})}),
                 dict(at='2026-09-14T02:01:00Z',runner_pid=666,total_claims=0,shared_max_active=14,
                      own_participants={},terminal_sample=True)]
        admission=self.write(old/'oracle-admission-observations.jsonl',
            ''.join(json.dumps(row)+'\n' for row in samples).encode())
        selected['admission_lifecycle']=dict(admission,sample_count=2,peak_own_claims=1,final_own_claims=0,
            shared_cap_respected=True,global_cap=14,passed=True)
        terminal_sample=dict(at='2026-09-14T03:00:00Z',runner_pid=666,total_claims=0,shared_max_active=14,
                             own_participants={},terminal_sample=True)
        terminal_sample_ref=self.write(folder/'terminal-observation.jsonl',terminal_sample)
        terminal_ref=self.write(folder/'terminal-absence.json',dict(kind='fresh_locked_terminal_absence',
            shared_observation=terminal_sample_ref,terminal_sample=terminal_sample,
            historical_runner=dict(pid=666,start_ticks='777',participant_key='666:777'),
            observed_runner_start_ticks=None,original_runner_absent=True,matching_containers=[],
            container_absent=True,claim_absent=True,trial_name_match='newtrial'))
        image=c.bound(selected['verifier_image_proof']);image.update(oracle_result_sha256=result['sha256'],
            private_tag='confirmation:newtrial')
        image_ref=self.write(folder/'verifier-image-proof.json',image);selected['verifier_image_proof']=image_ref
        tests_ref=self.write(capture_folder/'actual-test-file-sha256.json',
            {name:value for name,value in self.task['task_file_sha256'].items() if name.startswith('tests/')})
        layers_ref=self.write(capture_folder/'image-layer-comparison.json',dict(images=dict(original='sha256:'+'a'*64,
            confirmation=image['verifier_image_id']),equal_rootfs_layers=True,rootfs_layers=dict(original=['layer'],confirmation=['layer'])))
        helper_ref=self.write(capture_folder/'capture_image.py',b'fixture image capture helper')
        capture_ref=self.write(capture_folder/'capture.json',dict(kind='actual_normal_oracle_confirmation_image_capture',
            passed=True,model_calls=0,new_control_runs=0,task_checksum=c.TASK_CHECKSUM,
            task_manifest=c.reference(self.base/'task-manifest.json'),run_identity=source['run_identity'],
            oracle_trial=str(c.path(result['path']).parent.relative_to(self.root)),
            verifier_image_id=image['verifier_image_id'],private_tag=image['private_tag'],
            prior_private_tag='original:unchanged',prior_image_id='sha256:'+'a'*64,prior_tag_unchanged=True,
            filtered_inspection=dict(path=image['raw_docker_inspection_path'],sha256=image['raw_docker_inspection_sha256']),
            actual_test_files=tests_ref,image_layers=layers_ref,helper=helper_ref))
        owner_ref=self.write(capture_folder/'controller-owner.json',dict(kind='observed_existing_oracle_confirmation_controller',
            run_identity_sha256=source['run_identity']['sha256'],pid=444,start_ticks='555'))
        identity=c.bound(source['run_identity']);identity.update(kind='independent_normal_oracle_confirmation_revalidation',
            new_control_runs=0,model_calls=0,source_run=source_ref,original_controller=owner_ref)
        identity_ref=self.write(folder/'run-identity.json',identity)
        files={str(item.relative_to(old)):c.digest(item) for item in old.rglob('*') if item.is_file()}
        certificate=dict(kind='independent_normal_oracle_confirmation_validation',
            execution_kind='independent_revalidation_of_existing_normal_oracle_confirmation',passed=True,
            finished_at='2026-09-14T04:00:00Z',model_calls=0,new_control_runs=0,inputs_unchanged=True,tooling_unchanged=True,
            run_identity=identity_ref['path'],run_identity_sha256=identity_ref['sha256'],source_run=source_ref,
            original_control_record=old_record,original_observation_errors=errors,controls=dict(oracle=selected),
            original_run_file_sha256_before=files,original_run_file_sha256_after=files,terminal_absence=terminal_ref,
            original_controller_absence=dict(controller=owner_ref,observed_start_ticks=None,absent=True,at='2026-09-14T03:00:00Z'),
            image_capture=capture_ref)
        certificate_ref=self.write(folder/'summary.json',certificate)
        self.controls['source_runs'].append(dict(kind='independent_oracle_validation',summary=certificate_ref,run_identity=identity_ref))
        self.controls['controls']['oracle']=selected;self.seal()

    def rewrite_oracle_certificate(self, transform):
        source=self.controls['source_runs'][-1];certificate=c.bound(source['summary'])
        transform(certificate)
        source['summary']=self.write(c.path(source['summary']['path']),certificate);self.seal()

    def test_independent_oracle_preserves_failed_wrapper_and_selects_same_raw_one(self):
        self.independent_oracle_fixture()
        gate=self.gate()
        original=c.bound(self.controls['source_runs'][1]['summary'])
        self.assertFalse(original['passed']);self.assertFalse(original['controls']['oracle']['passed'])
        certificate=c.bound(self.controls['source_runs'][-1]['summary'])
        self.assertEqual(certificate['new_control_runs'],0)
        self.assertEqual(gate['image_proof'],certificate['controls']['oracle']['verifier_image_proof'])
        self.assertEqual(len(self.controls['source_runs']),4)

    def test_failed_confirmation_cannot_launch_without_independent_oracle(self):
        self.independent_oracle_fixture();self.controls['source_runs'].pop();self.seal()
        with patch.object(coordinator.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'explicit independent raw oracle'):
            coordinator.run_once(self.controls_path,self.ref['sha256'])
        popen.assert_not_called()

    def test_independent_oracle_cannot_hide_original_pin_error(self):
        self.independent_oracle_fixture()
        self.rewrite_oracle_certificate(lambda certificate:certificate.update(original_observation_errors=[]))
        with self.assertRaisesRegex(ValueError,'exact image-pin callback errors'):self.gate()

    def change_oracle_raw_result(self, update):
        certificate=c.bound(self.controls['source_runs'][-1]['summary'])
        selected=certificate['controls']['oracle'];raw=c.read(selected['result']);update(raw)
        result=self.write(c.path(selected['result']),raw)
        selected['result_sha256']=result['sha256']
        self.controls['controls']['oracle']=selected
        self.rewrite_oracle_certificate(lambda value:value.update(controls=dict(oracle=selected)))

    def test_independent_oracle_cannot_certify_raw_reward_zero(self):
        self.independent_oracle_fixture()
        self.change_oracle_raw_result(lambda raw:raw['verifier_result']['rewards'].update(reward=0))
        with self.assertRaisesRegex(ValueError,'raw reward one with no exception'):self.gate()

    def test_independent_oracle_cannot_certify_raw_exception(self):
        self.independent_oracle_fixture()
        self.change_oracle_raw_result(lambda raw:raw.update(exception_info=dict(exception_type='SyntheticRuntimeError')))
        with self.assertRaisesRegex(ValueError,'raw reward one with no exception'):self.gate()

    def test_independent_oracle_rejects_unexpected_error_even_when_preserved(self):
        self.independent_oracle_fixture()
        source=self.controls['source_runs'][1];original=c.bound(source['summary'])
        original['controls']['oracle']['observation']['errors'][0]['error']='Unrelated infrastructure error'
        source['summary']=self.write(c.path(source['summary']['path']),original)
        def update(certificate):
            certificate['source_run']['summary']=source['summary']
            certificate['original_control_record']=original['controls']['oracle']
            certificate['original_observation_errors']=original['controls']['oracle']['observation']['errors']
        identity_ref=self.controls['source_runs'][-1]['run_identity'];identity=c.bound(identity_ref)
        identity['source_run']['summary']=source['summary']
        changed=self.write(c.path(identity_ref['path']),identity)
        self.controls['source_runs'][-1]['run_identity']=changed
        def update_identity(certificate):
            update(certificate);certificate['run_identity_sha256']=changed['sha256']
        self.rewrite_oracle_certificate(update_identity)
        with self.assertRaisesRegex(ValueError,'unexpected errors require review'):self.gate()

    def test_independent_oracle_rejects_missing_controller_absence(self):
        self.independent_oracle_fixture()
        self.rewrite_oracle_certificate(lambda certificate:certificate['original_controller_absence'].update(absent=False))
        with self.assertRaisesRegex(ValueError,'controller absence'):self.gate()

    def test_independent_oracle_cannot_claim_new_execution(self):
        self.independent_oracle_fixture()
        self.rewrite_oracle_certificate(lambda certificate:certificate.update(new_control_runs=1))
        with self.assertRaisesRegex(ValueError,'exact image-pin callback errors'):self.gate()

    def test_independent_oracle_rejects_task_test_map_drift(self):
        self.independent_oracle_fixture()
        source=self.controls['source_runs'][-1];certificate=c.bound(source['summary']);capture=c.bound(certificate['image_capture'])
        capture['actual_test_files']=self.write(c.path(capture['actual_test_files']['path']),{'tests/unrelated.rs':'0'*64})
        changed=self.write(c.path(certificate['image_capture']['path']),capture)
        self.rewrite_oracle_certificate(lambda value:value.update(image_capture=changed))
        with self.assertRaisesRegex(ValueError,'different task tests'):self.gate()

    def test_independent_oracle_rejects_repointed_original_image_tag(self):
        self.independent_oracle_fixture()
        certificate=c.bound(self.controls['source_runs'][-1]['summary']);capture=c.bound(certificate['image_capture'])
        capture['prior_tag_unchanged']=False
        changed=self.write(c.path(certificate['image_capture']['path']),capture)
        self.rewrite_oracle_certificate(lambda value:value.update(image_capture=changed))
        with self.assertRaisesRegex(ValueError,'without repointing the old tag'):self.gate()

    def test_complete_proof_keeps_original_failure_and_new_image(self):
        gate = self.gate()
        self.assertEqual(gate['controls_reference'], self.ref)
        self.assertEqual(gate['image_proof'], self.controls['controls']['oracle']['verifier_image_proof'])
        self.assertEqual(c.bound(self.controls['superseded_oracle']['result'])['verifier_result']['rewards']['reward'], 0)
        self.assertEqual(len(self.validation_calls), 1)

    def test_pending_wrong_path_or_wrong_digest_cannot_launch(self):
        attempts=[(self.base/'wrong/summary.json',self.ref['sha256']), (self.controls_path,'0'*64)]
        self.controls['passed']=False;self.seal();attempts.append((self.controls_path,self.ref['sha256']))
        for path,checksum in attempts:
            with self.subTest(path=path,checksum=checksum), patch.object(coordinator.subprocess,'Popen') as popen:
                with self.assertRaises((ValueError,FileNotFoundError)):
                    coordinator.run_once(path,checksum)
                popen.assert_not_called()
                self.assertFalse(c.OUTPUT.exists())

    def test_quality_wrapper_pending_gate_cannot_launch(self):
        self.controls['passed']=False;self.seal()
        with patch.object(quality.subprocess,'Popen') as popen, self.assertRaises(ValueError):
            quality.run_once(self.controls_path,self.ref['sha256'])
        popen.assert_not_called();self.assertFalse(c.REVIEW_OUTPUT.exists())

    def test_canonical_outputs_and_prior_alternate_attempt_reject(self):
        for target in (c.REVIEW_OUTPUT,c.AUDIT,c.DIAGNOSTICS,c.PAIRED,c.OUTPUT):
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text('existing')
            try:
                with self.subTest(target=target), self.assertRaises(ValueError):c.fresh_outputs()
            finally:target.unlink()
        self.write(self.base/'reviews-other/jobs/unrelated/attempt/config.json',dict(task=dict(path='/old/'+c.TASK.name)))
        with self.assertRaisesRegex(ValueError,'prior final-task quality attempt'):c.fresh_outputs()

    def test_stale_audit_prevents_quality_popen(self):
        self.write(c.AUDIT,dict(valid_review_report=True))
        with patch.object(quality.subprocess,'Popen') as popen,self.assertRaises(ValueError):
            quality.run_once(self.controls_path,self.ref['sha256'])
        popen.assert_not_called()

    def test_original_reward_zero_cannot_be_relabelled(self):
        self.controls['superseded_oracle']['reward']=1;self.seal()
        with self.assertRaisesRegex(ValueError,'reward-zero'):self.gate()

    def test_original_observer_error_cannot_be_hidden(self):
        self.controls['superseded_oracle']['observation_errors']=[];self.seal()
        with self.assertRaisesRegex(ValueError,'observation error'):self.gate()

    def test_combined_rows_must_match_source_runs(self):
        self.controls['controls']['nop']=dict(passed=True,kind='different nop');self.seal()
        with self.assertRaisesRegex(ValueError,'exact source-run records'):self.gate()

    def test_source_run_identity_wrong_checksum_rejected(self):
        source=self.controls['source_runs'][1];identity=c.bound(source['run_identity']);identity['task_checksum']='wrong'
        source['run_identity']=self.write(c.path(source['run_identity']['path']),identity)
        run=c.bound(source['summary']);run['run_identity_sha256']=source['run_identity']['sha256']
        source['summary']=self.write(c.path(source['summary']['path']),run);self.seal()
        with self.assertRaisesRegex(ValueError,'another task'):self.gate()

    def test_duplicate_source_kind_rejected(self):
        self.controls['source_runs'][1]['kind']='original_controls';self.seal()
        with self.assertRaisesRegex(ValueError,'Both distinct'):self.gate()

    def test_old_image_proof_cannot_replace_new_oracle_image(self):
        image=self.controls['controls']['oracle']['verifier_image_proof'];value=c.bound(image)
        value['oracle_result_path']=self.controls['superseded_oracle']['result']['path']
        changed=self.write(c.path(image['path']),value)
        self.controls['controls']['oracle']['verifier_image_proof']=changed
        source=self.controls['source_runs'][1];run=c.bound(source['summary']);run['controls']['oracle']=self.controls['controls']['oracle']
        source['summary']=self.write(c.path(source['summary']['path']),run);self.seal()
        with self.assertRaisesRegex(ValueError,'own verifier-image proof'):self.gate()

    def test_previous_continuation_cannot_have_started_a_stage(self):
        prior=self.controls['prior_continuation'];summary=c.bound(prior['summary']);summary['stages']={'quality':{}}
        prior['summary']=self.write(c.path(prior['summary']['path']),summary);prior['stages']=summary['stages'];self.seal()
        with self.assertRaisesRegex(ValueError,'terminate before launching'):self.gate()

    def test_source_drift_fails_before_child(self):
        self.write(self.base/'frozen.py',b'changed fixture source')
        with patch.object(coordinator.subprocess,'Popen') as popen,self.assertRaisesRegex(ValueError,'source drift'):
            coordinator.run_once(self.controls_path,self.ref['sha256'])
        popen.assert_not_called()

    def test_task_drift_fails_before_child_even_if_manifest_is_unchanged(self):
        self.write(c.TASK/'instruction.md', b'changed delivered instruction')
        with patch.object(coordinator.subprocess, 'Popen') as popen, self.assertRaisesRegex(ValueError, 'Final task bytes changed'):
            coordinator.run_once(self.controls_path, self.ref['sha256'])
        popen.assert_not_called()

    def test_exact_controls_and_new_image_threaded_to_all_stages(self):
        gate=self.gate();stages=dict(coordinator.stage_commands(gate))
        for label in ('quality-review','focused-diagnostics','paired-file-gate','paired-full-regrades'):
            command=list(map(str,stages[label]));self.assertEqual(command[command.index('--controls-summary')+1],str(self.controls_path))
        for label in ('paired-file-gate','paired-full-regrades'):
            command=list(map(str,stages[label]));self.assertEqual(command[command.index('--verifier-image-proof')+1],str(c.path(gate['image_proof']['path'])))
            self.assertEqual(command[command.index('--verifier-image-id')+1],gate['verifier_image_id'])
        self.assertIn('--check-only',stages['paired-file-gate']);self.assertIn('--run',stages['paired-full-regrades'])

    def run_fake_sequence(self, fail_label=None, quality_check=None):
        self.write(c.BASE/'mutants/manifest.json',dict(mutants=[{}]*6))
        self.write(c.BASE/'saved-source-audit/full-source-preflight.json',dict(trials=[{}]*2))
        children=[];labels=[]
        expected=['quality-review','quality-evidence-audit','focused-diagnostics','paired-file-gate','paired-full-regrades']
        def popen(argv,**kwargs):
            self.assertTrue(all(ch.waited for ch in children),'Next stage started before previous child was reaped')
            label=expected[len(children)];labels.append(label)
            if label=='focused-diagnostics':
                self.write(c.DIAGNOSTICS/'summary.json',dict(passed=True,model_calls=0,counted_sweep_trials=0,
                    controls_summary_sha256=self.ref['sha256'],cases=[dict(kind='single_omission_probe')]*6+[dict(kind='saved_success_diagnostic')]*2))
            if label=='paired-full-regrades':
                self.write(c.PAIRED/'summary.json',dict(validation_passed=True,all_regrades_complete=True,regrades=[{}]*9,
                    model_calls=0,counted_sweep_trials=0,controls_summary_sha256=self.ref['sha256']))
            child=FakeChild(1000+len(children),1 if label==fail_label else 0);children.append(child);return child
        with contextlib.redirect_stdout(io.StringIO()), patch.object(coordinator.subprocess,'Popen',side_effect=popen),patch.object(coordinator,'quality_gate',side_effect=quality_check or (lambda _:{})):
            code=coordinator.run_once(self.controls_path,self.ref['sha256'])
        return code,labels,children

    def test_failed_quality_has_no_later_stage_or_retry(self):
        code,labels,children=self.run_fake_sequence('quality-review')
        self.assertEqual(code,1);self.assertEqual(labels,['quality-review']);self.assertTrue(children[0].waited)
        with patch.object(coordinator.subprocess,'Popen') as popen,self.assertRaises(ValueError):
            coordinator.run_once(self.controls_path,self.ref['sha256'])
        popen.assert_not_called()

    def test_failed_quality_gate_prevents_diagnostics(self):
        def fail(_):raise ValueError('11-criterion gate failed')
        code,labels,children=self.run_fake_sequence(quality_check=fail)
        self.assertEqual(code,1);self.assertEqual(labels,['quality-review','quality-evidence-audit'])
        self.assertTrue(all(c.waited for c in children))

    def test_successful_sequence_is_local_one_and_complete(self):
        code,labels,children=self.run_fake_sequence()
        self.assertEqual(code,0);self.assertEqual(len(labels),5);self.assertTrue(all(c.waited for c in children))
        status=c.read(c.OUTPUT/'summary.json');self.assertTrue(status['passed'])
        self.assertEqual(status['local_max_active_overall'],1);self.assertEqual(status['automatic_retries'],0)

    def test_failed_paired_file_gate_never_launches_regrades(self):
        code,labels,_=self.run_fake_sequence('paired-file-gate')
        self.assertEqual(code,1);self.assertNotIn('paired-full-regrades',labels)

    def test_timing_comparison_log_drift_rejected(self):
        self.write(self.base/'comparison/case/untouched_gold-1.log',b'changed runtime outcome')
        with self.assertRaisesRegex(ValueError,'raw log drift'):self.gate()

    def test_timing_comparison_duplicate_case_rejected(self):
        comp=self.controls['timing_comparison'];raw=c.read(self.base/'comparison/case/comparison.json')
        raw['cases'][5]=copy.deepcopy(raw['cases'][4])
        self.write(self.base/'comparison/case/comparison.json',raw)
        result=c.bound(comp['result']);result['test_outcomes']=raw['cases']
        comp['result']=self.write(c.path(comp['result']['path']),result)
        decision=c.bound(comp['decision']);decision['comparison_result']=comp['result']
        comp['decision']=self.write(c.path(comp['decision']['path']),decision);self.seal()
        with self.assertRaisesRegex(ValueError,'six distinct'):self.gate()

    def real_quality_fixture(self, fail=False):
        spec=importlib.util.spec_from_file_location('actual_quality_validator_tests',HERE.parents[2]/'scripts/package-takehome-evidence.py')
        validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)
        self.patches.append(patch.object(validator,'ROOT',self.root));self.patches[-1].start()
        checks={name:dict(outcome='pass') for name in validator.QUALITY_CRITERIA}
        if fail:checks[sorted(checks)[0]]['outcome']='fail'
        trial=c.REVIEW_OUTPUT/'jobs/review/trial'
        self.write(trial/'artifacts/check-result.json',checks)
        result=self.write(trial/'result.json',dict(finished_at='2026-09-14T02:00:00Z',exception_info=None,
            config=dict(agent=dict(name='mini-swe-agent',model_name='anthropic/claude-sonnet-5',
                kwargs=dict(reasoning_effort='high',version='2.4.6')))))
        report=self.write(c.REVIEW_OUTPUT/'check-report.json',dict(results=[dict(error=None,checks=checks)]))
        self.write(c.REVIEW_OUTPUT/'review-copy/instruction.md',(c.TASK/'instruction.md').read_bytes())
        audit=dict(task=self.task['task'],original_task_checksum=c.TASK_CHECKSUM,
            target_task_file_sha256=self.task['task_file_sha256'],
            reviewed_copy_path=str((c.REVIEW_OUTPUT/'review-copy').relative_to(self.root)),
            check_report=report['path'],check_report_sha256=report['sha256'],valid_review_report=True,
            rubric_pass=10 if fail else 11,rubric_fail=[sorted(checks)[0]] if fail else [],rubric_not_applicable=[],
            raw_trial=str(trial.relative_to(self.root)),
            raw_trial_file_sha256={str(p.relative_to(trial)):c.digest(p) for p in trial.rglob('*') if p.is_file()},
            evidence=dict(input_snapshot=dict(entries={}),identity=dict(agent='mini-swe-agent',agent_version='2.4.6')))
        self.write(c.AUDIT,audit)
        self.write(c.REVIEW_OUTPUT/'summary.json',dict(passed=True,controls_summary=self.ref,reviews={c.TASK.name:
            dict(check_report=report['path'],check_report_sha256=report['sha256'],result=result['path'],original_task_checksum=c.TASK_CHECKSUM)}))
        return validator

    def test_real_all_eleven_quality_criteria_pass(self):
        gate=self.gate();validator=self.real_quality_fixture()
        with patch.object(c,'module',return_value=validator):
            result=coordinator.quality_gate(gate)
        self.assertEqual(result['audit'],c.reference(c.AUDIT))

    def test_real_quality_reward_alone_cannot_hide_failed_criterion(self):
        gate=self.gate();validator=self.real_quality_fixture(fail=True)
        with patch.object(c,'module',return_value=validator),self.assertRaisesRegex(ValueError,'Every corrected-task quality criterion'):
            coordinator.quality_gate(gate)

    def test_quality_audit_must_describe_same_raw_trial(self):
        gate=self.gate();validator=self.real_quality_fixture()
        audit=c.read(c.AUDIT);audit['raw_trial']='unrelated/trial';self.write(c.AUDIT,audit)
        with patch.object(c,'module',return_value=validator),self.assertRaisesRegex(ValueError,'this one review'):
            coordinator.quality_gate(gate)

    def test_quality_command_keeps_single_authorized_model_attempt(self):
        command=quality.command(self.base/'proof','fixture-job')
        for flag,value in (('--model','anthropic/claude-sonnet-5'),('--n-concurrent','1'),('--n-attempts','1')):
            self.assertEqual(command[command.index(flag)+1],value)
        self.assertIn('version=2.4.6',command);self.assertIn('reasoning_effort=high',command)


if __name__ == '__main__':
    unittest.main(verbosity=2)

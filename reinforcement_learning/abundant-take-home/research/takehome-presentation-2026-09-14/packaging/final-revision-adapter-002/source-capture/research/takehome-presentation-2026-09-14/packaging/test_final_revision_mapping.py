"""Synthetic, temporary-file evidence fixtures; no real mapping, model call, or pack."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('packager', ROOT / 'scripts/package-takehome-evidence.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class FinalRevisionTests(unittest.TestCase):
    def write(self, name, value):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
        content = value if isinstance(value, bytes) else p.canonical(value)
        path.write_bytes(content)
        return dict(path=name, sha256=p.sha(content))

    def mutate(self, reference, change):
        value = json.loads((self.root / reference['path']).read_bytes())
        change(value)
        reference.update(self.write(reference['path'], value))

    def score(self, reward):
        groups = {name: {'pass': True} for name in p.ZENOH_GROUPS}
        if reward == 0:
            groups['hidden_pr_tests']['pass'] = False
        return dict(required_groups=p.ZENOH_GROUPS, missing_groups=[], groups=groups, reward=reward)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(p, 'ROOT', self.root); self.patch.start()
        self.old = p.RETAINED[p.ZENOH_HISTORY]
        self.new = 'research/test-only/corrected-zenoh'
        for name, content in [('instruction.md', b'An exact synthetic instruction.\n'), ('task.toml', b'unchanged budgets\n'),
                              ('environment/Dockerfile', b'unchanged image\n'), ('tests/test.sh', b'old grader\n'),
                              ('solution/solve.sh', b'old reference\n')]:
            self.write(self.old + '/' + name, content)
        shutil.copytree(self.root / self.old, self.root / self.new)
        self.write(self.new + '/tests/new.rs', b'synthetic extra assertions\n')
        self.write(self.new + '/solution/solve.sh', b'corrected reference\n')
        patch_ref = self.write(self.new + '/solution/admin-reply-stack.patch', b'synthetic correction\n')
        self.final = dict(task=self.new, harbor_task_checksum='f' * 64, task_file_sha256=p.file_hashes(self.new))
        self.task_ref = self.write('research/proof/task-manifest.json', self.final)
        failure_ref = self.write('research/proof/reference-failure.json', dict(status='complete', reward=0))
        failure_log = self.write('research/proof/reference-failure.log', b'Synthetic fixture; not an execution.\n')
        self.lineage_ref = self.write('research/proof/revision.json', dict(parent_task=self.old,
            parent_file_sha256=p.file_hashes(self.old), revision_task=self.new,
            revision_file_sha256=p.file_hashes(self.new),
            changed_files=['solution/admin-reply-stack.patch', 'solution/solve.sh', 'tests/new.rs'],
            instruction_and_budgets_unchanged=True, existing_v3_scores_and_campaign_unchanged=True,
            reference_result=failure_ref['path'], reference_result_sha256=failure_ref['sha256'],
            reference_failure_log=failure_log['path'], reference_failure_log_sha256=failure_log['sha256'],
            reference_correction_sha256=patch_ref['sha256']))
        self.definition = dict(path=self.old, harbor_task_checksum='a' * 64,
                               task_sha256=p.task_digest(p.inventory(self.old)))
        self.maps = {'campaign': {p.ZENOH_HISTORY: self.definition}}
        self.first, self.input_rows, self.original_records = [], [], {}
        for i, (model, effort) in enumerate((m, e) for m in ('fable-5-1', 'opus-5', 'sonnet-5')
                                           for e in ('medium', 'high', 'max')):
            trial = 'jobs/original-campaign/trial-' + str(i)
            reward = 0 if i == 0 else 1
            config = dict(task=dict(path=self.old), timeout_multiplier=1, extra_instruction_paths=[], environment={},
                          agent=dict(kwargs=dict(reasoning_effort=effort), model_name='anthropic/claude-' + model))
            result_ref = self.write(trial + '/result.json', dict(config=config, task_checksum='a' * 64))
            self.write(trial + '/config.json', config)
            prompt = 'Context\nAn exact synthetic instruction.\n'
            self.write(trial + '/agent/mini-swe-agent.trajectory.json', {'messages': [{'role': 'user', 'content': prompt}]})
            self.write(trial + '/agent/trajectory.json', {'steps': []})
            self.write(trial + '/benchmark-evidence.json', {'synthetic': True})
            self.write(trial + '/verifier/score.json', {'reward': reward})
            self.write(trial + '/agent/benchmark-agent-input.json', dict(delivery='final_run_task_config',
                       task_sha256=p.file_hashes(self.old)['instruction.md']))
            self.write(trial + '/artifacts/submission/commons/zenoh-protocol/src/lib.rs', ('source-' + str(i)).encode())
            sources = {'artifacts/submission/' + n: dict(kind='file', sha256=v['sha256'], size=v['bytes'])
                       for n, v in p.inventory(trial + '/artifacts/submission')['files'].items()}
            self.write(trial + '/benchmark-snapshot.json', dict(entries=sources))
            raw_files = {n: dict(kind='file', sha256=v['sha256'], size=v['bytes']) for n, v in p.inventory(trial)['files'].items()}
            row = dict(trial=trial, task=p.ZENOH_HISTORY, model=model, effort=effort, finished_at='2026-09-14T00:00:00Z',
                       reward=reward, raw_reward=reward, assistant_steps=1, tool_calls=1, status='scored',
                       artifacts={trial + '/result.json': dict(sha256=result_ref['sha256'], bytes=(self.root / result_ref['path']).stat().st_size)})
            row['cell'] = p.cell(row)
            self.first.append(row)
            record = {k: row[k] for k in ('trial', 'model', 'effort', 'finished_at', 'reward', 'raw_reward', 'assistant_steps', 'tool_calls')}
            record.update(first_counted=True, original_task_checksum='a' * 64, raw_files=raw_files,
                          source_files=sources, source_manifest_sha256=p.sha(p.canonical(sources)), source_file_count=1,
                          first_user_prompt=dict(sha256=p.sha(prompt.encode())))
            ref = self.write('research/proof/inputs/trials/' + str(i) + '.json', record)
            self.input_rows.append(dict(trial=trial, record='trials/' + str(i) + '.json', sha256=ref['sha256'],
                                        result_sha256=result_ref['sha256'], source_file_count=1))
            self.original_records[trial] = record
        task_inputs = self.write('research/proof/inputs/task-inputs.json', dict(task=self.old,
            whole_task_file_sha256=p.file_hashes(self.old), original_harbor_task_checksum='a' * 64))
        self.inputs_ref = self.write('research/proof/inputs/manifest.json', dict(
            kind='original_first_counted_trial_inputs_for_future_paired_verifier_regrades', passed=True, model_calls=0,
            counted_sweep_trials_added=0, original_scores_changed=False, missing_required_identity_evidence=[],
            required_cells=9, selected_cells=9, trials=self.input_rows, source_snapshots={},
            task_inputs=dict(path='task-inputs.json', sha256=task_inputs['sha256']),
            weak_identity_evidence=['Synthetic fixture; historical image identity unverified.'], missing_optional_runtime_markers=[]))
        self.controls_ref = self.controls_fixture()
        self.quality_ref = self.quality_fixture()
        self.pairs_ref = self.pairs_fixture()
        self.entry = dict(historical_task_id=p.ZENOH_HISTORY, final_task_manifest=self.task_ref,
                          revision_lineage=self.lineage_ref,
                          paired_inputs_manifest=self.inputs_ref, controls_summary=self.controls_ref,
                          final_quality=self.quality_ref, paired_regrades_summary=self.pairs_ref,
                          reviewed_changed_files=['solution/admin-reply-stack.patch', 'solution/solve.sh', 'tests/new.rs'])
        self.mapping = dict(schema_version=1, kind='final_revision_packaging_mapping', mappings=[self.entry])

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def controls_fixture(self):
        identity = self.write('research/proof/controls/run-identity.json', dict(task=self.new, task_checksum='f' * 64,
            inputs=dict(task_file_sha256=self.final['task_file_sha256'], task_manifest_sha256=self.task_ref['sha256'])))
        controls = {}
        for agent, reward in [('oracle', 1), ('nop', 0)]:
            root = 'research/proof/controls/' + agent
            config = dict(task=dict(path='/captured/linux/workspace/' + self.new), agent=dict(name=agent, model_name=None), verifier={}, environment={}, timeout_multiplier=1)
            result = self.write(root + '/result.json', dict(task_name='corrected-zenoh', task_checksum='f' * 64,
                config=config, agent_info=dict(name=agent, model_info=None),
                finished_at='2026-09-14T01:00:00Z', exception_info=None, verifier_result=dict(rewards=dict(reward=reward))))
            self.write(root + '/config.json', config)
            self.write(root + '/verifier/score.json', self.score(reward))
            self.write(root + '/verifier/reward.txt', str(reward).encode())
            if agent == 'oracle':
                for group, bins in {'hidden_pr_tests': {'timestamp_instrumentation': 47},
                    'robustness_tests': {'timestamp_robustness': 5}, 'admin_timestamp_tests': {'timestamp_adminspace': 1, 'timestamp_adminspace_reply_stack': 1},
                    'interop_gold': {'ts_interop': 6}, 'parity_python': {'ts_interop': 4}}.items():
                    self.write(root + '/verifier/' + group + '.json', dict(problems=[], binaries={n: dict(passed=c, failed=0, ignored=0, status='ok') for n, c in bins.items()},
                        tests={n: {'test-' + str(i): 'ok' for i in range(c)} for n, c in bins.items()}))
            admission = self.write(root + '-admission.jsonl', b'{"own_claims":0}\n')
            source_proof = self.write(root + '-runner-source.json', dict(cwd='/captured/linux/workspace',
                captured_tree_unchanged=True, all_local_imports_captured=True, repository_root_overrides=False))
            controls[agent] = dict(passed=True, model_calls=0, exit_code=0, result=result['path'], result_sha256=result['sha256'],
                runner_source_proof=source_proof['path'], runner_source_proof_sha256=source_proof['sha256'],
                trial_file_sha256=p.file_hashes(root), admission_lifecycle=dict(passed=True, peak_own_claims=1,
                final_own_claims=0, shared_cap_respected=True, **admission))
        self.host = dict(NanoCpus=4000000000, Memory=8192 * 1024 * 1024, MemorySwap=16384 * 1024 * 1024,
                         NetworkMode='none', StorageOpt=None)
        inspection = self.write('research/proof/controls/inspection.json', dict(Image='sha256:' + 'b' * 64,
            HostConfig=self.host, **{'Config.Labels': {'com.docker.compose.project': 'oracle__verifier__trial'}}))
        self.image_ref = self.write('research/proof/controls/image-proof.json', dict(passed=True,
            kind='normal_harbor_oracle_verifier_image', verifier_image_id='sha256:' + 'b' * 64,
            oracle_result_path=controls['oracle']['result'], oracle_result_sha256=controls['oracle']['result_sha256'],
            final_task_checksum='f' * 64, observed_host_config=self.host,
            raw_docker_inspection_path=inspection['path'], raw_docker_inspection_sha256=inspection['sha256']))
        controls['oracle']['verifier_image_proof'] = self.image_ref
        return self.write('research/proof/controls/summary.json', dict(passed=True, model_calls=0, inputs_unchanged=True,
            tooling_unchanged=True, controls=controls, run_identity=identity['path'], run_identity_sha256=identity['sha256']))

    def quality_fixture(self):
        checks = {name: dict(outcome='pass', explanation='Synthetic unit fixture.') for name in p.QUALITY_CRITERIA}
        report = self.write('research/proof/quality/check_report.json', dict(results=[dict(checks=checks, error=None)]))
        trial = 'research/proof/quality/trial'
        self.write(trial + '/artifacts/check-result.json', checks)
        self.write(trial + '/result.json', dict(finished_at='2026-09-14T01:00:00Z', exception_info=None,
            config=dict(agent=dict(name='mini-swe-agent', model_name='anthropic/claude-sonnet-5',
                                   kwargs=dict(reasoning_effort='high', version='2.4.6')))))
        copy_path = 'research/proof/quality/reviewed-copy'
        shutil.copytree(self.root / self.new, self.root / copy_path)
        return self.write('research/proof/quality/audit.json', dict(task=self.new, original_task_checksum='f' * 64,
            target_task_file_sha256=self.final['task_file_sha256'], reviewed_copy_path=copy_path,
            check_report=report['path'], check_report_sha256=report['sha256'], raw_trial=trial,
            raw_trial_file_sha256=p.file_hashes(trial), valid_review_report=True, rubric_pass=11,
            rubric_fail=[], rubric_not_applicable=[], evidence=dict(identity=dict(agent='mini-swe-agent', agent_version='2.4.6'),
            input_snapshot=dict(entries={n: dict(kind='file', sha256=v['sha256']) for n, v in p.inventory(trial)['files'].items()}))))

    def pairs_fixture(self):
        pairs = []
        for i, row in enumerate(self.first):
            record = self.original_records[row['trial']]
            root = 'research/proof/regrades/' + str(i)
            # Include a changed outcome and a valid zero; neither changes the original reward.
            reward = 0 if i == 1 else 1
            score = self.score(reward)
            score_ref = self.write(root + '/verifier/score.json', score)
            limits = dict(cpus=4, memory_mb=8192, storage_mb=30720, verifier_timeout_seconds=3600,
                          network_mode='no-network', local_max_active=1, shared_max_active=14)
            source_map = {n.removeprefix('artifacts/submission/'): v['sha256'] for n, v in record['source_files'].items()}
            test_map = {n: h for n, h in self.final['task_file_sha256'].items() if n.startswith('tests/')}
            pair = dict(kind='paired_verifier_regrade', full_regrade=True, model_calls=0, counted_sweep_trial=False,
                validation_passed=True, driver_exit_code=0,
                score_path=score_ref['path'], score_sha256=score_ref['sha256'], required_groups=score['required_groups'],
                groups=score['groups'], artifact_root=root,
                original_trial_path=row['trial'], original_result_sha256=self.input_rows[i]['result_sha256'],
                original_input_record_sha256=self.input_rows[i]['sha256'], original_task_checksum='a' * 64,
                original_reward=row['reward'], submitted_source_manifest_sha256=record['source_manifest_sha256'],
                final_task_checksum='f' * 64, reward=reward, source_file_sha256_before=source_map,
                source_file_sha256_after=source_map, test_file_sha256_before=test_map, test_file_sha256_after=test_map,
                source_files_unchanged=True, test_files_unchanged=True, verifier_image_id='sha256:' + 'b' * 64,
                declared_limits=limits, observed_limits=dict(cpus=4, memory_mb=8192, network_mode='none',
                verifier_timeout_seconds=3600, storage_mb=None, storage_opt=None, storage_note='Quota not configured.',
                memory_swap_bytes=self.host['MemorySwap']),
                cleanup=dict(container_absent=True, claim_absent=True, stopped_before_collection=True, errors=[]))
            for filename, value in [('source-before.json', source_map), ('source-after.json', source_map),
                                    ('tests-before.json', test_map), ('tests-after.json', test_map)]:
                self.write(root + '/artifacts/case/' + filename, value)
            self.write(root + '/artifacts/case/raw-result.json', dict(kind='paired_verifier_process_result',
                full_regrade=True, model_calls=0, verifier_exit_code=0, timed_out=False, workload_timeout_seconds=3600,
                source_files_unchanged=True, test_files_unchanged=True))
            self.write(root + '/artifacts/verifier/reward.txt', str(reward).encode())
            raw = self.write(root + '/result.json', dict(pair, finished_at='2026-09-14T02:00:00Z', status='complete'))
            self.write(root + '/verifier/run.log', b'Synthetic test output, not a real verifier run.\n')
            pair.update(result_path=raw['path'], result_sha256=raw['sha256'], score_path=score_ref['path'],
                        score_sha256=score_ref['sha256'], required_groups=score['required_groups'], groups=score['groups'],
                        artifact_root=root, artifact_file_sha256=p.file_hashes(root))
            pairs.append(pair)
        probes = []
        mutants = []
        for i in range(6):
            root = 'research/proof/probes/' + str(i)
            log = self.write(root + '/case/cargo.stdout.log', b'Synthetic expected assertion.\n')
            raw = self.write(root + '/result.json', dict(status='complete', case_exit_code=101, model_calls=0,
                container_absent=True, claim_absent=True, inputs_unchanged=True,
                artifact_sha256={'cargo.stdout.log': log['sha256']}))
            probes.append(dict(name='probe-' + str(i), kind='single_omission_probe', omission_detected=True,
                other_tests_passed=True, case_exit_code=101, result=raw['path'], result_sha256=raw['sha256'],
                expected_assertion_signatures=['Synthetic expected assertion.']))
            mutants.append(dict(id='probe-' + str(i)))
        mutants_ref = self.write(str(Path(self.new).parent / 'mutants/manifest.json'), dict(mutants=mutants))
        diagnostics = self.write('research/proof/probes/summary.json', dict(passed=True, model_calls=0,
            counted_sweep_trials=0, controls_summary_sha256=self.controls_ref['sha256'],
            mutant_manifest_sha256=mutants_ref['sha256'], cases=probes))
        task_identity = dict(path=self.new, harbor_task_checksum='f' * 64, task_manifest_sha256=self.task_ref['sha256'],
                             task_file_sha256=self.final['task_file_sha256'])
        inputs = self.write('research/proof/regrades/inputs.json', dict(task=task_identity,
            original_input_manifest=self.inputs_ref, control_summary_sha256=self.controls_ref['sha256'],
            verifier_image_proof_sha256=self.image_ref['sha256'], diagnostics_summary_sha256=diagnostics['sha256'],
            verifier_image_id='sha256:' + 'b' * 64, oracle_observed_host_config=self.host))
        return self.write('research/proof/regrades/summary.json', dict(kind='paired_verifier_regrades', validation_passed=True,
            all_regrades_complete=True, model_calls=0, counted_sweep_trials=0, original_input_manifest=self.inputs_ref,
            task=task_identity, inputs_path=inputs['path'], inputs_sha256=inputs['sha256'],
            controls_summary=self.controls_ref['path'], controls_summary_sha256=self.controls_ref['sha256'],
            verifier_image_proof=self.image_ref['path'], verifier_image_proof_sha256=self.image_ref['sha256'],
            diagnostics_summary=diagnostics['path'], diagnostics_summary_sha256=diagnostics['sha256'], regrades=pairs))

    def verify(self):
        return p.final_revision_mapping(self.mapping, self.first, self.maps)

    def test_default_mapping_keeps_historical_selection(self):
        self.assertEqual(p.final_revision_mapping(None, [], {}), (p.RETAINED, set(), []))

    def test_complete_mapping_keeps_both_outcomes_and_storage_uncertainty(self):
        before = copy.deepcopy(self.first)
        effective, sources, evidence = self.verify()
        self.assertEqual(effective[p.ZENOH_HISTORY], self.new)
        self.assertNotIn(self.old, effective.values())
        self.assertEqual(self.first, before)
        outcomes = evidence[0]['paired_outcomes']
        self.assertEqual((outcomes[0]['original_reward'], outcomes[0]['paired_reward']), (0, 1))
        self.assertEqual((outcomes[1]['original_reward'], outcomes[1]['paired_reward']), (1, 0))
        self.assertTrue(all(not r['counted_as_new_model_trial'] and r['observed_storage_mb'] is None for r in outcomes))
        self.assertIn('research/proof/regrades/8', sources)

    def test_changed_delivered_instruction_rejected(self):
        self.write(self.new + '/instruction.md', b'Changed model stimulus')
        self.mutate(self.task_ref, lambda d: d.update(task_file_sha256=p.file_hashes(self.new)))
        with self.assertRaisesRegex(ValueError, 'agent-facing'):
            self.verify()

    def test_changed_environment_rejected(self):
        self.write(self.new + '/environment/new-visible-file', b'new information')
        self.mutate(self.task_ref, lambda d: d.update(task_file_sha256=p.file_hashes(self.new)))
        with self.assertRaisesRegex(ValueError, 'agent-facing'):
            self.verify()

    def test_changed_task_budget_rejected(self):
        self.write(self.new + '/task.toml', b'increased budget')
        self.mutate(self.task_ref, lambda d: d.update(task_file_sha256=p.file_hashes(self.new)))
        with self.assertRaisesRegex(ValueError, 'agent-facing'):
            self.verify()

    def test_lineage_must_bind_parent_and_corrected_bytes(self):
        self.mutate(self.lineage_ref, lambda d: d.update(parent_file_sha256={}))
        with self.assertRaisesRegex(ValueError, 'Revision lineage'):
            self.verify()

    def test_unreviewed_changed_file_rejected(self):
        self.entry['reviewed_changed_files'] = ['tests/new.rs']
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            self.verify()

    def test_pending_controls_rejected(self):
        self.mutate(self.controls_ref, lambda d: d.update(passed=False))
        with self.assertRaisesRegex(ValueError, 'controls are not complete'):
            self.verify()

    def test_absolute_control_task_path_must_match_captured_execution_root(self):
        root = 'research/proof/controls/oracle'
        raw = json.loads((self.root / root / 'result.json').read_bytes())
        raw['config']['task']['path'] = '/different/workspace/' + self.new
        result_ref = self.write(root + '/result.json', raw)
        self.write(root + '/config.json', raw['config'])
        self.mutate(self.controls_ref, lambda d: d['controls']['oracle'].update(
            result_sha256=result_ref['sha256'], trial_file_sha256=p.file_hashes(root)))
        with self.assertRaisesRegex(ValueError, 'Control config/task path differs'):
            self.verify()

    def test_oracle_cannot_skip_new_test_even_with_passing_score(self):
        path = 'research/proof/controls/oracle/verifier/admin_timestamp_tests.json'
        details = json.loads((self.root / path).read_bytes())
        details['binaries']['timestamp_adminspace_reply_stack'].update(passed=0, ignored=1)
        self.write(path, details)
        self.mutate(self.controls_ref, lambda d: d['controls']['oracle'].update(
            trial_file_sha256=p.file_hashes('research/proof/controls/oracle')))
        with self.assertRaisesRegex(ValueError, 'oracle omitted required tests'):
            self.verify()

    def test_quality_reward_does_not_mask_failed_criterion(self):
        audit = json.loads((self.root / self.quality_ref['path']).read_bytes())
        ref = dict(path=audit['check_report'], sha256=audit['check_report_sha256'])
        self.mutate(ref, lambda d: d['results'][0]['checks']['behavior_in_tests'].update(outcome='fail'))
        self.mutate(self.quality_ref, lambda d: d.update(check_report_sha256=ref['sha256']))
        with self.assertRaisesRegex(ValueError, 'Every corrected-task quality criterion'):
            self.verify()

    def test_reviewer_delivered_copy_mismatch_rejected(self):
        self.write('research/proof/quality/reviewed-copy/tests/new.rs', b'different reviewed task')
        with self.assertRaisesRegex(ValueError, 'different task copy'):
            self.verify()

    def test_missing_or_duplicate_pair_rejected(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                ref_before = dict(self.pairs_ref); old = (self.root / self.pairs_ref['path']).read_bytes()
                self.mutate(self.pairs_ref, lambda d: d['regrades'].__setitem__(slice(8, 9), [d['regrades'][0]] if duplicate else []))
                with self.assertRaisesRegex(ValueError, 'every original first-counted cell'):
                    self.verify()
                (self.root / self.pairs_ref['path']).write_bytes(old); self.pairs_ref.update(ref_before)

    def test_focused_probe_or_model_trial_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0].update(full_regrade=False))
        with self.assertRaisesRegex(ValueError, 'Focused probes'):
            self.verify()

    def test_new_model_count_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d.update(counted_sweep_trials=9))
        with self.assertRaisesRegex(ValueError, 'counted as model trials'):
            self.verify()

    def test_changed_test_or_source_identity_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0].update(test_file_sha256_after={}))
        with self.assertRaisesRegex(ValueError, 'source/test identity differs'):
            self.verify()

    def test_original_source_mutation_rejected(self):
        self.write('jobs/original-campaign/trial-0/artifacts/submission/commons/zenoh-protocol/src/lib.rs', b'overwritten')
        with self.assertRaisesRegex(ValueError, 'Original paired raw artifact changed'):
            self.verify()

    def test_paired_resource_override_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0]['observed_limits'].update(cpus=8))
        with self.assertRaisesRegex(ValueError, 'observed resource limits'):
            self.verify()

    def test_different_verifier_image_cannot_use_same_task_name(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0].update(verifier_image_id='sha256:' + 'c' * 64))
        with self.assertRaisesRegex(ValueError, 'immutable identity'):
            self.verify()

    def test_different_control_pair_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d.update(controls_summary_sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'different final controls'):
            self.verify()

    def test_unconfigured_storage_requires_actual_evidence(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0]['observed_limits'].update(storage_opt={'size': '1G'}))
        with self.assertRaisesRegex(ValueError, 'StorageOpt evidence'):
            self.verify()

    def test_unclean_pair_rejected(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0]['cleanup'].update(claim_absent=False))
        with self.assertRaisesRegex(ValueError, 'cleanup remains incomplete'):
            self.verify()

    def test_interrupted_process_cannot_hide_behind_completed_summary(self):
        path = 'research/proof/regrades/0/artifacts/case/raw-result.json'
        raw = json.loads((self.root / path).read_bytes()); raw['timed_out'] = True
        self.write(path, raw)
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0].update(artifact_file_sha256=p.file_hashes('research/proof/regrades/0')))
        with self.assertRaisesRegex(ValueError, 'Full verifier process'):
            self.verify()

    def test_summary_cannot_rewrite_raw_paired_outcome(self):
        self.mutate(self.pairs_ref, lambda d: d['regrades'][0].update(reward=0))
        with self.assertRaisesRegex(ValueError, 'raw result disagrees'):
            self.verify()

    def test_task_basename_collision_rejected_across_sample_and_archive(self):
        with patch.object(p, 'task_paths', return_value=[self.old, 'candidates/corrected-zenoh']):
            with self.assertRaisesRegex(ValueError, 'basenames collide'):
                p.mapped_task_paths({p.ZENOH_HISTORY: self.new})

    def test_plan_maps_only_explicit_revision_and_keeps_original_raw_paths(self):
        base = self.root / 'research/takehome-presentation-2026-09-14'
        packaging = base / 'packaging'
        definitions = self.maps['campaign']
        for name, source in p.RETAINED.items():
            if name != p.ZENOH_HISTORY:
                self.write(source + '/task.toml', b'unchanged fixture task')
                definitions[name] = dict(path=source, task_sha256=p.task_digest(p.inventory(source)))
        self.write(p.SQLITE + '/task.toml', b'archived fixture task')
        self.write('candidates_v2/validation/jobs/copied/task.toml', b'not a candidate')
        data = dict(tasks={name: {} for name in p.RETAINED}, trials=self.first, selection='first_counted_result')
        cases = dict(cases=[dict(task_id=name, trials=[]) for name in p.RETAINED])
        controls = [dict(task_id=name, exact_revision_controls_pass=True) for name in p.RETAINED]
        documents = dict(results=p.canonical(data), scope=b'{}', cases=p.canonical(cases), final_controls=p.canonical(controls))
        self.write(str(base.relative_to(self.root)) + '/evidence/shortlist-cases.json', cases)
        self.write(str(base.relative_to(self.root)) + '/evidence/shortlist-controls-final.json', controls)
        mapping_ref = self.write('research/proof/explicit-test-mapping.json', self.mapping)
        with patch.object(p, 'BASE', base), patch.object(p, 'PACKAGING', packaging), \
             patch.object(p, 'original_materials', return_value={'synthetic': True}), \
             patch.object(p, 'load_documents', return_value=documents), \
             patch.object(p, 'verify_selection', return_value=(self.first, self.maps, dict(covered=9, required=99, ready=False))), \
             patch.object(p, 'control_sources', return_value=set()) as historical_gate:
            plan = p.make_plan(packaging / 'synthetic-only.json', final_revision=mapping_ref['path'])
        historical_gate.assert_called_once_with(controls, self.maps)
        entries = {entry['source']: entry for entry in plan['entries']}
        self.assertEqual(entries[self.new]['destination'], 'samples/corrected-zenoh')
        self.assertEqual(entries[self.old]['destination'], 'archive/' + Path(self.old).name)
        self.assertEqual(entries[self.old]['archive_role'], 'paired_historical_revision')
        self.assertEqual(plan['task_inventory']['count'], 5)
        self.assertEqual(plan['sizes']['retained_task_directories'], 3)
        self.assertEqual(plan['sizes']['archived_task_directories'], 2)
        self.assertNotIn('candidates_v2/validation/jobs/copied', entries)
        for row in self.first:
            self.assertEqual(entries[row['trial']]['destination'], row['trial'])
            self.assertEqual(entries[row['trial']]['reward'], row['reward'])
            self.assertEqual(entries[row['trial']]['task'], p.ZENOH_HISTORY)
        self.assertEqual(plan['captured_inputs']['final_revision_mapping']['sha256'], mapping_ref['sha256'])
        self.assertFalse(plan['readiness']['coverage_gate'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Local, no-runtime regression checks for the take-home evidence assembler."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('packager', ROOT / 'scripts/package-takehome-evidence.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(p, 'ROOT', self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def write(self, name, content=b'bytes\x00preserved\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def entry(self, source, destination):
        return dict(source=source, destination=destination, tree=p.inventory(source))

    def fixture(self):
        tasks = [f'task-{i}' for i in range(11)]
        scope = dict(count=99, cells={})
        for task in tasks:
            for model in ('fable-5-1', 'opus-5', 'sonnet-5'):
                for effort in ('medium', 'high', 'max'):
                    row = dict(task=task, model='anthropic/claude-' + model, effort=effort, job='campaign')
                    scope['cells'][p.cell(row)] = row
        rows, summary = [], []
        for name, finished in [('a', '2026-09-14T01:00:00-07:00'),
                               ('b', '2026-09-14T07:00:00+00:00'),
                               ('c', '2026-09-14T07:00:00Z')]:
            trial = 'jobs/campaign/' + name
            result = dict(task_name='task-0', task_checksum='task-hash', finished_at=finished,
                          config=dict(agent=dict(model_name='anthropic/claude-sonnet-5',
                                                 kwargs=dict(reasoning_effort='max'))),
                          verifier_result=dict(rewards=dict(reward=0)))
            evidence = dict(counts=dict(atif_agent_steps=4, atif_tool_calls=3))
            artifacts = {}
            for filename, value in [('result.json', result), ('benchmark-evidence.json', evidence)]:
                content = p.canonical(value)
                self.write(trial + '/' + filename, content)
                artifacts[trial + '/' + filename] = dict(sha256=p.sha(content), bytes=len(content))
            row = dict(trial=trial, task='task-0', model='sonnet-5', effort='max', status='scored',
                       campaign='campaign', raw_job='campaign', finished_at=finished, task_checksum='task-hash',
                       reward=0, raw_reward=0, assistant_steps=4, tool_calls=3, checks_passed=2, checks_total=3,
                       check_unit='test_cases', cost_usd=1, artifacts=artifacts,
                       first_counted_result=(name == 'b'))
            row['cell'] = p.cell(row)
            rows.append(row)
            summary.append(dict(row, result_sha256=artifacts[trial + '/result.json']['sha256'],
                                benchmark_evidence_sha256=artifacts[trial + '/benchmark-evidence.json']['sha256']))
        documents = dict(scope=p.canonical(scope), **{
            'source:plan': p.canonical(dict(tasks=[dict(id=t, path='tasks/' + t, harbor_task_checksum='task-hash')
                                                  for t in tasks], jobs=[dict(name='campaign')])),
            'source:summary': p.canonical(dict(job='campaign', trials=summary))})
        missing = sorted(set(scope['cells']) - {rows[0]['cell']})
        data = dict(scope_sha256=p.sha(documents['scope']), counted_statuses=list(p.COUNTED), data_quality_issues=[],
                    campaign_plans={'campaign': {'sha256': 'plan'}}, sources={'summary': {'sha256': 'summary'}},
                    trials=rows, adopted_jobs=[], tasks={t: {} for t in tasks},
                    coverage=dict(covered=1, required=99, ready=False, missing=[dict(cell=x) for x in missing]))
        return data, scope, documents

    def test_unsafe_relative_paths(self):
        for value in ('/absolute', '../outside', 'a/../b', 'a//b', './a', 'a\\b', 'a\x00b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.relative(value)

    def test_symlink_file_and_ancestor_rejected(self):
        self.write('actual/file')
        (self.root / 'alias').symlink_to(self.root / 'actual', target_is_directory=True)
        (self.root / 'actual/link').symlink_to('/etc/passwd')
        with self.assertRaises((OSError, ValueError)):
            p.read('alias/file')
        with self.assertRaises(ValueError):
            p.inventory('actual')

    def test_fifo_rejected_without_waiting_for_writer(self):
        os.mkfifo(self.root / 'pipe')
        start = time.monotonic()
        with self.assertRaises(ValueError):
            p.read('pipe')
        self.assertLess(time.monotonic() - start, 1)

    def test_destination_casefold_and_unicode_collisions(self):
        self.write('one'); self.write('two')
        for a, b in [('archive/A', 'archive/a'), ('archive/é', 'archive/e\u0301')]:
            with self.subTest(a=a), self.assertRaises(ValueError):
                p.destinations([self.entry('one', a), self.entry('two', b)])

    def test_file_as_casefolded_parent_rejected(self):
        self.write('one'); self.write('two')
        with self.assertRaises(ValueError):
            p.destinations([self.entry('one', 'archive/A'), self.entry('two', 'archive/a/child')])

    def test_exact_bytes_modes_and_empty_directories_copied(self):
        source = self.write('raw/agent/trajectory.json')
        source.chmod(0o640)
        (self.root / 'raw/artifacts/empty').mkdir(parents=True)
        entry = self.entry('raw', 'jobs/original-job/original-trial')
        (self.root / 'output').mkdir()
        p.copy_entry(entry, self.root / 'output')
        copied = p.inventory('output/jobs/original-job/original-trial')
        self.assertEqual(p.bytes_identity(entry['tree']), p.bytes_identity(copied))

    def test_hash_drift_during_copy_rejected(self):
        source = self.write('raw/file')
        entry = self.entry('raw', 'jobs/job/trial')
        source.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'changed during copy'):
            p.copy_entry(entry, self.root / 'output')

    def test_zip_round_trip_preserves_paths_and_bytes(self):
        self.write('pack/jobs/job/trial/result.json', b'{"reward":0}\n')
        for name in ('samples', 'archive', 'report'):
            (self.root / 'pack' / name).mkdir()
        result = p.zip_verified(self.root / 'pack', self.root / 'pack.zip')
        self.assertEqual(result['sha256'], p.sha((self.root / 'pack.zip').read_bytes()))
        with zipfile.ZipFile(self.root / 'pack.zip') as archive:
            self.assertEqual(archive.read('jobs/job/trial/result.json'), b'{"reward":0}\n')
            self.assertIn('report/', archive.namelist())
            self.assertEqual({x.split('/')[0] for x in archive.namelist()}, {'samples', 'jobs', 'archive', 'report'})

    def test_first_result_uses_utc_then_original_path(self):
        data, scope, docs = self.fixture()
        with patch.object(p, 'SCOPE_SHA', p.sha(docs['scope'])):
            first, _, coverage = p.verify_selection(data, scope, docs)
        self.assertEqual([r['trial'] for r in first], ['jobs/campaign/b'])
        self.assertEqual(coverage['covered'], 1)
        self.assertFalse(coverage['ready'])

    def test_projected_outcome_tampering_rejected(self):
        for field in ('reward', 'raw_reward', 'assistant_steps', 'tool_calls', 'checks_passed', 'cost_usd'):
            data, scope, docs = self.fixture()
            data['trials'][0][field] += 1
            with self.subTest(field=field), patch.object(p, 'SCOPE_SHA', p.sha(docs['scope'])), self.assertRaises(ValueError):
                p.verify_selection(data, scope, docs)

    def test_undeclared_raw_job_rejected(self):
        data, scope, docs = self.fixture()
        plan = json.loads(docs['source:plan']); plan['jobs'] = []
        docs['source:plan'] = p.canonical(plan)
        with patch.object(p, 'SCOPE_SHA', p.sha(docs['scope'])), self.assertRaisesRegex(ValueError, 'not declared'):
            p.verify_selection(data, scope, docs)

    def test_raw_hash_drift_rejected(self):
        data, scope, docs = self.fixture()
        self.write('jobs/campaign/a/result.json', b'{}')
        with patch.object(p, 'SCOPE_SHA', p.sha(docs['scope'])), self.assertRaisesRegex(ValueError, 'hash drift'):
            p.verify_selection(data, scope, docs)

    def test_plan_digest_tampering_rejected(self):
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            p.verify_plan(dict(plan_sha256='wrong', coverage={'covered': 99}))

    def test_discovery_includes_held_and_historical_but_excludes_validation_jobs(self):
        included = ['candidates/held', 'candidates/sqlite', 'candidates_v2/kept', 'research/task-revisions/old-v1']
        for path in included + ['candidates_v2/validation/jobs/trial', 'restaurant-supplied']:
            self.write(path + '/task.toml', b'name="task"\n')
        with patch.object(p, 'RETAINED', {'kept': 'candidates_v2/kept'}), patch.object(p, 'SQLITE', 'candidates/sqlite'):
            self.assertEqual(p.task_paths(), sorted(included))

    def controls_fixture(self):
        documents, controls, definitions = {}, [], {}
        for task, path in p.RETAINED.items():
            definitions[task] = dict(harbor_task_checksum='harbor-sha', task_sha256='whole-sha')
            manifest = path + '.manifest.json'; documents[manifest] = b'{}'
            group = dict(task_id=task, task_path=path, current_harbor_checksum='harbor-sha', current_task_sha256='whole-sha',
                         exact_revision_controls_pass=True, manifest_path=manifest, manifest_sha256=p.sha(b'{}'), controls=[])
            for agent, reward in [('oracle', 1), ('nop', 0)]:
                result_path = 'controls/' + task + '/' + agent + '/result.json'
                score_path = 'controls/' + task + '/' + agent + '/verifier/score.json'
                documents[result_path] = p.canonical(dict(task_name=task, task_checksum='harbor-sha',
                    config=dict(agent=dict(name=agent)), finished_at='2026-09-14T01:00:00Z', exception_info=None,
                    verifier_result=dict(rewards=dict(reward=reward))))
                documents[score_path] = p.canonical(dict(reward=reward))
                group['controls'].append(dict(agent=agent, path=result_path, result_sha256=p.sha(documents[result_path]),
                    score_path=score_path, score_sha256=p.sha(documents[score_path])))
            controls.append(group)
        return documents, controls, {'campaign': definitions}

    def test_controls_bind_six_actual_terminal_results_and_scores(self):
        documents, controls, definitions = self.controls_fixture()
        with patch.object(p, 'read', side_effect=lambda path: documents[path]):
            sources = p.control_sources(controls, definitions)
        self.assertEqual(sum(s.endswith(('/oracle', '/nop')) for s in sources), 6)

    def test_control_score_disagreement_rejected_even_with_matching_hash(self):
        documents, controls, definitions = self.controls_fixture()
        control = controls[0]['controls'][0]
        documents[control['score_path']] = p.canonical(dict(reward=0))
        control['score_sha256'] = p.sha(documents[control['score_path']])
        with patch.object(p, 'read', side_effect=lambda path: documents[path]), self.assertRaisesRegex(ValueError, 'expected terminal outcome'):
            p.control_sources(controls, definitions)


if __name__ == '__main__':
    unittest.main(verbosity=2)

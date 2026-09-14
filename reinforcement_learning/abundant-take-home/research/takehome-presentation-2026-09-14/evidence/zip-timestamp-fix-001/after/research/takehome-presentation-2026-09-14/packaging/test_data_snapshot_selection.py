"""Temporary-file tests only: no real data snapshot, model call, or pack assembly."""
import copy
import importlib.util
import io
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


class SnapshotSelectionTests(unittest.TestCase):
    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else p.canonical(value))
        return target

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'research/takehome-presentation-2026-09-14'
        self.prefix = self.base.relative_to(self.root).as_posix()
        self.patches = [patch.object(p, 'ROOT', self.root), patch.object(p, 'BASE', self.base),
                        patch.object(p, 'PACKAGING', self.base / 'packaging')]
        for current in self.patches:
            current.start()
        self.scope = p.canonical(dict(count=99, note='Synthetic scope fixture'))
        self.write(self.prefix + '/coverage-scope.json', self.scope)
        self.write(self.prefix + '/evidence/shortlist-cases.json', {})
        self.write(self.prefix + '/evidence/shortlist-controls-final.json', [])
        self.default = self.prefix + '/data'
        self.selected = self.prefix + '/data-snapshots/99-name-is-not-readiness'
        self.fixture(self.default, 'default-82')
        self.fixture(self.selected, 'separate-partial')

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        self.temp.cleanup()

    def fixture(self, directory, marker):
        sources = {}
        for name, value in [('scope.json', self.scope), ('summary.json', p.canonical(dict(job=marker))),
                            ('plan.json', p.canonical(dict(tasks=[], note=marker)))]:
            self.write(directory + '/sources/' + name, value)
            sources[name] = dict(snapshot='sources/' + name, sha256=p.sha(value), original='original/' + name)
        data = dict(marker=marker, scope_sha256=p.sha(self.scope), scope_source=sources['scope.json'],
                    sources={'campaign.summary.json': sources['summary.json']},
                    campaign_plans={'campaign': sources['plan.json']}, coverage=dict(covered=82, required=99, ready=False))
        self.write(directory + '/results.json', data)
        return data

    def capture(self, documents):
        captured = {}
        for key, value in documents.items():
            name = self.prefix + '/packaging/synthetic.inputs/' + p.sha(value) + '.json'
            self.write(name, value)
            captured[key] = dict(path=name, sha256=p.sha(value), bytes=len(value))
        return dict(captured_inputs=captured, selected_data=json.loads(documents['selected_data']))

    def test_default_and_separate_snapshot_are_selected_explicitly(self):
        before = p.file_hashes(self.default)
        default = p.load_documents()
        selected = p.load_documents(self.selected)
        self.assertEqual(json.loads(default['results'])['marker'], 'default-82')
        self.assertEqual(json.loads(selected['results'])['marker'], 'separate-partial')
        metadata = json.loads(selected['selected_data'])
        self.assertEqual(metadata['directory'], self.selected)
        self.assertEqual(metadata['results_path'], self.selected + '/results.json')
        self.assertEqual(set(metadata['files']), {'results.json', 'sources/scope.json', 'sources/summary.json', 'sources/plan.json'})
        self.assertFalse(json.loads(selected['results'])['coverage']['ready'])
        self.assertEqual(p.file_hashes(self.default), before)

    def test_missing_directory_or_referenced_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            p.load_documents(self.prefix + '/data-snapshots/missing')
        (self.root / self.selected / 'sources/summary.json').unlink()
        with self.assertRaises(FileNotFoundError):
            p.load_documents(self.selected)

    def test_wrong_source_bytes_or_scope_rejected(self):
        self.write(self.selected + '/sources/summary.json', b'{}')
        with self.assertRaisesRegex(ValueError, 'snapshot drift'):
            p.load_documents(self.selected)
        data = self.fixture(self.selected, 'partial')
        wrong_scope = p.canonical(dict(count=98))
        self.write(self.selected + '/sources/scope.json', wrong_scope)
        data['scope_source']['sha256'] = data['scope_sha256'] = p.sha(wrong_scope)
        self.write(self.selected + '/results.json', data)
        with self.assertRaisesRegex(ValueError, 'Scope snapshot differs'):
            p.load_documents(self.selected)

    def test_unsafe_directory_and_reference_rejected(self):
        for directory in ('../escape', self.prefix + '/packaging', self.prefix + '/data-snapshots'):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                p.load_documents(directory)
        data = self.fixture(self.selected, 'partial')
        data['sources']['campaign.summary.json']['snapshot'] = '../data/sources/summary.json'
        self.write(self.selected + '/results.json', data)
        with self.assertRaisesRegex(ValueError, 'Unsafe relative path'):
            p.load_documents(self.selected)

    def test_symlink_directory_or_file_rejected(self):
        alias = self.root / self.prefix / 'data-snapshots/alias'
        alias.symlink_to(self.root / self.selected, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            p.load_documents(alias)
        target = self.root / self.selected / 'sources/summary.json'
        target.unlink()
        target.symlink_to(self.root / self.default / 'sources/summary.json')
        with self.assertRaises((OSError, ValueError)):
            p.load_documents(self.selected)

    def test_shared_hash_preserves_each_relative_alias(self):
        data = self.fixture(self.selected, 'partial')
        source = dict(data['sources']['campaign.summary.json'], snapshot='other/summary-copy.json')
        content = (self.root / self.selected / 'sources/summary.json').read_bytes()
        self.write(self.selected + '/other/summary-copy.json', content)
        data['sources']['alias.summary.json'] = source
        self.write(self.selected + '/results.json', data)
        docs = p.load_documents(self.selected)
        selected = json.loads(docs['selected_data'])
        self.assertEqual(selected['files']['sources/summary.json']['document_key'], selected['files']['other/summary-copy.json']['document_key'])
        plan = self.capture(docs)
        output = self.root / 'synthetic-copy'
        p.copy_selected_data(plan, output)
        self.assertEqual((output / 'data-snapshot/other/summary-copy.json').read_bytes(), content)
        self.assertEqual((output / 'data-snapshot/sources/summary.json').read_bytes(), content)

    def test_snapshot_layout_collision_rejected(self):
        base_docs = p.load_documents(self.selected)
        for first, second in [('results.json', 'sources/plan.json'), ('A.json', 'a.json'), ('A', 'a/child.json'),
                              ('same.json', 'same.json')]:
            docs = copy.deepcopy(base_docs)
            data = json.loads(docs['results'])
            data['sources']['campaign.summary.json']['snapshot'] = first
            data['campaign_plans']['campaign']['snapshot'] = second
            docs['results'] = p.canonical(data)
            with self.subTest(paths=(first, second)), self.assertRaises(ValueError):
                p.data_snapshot_metadata(self.selected, docs)

    def test_captured_snapshot_copies_after_original_directory_removed(self):
        docs = p.load_documents(self.selected)
        plan = self.capture(docs)
        shutil.rmtree(self.root / self.selected)
        output = self.root / 'synthetic-copy'
        p.copy_selected_data(plan, output)
        self.assertEqual((output / 'data-snapshot/results.json').read_bytes(), docs['results'])
        data = json.loads((output / 'data-snapshot/results.json').read_bytes())
        for reference in [data['scope_source'], *data['sources'].values(), *data['campaign_plans'].values()]:
            self.assertEqual(p.sha((output / 'data-snapshot' / reference['snapshot']).read_bytes()), reference['sha256'])

    def test_captured_or_projected_metadata_drift_rejected(self):
        docs = p.load_documents(self.selected)
        plan = self.capture(docs)
        bad = copy.deepcopy(plan)
        bad['selected_data']['directory'] = self.default
        with self.assertRaisesRegex(ValueError, 'binding differs'):
            p.verify_selected_data(bad, docs)
        source = plan['captured_inputs']['results']['path']
        self.write(source, b'changed frozen bytes')
        with self.assertRaisesRegex(ValueError, 'Frozen plan provenance changed'):
            p.copy_selected_data(plan, self.root / 'synthetic-copy')

    def test_results_drift_during_load_rejected(self):
        original = p.file_info
        def changed(path):
            if path == self.selected + '/results.json':
                self.write(path, b'changed after first read')
            return original(path)
        with patch.object(p, 'file_info', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed while loading'):
            p.load_documents(self.selected)

    def test_legacy_plan_has_no_inferred_selector(self):
        self.assertIsNone(p.verify_selected_data({}, {'results': b'{}'}))
        with self.assertRaisesRegex(ValueError, 'missing from captured'):
            p.verify_selected_data({'selected_data': {'directory': self.selected}}, {'results': b'{}'})

    def test_cli_selector_only_allowed_during_planning(self):
        with patch('sys.argv', ['package', '--data-dir', self.selected]), self.assertRaisesRegex(ValueError, '--plan-only'):
            p.main()
        result = dict(coverage={}, sizes={}, report={}, plan_sha256='synthetic')
        with patch('sys.argv', ['package', '--plan-only', '--data-dir', self.selected]), \
             patch.object(p, 'make_plan', return_value=result) as make, patch('sys.stdout', new_callable=io.StringIO):
            p.main()
        self.assertEqual(make.call_args.args[-1], Path(self.selected))


if __name__ == '__main__':
    unittest.main(verbosity=2)

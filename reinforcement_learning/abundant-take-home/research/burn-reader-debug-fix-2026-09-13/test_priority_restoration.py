import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import promote_v4 as promotion


class PriorityGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.base = self.root / 'proof'
        self.base.mkdir()
        self.saved = promotion.ROOT, promotion.BASE
        promotion.ROOT, promotion.BASE = self.root, self.base

    def tearDown(self):
        promotion.ROOT, promotion.BASE = self.saved
        self.temporary.cleanup()

    def fixture(self):
        journal = self.base / 'validation-priority'
        journal.mkdir()
        changes, restored = [], []
        for index in range(3):
            name = f'control-{index}.json'
            (self.root / name).write_text('original')
            digest = hashlib.sha256(b'original').hexdigest()
            changes.append(dict(path=name, original_sha256=digest))
            restored.append(dict(path=name, status='restored', current_sha256=digest))
        (journal / 'intent.json').write_text(json.dumps(dict(changes=changes)))
        (journal / 'restoration.json').write_text(json.dumps(dict(passed=True, controls=restored)))
        return journal

    def test_no_reservation(self):
        self.assertIsNone(promotion.validate_priority_restored())

    def test_pending_refused(self):
        journal = self.fixture()
        (journal / 'restoration.json').unlink()
        with self.assertRaises(FileNotFoundError):
            promotion.validate_priority_restored()

    def test_verified_restoration(self):
        journal = self.fixture()
        self.assertEqual(promotion.validate_priority_restored(),
                         promotion.digest(journal / 'restoration.json'))

    def test_concurrent_change_preserved_and_refused(self):
        self.fixture()
        (self.root / 'control-0.json').write_text('new hold')
        with self.assertRaises(AssertionError):
            promotion.validate_priority_restored()
        self.assertEqual((self.root / 'control-0.json').read_text(), 'new hold')

    def test_partial_restoration_refused(self):
        journal = self.fixture()
        report = json.loads((journal / 'restoration.json').read_text())
        report['controls'][0]['status'] = 'concurrent_edit_preserved'
        (journal / 'restoration.json').write_text(json.dumps(report))
        with self.assertRaises(AssertionError):
            promotion.validate_priority_restored()

    def test_duplicate_paths_refused(self):
        journal = self.fixture()
        report = json.loads((journal / 'restoration.json').read_text())
        report['controls'][0] = report['controls'][1]
        (journal / 'restoration.json').write_text(json.dumps(report))
        with self.assertRaises(AssertionError):
            promotion.validate_priority_restored()


if __name__ == '__main__':
    unittest.main()

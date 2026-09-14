"""Regression tests for selection refresh racing with a locked task promotion.

All state and job files are disposable fixtures. No live queue or model runs.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import benchmark_interleaving as rounds


class FixtureShared:
    def __init__(self, root, policy):
        self.path = root / 'shared.control.json'
        self.state = {'interleaving': policy, 'unrelated_shared_state': {'preserve': True}}
        self.on_lock = None

    @contextmanager
    def locked(self):
        # A competing promotion can finish after an unlocked reader samples the
        # old plan, but before that reader obtains the shared admission lock.
        if self.on_lock is not None:
            callback, self.on_lock = self.on_lock, None
            callback()
        yield {}, self.state


class SelectionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user_path = self.root / 'benchmark-user-plan.json'
        self.old = {'active_full_campaign': 'main', 'active_revision_campaigns': ['old'],
                    'excluded_tasks': []}
        self.new = dict(self.old, active_revision_campaigns=['new'])
        for campaign in ('main', 'old', 'new'):
            directory = self.root / campaign
            directory.mkdir()
            (self.root / f'{campaign}.plan.json').write_text(json.dumps({'attempts': 20}))
            (directory / 'config.json').write_text(json.dumps({
                'agents': [{'model_name': 'model', 'kwargs': {'reasoning_effort': 'high'}}],
                'tasks': [{'path': campaign + '-task'}]}))
        self.user_path.write_text(json.dumps(self.old))
        self.shared = FixtureShared(self.root, rounds.seed(self.root, self.old))
        rounds._cache.clear()

    def test_plan_is_sampled_after_competing_promotion_releases_shared_lock(self):
        promoted = rounds.seed(self.root, self.new)
        promoted['superseded_campaigns'] = {'old': {'counts': {'old-task': 4}, 'receipts': {'past': 1}}}

        def competing_promotion():
            self.user_path.write_text(json.dumps(self.new))
            self.shared.state['interleaving'] = deepcopy(promoted)

        self.shared.on_lock = competing_promotion
        rounds.refresh_selection(self.shared)
        self.assertEqual(self.shared.state['interleaving'], promoted)
        self.assertEqual(self.shared.state['unrelated_shared_state'], {'preserve': True})

    def test_legitimate_refresh_preserves_unknown_history_and_existing_receipts(self):
        policy = self.shared.state['interleaving']
        main_key = rounds.cell_key('main-task', 'model', 'high')
        old_key = rounds.cell_key('old-task', 'model', 'high')
        main_receipt = {'cell': main_key, 'job': 'main', 'iteration': 1,
                        'source': 'atomic_admission', 'started_at': 123.0}
        old_receipt = {'cell': old_key, 'job': 'old', 'iteration': 1,
                       'source': 'atomic_admission', 'started_at': 124.0}
        policy.update(installed_at=12.0,
                      receipts={'main-trial': main_receipt, 'old-trial': old_receipt},
                      counts={main_key: 1, old_key: 1},
                      retired_receipts={'previous': {'reason': 'unrelated recovery'}},
                      superseded_campaigns={'older': {'counts': [1, 2], 'receipts': {'keep': {'value': 3}}}},
                      custom_review_metadata={'nested': ['retain', {'exact': True}]})
        before = deepcopy(policy)
        self.user_path.write_text(json.dumps(self.new))
        rounds.refresh_selection(self.shared)
        after = self.shared.state['interleaving']
        self.assertEqual(after['campaigns'], ['main', 'new'])
        for key in ('superseded_campaigns', 'custom_review_metadata', 'installed_at'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['receipts']['main-trial'], main_receipt)
        self.assertNotIn('old-trial', after['receipts'])
        self.assertEqual(after['retired_receipts']['previous'], before['retired_receipts']['previous'])
        self.assertEqual(after['retired_receipts']['old-trial'],
                         dict(old_receipt, reason='task version superseded by user plan'))
        self.assertEqual(after['counts'], {main_key: 1, rounds.cell_key('new-task', 'model', 'high'): 0})
        self.assertEqual(policy, before)
        self.assertEqual(self.shared.state['unrelated_shared_state'], {'preserve': True})

    def test_unchanged_selection_and_missing_user_plan_are_noops(self):
        before = deepcopy(self.shared.state)
        rounds.refresh_selection(self.shared)
        self.assertEqual(self.shared.state, before)
        self.user_path.unlink()
        rounds.refresh_selection(self.shared)
        self.assertEqual(self.shared.state, before)


if __name__ == '__main__':
    unittest.main()

from copy import deepcopy
import json
import unittest

from promotion_policy import OLD_TASK, NEW_TASK, replace_policy, replace_user_plan


class PromotionTests(unittest.TestCase):
    def policy(self):
        cells, counts, receipts = {}, {}, {}
        for i in range(13):
            for model in ('fable-5-1', 'opus-5', 'sonnet-5'):
                for effort in ('medium', 'high', 'max'):
                    task = OLD_TASK if i == 5 else 'task-' + str(i)
                    name = 'old' if i == 5 else 'campaign-' + str(i)
                    model_id = 'anthropic/claude-' + model
                    key = json.dumps([task, model_id, effort], separators=(',', ':'))
                    cells[key] = dict(task=task, model=model_id, effort=effort, job=name, target=20)
                    counts[key] = 1
                    receipts['trial-' + str(len(receipts))] = dict(cell=key, job=name, iteration=1)
        return dict(version=1, campaigns=['main', 'old', 'zenoh', 'diskcache'],
                    cells=cells, counts=counts, receipts=receipts, installed_at=123,
                    retired_receipts={'prior': {'reason': 'unrelated recovery'}})

    def test_preserves_other_progress_and_rank_and_all_history(self):
        before = self.policy()
        original = deepcopy(before)
        after = replace_policy(before, 'old', 'new', 'now')
        self.assertEqual(before, original)
        unchanged = [k for k, c in before['cells'].items() if c['job'] != 'old']
        self.assertEqual(len(unchanged), 108)
        for k in unchanged:
            self.assertEqual(after['cells'][k], before['cells'][k])
            self.assertEqual(after['counts'][k], before['counts'][k])
        self.assertEqual(sum(after['counts'].values()), 108)
        self.assertEqual(len(after['superseded_campaigns']['old']['receipts']), 9)
        self.assertEqual(after['retired_receipts'], before['retired_receipts'])
        self.assertEqual(sum(c['target'] for c in after['cells'].values()), 2340)
        self.assertTrue(all(c['task'] == NEW_TASK for c in after['cells'].values() if c['job'] == 'new'))

    def test_refuses_duplicate_promotion_and_incompatible_policy(self):
        p = self.policy()
        after = replace_policy(p, 'old', 'new', 'now')
        with self.assertRaises(AssertionError):
            replace_policy(after, 'old', 'new', 'later')
        for change in ('target', 'count', 'receipt', 'combination'):
            p = self.policy()
            k = next(k for k, c in p['cells'].items() if c['job'] == 'old')
            if change == 'target': p['cells'][k]['target'] = 19
            if change == 'count': p['counts'][k] = -1
            if change == 'receipt': p['receipts']['trial-0']['cell'] = 'missing'
            if change == 'combination': p['cells'][k]['model'] = 'different-model'
            with self.subTest(change=change), self.assertRaises(AssertionError):
                replace_policy(p, 'old', 'new', 'now')

    def test_user_plan_preserves_other_cohorts_and_targets(self):
        before = dict(total_full_trials=2340, shared_concurrency=12,
                      iterations_per_task_model_effort=20,
                      active_revision_campaigns=['zenoh', 'old', 'diskcache'],
                      superseded_do_not_resume=['older'],
                      task_revisions=[dict(task=OLD_TASK, campaign='old', untouched='value'),
                                      dict(task='zenoh', campaign='zenoh')])
        saved = deepcopy(before)
        after = replace_user_plan(before, 'old', 'new',
                                  dict(id=NEW_TASK, revision_of='rs-burn-store-pytorch-reader'),
                                  'hash', 'now')
        self.assertEqual(before, saved)
        self.assertEqual(after['active_revision_campaigns'], ['zenoh', 'new', 'diskcache'])
        self.assertEqual(after['superseded_do_not_resume'], ['older', 'old'])
        self.assertEqual(after['task_revisions'][1], before['task_revisions'][1])
        self.assertEqual(after['historical_task_revisions'], [before['task_revisions'][0]])
        self.assertEqual(after['total_full_trials'], 2340)


if __name__ == '__main__':
    unittest.main()

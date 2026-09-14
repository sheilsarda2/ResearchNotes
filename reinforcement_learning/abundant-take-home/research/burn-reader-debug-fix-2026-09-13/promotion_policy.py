"""Pure, reviewed transformations for the Burn reader v3 -> v4 promotion.

The caller must hold the shared admission lock and verify that the retired
campaign has no runner, live containers or reservations. This module never
reads or writes the live campaign state.
"""
from copy import deepcopy
from collections import Counter
import json

OLD_TASK = 'rs-burn-store-pytorch-reader-v3'
NEW_TASK = 'rs-burn-store-pytorch-reader-v4'


def replace_policy(policy, old_campaign, new_campaign, at):
    assert policy['version'] == 1
    assert policy['campaigns'].count(old_campaign) == 1
    assert new_campaign not in policy['campaigns']
    assert len(policy['cells']) == 117
    assert set(policy['counts']) == set(policy['cells'])
    old_keys = [key for key, cell in policy['cells'].items()
                if cell['job'] == old_campaign]
    assert len(old_keys) == 9
    assert {policy['cells'][key]['task'] for key in old_keys} == {OLD_TASK}
    combinations = {(policy['cells'][key]['model'], policy['cells'][key]['effort'])
                    for key in old_keys}
    expected = {(f'anthropic/claude-{model}', effort)
                for model in ('fable-5-1', 'opus-5', 'sonnet-5')
                for effort in ('medium', 'high', 'max')}
    assert combinations == expected
    assert all(cell['target'] == 20 for cell in policy['cells'].values())
    assert all(0 <= policy['counts'][key] <= cell['target']
               for key, cell in policy['cells'].items())
    assert all(key == json.dumps([cell['task'], cell['model'], cell['effort']], separators=(',', ':'))
               for key, cell in policy['cells'].items())
    for name, receipt in policy['receipts'].items():
        assert receipt['cell'] in policy['cells'], name
        assert receipt['job'] == policy['cells'][receipt['cell']]['job'], name
    receipt_counts = Counter(r['cell'] for r in policy['receipts'].values())
    assert all(policy['counts'][key] == receipt_counts[key] for key in policy['cells'])

    result = deepcopy(policy)
    result['campaigns'] = [new_campaign if name == old_campaign else name
                           for name in policy['campaigns']]
    cells, counts, mapping = {}, {}, {}
    for key, cell in policy['cells'].items():
        if key not in old_keys:
            cells[key], counts[key] = deepcopy(cell), policy['counts'][key]
            continue
        new_key = json.dumps([NEW_TASK, cell['model'], cell['effort']], separators=(',', ':'))
        assert new_key not in policy['cells']
        mapping[key] = new_key
        cells[new_key] = dict(cell, task=NEW_TASK, job=new_campaign)
        counts[new_key] = 0
    result['cells'], result['counts'] = cells, counts
    history = {
        'retired_at': at, 'reason': 'Undocumented Debug bound in hidden error assertions',
        'replacement_campaign': new_campaign, 'replacement_task': NEW_TASK,
        'cells': {k: deepcopy(policy['cells'][k]) for k in old_keys},
        'counts': {k: policy['counts'][k] for k in old_keys},
        'receipts': {n: deepcopy(r) for n, r in policy['receipts'].items()
                     if r['cell'] in old_keys},
        'replacement_keys': mapping,
    }
    retired = result.setdefault('superseded_campaigns', {})
    assert old_campaign not in retired
    retired[old_campaign] = history
    result['receipts'] = {n: deepcopy(r) for n, r in policy['receipts'].items()
                          if r['cell'] not in old_keys}
    assert len(cells) == 117 and sum(c['target'] for c in cells.values()) == 2340
    assert list(cells) == [mapping.get(k, k) for k in policy['cells']]
    assert set(result['receipts']) | set(history['receipts']) == set(policy['receipts'])
    for key in set(policy) - {'campaigns', 'cells', 'counts', 'receipts', 'superseded_campaigns'}:
        assert result[key] == policy[key]
    return result


def replace_user_plan(plan, old_campaign, new_campaign, task, manifest_sha256, at):
    assert plan['total_full_trials'] == 2340 and plan['shared_concurrency'] == 12
    assert plan['iterations_per_task_model_effort'] == 20
    assert plan['active_revision_campaigns'].count(old_campaign) == 1
    assert new_campaign not in plan['active_revision_campaigns']
    assert task['id'] == NEW_TASK and task['revision_of'] == 'rs-burn-store-pytorch-reader'
    result = deepcopy(plan)
    result['updated_at'] = at
    result['active_revision_campaigns'] = [new_campaign if n == old_campaign else n
                                           for n in plan['active_revision_campaigns']]
    retired = result.setdefault('superseded_do_not_resume', [])
    assert old_campaign not in retired
    retired.append(old_campaign)
    matches = [r for r in result['task_revisions'] if r['campaign'] == old_campaign]
    assert len(matches) == 1 and matches[0]['task'] == OLD_TASK
    result.setdefault('historical_task_revisions', []).append(deepcopy(matches[0]))
    matches[0].update(task=NEW_TASK, campaign=new_campaign,
                     manifest_sha256=manifest_sha256,
                     validation='oracle=1, nop=0; captured standard runtime; saved alternative implementation compiles all 108 hidden tests',
                     evidence='research/burn-reader-debug-fix-2026-09-13',
                     historical_results_remain_separate=True)
    return result

"""Prioritize first counted results using supervisor evidence and live claims.

Workers use only the already-loaded shared state. A separate publisher performs
all summary I/O, outside the active trial processes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

COUNTED = frozenset({'scored', 'timeout', 'verifier_timeout'})


def coverage_status(state, held=()):
    policy = state['interleaving']
    coverage = policy.get('first_sweep')
    if not coverage or not coverage.get('enabled'):
        return None
    cells = policy['cells']
    completed = coverage['completed']
    scope = {key: cell['job'] for key, cell in cells.items()}
    if (coverage.get('version') != 1 or coverage.get('scope') != scope
            or set(completed) != set(cells)
            or any(type(n) is not int or not 0 <= n <= cells[k]['target'] for k, n in completed.items())
            or not isinstance(coverage['settled_receipts'], dict)):
        raise ValueError('Coverage snapshot does not match current campaign selection')
    active = {name for p in state.get('participants', {}).values() for name in p.get('trials', {})}
    busy = set()
    for name, receipt in policy['receipts'].items():
        key = receipt['cell']
        if key not in cells or cells[key]['job'] != receipt['job']:
            continue
        # A finalized result may precede container cleanup. Conversely a slot
        # can be released before the supervisor has classified that result.
        # Both cases must prevent a duplicate first-result attempt.
        if name in active or name not in coverage['settled_receipts']:
            busy.add(key)
    missing = {key for key, count in completed.items() if count == 0 and key not in held}
    available = {key for key in missing - busy if policy['counts'][key] < cells[key]['target']}
    return completed, busy, available


def priority_decision(state, key, held=()):
    """Return (bypass dispatch-round barrier, wait reason), never raise on data."""
    try:
        status = coverage_status(state, held)
        if status is None:
            return False, None
        completed, busy, available = status
        if key in available:
            return True, None
        if completed[key] == 0 and key in busy:
            return False, 'interleaving: first result already running or awaiting classification'
        if available:
            return False, 'interleaving: prioritizing missing first results'
        return False, None
    except (KeyError, TypeError, ValueError, AttributeError):
        return False, 'interleaving: waiting for valid first-result snapshot'


def build_snapshot(jobs_root, policy):
    root = Path(jobs_root)
    cells = policy['cells']
    scope = {key: cell['job'] for key, cell in cells.items()}
    completed, settled, sources = {}, {}, {}
    for campaign in policy['campaigns']:
        path = root / f'{campaign}.summary.json'
        content = path.read_bytes()
        summary = json.loads(content)
        if summary['job'] != campaign:
            raise ValueError('Summary belongs to a different campaign')
        sources[campaign] = {'sha256': hashlib.sha256(content).hexdigest(),
                             'updated_at': summary['updated_at']}
        actual = {}
        for row in summary['settings']:
            key = json.dumps([row['task'], 'anthropic/claude-' + row['model'], row['effort']], separators=(',', ':'))
            if key not in scope or scope[key] != campaign:
                continue
            if key in completed or row['target'] != cells[key]['target'] or type(row['completed']) is not int:
                raise ValueError('Invalid or duplicate result-count row')
            completed[key] = row['completed']
            actual[key] = 0
        for record in summary['trials']:
            key = json.dumps([record['task'], 'anthropic/claude-' + record['model'], record['effort']], separators=(',', ':'))
            if key not in actual:
                continue
            status = record['status']
            if status in COUNTED:
                actual[key] += 1  # Includes graded failures and counted timeouts.
            if status not in COUNTED | {'infrastructure'}:
                continue  # Unknown outcomes remain pending, without automatic retries.
            trial = Path(record['trial'])
            if trial.parent.name == campaign:
                settled[trial.name] = {'cell': key, 'job': campaign, 'status': status}
        if any(actual[k] != completed[k] for k in actual):
            raise ValueError('Count rows disagree with classified trial records')
    if set(completed) != set(cells):
        raise ValueError('Coverage snapshot omitted a selected cell')
    if any(not 0 <= n <= cells[k]['target'] for k, n in completed.items()):
        raise ValueError('Counted results exceed campaign target')
    return {'version': 1, 'enabled': True, 'scope': scope, 'completed': completed,
            'settled_receipts': settled, 'sources': sources, 'updated_at': time.time(),
            'phase': 'complete' if all(completed.values()) else 'prioritizing_missing_results'}


def dump(path, value):
    temporary = path.with_name(path.name + f'.coverage-{os.getpid()}.tmp')
    with temporary.open('w') as output:
        json.dump(value, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


@contextmanager
def state_lock(control):
    with control.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def publish(control):
    control = Path(control)
    state_path = control.with_suffix('.state.json')
    # Summary reads never happen under the resource lock.
    state = json.loads(state_path.read_text())
    snapshot = build_snapshot(control.parent, state['interleaving'])
    with state_lock(control):
        current = json.loads(state_path.read_text())
        scope = {key: c['job'] for key, c in current['interleaving']['cells'].items()}
        if scope != snapshot['scope']:
            raise ValueError('Campaign selection changed during coverage refresh')
        current['interleaving']['first_sweep'] = snapshot
        # Counts, receipts, participant claims and resource controls are retained.
        dump(state_path, current)
    return {'phase': snapshot['phase'], 'covered': sum(n > 0 for n in snapshot['completed'].values()),
            'cells': len(snapshot['completed']), 'updated_at': snapshot['updated_at']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    if not 1 <= args.interval <= 30:
        parser.error('Use an interval between 1 and 30 seconds')
    # Singleton publisher; a guardian can restart it without concurrent writers.
    with args.shared.with_suffix('.coverage.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status_path = args.shared.with_suffix('.coverage.json')
        while True:
            try:
                report = {'pid': os.getpid(), 'healthy': True, **publish(args.shared)}
            except (OSError, ValueError, KeyError, TypeError) as error:
                # Preserve the last good snapshot; running trials remain untouched.
                report = {'pid': os.getpid(), 'healthy': False, 'error_type': type(error).__name__,
                          'updated_at': time.time()}
            dump(status_path, report)
            if not args.watch:
                print(json.dumps(report))
                return 0 if report['healthy'] else 1
            time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())

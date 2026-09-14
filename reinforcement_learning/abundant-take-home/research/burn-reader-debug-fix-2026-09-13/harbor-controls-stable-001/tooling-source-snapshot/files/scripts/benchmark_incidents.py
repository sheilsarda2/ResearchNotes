"""Classify individually audited orchestration incidents without changing raw trials."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from benchmark_evidence import hash_file
import benchmark_trial_intervention as interventions

ROOT = Path(__file__).resolve().parents[1]
# Keep qualified historical helpers when a new implementation is validated.
# This one passed the real Harbor sibling-isolation and Docker teardown checks.
APPROVED_CONTAINMENT_HELPERS = {
    '3298ed48b36cb0c8704cce006e1f23162a55689bac0b7cac2de1d7507ac8ca8e',
}


def registry_cache_stamp():
    """Invalidate summaries when an audited incident or its fix proof changes.

    Malformed metadata gets a stable fingerprint here; classify_incident then
    rejects it through the supervisor's normal evidence-review boundary.
    """
    entries = []
    for path in sorted((ROOT / 'research/benchmark-incidents').glob('*/incident.json')):
        name = str(path.relative_to(ROOT))
        try:
            content = path.read_bytes()
            entries.append((name, hashlib.sha256(content).hexdigest()))
            resolution = json.loads(content).get('resolution')
            if not isinstance(resolution, dict) or not isinstance(resolution.get('proof'), str):
                continue
            proof = (ROOT / resolution['proof']).resolve()
            if not proof.is_relative_to(ROOT / 'research'):
                entries.append((name, 'proof_outside_research'))
                continue
            entries.append((str(proof.relative_to(ROOT)), hash_file(proof)))
        except (OSError, ValueError, TypeError, AttributeError, RuntimeError) as error:
            entries.append((name, type(error).__name__))
    return tuple(entries) + interventions.registry_cache_stamp(root=ROOT)


def containment_pending(trial_dir, result, *, now=None):
    """Wait briefly for a qualified post-finalization containment marker."""
    error = (result.get('exception_info') or {}).get('exception_type')
    if (error != 'AgentQuiescenceError' or result.get('verifier') is not None
            or result.get('verifier_result') is not None
            or (Path(trial_dir) / 'benchmark-containment.json').exists()):
        return False
    runtime_path = Path(trial_dir) / 'benchmark-runtime.json'
    if not runtime_path.exists():
        return False
    runtime = json.loads(runtime_path.read_text())
    assert isinstance(runtime, dict), 'Invalid containment runtime metadata'
    if runtime.get('trial_containment_sha256') not in APPROVED_CONTAINMENT_HELPERS:
        return False
    finished = result.get('finished_at')
    if not isinstance(finished, str):
        return False
    stamp = datetime.fromisoformat(finished.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        return False
    age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    return 0 <= age < 30


def contained_failure(trial_dir, result):
    marker_path = trial_dir / 'benchmark-containment.json'
    if not marker_path.exists():
        return None
    marker = json.loads(marker_path.read_text())
    runtime = json.loads((trial_dir / 'benchmark-runtime.json').read_text())
    assert marker['version'] == 1 and marker['reason'] == 'finalized_ungraded_guard_failure'
    assert marker['escaped_exception_type'] == 'AgentQuiescenceError'
    assert marker['result_sha256'] == hash_file(trial_dir / 'result.json', root=trial_dir)
    assert runtime['trial_containment_sha256'] in APPROVED_CONTAINMENT_HELPERS
    assert marker['end_hooks_completed'] is True and marker['grading_withheld'] is True
    assert type(marker['running_project_containers']) is int and marker['running_project_containers'] == 0
    assert isinstance(marker['compose_project'], str) and marker['compose_project']
    assert result.get('verifier_result') is None
    return {'code': 'agent_cleanup_failure_contained', 'resolved': False,
            'containment_proof': str(marker_path.relative_to(ROOT)),
            'containment_sha256': hash_file(marker_path, root=trial_dir)}


def classify_incident(trial_dir, result):
    if intervention := interventions.classify_intervention(trial_dir, result, root=ROOT):
        return intervention
    error = (result.get('exception_info') or {}).get('exception_type')
    if error not in {'AgentQuiescenceError', 'CancelledError'}:
        return None
    if not result.get('finished_at') or result.get('verifier') is not None:
        return None
    if (result.get('verifier_result') or {}).get('rewards'):
        return None
    trial_dir = Path(trial_dir).resolve()
    if error == 'AgentQuiescenceError' and (proof := contained_failure(trial_dir, result)):
        return proof
    relative = str(trial_dir.relative_to(ROOT))
    for path in sorted((ROOT / 'research/benchmark-incidents').glob('*/incident.json')):
        incident = json.loads(path.read_text())
        assert isinstance(incident, dict) and isinstance(incident.get('trials', {}), dict), 'Invalid incident registry entry'
        entry = incident.get('trials', {}).get(relative)
        if entry is None:
            continue
        assert incident['kind'] == 'guard_failure_cancelled_sibling_trials'
        assert entry['exception_type'] == error
        assert set(entry['files']) == {'result.json', 'benchmark-evidence.json', 'benchmark-deadline.json'}
        for name, expected in entry['files'].items():
            assert hash_file(trial_dir / name, root=trial_dir) == expected, 'Audited incident evidence changed'
        trigger = ROOT / incident['trigger_trial']
        assert trigger.resolve().is_relative_to(ROOT / 'jobs')
        assert hash_file(trigger / 'result.json', root=trigger) == incident['trigger_result_sha256']
        resolved = False
        resolution = incident.get('resolution')
        if resolution:
            proof_path = (ROOT / resolution['proof']).resolve()
            assert proof_path.is_relative_to(ROOT / 'research')
            assert hash_file(proof_path) == resolution['proof_sha256']
            proof = json.loads(proof_path.read_text())
            assert proof['passed'] is True and proof['model_calls'] == 0
            resolved = True
        return {'code': 'guard_failure_cancelled_sibling_trials',
                'incident': str(path.relative_to(ROOT)), 'incident_sha256': hash_file(path),
                'resolved': resolved, 'trigger_trial': incident['trigger_trial']}
    return None

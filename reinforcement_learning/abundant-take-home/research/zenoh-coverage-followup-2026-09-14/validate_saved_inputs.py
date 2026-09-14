"""Read-only provenance gate for two structurally different saved successes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    audit_path = BASE / 'saved-source-audit/audit.json'
    audit = json.loads(audit_path.read_text())
    records = []
    for entry in audit['trials']:
        trial = ROOT / entry['trial']
        snapshot_path = trial / 'benchmark-snapshot.json'
        snapshot = json.loads(snapshot_path.read_text())
        source_root = trial / 'artifacts/submission'
        actual = {}
        for path in sorted(source_root.rglob('*')):
            assert not path.is_symlink(), 'Do not replay a symlinked source tree'
            if path.is_file():
                actual[str(path.relative_to(trial))] = {
                    'kind': 'file', 'sha256': digest(path), 'size': path.stat().st_size}
        expected = {name: value for name, value in snapshot['entries'].items()
                    if name.startswith('artifacts/submission/') and value['kind'] == 'file'}
        assert expected and actual == expected, 'Saved submitted source differs from pre-verifier capture'
        result_path = trial / 'result.json'
        result = json.loads(result_path.read_text())
        assert result['task_checksum'] == entry['task_checksum']
        assert result['verifier_result']['rewards']['reward'] == entry['reward'] == 1
        records.append({'trial': entry['trial'], 'original_reward': 1,
                        'original_task_checksum': entry['task_checksum'],
                        'result_sha256': digest(result_path),
                        'snapshot_sha256': digest(snapshot_path),
                        'source_file_count': len(actual),
                        'source_files': actual, 'matches_original_pre_verifier_capture': True,
                        'source_manifest_sha256': hashlib.sha256(json.dumps(
                            actual, sort_keys=True, separators=(',', ':')).encode()).hexdigest()})
    output = {'schema_version': 1, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'model_calls': 0, 'replays_started': 0, 'passed': True,
              'source_audit_sha256': digest(audit_path),
              'helper_sha256': digest(Path(__file__)), 'trials': records}
    target = BASE / 'saved-source-audit/full-source-preflight.json'
    with target.open('x') as file:
        json.dump(output, file, indent=2)
        file.write('\n')
    print(json.dumps({'passed': True, 'model_calls': 0,
                      'source_file_counts': [r['source_file_count'] for r in records],
                      'output': str(target.relative_to(ROOT)), 'sha256': digest(target)}))


if __name__ == '__main__':
    main()

"""Single-use continuation; never retries controls, reviewers, or regrades."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def identity(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    output = BASE / 'continuation-001'
    controls_path = BASE / 'harbor-controls-final/summary.json'
    latest = read(BASE / 'latest-controls.json')
    assert latest['output'] == str(controls_path.parent.relative_to(ROOT))
    owner_pid = latest['pid']
    owner_identity = identity(owner_pid)
    assert owner_identity is not None or read(controls_path).get('finished_at')
    sources = [BASE / name for name in (
        'continue_validation.py', 'run_followup_diagnostics.py',
        'run_final_quality_review.py', 'execute_quality_check.py',
        'audit_final_quality_review.py', 'validate_controls_immutable.py',
        'harness/focused_validation_v2.py', 'harness/case_inside_v2.py',
        'paired-regrades/run_paired_regrades.py', 'paired-regrades/case_inside.py',
        'task-manifest.json', 'revision.json', 'paired-inputs/manifest.json',
        'mutants/manifest.json', 'saved-source-audit/full-source-preflight.json')]
    expected = {str(p.relative_to(ROOT)): digest(p) for p in sources}
    fresh = [BASE / 'reviews-final-001', BASE / 'harness/diagnostics-final',
             BASE / 'paired-regrades/run-001',
             BASE / 'completed-review-audits/rs-zenoh-timestamp-instrumentation-v4-validation.json']
    assert not output.exists() and not any(p.exists() for p in fresh), 'Never duplicate an existing stage'
    if not args.run:
        print(json.dumps({'check_only': True, 'passed': True, 'controls_pid': owner_pid,
                          'controls_identity': owner_identity, 'source_sha256': expected,
                          'one_authorized_quality_review': True, 'solver_model_calls': 0,
                          'launches': 0}, indent=2))
        return
    with (BASE / 'continuation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir()
        status = {'started_at': datetime.now(timezone.utc).isoformat(), 'passed': False,
                  'controls_pid': owner_pid, 'controls_identity': owner_identity,
                  'source_sha256': expected, 'one_authorized_quality_review': True,
                  'solver_model_calls': 0, 'local_max_active_overall': 1, 'stages': {}}
        children = []

        def save():
            temp = output / 'summary.tmp'
            temp.write_text(json.dumps(status, indent=2) + '\n')
            temp.replace(output / 'summary.json')

        def unchanged():
            assert {str(p.relative_to(ROOT)): digest(p) for p in sources} == expected

        def start(label, tail):
            unchanged()
            assert not any(child.poll() is None for _, child in children), 'Only one active follow-up child'
            command = [sys.executable, '-B', *map(str, tail)]
            stream = (output / (label + '.log')).open('x')
            try:
                child = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            finally:
                stream.close()
            children.append((label, child))
            status['stages'][label] = {'command': command, 'pid': child.pid,
                                       'started_at': datetime.now(timezone.utc).isoformat()}
            save()
            print(datetime.now(timezone.utc).isoformat(), 'started', label, flush=True)
            return child

        def finish(label, child):
            code = child.wait()
            status['stages'][label].update(exit_code=code, finished_at=datetime.now(timezone.utc).isoformat())
            save()
            return code

        save()
        try:
            deadline = time.monotonic() + 21600
            while not read(controls_path).get('finished_at'):
                assert identity(owner_pid) == owner_identity, 'Existing control controller disappeared'
                assert time.monotonic() < deadline, 'Controls wait deadline; no process was stopped'
                time.sleep(5)
            assert read(controls_path)['passed'], 'Exact controls did not pass; no later stage started'
            status['controls_summary_sha256'] = digest(controls_path)
            quality = start('quality-review', [BASE / 'run_final_quality_review.py'])
            assert finish('quality-review', quality) == 0
            audit = start('quality-evidence-audit', [BASE / 'audit_final_quality_review.py'])
            assert finish('quality-evidence-audit', audit) == 0
            review = read(fresh[3])
            quality_summary = read(fresh[0] / 'summary.json')
            assert quality_summary['passed'] and len(quality_summary['reviews']) == 1
            quality_row = quality_summary['reviews']['rs-zenoh-timestamp-instrumentation-v4-validation']
            assert review['check_report'] == quality_row['check_report']
            assert review['check_report_sha256'] == quality_row['check_report_sha256'] == digest(ROOT / quality_row['check_report'])
            assert review['raw_trial'] == str(Path(quality_row['result']).parent)
            assert review['original_task_checksum'] == quality_row['original_task_checksum'] == read(BASE / 'task-manifest.json')['harbor_task_checksum']
            assert review['valid_review_report'] and review['rubric_pass'] == 11 and not review['rubric_fail'], 'Final quality criterion requires review'
            assert len(read(BASE / 'mutants/manifest.json')['mutants']) == 6
            assert len(read(BASE / 'saved-source-audit/full-source-preflight.json')['trials']) == 2
            diagnostics = start('focused-diagnostics', [BASE / 'run_followup_diagnostics.py',
                '--controls-summary', controls_path, '--output', fresh[1]])
            assert finish('focused-diagnostics', diagnostics) == 0
            assert read(fresh[1] / 'summary.json')['passed']
            image_proof = controls_path.parent / 'verifier-image-proof.json'
            paired_args = [BASE / 'paired-regrades/run_paired_regrades.py',
                '--task-manifest', BASE / 'task-manifest.json', '--controls-summary', controls_path,
                '--diagnostics-summary', fresh[1] / 'summary.json', '--verifier-image-proof', image_proof,
                '--verifier-image-id', read(image_proof)['verifier_image_id'], '--output', fresh[2]]
            gate = start('paired-file-gate', [*paired_args, '--check-only'])
            assert finish('paired-file-gate', gate) == 0
            paired = start('paired-full-regrades', [*paired_args, '--run'])
            assert finish('paired-full-regrades', paired) == 0
            unchanged()
            status['passed'] = True
            status['meaning'] = 'Execution/provenance completion. Quality criteria and paired rewards require separate review.'
        except BaseException as error:
            status.update(error_type=type(error).__name__, error=str(error))
        finally:
            # Observe/reap every already-started child. Never launch a replacement,
            # leave a reviewer running untracked, or signal existing controls.
            for label, child in children:
                if 'exit_code' not in status['stages'][label]:
                    finish(label, child)
            status['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()
        if not status['passed']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()

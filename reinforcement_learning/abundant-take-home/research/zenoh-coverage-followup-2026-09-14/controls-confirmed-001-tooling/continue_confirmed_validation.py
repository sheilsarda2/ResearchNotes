"""Additive single-use continuation after a reviewed combined-control proof."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
import recovery_common as common


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def quality_gate(gate):
    summary = common.read(common.REVIEW_OUTPUT / 'summary.json')
    require = common.require
    require(summary['passed'] is True and summary['controls_summary'] == gate['controls_reference']
            and len(summary['reviews']) == 1, 'One matching completed quality review is required')
    row = summary['reviews'][common.TASK.name]
    review = common.read(common.AUDIT)
    require(review['check_report'] == row['check_report']
            and review['check_report_sha256'] == row['check_report_sha256'] == common.digest(row['check_report'])
            and review['raw_trial'] == str(common.path(row['result']).parent.relative_to(common.ROOT))
            and review['original_task_checksum'] == row['original_task_checksum'] == common.TASK_CHECKSUM,
            'Quality audit does not describe this one review')
    validator = common.module('confirmed_final_quality_gate', common.ROOT / 'scripts/package-takehome-evidence.py')
    validator.verify_revision_quality(common.reference(common.AUDIT), gate['task'], set())
    return dict(summary=common.reference(common.REVIEW_OUTPUT / 'summary.json'), audit=common.reference(common.AUDIT))


def stage_commands(gate):
    controls = common.path(gate['controls_reference']['path'])
    image = common.path(gate['image_proof']['path'])
    paired = [common.BASE / 'paired-regrades/run_paired_regrades.py',
              '--task-manifest', common.BASE / 'task-manifest.json', '--controls-summary', controls,
              '--diagnostics-summary', common.DIAGNOSTICS / 'summary.json', '--verifier-image-proof', image,
              '--verifier-image-id', gate['verifier_image_id'], '--output', common.PAIRED]
    return [
        ('quality-review', [common.HERE / 'run_confirmed_quality_review.py', '--controls-summary', controls,
                            '--controls-sha256', gate['controls_reference']['sha256'], '--run']),
        ('quality-evidence-audit', [common.BASE / 'audit_final_quality_review.py']),
        ('focused-diagnostics', [common.BASE / 'run_followup_diagnostics.py', '--controls-summary', controls,
                                 '--output', common.DIAGNOSTICS]),
        ('paired-file-gate', [*paired, '--check-only']),
        ('paired-full-regrades', [*paired, '--run']),
    ]


def run_once(controls_path, controls_sha256):
    # Reuse the original continuation lock. A live prior controller cannot overlap this owner.
    with (common.BASE / 'continuation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source_manifest = common.frozen_sources()
        common.fresh_outputs()
        gate = common.combined_controls(controls_path, controls_sha256)
        common.OUTPUT.mkdir()
        status = dict(schema_version=1, started_at=now(), passed=False, pid=os.getpid(),
            controls_summary=gate['controls_reference'], recovery_tooling=source_manifest,
            one_authorized_quality_review=True, solver_model_calls=0, local_max_active_overall=1,
            automatic_retries=0, stages={})
        children = []

        def save():
            write(common.OUTPUT / 'summary.json', status)

        def finish(label, child):
            code = child.wait()
            status['stages'][label].update(exit_code=code, finished_at=now())
            save()
            return code

        def start(label, tail):
            common.frozen_sources()
            common.combined_controls(controls_path, controls_sha256)
            common.require(not any(child.poll() is None for _, child in children), 'Only one active follow-up child')
            argv = [sys.executable, '-B', *map(str, tail)]
            with (common.OUTPUT / (label + '.log')).open('x') as log:
                child = subprocess.Popen(argv, cwd=common.ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            children.append((label, child))
            status['stages'][label] = dict(pid=child.pid, command=argv, started_at=now())
            save()
            print(now(), 'started', label, flush=True)
            return child

        save()
        try:
            for label, command in stage_commands(gate):
                if label == 'focused-diagnostics':
                    status['quality_gate'] = quality_gate(gate)
                    common.require(len(common.read(common.BASE / 'mutants/manifest.json')['mutants']) == 6,
                                   'All six omission probes are required')
                    common.require(len(common.read(common.BASE / 'saved-source-audit/full-source-preflight.json')['trials']) == 2,
                                   'Both original saved-success diagnostics are required')
                if label == 'paired-file-gate':
                    diagnostics = common.read(common.DIAGNOSTICS / 'summary.json')
                    common.require(diagnostics['passed'] is True and diagnostics['model_calls'] == 0
                                   and diagnostics['counted_sweep_trials'] == 0
                                   and diagnostics['controls_summary_sha256'] == controls_sha256,
                                   'Focused diagnostics do not bind the combined controls')
                    common.require(sum(r['kind'] == 'single_omission_probe' for r in diagnostics['cases']) == 6
                                   and sum(r['kind'] == 'saved_success_diagnostic' for r in diagnostics['cases']) == 2,
                                   'Require all six omission probes and both saved diagnostics')
                child = start(label, command)
                common.require(finish(label, child) == 0, label + ' failed; no later stage or retry is authorized')
            paired = common.read(common.PAIRED / 'summary.json')
            common.require(paired['validation_passed'] is True and paired['all_regrades_complete'] is True
                           and len(paired['regrades']) == 9 and paired['model_calls'] == paired['counted_sweep_trials'] == 0
                           and paired['controls_summary_sha256'] == controls_sha256,
                           'All nine full saved-source regrades must complete under these controls')
            common.frozen_sources()
            common.combined_controls(controls_path, controls_sha256)
            status['paired_summary'] = common.reference(common.PAIRED / 'summary.json')
            status['passed'] = True
            status['meaning'] = 'Validation execution/provenance complete; original and paired rewards remain distinct. No packaging or promotion performed.'
        except BaseException as error:
            status.update(error_type=type(error).__name__, error=str(error))
        finally:
            for label, child in children:
                if 'exit_code' not in status['stages'][label]:
                    finish(label, child)
            status['finished_at'] = now()
            save()
        return 0 if status['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controls-summary', required=True)
    parser.add_argument('--controls-sha256', required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        frozen = common.frozen_sources()
        common.fresh_outputs()
        gate = common.combined_controls(args.controls_summary, args.controls_sha256)
        print(json.dumps(dict(check_only=True, passed=True, launches=0, solver_model_calls=0,
                             recovery_tooling=frozen, controls_summary=gate['controls_reference'])))
        return
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Recovery interrupted; existing children will be reaped without replacement')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(run_once(args.controls_summary, args.controls_sha256))


if __name__ == '__main__':
    main()

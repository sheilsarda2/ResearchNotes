"""Single-use full-regrade recovery; reuse all completed controls, quality and diagnostics."""
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


def preflight(controls_path, controls_sha256, *, fresh):
    source = common.frozen_sources()
    if fresh:
        common.fresh_outputs()
    previous = common.prior_failure_gate()
    repair = common.repair_preflight_gate()
    diagnostics = common.diagnostics_gate(controls_sha256)
    gate = common.combined_controls(controls_path, controls_sha256)
    quality = common.quality_gate(gate)
    return gate, dict(recovery_tooling=source, prior_attempt=previous,
                      repair_preflight=repair, reused_quality=quality, reused_diagnostics=diagnostics)


def stage_commands(gate):
    controls = common.path(gate['controls_reference']['path'])
    image = common.path(gate['image_proof']['path'])
    paired = [common.BASE / 'paired-regrades/run_paired_regrades_v2.py',
              '--task-manifest', common.BASE / 'task-manifest.json', '--controls-summary', controls,
              '--diagnostics-summary', common.DIAGNOSTICS / 'summary.json', '--verifier-image-proof', image,
              '--verifier-image-id', gate['verifier_image_id'], '--output', common.PAIRED]
    return [
        ('paired-file-gate', [*paired, '--check-only']),
        ('paired-full-regrades', [*paired, '--run']),
    ]


def run_once(controls_path, controls_sha256):
    # The same lock excludes every prior and additive continuation owner.
    with (common.BASE / 'continuation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        gate, evidence = preflight(controls_path, controls_sha256, fresh=True)
        common.OUTPUT.mkdir()
        status = dict(schema_version=1, started_at=now(), passed=False, pid=os.getpid(),
            controls_summary=gate['controls_reference'], **evidence,
            new_quality_reviews=0, new_focused_diagnostics=0, model_calls=0, counted_sweep_trials=0,
            local_max_active_overall=1, automatic_retries=0, stages={})
        children = []

        def save():
            write(common.OUTPUT / 'summary.json', status)

        def finish(label, child):
            code = child.wait()
            status['stages'][label].update(exit_code=code, finished_at=now())
            save()
            return code

        def start(label, tail):
            preflight(controls_path, controls_sha256, fresh=False)
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
                if label.startswith('paired-'):
                    status['diagnostics_gate'] = common.diagnostics_gate(controls_sha256)
                child = start(label, command)
                common.require(finish(label, child) == 0, label + ' failed; no later stage or retry is authorized')
            status['paired_summary'] = common.paired_gate(controls_sha256)
            preflight(controls_path, controls_sha256, fresh=False)
            status['passed'] = True
            status['meaning'] = ('Zero-model validation execution/provenance complete; quality002 and all eight diagnostics reused unchanged. '
                                 'Original and paired rewards remain distinct. No packaging or promotion performed.')
        except BaseException as error:
            status.update(error_type=type(error).__name__, error=str(error))
        finally:
            # Do not release the overall lock or start a replacement while a child owns resources.
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
        gate, evidence = preflight(args.controls_summary, args.controls_sha256, fresh=True)
        print(json.dumps(dict(check_only=True, passed=True, launches=0, model_calls=0,
                             new_quality_reviews=0, controls_summary=gate['controls_reference'], **evidence)))
        return
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Recovery interrupted; existing children will be reaped without replacement')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(run_once(args.controls_summary, args.controls_sha256))


if __name__ == '__main__':
    main()

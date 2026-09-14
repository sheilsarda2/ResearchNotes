"""One canonical quality attempt after combined controls; no retry or alternate output."""
import argparse
from datetime import datetime, timezone
import json
import os
import signal
import subprocess
import sys

sys.dont_write_bytecode = True
import recovery_common as common


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def command(proof_dir, job_name):
    return [sys.executable, '-B', str(common.HERE / 'execute_quality_retry.py'), '--proof-dir', str(proof_dir), '--',
            'check', str(common.TASK), '--agent', 'mini-swe-agent', '--model', 'anthropic/claude-sonnet-5',
            '--ak', 'version=2.4.6', '--ak', 'reasoning_effort=high',
            '--ae', 'ANTHROPIC_BASE_URL=https://take-home-automation.vercel.app',
            '--ae', 'ANTHROPIC_API_BASE=https://take-home-automation.vercel.app',
            '--n-concurrent', '1', '--n-attempts', '1', '--jobs-dir', str(common.REVIEW_OUTPUT / 'jobs'), '--job-name', job_name]


def run_once(controls_path, controls_sha256):
    frozen = common.frozen_sources()
    common.fresh_outputs(include_coordinator=False)
    gate = common.combined_controls(controls_path, controls_sha256)
    # Atomic canonical directory creation is the durable once-only guard before any model process.
    output = common.REVIEW_OUTPUT
    output.mkdir()
    summary = dict(schema_version=1, started_at=now(), model_calls=True, passed=False, reviews={},
                   controls_summary=gate['controls_reference'], recovery_tooling=frozen, automatic_retries=0)
    dump(output / 'summary.json', summary)
    child = None
    label = common.TASK.name
    record = dict(task=str(common.TASK.relative_to(common.ROOT)), started_at=now(), passed=False)
    try:
        legacy = common.module('confirmed_quality_legacy_validation', common.BASE / 'run_final_quality_review.py')
        observer = common.module('confirmed_quality_observer', common.OBSERVER)
        from dotenv import load_dotenv
        from harbor.models.task.task import Task
        load_dotenv(common.ROOT / '.env')
        os.environ['ANTHROPIC_API_KEY'] = os.environ['TAKE_HOME_TOKEN']
        assert os.environ['ANTHROPIC_API_KEY'], 'Existing routed credential missing'
        control = output / 'control.json'
        shared = common.ROOT / 'jobs/candidate-campaigns-shared.control.json'
        legacy.dump(control, dict(max_active=1, min_total_mb=30000, reserve_mb=4096,
            startup_reserve_mb=1536, startup_window_sec=120, start_interval_sec=5,
            max_memory_pressure_pct=1.0, pressure_cooldown_sec=60, network_pool_cidr='172.31.0.0/16',
            paused=False, shared_pool=str(shared)))
        task_key = str(common.TASK.relative_to(common.ROOT))
        files = legacy.hashes(common.TASK)
        assert files == gate['task']['task_file_sha256'] and Task(common.TASK).checksum == common.TASK_CHECKSUM
        legacy.dump(output / 'run-identity.json', dict(schema_version=1, started_at=now(), pid=os.getpid(),
            model_calls=True, model='anthropic/claude-sonnet-5', reasoning_effort='high', mini_version='2.4.6',
            inputs={task_key: dict(task_checksum=common.TASK_CHECKSUM, files=files)},
            control_sha256=common.digest(control), controls_summary=gate['controls_reference']['path'],
            controls_summary_sha256=controls_sha256, task_manifest_sha256=common.TASK_MANIFEST_SHA,
            recovery_tooling=frozen, observation_helper_sha256=common.digest(common.OBSERVER),
            canonical_output_once=True, automatic_retries=0))
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        job_name = label + '-quality-' + stamp
        proof_dir = output / (label + '-proof')
        observations = output / (label + '-admission.jsonl')
        gaps = output / (label + '-observation-gaps.jsonl')
        argv = command(proof_dir, job_name)
        record.update(command=argv, model='anthropic/claude-sonnet-5', effort='high', attempts=1)
        env = dict(os.environ, HARBOR_ADMISSION_CONTROL=str(control), PYTHONDONTWRITEBYTECODE='1')
        with (output / (label + '.log')).open('x') as log:
            common.frozen_sources()
            common.combined_controls(controls_path, controls_sha256)
            child = subprocess.Popen(argv, cwd=common.ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            record['pid'] = child.pid
            summary['reviews'][label] = record
            legacy.dump(output / 'summary.json', summary)
            observation = observer.wait_with_observation(child, lambda terminal=False: observer.observe_once(
                observations, control, child.pid, shared_control=shared, gap_path=gaps, terminal=terminal))
        record.update(exit_code=child.returncode, observation=observation, observation_errors=observation['errors'])
        assert child.returncode == 0 and not observation['errors'] and observation['terminal_sample_valid'], 'Review execution/terminal observation failed'
        record.update(legacy.validate(output / 'jobs' / job_name, proof_dir, observations))
        common.frozen_sources()
        common.combined_controls(controls_path, controls_sha256)
        assert legacy.hashes(common.TASK) == files, 'Reviewed task bytes changed'
        summary['inputs_unchanged'] = True
        summary['passed'] = True
    except BaseException as error:
        record.update(passed=False, error_type=type(error).__name__, error=str(error))
    finally:
        if child is not None:
            child.wait()  # Observe/reap the one attempt; never signal or replace it.
            record['exit_code'] = child.returncode
        record['finished_at'] = now()
        summary['reviews'][label] = record
        summary['finished_at'] = now()
        dump(output / 'summary.json', summary)
    return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controls-summary', required=True)
    parser.add_argument('--controls-sha256', required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        common.frozen_sources(); common.fresh_outputs(include_coordinator=False)
        common.combined_controls(args.controls_summary, args.controls_sha256)
        print('Check-only passed; no model process launched.')
        return
    def interrupted(signum, frame):
        raise KeyboardInterrupt('Quality wrapper interrupted; its one existing child will be reaped')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(run_once(args.controls_summary, args.controls_sha256))


if __name__ == '__main__':
    main()

"""Read-only observation of the one authorized canary; writes only own evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUTPUT = HERE / 'run-001'
TRIAL = ROOT / 'jobs/streaming-canary-luigi-fable-max-20260914-001/luigi-generation-target__RigDBsV'
PID = 76965
IDENTITY = '5940603'
DEADLINE = 1789382406.6793282


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def process_identity(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return dict(identity=fields[19], state=fields[0], live=fields[0] != 'Z')
    except (OSError, IndexError):
        return dict(identity=None, state=None, live=False)


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw), digest(raw)


def observe():
    value = dict(at=now(), kind='read_only_canary_observation', pid=PID,
                 process=process_identity(PID), trial_path=str(TRIAL.relative_to(ROOT)),
                 deadline_utc=datetime.fromtimestamp(DEADLINE, timezone.utc).isoformat(),
                 deadline_remaining_seconds=round(DEADLINE-time.time(), 3), errors=[])
    value['runner_identity_matches'] = value['process']['live'] and value['process']['identity'] == IDENTITY
    try:
        value['resources'], _ = read_json(HERE / 'control.resources.json')
        trajectory, trajectory_sha = read_json(TRIAL / 'agent/mini-swe-agent.trajectory.json')
        messages = trajectory['messages']
        value.update(trajectory_sha256_at_observation=trajectory_sha,
                     assistant_turns=sum(m['role'] == 'assistant' for m in messages),
                     tool_results=sum(m['role'] == 'tool' for m in messages),
                     model_stats=trajectory['info']['model_stats'],
                     trajectory_exit_status=trajectory['info'].get('exit_status'))
        raw = (TRIAL / 'agent/benchmark-streaming-events.jsonl').read_bytes()
        # A concurrent append may leave an incomplete last line. Defer that line
        # to the next sample; never interpret it as completed protocol evidence.
        lines = raw.splitlines(keepends=True)
        events = [json.loads(line) for line in lines if line.endswith(b'\n')]
        queries = []
        for event in events:
            name = event['event']
            if name == 'query_entered':
                queries.append(dict(number=len(queries)+1, entered=event['at_epoch'], complete=False))
            elif queries:
                query = queries[-1]
                if name == 'complete_response_returned':
                    query.update(complete=True, completed=event['at_epoch'])
                elif name == 'http_response_opened':
                    query['http_opened'] = event['at_epoch']
                elif name == 'query_resources_closed':
                    query.update(closed=event['at_epoch'], all_closed=event['all_closed'],
                                 cleanup_error_types=event['error_types'])
        value.update(query_entries=len(queries), complete_responses=sum(q['complete'] for q in queries),
                     closed_without_completion=[dict(number=q['number'], duration_seconds=q['closed']-q['entered'],
                                                      all_closed=q['all_closed'], cleanup_error_types=q['cleanup_error_types'])
                                                for q in queries if 'closed' in q and not q['complete']],
                     all_observed_closures_successful=all(q['all_closed'] and not q['cleanup_error_types']
                                                         for q in queries if 'closed' in q),
                     open_query_age_seconds=round(time.time()-queries[-1]['entered'], 3)
                     if queries and 'closed' not in queries[-1] else None)
        logfile = (TRIAL / 'agent/mini-swe-agent.txt').read_bytes()
        value['error_marker_counts'] = {marker: logfile.count(marker.encode()) for marker in
                                       ('BadGateway', 'MidStreamFallbackError', 'IncompleteAnthropicStream')}
    except (OSError, ValueError, KeyError) as error:
        value['errors'].append(dict(stage='live_agent_artifacts', error_type=type(error).__name__))
    for filename, field in (('result.json', 'raw_result'), ('benchmark-deadline.json', 'deadline_result')):
        path = TRIAL / filename
        if path.exists():
            try:
                data, sha = read_json(path)
                if field == 'raw_result':
                    value[field] = dict(path=str(path.relative_to(ROOT)), sha256=sha,
                                        started_at=data.get('started_at'), finished_at=data.get('finished_at'),
                                        verifier_result=data.get('verifier_result'),
                                        exception_type=(data.get('exception_info') or {}).get('exception_type'))
                else:
                    value[field] = dict(path=str(path.relative_to(ROOT)), sha256=sha)
            except (OSError, ValueError, KeyError) as error:
                value['errors'].append(dict(stage=field, error_type=type(error).__name__))
    terminal = OUTPUT / 'runner-outcome.json'
    value['runner_terminal_record_present'] = terminal.exists()
    if terminal.exists():
        try:
            outcome, sha = read_json(terminal)
            value['runner_outcome'] = dict(path=str(terminal.relative_to(ROOT)), sha256=sha,
                                          status=outcome.get('status'), exit_type=outcome.get('exit_type'),
                                          exit_code=outcome.get('exit_code'), finished_at=outcome.get('finished_at'),
                                          runtime_inspection_errors=outcome.get('runtime_inspection_errors'),
                                          captured_source_files_unchanged=outcome.get('captured_source_files_unchanged'),
                                          task_files_unchanged=outcome.get('task_files_unchanged'))
        except (OSError, ValueError, KeyError) as error:
            value['errors'].append(dict(stage='runner_outcome', error_type=type(error).__name__))
    with (OUTPUT / 'monitor-observations.jsonl').open('a') as stream:
        stream.write(json.dumps(value, sort_keys=True)+'\n')
    temporary = OUTPUT / ('monitor-latest.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    temporary.replace(OUTPUT / 'monitor-latest.json')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    if args.watch:
        identity = dict(pid=os.getpid(), process=process_identity(os.getpid()), started_at=now(),
                        source_sha256=digest(Path(__file__).read_bytes()), poll_seconds=30,
                        stop_at_epoch=DEADLINE+900+600, read_only=True, model_calls=0)
        with (OUTPUT / 'monitor-identity.json').open('x') as stream:
            json.dump(identity, stream, indent=2, sort_keys=True)
            stream.write('\n')
    while True:
        value = observe()
        print(json.dumps(value, sort_keys=True), flush=True)
        if not args.watch or value['runner_terminal_record_present'] or not value['runner_identity_matches']:
            break
        if time.time() >= DEADLINE+900+600:
            print(json.dumps(dict(at=now(), observer_bound_reached=True, trial_action='none')), flush=True)
            break
        time.sleep(30)


if __name__ == '__main__':
    main()

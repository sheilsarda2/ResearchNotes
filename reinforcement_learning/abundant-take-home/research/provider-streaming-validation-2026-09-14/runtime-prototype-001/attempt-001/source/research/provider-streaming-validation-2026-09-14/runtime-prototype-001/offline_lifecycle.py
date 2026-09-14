"""Bounded offline CLI/guard proof in a disposable network-none container."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INSTRUCTION = "Synthetic task 🦀 'quote'\nNo real model or benchmark task."
COMMAND = "printf fixture; printf x >> /tmp/prototype-tool-markers"
MODEL = 'anthropic/claude-fable-5-1'


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def content():
    return [dict(type='thinking', thinking='Synthetic first thought.', signature='fixture-signature-A'),
            dict(type='text', text='Alpha '), dict(type='redacted_thinking', data='fixture-redacted'),
            dict(type='thinking', thinking='', signature='fixture-signature-empty'),
            dict(type='thinking', thinking='Synthetic second thought.', signature='fixture-signature-B'),
            dict(type='text', text='Beta.'),
            dict(type='tool_use', id='toolu_fixture', name='bash', input={'command': COMMAND})]


def events():
    start = dict(id='msg_fixture', type='message', role='assistant', model='claude-fable-5-1', content=[],
                 stop_reason=None, stop_sequence=None,
                 usage=dict(input_tokens=100, output_tokens=1, cache_creation_input_tokens=20, cache_read_input_tokens=30))
    rows = [dict(type='message_start', message=start)]
    for index, block in enumerate(content()):
        typ = block['type']
        initial = copy.deepcopy(block)
        if typ in ('thinking', 'text'):
            initial = {'type': typ, typ: ''}
        elif typ == 'tool_use':
            initial['input'] = {}
        rows.append(dict(type='content_block_start', index=index, content_block=initial))
        if typ == 'thinking':
            for text in (block['thinking'][:4], block['thinking'][4:]):
                rows.append(dict(type='content_block_delta', index=index, delta=dict(type='thinking_delta', thinking=text)))
            for signature in (block['signature'][:8], block['signature'][8:]):
                rows.append(dict(type='content_block_delta', index=index, delta=dict(type='signature_delta', signature=signature)))
        elif typ == 'text':
            rows.append(dict(type='content_block_delta', index=index, delta=dict(type='text_delta', text=block['text'])))
        elif typ == 'tool_use':
            encoded = json.dumps(block['input'])
            for part in (encoded[:15], encoded[15:]):
                rows.append(dict(type='content_block_delta', index=index, delta=dict(type='input_json_delta', partial_json=part)))
        rows.append(dict(type='content_block_stop', index=index))
    rows += [dict(type='message_delta', delta=dict(stop_reason='tool_use', stop_sequence=None), usage=dict(output_tokens=25)),
             dict(type='message_stop')]
    return rows


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, mode):
        self.mode = mode
        self.records = []
        self.lock = threading.Lock()
        self.ready = threading.Event()
        super().__init__(('127.0.0.1', 0), Handler)

    def record(self, event, **values):
        with self.lock:
            self.records.append(dict(event=event, at_epoch=time.time(), **values))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def wait_for_peer(self):
        self.server.ready.set()
        self.connection.settimeout(0.2)
        until = time.monotonic() + 65
        while time.monotonic() < until:
            try:
                if not self.connection.recv(1024):
                    self.server.record('peer_connection_closed', request_number=self.request_number)
                    return
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError):
                self.server.record('peer_connection_reset', request_number=self.request_number)
                return
        self.server.record('peer_close_not_observed')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.request_number = 1 + sum(r['event'] == 'request' for r in self.server.records)
        self.server.record('request', path=self.path, body=body)
        mode = self.server.mode
        if mode == 'http502_retry' and self.request_number == 1:
            payload = json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'Synthetic HTTP502'}}).encode()
            self.send_response(502)
            self.send_header('Connection', 'close')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()
            self.server.record('fixture_http502_sent', request_number=self.request_number)
            self.wait_for_peer()
            return
        if mode == 'cancel_before_headers':
            self.server.record('fixture_blocked', phase='before_headers')
            self.wait_for_peer()
            return
        self.send_response(200)
        self.send_header('Connection', 'close')
        if not body.get('stream'):
            value = dict(events()[0]['message'], content=content(), stop_reason='tool_use',
                         usage=dict(input_tokens=100, output_tokens=25, cache_creation_input_tokens=20, cache_read_input_tokens=30))
            encoded = json.dumps(value).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            self.wfile.flush()
            self.wait_for_peer()
            return
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        rows = events()
        if mode == 'missing_usage':
            rows[-2]['usage'] = {}
        elif mode in ('explicit_error', 'retry_backoff'):
            rows = rows[:-2] + [dict(type='error', error=dict(type='api_error', message='Synthetic fixture error'))]
        elif mode in ('eof', 'cancel_after_delta'):
            rows = rows[:-1]
        elif mode in ('partial_tool', 'cancel_mid_tool'):
            stop = next(i for i, row in enumerate(rows) if row.get('delta', {}).get('type') == 'input_json_delta')
            rows = rows[:stop + 1]
        elif mode == 'cancel_mid_thinking':
            rows = rows[:3]
        try:
            for row in rows:
                encoded = ('event: ' + row['type'] + '\ndata: ' + json.dumps(row) + '\n\n').encode()
                # Deliberately fragment transport bytes through the real HTTPX/SSE decoder.
                for offset in range(0, len(encoded), 17):
                    self.wfile.write(encoded[offset:offset + 17])
                self.wfile.flush()
            self.server.record('fixture_rows_sent', count=len(rows), message_stop=rows[-1]['type'] == 'message_stop')
            if mode.startswith('cancel_'):
                self.server.record('fixture_blocked', phase=mode)
                self.wait_for_peer()
            elif mode in ('eof', 'partial_tool', 'missing_usage'):
                self.server.record('fixture_intentional_eof')
                self.close_connection = True
            else:
                self.wait_for_peer()
        except (ConnectionResetError, BrokenPipeError):
            self.server.record('peer_connection_reset')


def case(output, python, mode, registry):
    folder = output / mode
    folder.mkdir()
    server = Server(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api_base = 'http://127.0.0.1:' + str(server.server_port)
    config = dict(agent=dict(step_limit=2, cost_limit=10), model=dict(litellm_model_registry=str(registry),
                  model_kwargs=dict(api_base=api_base, thinking={'type': 'adaptive'}, output_config={'effort': 'max'},
                                    max_tokens=64000, num_retries=0, drop_params=True)))
    dump(folder / 'mini.json', config)
    cancelled = mode.startswith('cancel_') or mode == 'retry_backoff'
    guard = ROOT / 'scripts/benchmark_process_guard.py'
    phase = folder / 'guard'
    phase.mkdir()
    marker = Path('/tmp/prototype-tool-markers')
    marker.unlink(missing_ok=True)
    stream_events = Path('/logs/agent/benchmark-streaming-events.jsonl')
    stream_events.unlink(missing_ok=True)
    spec = dict(output=str(folder), mini_config=str(folder / 'mini.json'), instruction=INSTRUCTION,
                api_base=api_base, streaming=mode != 'baseline', fixture_retry_attempts=10 if mode in ('retry_backoff', 'http502_retry') else 1)
    dump(folder / 'case-input.json', spec)
    started = time.time()
    # Short fixture deadline only; no production deadline/retry behavior is modified.
    use_deadline = mode in ('cancel_before_headers', 'cancel_mid_tool', 'cancel_after_delta')
    deadline = started + (15 if use_deadline else 60)
    import shlex
    command = shlex.join([python, '-B', str(HERE / 'fixture_worker.py'), '--case', str(folder / 'case-input.json')])
    args = [python, '-B', str(guard), 'run', '--phase', str(phase), '--execution', 'fixture', '--deadline', str(deadline), '--command', command]
    log = (folder / 'worker.stdout.log').open('w')
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    error = None
    try:
        if cancelled and not use_deadline:
            if not server.ready.wait(35):
                raise AssertionError('Fixture did not reach its blocking/retry point')
            if mode == 'retry_backoff':
                until = time.monotonic() + 3
                while not any(r['event'].startswith('peer_connection_') for r in server.records) and time.monotonic() < until:
                    time.sleep(.02)
                time.sleep(.2)
            closing = subprocess.run([python, '-B', str(guard), 'close', '--phase', str(phase)],
                                     capture_output=True, text=True, timeout=20)
            (folder / 'guard-close.stdout.json').write_text(closing.stdout)
            if closing.returncode:
                raise AssertionError('Actual guard close failed')
        proc.wait(timeout=75)
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
        subprocess.run([python, '-B', str(guard), 'close', '--phase', str(phase)], capture_output=True, timeout=20)
        proc.wait(timeout=20)
    finally:
        log.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    time.sleep(.1)
    dump(folder / 'server-events.json', server.records)
    if stream_events.exists():
        shutil.copyfile(stream_events, folder / 'streaming-events.jsonl')
    record = json.loads((phase / 'fixture.json').read_text())
    result = dict(name=mode, elapsed_seconds=round(time.time() - started, 3), process_returncode=proc.returncode,
                  production_model_requests=0, fixture_deadline_epoch=deadline, guard=record,
                  fixture_retry_attempts=spec['fixture_retry_attempts'], error=error, passed=False)
    try:
        assert error is None, error
        assert record['quiescent'] is True
        assert not Path('/proc/' + str(proc.pid)).exists(), 'Guard was not reaped'
        requests = [r for r in server.records if r['event'] == 'request']
        result['synthetic_requests'] = len(requests)
        for row in requests:
            body = row['body']
            assert row['path'] == '/v1/messages'
            assert body['model'] == 'claude-fable-5-1'
            assert body['thinking'] == {'type': 'adaptive'} and body['output_config'] == {'effort': 'max'}
            assert body['max_tokens'] == 64000
            assert body.get('stream', False) == (mode != 'baseline')
            assert row['at_epoch'] < record['quiescent_at_epoch']
            assert row['at_epoch'] <= deadline
        trajectory_path = folder / 'trajectory.json'
        trajectory = json.loads(trajectory_path.read_text()) if trajectory_path.exists() else {}
        accepted = [m for m in trajectory.get('messages', []) if m.get('role') == 'assistant']
        tools = len(marker.read_bytes()) if marker.exists() else 0
        result.update(accepted_assistant_turns=len(accepted), tool_executions=tools,
                      trajectory_exists=bool(trajectory), peer_close_observed=any(r['event'].startswith('peer_connection_') for r in server.records))
        if mode in ('baseline', 'complete', 'http502_retry'):
            assert len(requests) == (3 if mode == 'http502_retry' else 2)
            assert len(accepted) == tools == 2
            assert trajectory['info']['model_stats']['api_calls'] == 2
            assert record['reason'] == 'completed'
            assert result['peer_close_observed']
            for message in accepted:
                assert message['thinking_blocks'] == [b for b in content() if b['type'] in ('thinking', 'redacted_thinking')]
                assert message['content'] == 'Alpha Beta.'
                usage = message['extra']['response']['usage']
                assert (usage['prompt_tokens'], usage['completion_tokens'], usage['total_tokens']) == (150, 25, 175)
                assert usage['cache_creation_input_tokens'] == 20 and usage['cache_read_input_tokens'] == 30
                assert abs(message['extra']['cost'] - .000178) < 1e-10
            assert abs(trajectory['info']['model_stats']['instance_cost'] - .000356) < 1e-10
            order = json.loads((folder / 'wrapper-order.json').read_text())
            phases = [r['phase'] for r in order if r['action'] == 'exec']
            expected = ['setup', 'tool_preflight'] + (['stream_preflight'] if mode != 'baseline' else []) + ['final_cli']
            assert phases == expected, phases
            delivered = json.loads((folder / 'benchmark-agent-input.json').read_text())
            assert delivered['task_sha256'] == hashlib.sha256(INSTRUCTION.encode()).hexdigest()
            if mode == 'http502_retry':
                closed = [r for r in server.records if r['event'].startswith('peer_connection_') and r.get('request_number') == 1]
                assert closed and closed[0]['at_epoch'] < requests[1]['at_epoch'], 'HTTP502 response not closed before retry'
                assert requests[1]['at_epoch'] - requests[0]['at_epoch'] >= 3.8, 'Inherited retry backoff was shortened'
                result['http502_closed_before_successful_retry'] = True
        else:
            assert len(accepted) == tools == 0, 'Incomplete stream produced an action'
            assert len(requests) == 1, 'Unexpected retry/request before cancellation'
            if cancelled:
                assert record['reason'] == ('deadline' if use_deadline else 'cancelled')
                assert result['peer_close_observed']
            else:
                assert trajectory['info']['exit_status'] not in ('Submitted', 'LimitsExceeded')
        if stream_events.exists() and not (cancelled and mode != 'retry_backoff'):
            rows = [json.loads(line) for line in stream_events.read_text().splitlines()]
            closed = [r for r in rows if r['event'] == 'query_resources_closed']
            assert len(closed) == len(requests)
            assert all(r['all_closed'] and not r['error_types'] for r in closed)
        result['passed'] = True
    except BaseException as exc:
        result['validation_error'] = type(exc).__name__ + ': ' + str(exc)
    dump(folder / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mini-python', required=True)
    args = parser.parse_args()
    assert platform.python_version() == '3.12.11'
    args.output.mkdir(parents=True, exist_ok=False)
    assert not any(os.environ.get(k) for k in ('TAKE_HOME_TOKEN', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY')), 'Do not forward credentials'
    # Fixture uv-tool lookup only; the original runtime bootstrap remains exact.
    binpath = Path('/tmp/prototype-fixture-bin')
    binpath.mkdir()
    uv = binpath / 'uv'
    uv.write_text('#!/bin/sh\n[ "$1 $2" = "tool dir" ] || exit 2\nprintf /opt\n')
    uv.chmod(0o755)
    Path('/opt/mini-swe-agent').symlink_to('/opt/mini')
    envfile = Path.home() / '.local/bin/env'
    envfile.parent.mkdir(parents=True, exist_ok=True)
    assert not envfile.exists()
    envfile.write_text('export PATH=/tmp/prototype-fixture-bin:$PATH\n')
    Path('/logs/agent').mkdir(parents=True, exist_ok=True)
    package = Path(importlib.util.find_spec('litellm').origin).parent
    metadata = json.loads((package / 'model_prices_and_context_window_backup.json').read_text())['claude-fable-5']
    metadata.update(input_cost_per_token=.000001, output_cost_per_token=.000002,
                    cache_read_input_token_cost=.0000001, cache_creation_input_token_cost=.00000125)
    registry = args.output / 'synthetic-price-registry.json'
    dump(registry, {MODEL: metadata, 'claude-fable-5-1': metadata})
    modes = ['baseline', 'complete', 'http502_retry', 'eof', 'missing_usage', 'explicit_error', 'partial_tool',
             'cancel_before_headers', 'cancel_mid_thinking', 'cancel_mid_tool', 'cancel_after_delta', 'retry_backoff']
    results = []
    for mode in modes:
        value = case(args.output, args.mini_python, mode, registry)
        results.append(value)
        print(json.dumps(dict(case=mode, passed=value['passed'], error=value.get('validation_error'), elapsed=value['elapsed_seconds'])), flush=True)
    comparison = False
    if results[0]['passed'] and results[1]['passed']:
        baseline = json.loads((args.output / 'baseline/server-events.json').read_text())
        streamed = json.loads((args.output / 'complete/server-events.json').read_text())
        a = [r['body']['messages'] for r in baseline if r['event'] == 'request']
        b = [r['body']['messages'] for r in streamed if r['event'] == 'request']
        comparison = a == b
    summary = dict(schema_version=1, kind='offline_streaming_runtime_lifecycle', model_calls=0,
                   external_requests=0, python=platform.python_version(), cases=results,
                   baseline_stream_next_request_equal=comparison,
                   fixture_only_overrides=['loopback route and synthetic credential/tariff', 'step_limit2',
                                           '15/60-second guard deadline', 'one retry attempt except default10 backoff/HTTP502 cases; inherited4-second backoff unchanged',
                                           'LiteLLM num_retries0', 'Harbor Docker exec replaced by local transport double', 'uv tool-dir fixture'],
                   passed=all(row['passed'] for row in results) and comparison)
    dump(args.output / 'summary.json', summary)
    raise SystemExit(0 if summary['passed'] else 1)


if __name__ == '__main__':
    main()

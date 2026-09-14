"""Bounded shared-state observation; never initializes or changes shared state.

Use a separate observations JSONL and gap JSONL in the caller's new evidence
directory. Existing validate_admission accepts the successful observation rows.
wait_with_observation requires a fresh callback result after the child exits.
"""
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

sys.dont_write_bytecode = True
MAX_JSON_BYTES = 16 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def _open_regular(path, flags=os.O_RDONLY):
    """No symlink traversal, file creation only if explicitly requested for logs."""
    path = Path(path).absolute()
    if '..' in path.parts:
        raise ValueError('Parent traversal is not allowed')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=directory)
            os.close(directory)
            directory = following
        fd = os.open(path.name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     0o600, dir_fd=directory)
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Expected an existing regular file: ' + str(path))
    return fd


def _read_bytes(path):
    with os.fdopen(_open_regular(path), 'rb') as stream:
        value = stream.read(MAX_JSON_BYTES + 1)
    if len(value) > MAX_JSON_BYTES:
        raise ValueError('Observation JSON exceeds size bound: ' + str(path))
    return value


def _event(error, phase, path, attempt):
    row = dict(at=now(), attempt=attempt, phase=phase, path=str(path),
               error_type=type(error).__name__)
    if isinstance(error, OSError):
        row['errno'] = error.errno
    if isinstance(error, json.JSONDecodeError):
        row.update(line=error.lineno, column=error.colno)
    return row


class ObservationReadError(RuntimeError):
    def __init__(self, gaps, elapsed_seconds):
        self.gaps = gaps
        self.elapsed_seconds = elapsed_seconds
        super().__init__('No fresh shared-state observation within the bounded read deadline')


def read_shared_pair(shared_control, *, timeout=2.0, retry_interval=0.05):
    """Read exact config/state bytes under the existing lock, with bounded retries.

    Only lock contention, ENOENT and JSON decoding failures are retried. Every
    recovered attempt remains in ``recovered_gaps``. All other errors propagate.
    No writable admission helper, missing-state fallback, or cached sample exists.
    """
    if not (0 < timeout <= 10 and 0 < retry_interval <= timeout):
        raise ValueError('Require 0 < retry_interval <= timeout <= 10 seconds')
    shared = Path(shared_control).absolute()
    state_path = shared.with_suffix('.state.json')
    lock_path = shared.with_suffix('.lock')
    start = time.monotonic()
    deadline = start + timeout
    gaps = []
    attempt = 0
    while True:
        if attempt and time.monotonic() >= deadline:
            raise ObservationReadError(gaps, time.monotonic() - start)
        attempt += 1
        lock_fd = None
        phase, path = 'lock_open', lock_path
        try:
            lock_fd = _open_regular(lock_path)  # Existing file, O_RDONLY; never create/truncate.
            phase = 'lock_acquire'
            fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            phase, path = 'config_read', shared
            config_bytes = _read_bytes(shared)
            phase, path = 'state_read', state_path
            state_bytes = _read_bytes(state_path)
            phase, path = 'config_decode', shared
            config = json.loads(config_bytes)
            phase, path = 'state_decode', state_path
            state = json.loads(state_bytes)
            if not isinstance(config, dict) or not isinstance(state, dict):
                raise ValueError('Shared config/state must be JSON objects')
            if type(config.get('max_active')) is not int or config['max_active'] < 1:
                raise ValueError('Shared configuration lacks a positive integer max_active')
            if not isinstance(state.get('participants'), dict):
                raise ValueError('Shared state lacks participants; never substitute empty state')
            return dict(config=config, state=state, read_at=now(),
                        shared_control_sha256=hashlib.sha256(config_bytes).hexdigest(),
                        shared_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
                        attempts=attempt, elapsed_seconds=time.monotonic() - start,
                        recovered_gaps=gaps)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            gaps.append(_event(error, phase, path, attempt))
        except BlockingIOError as error:
            if phase != 'lock_acquire' or error.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            gaps.append(_event(error, phase, path, attempt))
        finally:
            if lock_fd is not None:
                os.close(lock_fd)  # Releases any acquired SH lock before sleeping.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationReadError(gaps, time.monotonic() - start)
        time.sleep(min(retry_interval, remaining))


def _append(path, value):
    with os.fdopen(_open_regular(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND), 'a') as output:
        output.write(json.dumps(value, sort_keys=True) + '\n')


def observe_once(path, control, pid, *, shared_control, gap_path,
                 terminal=False, timeout=2.0, retry_interval=0.05):
    """Append only successful, fresh samples; gap records have their own file."""
    protected = {Path(shared_control).absolute(), Path(shared_control).absolute().with_suffix('.lock'),
                 Path(shared_control).absolute().with_suffix('.state.json'), Path(control).absolute()}
    if Path(path).absolute() in protected or Path(gap_path).absolute() in protected:
        raise ValueError('Observation outputs cannot overwrite shared/local controls')
    if Path(path).absolute() == Path(gap_path).absolute():
        raise ValueError('Successful samples and read gaps require separate files')
    try:
        pair = read_shared_pair(shared_control, timeout=timeout, retry_interval=retry_interval)
    except ObservationReadError as error:
        _append(gap_path, dict(at=now(), recovered=False, terminal=terminal,
                              elapsed_seconds=error.elapsed_seconds, gaps=error.gaps))
        raise
    if pair['recovered_gaps']:
        _append(gap_path, dict(at=now(), recovered=True, terminal=terminal,
                              elapsed_seconds=pair['elapsed_seconds'], gaps=pair['recovered_gaps']))
    participants = pair['state']['participants']
    if any(not isinstance(p, dict) or not isinstance(p.get('trials'), dict)
           or not isinstance(p.get('control'), str) for p in participants.values()):
        raise ValueError('Malformed participant record')
    own = {key: value for key, value in participants.items() if value['control'] == str(control)}
    row = dict(at=pair['read_at'], runner_pid=pid, shared_max_active=pair['config']['max_active'],
               shared_control_sha256=pair['shared_control_sha256'],
               shared_state_sha256=pair['shared_state_sha256'],
               total_claims=sum(len(p['trials']) for p in participants.values()), own_participants=own,
               terminal_sample=bool(terminal), read_attempts=pair['attempts'],
               recovered_read_gaps=len(pair['recovered_gaps']))
    _append(path, row)
    return row


def wait_with_observation(child, callback, *, interval=1.0):
    """Reap the one existing child, requiring a new successful terminal sample.

    ``callback(terminal=bool)`` must call observe_once. Return errors are fatal to
    the caller's validation; recovered within-read gaps are not permanent errors.
    This function does not launch, retry, restart, or signal any process.
    """
    if not (0 < interval <= 30):
        raise ValueError('Observation interval must be in (0, 30] seconds')
    errors = []
    terminal_row = None
    try:
        while True:
            finished = child.poll() is not None
            try:
                row = callback(terminal=finished)
                if finished:
                    if not isinstance(row, dict) or row.get('terminal_sample') is not True:
                        raise ValueError('Fresh successful terminal observation is required')
                    terminal_row = row
            except Exception as error:
                errors.append(dict(at=now(), error_type=type(error).__name__, error=str(error),
                                   terminal=finished))
            if finished:
                break
            time.sleep(interval)
    finally:
        child.wait()  # Also reaps on caller interruption or unexpected callback failure.
    return dict(exit_code=child.returncode, errors=errors,
                terminal_sample_valid=terminal_row is not None,
                terminal_sample_at=terminal_row['at'] if terminal_row else None)

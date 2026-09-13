"""A host-wide slot and memory gate shared by independent benchmark jobs."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time


def process_identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


class SharedAdmission:
    def __init__(self, config_path, campaign_control):
        self.path = Path(config_path)
        self.state_path = self.path.with_suffix('.state.json')
        self.pid = os.getpid()
        self.identity = process_identity(self.pid)
        self.key = f'{self.pid}:{self.identity}'
        with self.locked() as (_, state):
            state['participants'][self.key] = {
                'pid': self.pid, 'identity': self.identity,
                'control': str(Path(campaign_control).resolve()), 'trials': {}}

    @contextmanager
    def locked(self):
        with self.path.with_suffix('.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            config = json.loads(self.path.read_text())
            state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'participants': {}}
            # Keep occupied reservations after an abnormal exit for recovery;
            # otherwise orphaned Docker work could become invisible to the cap.
            state['participants'] = {k: p for k, p in state['participants'].items()
                                     if p['trials'] or process_identity(p['pid']) == p['identity']}
            yield config, state
            state['updated_at'] = time.time()
            temporary = self.state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, indent=2) + '\n')
            temporary.replace(self.state_path)

    def try_acquire(self, name, snapshot, local):
        with self.locked() as (config, state):
            now = time.time()
            participants = state['participants']
            own = participants[self.key]['trials']
            if name in own:
                return None
            external = []
            for peer in config.get('external_peers', []):
                if process_identity(peer['pid']) == peer['identity']:
                    data = json.loads(Path(peer['resources']).read_text())
                    # The legacy runner does not take this lock. Reserve its
                    # configured ceiling even between individual admissions.
                    external.append(max(data['active_trials'], peer.get('reserved_slots', 0)))
            claims = [trial for participant in participants.values() for trial in participant['trials'].values()]
            active = len(claims) + sum(external)
            limit = config.get('max_active', 32)
            fair_limit = max(1, limit // (len(participants) + len(external)))
            pressure = snapshot['memory_pressure_pct']
            if pressure > config.get('max_memory_pressure_pct', 1):
                state['last_pressure'] = now
            reserved = sum(t['startup_reserve_mb'] for t in claims
                           if now - t['started_at'] < config.get('startup_window_sec', 120))
            startup = local.get('startup_reserve_mb', 768)
            if config.get('paused'):
                reason = 'shared queue paused'
            elif active >= limit:
                reason = 'shared concurrency'
            elif len(own) >= fair_limit:
                reason = 'shared campaign allocation'
            elif snapshot['total_mb'] < config.get('min_total_mb', 30000):
                reason = 'Docker memory allocation'
            elif snapshot['available_mb'] - reserved < config.get('reserve_mb', 4096) + startup:
                reason = 'shared memory reserve'
            elif pressure > config.get('max_memory_pressure_pct', 1):
                reason = 'memory pressure'
            elif now - state.get('last_pressure', 0) < config.get('pressure_cooldown_sec', 60):
                reason = 'memory pressure cooldown'
            elif now - state.get('last_start', 0) < config.get('start_interval_sec', 5):
                reason = 'shared staggered startup'
            else:
                own[name] = {'started_at': now, 'startup_reserve_mb': startup}
                state['last_start'] = now
                active += 1
                reason = None
            state.update(active_trials=active, external_trials=sum(external),
                         peak_active_trials=max(active, state.get('peak_active_trials', 0)),
                         available_mb=snapshot['available_mb'], memory_pressure_pct=pressure,
                         last_wait_reason=reason)
            return reason

    def release(self, name):
        with self.locked() as (_, state):
            state['participants'][self.key]['trials'].pop(name, None)

"""Single-episode rollout implementing the clock knobs and the chunk selector.

Timebases, to keep the bookkeeping honest:
- model step: one action at the model's native rate (native_hz).
- env step: one controller tick at native_hz * exec_multiplier.
- A chunk of chunk_len model steps therefore expands to chunk_len * m env steps.

Knobs:
- replan_k: model steps executed per query (k = chunk_len is native open-chunk).
- selector: 'naive' takes one sample per query; 'coherence' draws n_samples
  candidate chunks and keeps the one closest to the unexecuted remainder of the
  previous chunk (Bidirectional Decoding's backward-coherence rule, arXiv:2408.17355,
  test-time only, no retraining).
- exec_multiplier m: chunk retimed via split_delta_chunk; controller runs m-times faster.
- obs_hold j: the frame fed to the model refreshes every j env steps (j=1 = always fresh).
- ou_sigma/theta: OU noise applied per env step to executed motion dims.
- timewarp: validation arm; deltas not divided by m (motion runs m-times fast).
"""

from dataclasses import dataclass

import numpy as np

from env_libero import OUNoise, build_state, eef_position, extract_images, split_delta_chunk
from metrics import chunk_thrash, coherence_cost, mean_squared_jerk, sparc


@dataclass
class RolloutParams:
    native_hz: int = 10
    chunk_len: int = 10
    replan_k: int = 10
    selector: str = "naive"     # 'naive' | 'coherence'
    n_samples: int = 1          # candidate chunks per query (coherence arm)
    coherence_rho: float = 0.5  # decay on future steps in the selection cost
    exec_multiplier: int = 1
    obs_hold: int = 1
    ou_sigma: float = 0.0
    ou_theta: float = 0.15
    max_seconds: float = 60.0
    timewarp: bool = False


def select_chunk(candidates, prev_chunk, executed_k, rho):
    """Pick the candidate that stays closest to what the previous query planned.

    candidates: [N, T, 7]. prev_chunk[executed_k:] predicts the same model steps
    as the head of each candidate. On the first query (prev_chunk is None) or when
    there is no overlap, the first candidate wins, which is the naive choice.
    """
    if prev_chunk is None:
        return candidates[0]
    reference = prev_chunk[executed_k:]
    if len(reference) == 0:
        return candidates[0]
    costs = [coherence_cost(reference, cand, rho) for cand in candidates]
    return candidates[int(np.argmin(costs))]


def run_episode(env, instruction, policy, params, rng):
    m = params.exec_multiplier
    env_dt = 1.0 / (params.native_hz * m)
    max_env_steps = int(params.max_seconds * params.native_hz * m)

    noise = OUNoise(params.ou_sigma, params.ou_theta, rng=rng)
    noise.reset()

    obs, _, _, _ = env.step(np.zeros(7))  # settle one tick after reset/init-state
    held_images = extract_images(obs)
    env_steps_since_refresh = 0

    positions = [eef_position(obs)]
    thrash_vals = []        # thrash of the executed chunk vs the previous plan
    thrash_first_vals = []  # counterfactual: thrash if the first sample had run
    prev_chunk = None
    success = False
    env_step_count = 0

    while env_step_count < max_env_steps:
        # --- query the frozen model (sim is paused here: inference is free) ---
        if env_steps_since_refresh >= params.obs_hold or prev_chunk is None:
            held_images = extract_images(obs)
            env_steps_since_refresh = 0
        state = build_state(obs)
        if params.selector == "coherence":
            candidates = policy.predict_chunks(
                held_images[0], held_images[1], instruction, state, params.n_samples
            )
            first_sample = candidates[0]
            chunk = select_chunk(
                candidates, prev_chunk, params.replan_k, params.coherence_rho
            )
        else:
            chunk = policy.predict_chunk(
                held_images[0], held_images[1], instruction, state
            )
            first_sample = chunk

        if prev_chunk is not None:
            thrash_vals.append(chunk_thrash(prev_chunk, params.replan_k, chunk))
            thrash_first_vals.append(
                chunk_thrash(prev_chunk, params.replan_k, first_sample)
            )
        prev_chunk = chunk

        # --- execute replan_k model steps of the chunk, retimed for the controller ---
        to_execute = chunk[: params.replan_k]
        env_actions = split_delta_chunk(to_execute, m, timewarp=params.timewarp)
        for action in env_actions:
            obs, _, done, _ = env.step(noise.apply(action))
            positions.append(eef_position(obs))
            env_step_count += 1
            env_steps_since_refresh += 1
            if env.check_success():
                success = True
                break
            if done or env_step_count >= max_env_steps:
                break
        if success or done:
            break

    positions = np.asarray(positions)
    speed = np.linalg.norm(np.diff(positions, axis=0), axis=1) / env_dt
    return {
        "success": bool(success),
        "motion_time_s": env_step_count * env_dt,
        "env_steps": env_step_count,
        "msj": mean_squared_jerk(positions, env_dt),
        "sparc": sparc(speed, fs=params.native_hz * m),
        "thrash": float(np.nanmean(thrash_vals)) if thrash_vals else float("nan"),
        "thrash_first": (
            float(np.nanmean(thrash_first_vals)) if thrash_first_vals else float("nan")
        ),
        "n_queries": len(thrash_vals) + 1,
    }

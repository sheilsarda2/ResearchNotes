"""Episode metrics: smoothness (mean squared jerk, SPARC) and chunk thrash."""

import numpy as np


def mean_squared_jerk(positions, dt):
    """Mean squared jerk of the end-effector path. positions: [N, 3], dt seconds."""
    p = np.asarray(positions, dtype=np.float64)
    if len(p) < 4:
        return float("nan")
    jerk = np.diff(p, n=3, axis=0) / dt**3
    return float(np.mean(np.sum(jerk**2, axis=1)))


def sparc(speed, fs, padlevel=4, fc=10.0, amp_th=0.05):
    """Spectral arc length (Balasubramanian et al. 2015) of a speed profile.

    speed: 1D movement-speed samples, fs: sample rate in Hz.
    More negative = less smooth.
    """
    speed = np.asarray(speed, dtype=np.float64)
    if len(speed) < 4 or np.allclose(speed, 0):
        return float("nan")
    n = int(2 ** np.ceil(np.log2(len(speed)) + padlevel))
    freq = np.arange(n) * fs / n
    mag = np.abs(np.fft.fft(speed, n))
    mag = mag / mag.max()
    sel = freq <= fc
    freq_sel, mag_sel = freq[sel], mag[sel]
    active = np.where(mag_sel >= amp_th)[0]
    if len(active) < 2:
        return float("nan")
    freq_sel = freq_sel[: active[-1] + 1]
    mag_sel = mag_sel[: active[-1] + 1]
    df = np.diff(freq_sel / freq_sel[-1])
    dm = np.diff(mag_sel)
    return float(-np.sum(np.sqrt(df**2 + dm**2)))


def coherence_cost(reference, candidate, rho):
    """Decay-weighted disagreement with the previous plan, for chunk selection.

    reference: unexecuted rows of the previous chunk; candidate: a fresh sample.
    Both predict the same future model steps on their overlap. Returns the sum of
    rho**t * L2 over the motion dims, the backward-coherence criterion from
    Bidirectional Decoding (arXiv:2408.17355, Eq. 6). Gripper dim excluded so its
    binary jumps don't dominate the cost.
    """
    overlap = min(len(reference), len(candidate))
    if overlap == 0:
        return 0.0
    diff = reference[:overlap, :6] - candidate[:overlap, :6]
    weights = rho ** np.arange(overlap)
    return float(np.sum(weights * np.linalg.norm(diff, axis=1)))


def chunk_thrash(prev_chunk, executed_k, new_chunk):
    """Disagreement between consecutive queries on the timesteps they both predict.

    prev_chunk [T, 7] was queried one step earlier in model-time; executed_k of its
    actions ran before requery. Its remaining rows predict the same future steps as
    the head of new_chunk. Returns mean L2 over the motion dims of the overlap, or
    NaN when there is none (executed_k == chunk length).
    """
    remaining = prev_chunk[executed_k:]
    overlap = min(len(remaining), len(new_chunk))
    if overlap == 0:
        return float("nan")
    diff = remaining[:overlap, :6] - new_chunk[:overlap, :6]
    return float(np.mean(np.linalg.norm(diff, axis=1)))

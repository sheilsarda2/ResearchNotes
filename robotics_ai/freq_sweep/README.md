# freq_sweep: replanning-rate eval for a frozen VLA

Two questions. Does a frozen MolmoAct2-LIBERO checkpoint get better when inference
is free? And if it doesn't, which constraint binds instead: sampling consistency
across replans, or the decision content of the demonstrations?

The policy executes k actions from each 10-action chunk before requerying the
model. k=10 is today's deployment (query once per second, run the chunk open-loop).
k=1 requeries after every action, which in sim means zero-latency replanning: the
inference bottleneck removed. Two axes cross the k sweep:

- Disturbance: each cell runs deterministic (sigma=0) and with Ornstein-Uhlenbeck
  action noise (sigma* fixed by the pilot below).
- Selector: `naive` takes one sample per query. `coherence` draws n_samples
  candidate chunks and keeps the one closest to the unexecuted remainder of the
  previous chunk (Bidirectional Decoding's backward-coherence rule, arXiv:2408.17355
  Eq. 6; test-time only, frozen model). Selection needs chunk overlap, so this arm
  runs at k in {1, 2}.

Grids: `main` = k {1,2,5,10} x sigma {0, sigma*} with the naive selector (8 cells).
`mech` = k {1,2} x sigma {0, sigma*} with the coherence selector (4 cells).
10 LIBERO-Long tasks x 20 episodes = 200 episodes per cell.

## Pre-registered hypotheses

Statistical null for every comparison: success rates are equal. Three primary
comparisons, two-sided Fisher exact tests, Holm-corrected at alpha 0.05:

- **P1** (deterministic, naive, k=1 vs k=10). The latency-bottleneck view predicts
  k=1 >= k=10: fresh observations at zero cost can only help. Registered
  prediction: k=1 loses by 10+ points. Prior evidence: BID's VQ-BeT scores 48.9 vs
  64.0 on deterministic Push-T; Seohong Park's zero-latency closed-loop collapse
  under infinite data; Zhang et al. (arXiv:2507.09061) prove per-step replanning of
  a chunked predictor keeps the exponential compounding-error lower bound.
- **P2** (noisy, naive, k=1 vs k=10). Registered prediction: the ranking flips and
  k=1 wins (the noise half of BID's result). Confirms reactivity has value once
  the environment pushes back, and guards against reading P1 as "replanning is
  useless."
- **P3** (deterministic, k=1, coherence vs naive). The mechanism arm. If coherence
  recovers at least half of the P1 gap, the constraint after latency is sampling
  consistency, fixable at test time for free. If it recovers little, the
  constraint is informational: the demonstrations contain few decisions per
  second, and no test-time sampler adds decisions the data never held. Lazzati's
  delayed-policy results and Park's failed history-conditioning keep this second
  branch live.

Secondary, thrash mediation: `thrash_first` minus `thrash` (the counterfactual
column logged in the coherence arm) measures how much selection cuts plan-to-plan
disagreement inside the same episodes; in the naive arm, episode thrash should
predict failure at k=1.

Power: 200 episodes per cell resolves a 14-point success gap at alpha 0.05, power
0.8, two-sided. Dropping episodes_per_point to 10 halves compute and resolves ~19
points. Report Wilson 95% CIs per cell, plus per-task means with a sign test
across the 10 tasks as a robustness check (episodes within a task are correlated).

## Noise calibration pilot (run before the full grid)

40 episodes at k=10, naive, sigma=0.1. If success lands in [40%, 70%], fix
sigma* = 0.1. Below the band use 0.05, above it use 0.2, and re-pilot once. Pilot
episodes are excluded from analysis. This keeps the noisy arm off both floor and
ceiling so P2 stays resolvable.

## Run order

```bash
python sweep.py --config config.yaml --axis smoke   # 1 episode, native settings
# sigma pilot: temporarily set episodes_per_point to 4 (x10 tasks = 40 episodes)
python sweep.py --config config.yaml --axis main    # 8 cells, naive selector
python sweep.py --config config.yaml --axis mech    # 4 cells, coherence selector
```

Compute, assuming ~0.3s per model query in bf16: k=10 cells are cheap (at most 60
queries per episode). k=1 naive cells run up to 600 queries per episode, roughly
10 GPU-hours per 200-episode cell at the timeout, less in practice because
episodes end on success. The k=1 coherence cells multiply that by n_samples=4.
Ballpark for all 12 cells: 60-150 GPU-hours. Stage it: main deterministic, then
the pilot, then main noisy, then mech.

`rollout.py` also keeps execution-rate interpolation, observation-hold, and
time-warp parameters for follow-up experiments. The sweep runner doesn't exercise
them.

## Hardware

One NVIDIA GPU. MolmoAct2-LIBERO is 5B params: ~26GB VRAM in fp32, under 16GB in
bf16 (set `policy.dtype: bfloat16` in config.yaml). LIBERO/MuJoCo rendering is
cheap.

## Setup (Linux, CUDA)

```bash
cd freq_sweep
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt

git clone https://github.com/Lifelong-Robot-Learning/LIBERO.git
pip install -e LIBERO
python -c "import libero; print('libero ok')"

export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl
```

## Verify before the first sweep

Six assumptions are marked `# VERIFY` in the code. Check them against the
[model card](https://huggingface.co/allenai/MolmoAct2-LIBERO) on the GPU box:

1. `predict_action` keyword names (`policy_molmoact2.py`, single call site).
2. The 8-dim proprio state layout (`env_libero.py:build_state`).
3. Image orientation: LIBERO offscreen renders arrive upside down
   (`env_libero.py:extract_images`).
4. `native_hz` in config: the MolmoAct2 paper says LIBERO runs 10Hz/10-step chunks,
   but stock LIBERO uses control_freq=20. Match the checkpoint.
5. Gripper action sign convention.
6. Sample diversity: repeated `predict_action` calls must return distinct chunks
   (fresh flow noise per call). Check pairwise L2 between candidates is nonzero;
   identical samples silently turn the coherence arm into the naive arm
   (`policy_molmoact2.py:predict_chunks`).

## Outputs

Results append to `results/results.csv` (one row per episode) and
`results/<axis>.jsonl`. Columns include `selector`, `n_samples`, `thrash`, and the
counterfactual `thrash_first`. Deliverable plots: success vs k with one line per
(noise, selector) arm, and the P3 pair (k=1 naive vs k=1 coherence) with CIs.
Plotting comes after the first data exists.

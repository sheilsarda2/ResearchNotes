"""Sweep runner.

Usage:
    python sweep.py --config config.yaml --axis smoke   # 1 episode, native settings
    python sweep.py --config config.yaml --axis main    # naive selector: replan_k x ou_sigma
    python sweep.py --config config.yaml --axis mech    # coherence selector: coherence_k x ou_sigma

The main grid is 8 cells: k in {1,2,5,10} x {deterministic, OU noise}, one sample
per query. The mech grid adds 4 cells: k in {1,2} x the same two noise arms, with
n_samples candidates per query and backward-coherence selection. Each episode
appends one row to results/results.csv.

rollout.py keeps exec_multiplier / obs_hold / timewarp parameters for follow-up
experiments, but this runner does not exercise them; they stay at native values.
"""

import argparse
import csv
import json
import os
import time

import numpy as np
import yaml

from env_libero import make_env
from policy_molmoact2 import MolmoAct2Policy, PolicyConfig
from rollout import RolloutParams, run_episode

FIELDS = [
    "axis", "suite", "task_id", "episode", "seed",
    "replan_k", "selector", "n_samples",
    "exec_multiplier", "obs_hold", "ou_sigma", "timewarp",
    "success", "motion_time_s", "env_steps", "msj", "sparc",
    "thrash", "thrash_first", "n_queries", "wall_s",
]


def native_params(cfg):
    return dict(
        native_hz=cfg["native"]["native_hz"],
        chunk_len=cfg["native"]["chunk_len"],
        replan_k=cfg["native"]["chunk_len"],
        selector="naive",
        n_samples=1,
        coherence_rho=cfg["sweep"]["coherence_rho"],
        exec_multiplier=1,
        obs_hold=1,
        ou_sigma=0.0,
        ou_theta=cfg["sweep"]["ou_theta"],
        max_seconds=cfg["env"]["max_seconds"],
        timewarp=False,
    )


def grid_for_axis(cfg, axis):
    base = native_params(cfg)
    s = cfg["sweep"]
    if axis == "smoke":
        return [dict(base)]
    if axis == "main":
        return [
            dict(base, replan_k=k, ou_sigma=sig)
            for sig in s["ou_sigma"]
            for k in s["replan_k"]
        ]
    if axis == "mech":
        return [
            dict(base, replan_k=k, ou_sigma=sig,
                 selector="coherence", n_samples=s["n_samples"])
            for sig in s["ou_sigma"]
            for k in s["coherence_k"]
        ]
    raise ValueError(f"unknown axis: {axis}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--axis", required=True)
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    out_dir = cfg["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "results.csv")
    jsonl_path = os.path.join(out_dir, f"{args.axis}.jsonl")
    new_file = not os.path.exists(csv_path)

    policy = MolmoAct2Policy(PolicyConfig(**cfg["policy"]))
    points = grid_for_axis(cfg, args.axis)
    seeds = cfg["sweep"]["seeds"]
    n_eps = 1 if args.axis == "smoke" else cfg["sweep"]["episodes_per_point"]

    with open(csv_path, "a", newline="") as fcsv, open(jsonl_path, "a") as fjson:
        writer = csv.DictWriter(fcsv, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()

        for suite in [cfg["env"]["suite"]]:
            for task_id in cfg["env"]["task_ids"]:
                for point in points:
                    for ep in range(n_eps):
                        seed = seeds[ep % len(seeds)]
                        rng = np.random.default_rng(seed * 1000 + ep)
                        control_freq = point["native_hz"] * point["exec_multiplier"]
                        env, instruction, init_states = make_env(
                            suite, task_id, cfg["env"]["camera_hw"], control_freq, seed
                        )
                        env.reset()
                        env.set_init_state(init_states[ep % len(init_states)])

                        t0 = time.time()
                        result = run_episode(
                            env, instruction, policy, RolloutParams(**point), rng
                        )
                        env.close()

                        row = {
                            "axis": args.axis, "suite": suite, "task_id": task_id,
                            "episode": ep, "seed": seed,
                            "replan_k": point["replan_k"],
                            "selector": point["selector"],
                            "n_samples": point["n_samples"],
                            "exec_multiplier": point["exec_multiplier"],
                            "obs_hold": point["obs_hold"],
                            "ou_sigma": point["ou_sigma"],
                            "timewarp": point["timewarp"],
                            **result,
                            "wall_s": round(time.time() - t0, 1),
                        }
                        writer.writerow(row)
                        fcsv.flush()
                        fjson.write(json.dumps(row) + "\n")
                        fjson.flush()
                        print(
                            f"[{args.axis}] task={task_id} k={point['replan_k']} "
                            f"sel={point['selector']} n={point['n_samples']} "
                            f"sigma={point['ou_sigma']} ep={ep} -> "
                            f"success={result['success']} "
                            f"t={result['motion_time_s']:.1f}s"
                        )


if __name__ == "__main__":
    main()

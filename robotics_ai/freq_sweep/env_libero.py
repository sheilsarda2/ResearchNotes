"""LIBERO environment construction and observation adapters.

LIBERO actions are 7D OSC deltas: [dx, dy, dz, droll, dpitch, dyaw, gripper].
The first six are per-step displacements, which is what makes execution-rate
interpolation well defined: splitting a step into m substeps divides the deltas
by m and holds the gripper command.
"""

import os

import numpy as np
from scipy.spatial.transform import Rotation


def make_env(suite_name, task_id, camera_hw, control_freq, seed):
    """Create an offscreen LIBERO env for one task at a given controller rate.

    Returns (env, instruction, init_states).
    """
    from libero.libero import benchmark, get_libero_path
    from libero.libero.envs import OffScreenRenderEnv

    task_suite = benchmark.get_benchmark_dict()[suite_name]()
    task = task_suite.get_task(task_id)
    bddl_path = os.path.join(
        get_libero_path("bddl_files"), task.problem_folder, task.bddl_file
    )
    env = OffScreenRenderEnv(
        bddl_file_name=bddl_path,
        camera_heights=camera_hw,
        camera_widths=camera_hw,
        control_freq=control_freq,
    )
    env.seed(seed)
    init_states = task_suite.get_task_init_states(task_id)
    return env, task.language, init_states


def extract_images(obs):
    """Pull (agentview, wrist) RGB uint8 arrays out of a LIBERO observation.

    VERIFY: LIBERO offscreen renders arrive vertically flipped relative to what
    most VLA preprocessing expects; MolmoAct2's remote code may handle this.
    Confirm against the model card example before the first real sweep, and
    adjust the [::-1] below to match.
    """
    agentview = obs["agentview_image"][::-1].copy()
    wrist = obs["robot0_eye_in_hand_image"][::-1].copy()
    return agentview, wrist


def build_state(obs):
    """8-dim proprio vector for MolmoAct2-LIBERO.

    VERIFY: layout assumed as eef_pos(3) + eef_rpy(3) + gripper_qpos(2),
    matching the common LIBERO convention. Confirm ordering and rotation
    parameterization against the model card example.
    """
    pos = np.asarray(obs["robot0_eef_pos"], dtype=np.float32)
    quat = np.asarray(obs["robot0_eef_quat"], dtype=np.float32)  # xyzw
    rpy = Rotation.from_quat(quat).as_euler("xyz").astype(np.float32)
    grip = np.asarray(obs["robot0_gripper_qpos"], dtype=np.float32)
    return np.concatenate([pos, rpy, grip])


def eef_position(obs):
    return np.asarray(obs["robot0_eef_pos"], dtype=np.float64)


def split_delta_chunk(chunk, m, timewarp=False):
    """Retime a chunk of delta actions for an m-times-faster controller.

    Normal mode: each action becomes m substeps of delta/m (gripper held), so the
    motion covers the same physical path in the same physical time at a finer rate.
    Timewarp mode (harness validation): actions pass through undivided, so the
    same deltas fire m times faster and the motion runs m-times fast. This arm
    exists to fail; if it doesn't, the harness clock plumbing is wrong.
    """
    if m == 1:
        return chunk.copy()
    out = []
    for action in chunk:
        for _ in range(m):
            sub = action.copy()
            if not timewarp:
                sub[:6] = sub[:6] / m
            out.append(sub)
    return np.stack(out)


class OUNoise:
    """Ornstein-Uhlenbeck noise on the 6 motion dims: temporally correlated,
    matching the stochastic-arm protocol from Bidirectional Decoding."""

    def __init__(self, sigma, theta, dim=6, rng=None):
        self.sigma = sigma
        self.theta = theta
        self.dim = dim
        self.rng = rng or np.random.default_rng()
        self.x = np.zeros(dim, dtype=np.float64)

    def reset(self):
        self.x[:] = 0.0

    def sample(self):
        self.x += -self.theta * self.x + self.sigma * self.rng.standard_normal(self.dim)
        return self.x.copy()

    def apply(self, action):
        if self.sigma == 0.0:
            return action
        noisy = action.copy()
        noisy[:6] = noisy[:6] + self.sample()
        return noisy

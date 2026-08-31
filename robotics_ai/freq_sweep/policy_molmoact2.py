"""Frozen MolmoAct2-LIBERO wrapper.

The model is never trained or modified here; this file exists to give the rollout
loop a single, minimal interface: predict_chunk(images, instruction, state) -> [T, 7].
"""

from dataclasses import dataclass

import numpy as np
import torch


@dataclass
class PolicyConfig:
    model_id: str = "allenai/MolmoAct2-LIBERO"
    dtype: str = "bfloat16"
    device: str = "cuda"
    flow_num_steps: int = 10


class MolmoAct2Policy:
    def __init__(self, cfg: PolicyConfig):
        from transformers import AutoModelForImageTextToText

        torch_dtype = torch.float32 if cfg.dtype == "float32" else torch.bfloat16
        self.model = (
            AutoModelForImageTextToText.from_pretrained(
                cfg.model_id,
                trust_remote_code=True,
                dtype=torch_dtype,
            )
            .to(cfg.device)
            .eval()
        )
        self.cfg = cfg

    @torch.inference_mode()
    def predict_chunk(self, agentview_rgb, wrist_rgb, instruction, state):
        """Query the frozen model once; return one action chunk.

        agentview_rgb, wrist_rgb: HxWx3 uint8 RGB arrays (camera order matters).
        instruction: task string from the LIBERO task definition.
        state: np.float32 proprio vector (8-dim for LIBERO; see env_libero.build_state).
        Returns np.float32 array [chunk_len, 7] in robot scale (unnormalized).
        """
        # VERIFY: keyword names below against the MolmoAct2-LIBERO model card snippet.
        # This is the only call site; fix it once here.
        out = self.model.predict_action(
            images=[agentview_rgb, wrist_rgb],
            task=instruction,
            state=np.asarray(state, dtype=np.float32),
            norm_tag="libero",
            inference_action_mode="continuous",
            num_steps=self.cfg.flow_num_steps,
        )
        chunk = np.asarray(out.actions, dtype=np.float32)
        if chunk.ndim != 2 or chunk.shape[1] != 7:
            raise ValueError(f"unexpected chunk shape {chunk.shape}; expected [T, 7]")
        return chunk

    @torch.inference_mode()
    def predict_chunks(self, agentview_rgb, wrist_rgb, instruction, state, n):
        """Draw n candidate chunks for one observation. Returns [n, chunk_len, 7].

        VERIFY: repeated predict_action calls must return distinct samples (fresh
        flow-matching noise per call). If the remote code seeds deterministically,
        pass a generator/seed per call. Check pairwise L2 > 0 between candidates on
        the GPU box before trusting the coherence arm; identical samples silently
        reduce it to the naive selector. Batch the n calls if the remote code
        supports it; the loop below is the safe fallback.
        """
        chunks = [
            self.predict_chunk(agentview_rgb, wrist_rgb, instruction, state)
            for _ in range(n)
        ]
        return np.stack(chunks)

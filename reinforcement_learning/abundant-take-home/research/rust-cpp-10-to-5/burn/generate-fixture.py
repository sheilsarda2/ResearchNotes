"""Generate tiny real checkpoints with distinct values for each parameter."""
import json
from pathlib import Path
import torch
from safetensors.torch import save_file

root = Path(__file__).parent / "probe"
weights = {}
for prefix, base in [("flow.flows", 10), ("encoder.layers", 20)]:
    for index in [0, 2, 4]:
        weights[f"{prefix}.{index}.weight"] = torch.tensor([float(base + index)])
torch.save(weights, root / "mixed.pt")
save_file(weights, str(root / "mixed.safetensors"))
print(json.dumps({"torch": torch.__version__, "weights": {k: v.tolist() for k,v in weights.items()}}, indent=2))

# Burn reader v4 — Fable and Opus / max

Both audited max-effort runs are normal **reward-1 passes**. Each passes all eight
groups, including build and source checks, 108 PyTorch unit tests, 52 safetensors
tests, 20 integration tests, 37 pytorch-tests checks, the 62-check fixture matrix
covering 428 tensor rows, and 32 rejection cases. There are no functional failures.

| Run | Completed UTC | Assistant steps | Tool calls / returns | Agent / verifier minutes | Recorded cost |
|---|---|---:|---:|---:|---:|
| Fable `8dJTVHw` | 2026-09-14 04:33:07 | 158 | 158 / 158 | 71.02 / 1.63 | $28.79 |
| Opus `roFDdeS` | 2026-09-14 04:35:41 | 158 | 169 / 169 | 73.71 / 1.40 | $31.29 |

Native and ATIF step counts agree. Both runtime preflights passed, every logical
model query has a saved response, usage is uncensored, and cleanup was quiescent
before verification. Neither trial has a Harbor exception or an observed new
task/spec mismatch.

Raw trial paths:

- `jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__8dJTVHw`
- `jobs/candidates-burn-reader-v4-efforts-20-20260914T011100Z/rs-burn-store-pytorch-reader-v4__roFDdeS`

Both use task checksum
`60ad8cb53469867e4e2dd5333e7b028677cda254452eae2aff018e707def3c11`.
[max-effort-completions.json](max-effort-completions.json) binds the full raw
result, score, runtime, native/ATIF trajectories, and evidence paths with SHA256
hashes. These were audits of saved runs, not replays.

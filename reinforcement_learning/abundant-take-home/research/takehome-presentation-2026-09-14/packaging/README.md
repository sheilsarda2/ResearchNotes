# Take-home packaging tooling

The verified current plan is `plan-82-expanded.json`: 82 of the frozen 99 cells, 25 first-counted retained-task trials, three retained task directories, and 33 archived task directories. It inventories 729,008,993 uncompressed submission bytes plus 15,920,374 supporting-source bytes. Frozen JSON inputs add a small further supporting cost. ZIP size and the size of the two remaining retained-task trials are not estimated.

`plan-82.json` and `plan-only-verification.json` preserve the earlier, narrower archive plan. They are historical. The expanded plan includes every direct task directory containing `task.toml` under `candidates/`, `candidates_v2/`, and `research/task-revisions/`. It excludes validation job trees and the supplied restaurant task. The expanded verification receipt is `plan-82-expanded-verification.json`.

The planned single submission ZIP contains exactly these four root directories:

- `samples/`: the unchanged Rerun optimizer, Zenoh timestamp v3, and Burn reader v4 task directories, using their existing basenames.
- `jobs/<original-job>/<original-trial>/`: the entire raw trial directories for those three tasks. The earliest counted terminal outcome per frozen task/model/effort cell is selected by UTC finish time, with original path as the tie-breaker. At 99/99 this produces 27 trial directories. Raw source artifacts, result/config, native trajectory, ATIF, timing/evidence sidecars, and verifier outputs keep their names and bytes.
- `archive/`: the other 33 built task directories, including held candidates, superseded revisions, and the original SQLite candidate from the NUL audit. Metadata in the external plan distinguishes the eight current comparison tasks, the SQLite audit candidate, and historical tasks.
- `report/`: one supplied report file when available. Its prose must be human-written under the original brief. No report has been supplied or generated; the current plan has an empty report directory and cannot be called a completed submission.

A separate `*-supporting-evidence.zip` contains the frozen provenance inputs, the complete presentation evidence directory, the six actual exact-revision oracle/no-op control trial directories and their manifests/proofs, and any explicitly supplied deck/CSV/tooling assets. Controls never count toward the model-trial coverage gate. All eight current shortlist case trial directories, including Burn Sonnet/max `X6pyirF`, are already in the planned raw jobs. If a future case is a counted retained-task repeat, its whole trial directory goes into supporting evidence instead.

The assembler runs locally with Python's standard library. It has no scheduler, model, upload, or task-editing actions. It checks all 36 original files against commit `c1ae968`, validates the frozen collector sources and raw outcome identities, binds exact control results/scores, rejects duplicate destinations and symlinks, checks source drift, and verifies copied file and ZIP bytes. An incomplete 82/99 plan was explicitly rejected before any pack output was created. Sixteen local regression checks cover these failure modes and exact copy behavior.

After the collector has produced the complete 99-cell snapshot, create a **new** plan. Never edit an existing plan or its `.inputs` snapshots. Include a finalized presentation file through another `--supporting-source` argument; omit private `.build` intermediates. Include an existing human-written report with `--report path/to/report.md` once supplied. All source paths must be inside the workspace.

```sh
python3 -B scripts/package-takehome-evidence.py --plan-only \
  --plan research/takehome-presentation-2026-09-14/packaging/plan-99.json \
  --supporting-source research/takehome-presentation-2026-09-14/data/trials.csv \
  --supporting-source scripts/collect-takehome-evidence.py \
  --supporting-source scripts/package-takehome-evidence.py
```

Review that plan, then assemble from it with a fresh output name:

```sh
python3 -B scripts/package-takehome-evidence.py \
  --plan research/takehome-presentation-2026-09-14/packaging/plan-99.json \
  --output research/takehome-presentation-2026-09-14/packaging/review-pack-99
```

Assembly requires 99/99 and unchanged planned inputs. It always labels its output a review pack; human report authorship/content are not machine-verifiable. The final `*.verification.json` receipt is written only after both archives verify. A failed assembly may leave partial outputs without that completion receipt; preserve those for diagnosis and use a new output name after resolving the cause. No final pack was assembled during this work.

The remaining report work is human-authored prose addressing the original brief's assumptions, capability and model-gap argument, observed trajectory failures and headroom, relevant external comparisons, and a credible path from these samples to 1,000 quality-controlled tasks. The reviewed presentation and evidence can support that writing but do not establish human authorship.

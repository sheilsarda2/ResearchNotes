# Construction evidence

Built 2026-09-13 from sqlite-utils commit `85b1be10c81d9dd3567e36faf8dd411e4a8789bd`.

The final Docker image `abundant-candidate-sqlite-resumable:85b1be1` built successfully. It uses Python 3.12.11 and SQLite 3.40.1 on ARM64; all Python dependencies and editable package installation are complete at build time. Validation used fresh containers with `--network none --cpus 2 --memory 4096m`.

- Golden solution: **182 passed**, reward **1**, no skips or xfails. `container-oracle.log`, `container-oracle/diagnostics.json`, and `container-oracle/results.xml` contain the final results.
- Untouched baseline: **50 failed, 132 passed**, reward **0**, no skips or xfails. `container-noop.log`, `container-noop/diagnostics.json`, and `container-noop/results.xml` contain the final results. Missing API/CLI behavior causes failure; one unsupported-view test is already rejected by the existing library.
- The verifier includes **51 new contract cases** and **131 fixed upstream regressions**. The selected upstream files and fixture were copied verbatim from the pinned baseline into `tests/upstream/`, so submitted edits to repository tests cannot weaken that regression gate.
- Earlier local validation used Python 3.12.13 and passed the same 182 cases; the final Docker evidence is authoritative for the task environment.

The golden patch touches the existing library and CLI, adds a focused resumable import module, and documents both interfaces. No hidden tests or oracle files are in `environment/upstream.tar.gz`. `provenance.json` records the archive and patch hashes.

Recovery checks use actual subprocess termination in two places: inside a SQLite checkpoint trigger before commit, and from the documented progress callback after commit. They reopen the file database and resume through the real CLI. Other checks cover checkpoint write errors, trigger side effects, schema rollback, insertion sub-batches, source/options identity, Unicode byte framing, invalid/incomplete final records and compound-key upserts.

Memory verification guards unbounded file reads; the oracle itself reads a fixed-size fingerprint buffer and one logical batch at a time. Input mutation during one invocation, concurrent import workers, virtual tables, compressed files and CSV are explicitly outside the public task scope.

No paid model runs were performed. These results show that the implementation is executable and the verifier distinguishes it from no-op. They do not establish a >75-step horizon, model headroom, or training benefit. Harbor oracle/no-op execution and broader independent review remain root-owned.

Original supplied take-home paths were checked against commit `c1ae968` with `git diff --exit-code` from the `abundant-take-home` directory; no differences were found.

Current verifier audit completed 2026-09-13: 57 oracle checks passed, untouched baseline reward 0, and all representative valid implementations passed their complete suites. See [measurement audit](measurement-audit/report.md) for the current results and artifact hashes. The initial construction record below is historical; its test counts and method-specific injections are superseded where the audit explains a change.

---

Built and verified 2026-09-13. No paid model runs.

The Docker image uses Python 3.12.11 and the vendored clean Luigi commit
715f65c4a56a908ef0a1df4df6fc33b8420e2e6c. The baseline archive contains the
upstream license and tests, and contains no reference implementation or hidden
tests. The golden patch changes the package export, adds the implementation,
and adds linked documentation. Its bytes were checked against the isolated
worktree diff.

Direct Docker verification ran with networking disabled, 2 CPUs and 4 GiB RAM:

| Variant | Reward | Result |
| --- | --- | --- |
| Untouched upstream | 0 | 48 missing-feature errors; 9 upstream regressions passed |
| Golden patch | 1 | 57 passed; no failures/errors/skips |

The 48 behavioral cases cover validation, immutable manifests, raw/text/format
streams, external-tool paths, owned-handle lifecycle, staged failures, captured
and explicit CAS, competing subprocess writers, history pruning, live/dead
reader pins, live/dead staging leases, interruption immediately before and
after CURRENT replacement, an exception after replacement, same-length content
corruption, malformed metadata, symlinks, I/O error propagation, and actual
Luigi dependency execution/completion with one and two workers. Nine selected
upstream Target/LocalTarget cases verify backward compatibility.

Three temporary source mutants were rejected, and the source was restored:

| Mutant | Selected tests | Failures |
| --- | --- | --- |
| Remove compare-and-swap condition | 2 | 2 |
| Check only size, ignoring content digest | 8 | 2 |
| Remove reader shared locks | 2 | 2 |

Detailed JUnit and stdout evidence is in adjacent `docker-baseline/`,
`docker-oracle/`, and `mutant-*` files. Local macOS Python 3.12.13 checks also
passed all 48 behavioral cases. The first direct Docker attempt exposed
upstream tox.ini coverage addopts without a coverage plugin; the verifier now
explicitly overrides addopts while keeping the selected real upstream tests.

Harbor-level oracle/nop orchestration is delegated to the parent. A 75-step
trajectory and model headroom remain hypotheses pending user benchmarking.
All supplied take-home files still match original commit c1ae968.

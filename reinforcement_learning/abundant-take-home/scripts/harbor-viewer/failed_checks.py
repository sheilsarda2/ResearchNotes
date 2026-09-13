"""Read verifier check counts for the jobs monitor without modifying run artifacts."""

from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=4096)
def _read_report(path: str, mtime_ns: int, size: int) -> tuple[int, int, int] | None:
    # The stat fields invalidate cached counts when a running verifier writes.
    try:
        data = json.loads(Path(path).read_text())
        results = data["results"]
        tests = results.get("tests")
        if isinstance(tests, list) and tests:
            if not all(isinstance(test, dict) and isinstance(test.get("status"), str)
                       for test in tests):
                return None
            return (sum(test["status"] == "passed" for test in tests),
                    sum(test["status"] == "failed" for test in tests), len(tests))
        summary = results.get("summary", {})
        passed, failed, total = (summary.get(key) for key in ("passed", "failed", "tests"))
        if (all(type(value) is int for value in (passed, failed, total))
                and total > 0 and passed >= 0 and failed >= 0 and passed + failed <= total):
            return passed, failed, total
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def summarize_checks(job_dir: Path) -> dict[str, int | float | None]:
    """Count individual checks across readable reports; missing reports are unknown.

    The pass percentage is weighted by check count, not averaged across trials.
    Skipped/pending/other checks remain in the denominator and are not passes.
    """
    passed = failed = total = reports = 0
    try:
        trials = list(job_dir.iterdir())
    except OSError:
        trials = []
    for trial in trials:
        if not trial.is_dir():
            continue
        root_report = trial / "verifier/ctrf.json"
        # A root report takes precedence over per-step reports to avoid counting
        # an aggregate report and its component steps twice.
        paths = [root_report] if root_report.exists() else sorted(trial.glob("steps/*/verifier/ctrf.json"))
        if not paths:
            continue
        counts = []
        for path in paths:
            try:
                stat = path.stat()
                count = _read_report(str(path), stat.st_mtime_ns, stat.st_size)
            except OSError:
                count = None
            if count is not None:
                counts.append(count)
        if len(counts) == len(paths):
            passed += sum(count[0] for count in counts)
            failed += sum(count[1] for count in counts)
            total += sum(count[2] for count in counts)
            reports += 1
    return {"n_failed_checks": failed if reports else None,
            "n_passed_checks": passed if reports else None,
            "n_total_checks": total, "n_check_reports": reports,
            "checks_pass_pct": 100 * passed / total if total else None}


def count_failed_checks(job_dir: Path) -> tuple[int | None, int]:
    """Compatibility for viewer processes running the earlier count-only patch."""
    summary = summarize_checks(job_dir)
    return summary["n_failed_checks"], summary["n_check_reports"]

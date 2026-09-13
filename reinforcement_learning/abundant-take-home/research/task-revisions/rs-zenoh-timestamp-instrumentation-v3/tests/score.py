#!/usr/bin/env python3
"""Scoring helpers for the rs-zenoh-timestamp-instrumentation verifier (test.sh drives it).

Subcommands
  record <group> <pass|fail> [key=json ...]        append one group result
  anticheat <submission_root> <pristine_root> <crate...>
  check-list <expected.list> <actual.list>          every pristine test name still present
  parse-run <cargo.log> [--expect bin=N ...] [--expect-list <pristine.list>]
            [--expect-ignored <pristine.ignored.list>] [--exclude <name> ...]
            [--exit-code N] [--json out]
  failed <cargo.log>                                print failed test names (one per line)
  failed-bins <cargo.log>                           print "<binary>\\t<name>" per failed test
  finalize <required_group...>                      write score.json + reward.txt

Lists are `cargo test -- --list` output captured with 2>&1: cargo's per-binary
"Running ... (<bin>)" headers (stderr) are what attributes names to binaries.
"""
import json
import os
import re
import sys

LOGS = os.environ.get("VERIFIER_LOGS", "/logs/verifier")
PARTS = os.path.join(LOGS, "score_parts.jsonl")


def record(group, ok, **extra):
    os.makedirs(LOGS, exist_ok=True)
    with open(PARTS, "a") as f:
        f.write(json.dumps({"group": group, "pass": bool(ok), **extra}) + "\n")


def cmd_record(args):
    group, status = args[0], args[1]
    extra = {}
    for kv in args[2:]:
        k, _, v = kv.partition("=")
        try:
            extra[k] = json.loads(v)
        except Exception:
            extra[k] = v
    record(group, status == "pass", **extra)


# ---------------------------------------------------------------- anti-cheat
BANNED = [
    "process::Command", "Command::new(", "/logs", "/tests/", "/opt/gold", "/opt/pristine",
    "ts_peer", "py_peer", "TS_PEER", "TS_PY_PEER", "reward.txt", "score.json", "include_bytes!",
    "/workspace/build",
]
# Patterns that legitimately occur upstream: the submission may not add occurrences.
COUNTED = ["#[ignore", "atexit", "env::var", "include_str!", "std::env"]


def scan(root, crates):
    counts = {p: 0 for p in BANNED + COUNTED}
    symlinks, files = [], 0
    for crate in crates:
        base = os.path.join(root, crate, "src")
        if not os.path.isdir(base):
            return None, f"missing {base}"
        for dirpath, dirnames, filenames in os.walk(base):
            for name in dirnames + filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    symlinks.append(full)
            for name in filenames:
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                files += 1
                try:
                    text = open(full, "r", encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                for p in counts:
                    counts[p] += text.count(p)
    return {"counts": counts, "symlinks": symlinks, "files": files}, None


def cmd_anticheat(args):
    sub_root, pristine_root, crates = args[0], args[1], args[2:]
    sub, err = scan(sub_root, crates)
    problems = []
    if err:
        problems.append(err)
        record("anti_cheat", False, problems=problems)
        print(json.dumps({"problems": problems}))
        sys.exit(1)
    pristine, perr = scan(pristine_root, crates)
    if perr:
        problems.append("pristine: " + perr)
    if sub["symlinks"]:
        problems.append(f"symlinks in submission: {sub['symlinks'][:5]}")
    for p in BANNED:
        if sub["counts"][p] > 0:
            problems.append(f"banned pattern {p!r} occurs {sub['counts'][p]}x")
    for p in COUNTED:
        base = pristine["counts"][p] if pristine else 0
        if sub["counts"][p] > base:
            problems.append(f"pattern {p!r} grew from {base} to {sub['counts'][p]}")
    result = {"files": sub["files"], "counts": sub["counts"],
              "pristine_counts": pristine["counts"] if pristine else None, "problems": problems}
    record("anti_cheat", not problems, **result)
    print(json.dumps(result, indent=1))
    sys.exit(0 if not problems else 1)


# ---------------------------------------------------------------- cargo test parsing
RUNNING_RE = re.compile(r"^\s*Running (?:unittests )?(?P<src>\S+) \((?P<bin>\S+)\)")
LIST_RE = re.compile(r"^(\S.*): test$")
# With --test-threads=1 libtest prints "test NAME ... " before the test runs and the verdict
# after it, so a test's own stderr (tracing ERROR lines) can land in between: the verdict then
# starts a later line on its own. TEST_START_RE captures the name (and the verdict when it is
# on the same line); RESULT_TOKEN_RE resolves a pending name from a later line.
TEST_START_RE = re.compile(r"^test (?P<name>\S+) \.\.\. ?(?P<rest>.*)$")
RESULT_TOKEN_RE = re.compile(r"^(?P<res>ok|FAILED|ignored)\b")
RESULT_RE = re.compile(
    r"^test result: (?P<status>\w+)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; "
    r"(?P<ignored>\d+) ignored; (?P<measured>\d+) measured; (?P<filtered>\d+) filtered out"
)


def bin_name(path):
    base = os.path.basename(path)
    return base.rsplit("-", 1)[0] if "-" in base else base


def parse_list(text):
    """{binary: {test names}} from `cargo test -- --list 2>&1` output."""
    out, cur = {}, None
    for line in text.splitlines():
        m = RUNNING_RE.match(line)
        if m:
            cur = bin_name(m.group("bin"))
            out.setdefault(cur, set())
            continue
        m = LIST_RE.match(line)
        if m and cur is not None:
            out[cur].add(m.group(1))
    return out


def load_list(path):
    return parse_list(open(path, errors="replace").read())


def cmd_check_list(args):
    exp = load_list(args[0])
    act = load_list(args[1])
    missing = {b: sorted(n - act.get(b, set())) for b, n in exp.items() if n - act.get(b, set())}
    result = {"expected_total": sum(len(v) for v in exp.values()),
              "actual_total": sum(len(v) for v in act.values()), "missing": missing}
    if result["expected_total"] == 0:
        result["problem"] = f"expected list {args[0]} names no tests (no 'Running' headers?): verifier defect"
    print(json.dumps(result, indent=1))
    sys.exit(0 if not missing and result["expected_total"] > 0 else 1)


def parse_run(text):
    bins, cur, pending = {}, None, None
    for line in text.splitlines():
        m = RUNNING_RE.match(line)
        if m:
            cur = bin_name(m.group("bin"))
            bins.setdefault(cur, {"passed": 0, "failed": 0, "ignored": 0, "status": None, "tests": {}})
            pending = None
            continue
        m = TEST_START_RE.match(line)
        if m and cur is not None:
            r = RESULT_TOKEN_RE.match(m.group("rest"))
            if r:
                bins[cur]["tests"][m.group("name")] = r.group("res")
                pending = None
            else:
                pending = m.group("name")   # verdict follows on a later line
            continue
        if pending is not None and cur is not None:
            r = RESULT_TOKEN_RE.match(line)
            if r:
                bins[cur]["tests"][pending] = r.group("res")
                pending = None
                continue
        m = RESULT_RE.match(line)
        if m and cur is not None:
            b = bins[cur]
            b["passed"] += int(m.group("passed"))
            b["failed"] += int(m.group("failed"))
            b["ignored"] += int(m.group("ignored"))
            b["status"] = m.group("status")
    return bins


def bin_problem(b, got):
    if got["failed"] or got["status"] != "ok":
        return f"{b}: {got['failed']} failed / {got['ignored']} ignored / status {got['status']}"
    return None


def cmd_parse_run(args):
    logfile = args[0]
    expects, expect_list, expect_ignored, excludes = {}, None, None, set()
    exit_code, out_json = None, None
    i = 1
    while i < len(args):
        if args[i] == "--expect":
            b, _, n = args[i + 1].partition("=")
            expects[b] = int(n)
            i += 2
        elif args[i] == "--expect-list":
            expect_list = args[i + 1]
            i += 2
        elif args[i] == "--expect-ignored":
            expect_ignored = args[i + 1]
            i += 2
        elif args[i] == "--exclude":
            excludes.add(args[i + 1])
            i += 2
        elif args[i] == "--exit-code":
            exit_code = int(args[i + 1])
            i += 2
        elif args[i] == "--json":
            out_json = args[i + 1]
            i += 2
        else:
            raise SystemExit(f"unknown option {args[i]}")
    bins = parse_run(open(logfile, errors="replace").read())
    problems = []
    covered = set()
    if not bins:
        problems.append("no test binary ran (cargo failed or was killed before any test ran)")
    for b, n in expects.items():
        covered.add(b)
        got = bins.get(b)
        if got is None:
            problems.append(f"{b}: binary did not run")
        elif got["passed"] != n or got["failed"] or got["ignored"] or got["status"] != "ok":
            problems.append(f"{b}: expected {n} passed, got {got['passed']} passed / "
                            f"{got['failed']} failed / {got['ignored']} ignored / status {got['status']}")
    excluded_seen = []
    if expect_list:
        exp = load_list(expect_list)
        allowed_ignored = load_list(expect_ignored) if expect_ignored else {}
        if sum(len(v) for v in exp.values()) == 0:
            problems.append(f"expected list {expect_list} names no tests (no 'Running' headers?): "
                            "verifier defect")
        for b, names in exp.items():
            covered.add(b)
            got = bins.get(b)
            if got is None:
                problems.append(f"{b}: binary did not run")
                continue
            ignorable = allowed_ignored.get(b, set())
            required = names - ignorable - excludes
            excluded_seen += sorted(names & excludes)
            passed = {n for n, r in got["tests"].items() if r == "ok"}
            missing = sorted(required - passed)
            if missing:
                problems.append(f"{b}: pristine tests not passed: {missing[:10]}"
                                + (" ..." if len(missing) > 10 else ""))
            ignored_now = {n for n, r in got["tests"].items() if r == "ignored"}
            bad_ignored = sorted(ignored_now - ignorable)
            if bad_ignored:
                problems.append(f"{b}: ignored but not ignored upstream: {bad_ignored[:10]}")
            p = bin_problem(b, got)
            if p:
                problems.append(p)
    # Any binary that failed is a problem, expected or not.
    for b, got in bins.items():
        if b in covered:
            continue
        p = bin_problem(b, got)
        if p:
            problems.append(p)
    if exit_code is not None and exit_code != 0:
        problems.append(f"cargo test exit code {exit_code}")
    summary = {b: {k: v for k, v in g.items() if k != "tests"} for b, g in bins.items()}
    failed = sorted(n for g in bins.values() for n, r in g["tests"].items() if r == "FAILED")
    result = {"binaries": summary, "failed_tests": failed, "excluded_tests": excluded_seen,
              "exit_code": exit_code, "problems": problems, "ok": not problems}
    if out_json:
        with open(out_json, "w") as f:
            json.dump({**result, "tests": {b: g["tests"] for b, g in bins.items()}}, f, indent=1)
    print(json.dumps(result, indent=1))
    sys.exit(0 if not problems else 1)


def cmd_failed(args):
    bins = parse_run(open(args[0], errors="replace").read())
    for g in bins.values():
        for n, r in g["tests"].items():
            if r == "FAILED":
                print(n)


def cmd_failed_bins(args):
    bins = parse_run(open(args[0], errors="replace").read())
    for b, g in bins.items():
        for n, r in g["tests"].items():
            if r == "FAILED":
                print(f"{b}\t{n}")


# ---------------------------------------------------------------- finalize
def cmd_finalize(args):
    required = args
    groups = {}
    if os.path.exists(PARTS):
        for line in open(PARTS):
            line = line.strip()
            if line:
                d = json.loads(line)
                groups[d["group"]] = d
    missing = [g for g in required if g not in groups]
    all_pass = not missing and all(groups[g]["pass"] for g in required)
    score = {
        "reward": 1 if all_pass else 0,
        "required_groups": required,
        "missing_groups": missing,
        "groups": groups,
    }
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "score.json"), "w") as f:
        json.dump(score, f, indent=1)
    with open(os.path.join(LOGS, "reward.txt"), "w") as f:
        f.write("1\n" if all_pass else "0\n")
    print(json.dumps({"reward": score["reward"], "missing_groups": missing,
                      "failed_groups": [g for g in required if g in groups and not groups[g]["pass"]]}))


COMMANDS = {
    "record": cmd_record, "anticheat": cmd_anticheat, "check-list": cmd_check_list,
    "parse-run": cmd_parse_run, "failed": cmd_failed, "failed-bins": cmd_failed_bins,
    "finalize": cmd_finalize,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])

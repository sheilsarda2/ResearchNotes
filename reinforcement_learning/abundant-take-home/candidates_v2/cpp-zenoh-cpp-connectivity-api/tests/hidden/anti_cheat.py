"""Submission inventory and anti-cheat scan for the transferred include/ tree.

Usage:
  anti_cheat.py baseline <include_dir>                 -> JSON token counts (pristine)
  anti_cheat.py check <include_dir> <baseline.json>    -> JSON report, exit 0 iff clean

Rules: only regular *.hxx files (no symlinks, no other extensions), total size under 8 MiB,
and for every suspicious token the occurrence count must not exceed the pristine count
(the pristine tree mentions `exit()` once in a doc comment; everything else is at zero).
"""
import json
import os
import sys

TOKENS = [
    "system(", "popen(", "execv", "execl", "fork(", "atexit(", "/logs", "reward", "zenohd",
    "fstream", "getenv(", "dlopen(", "#define assert", "NDEBUG", "__attribute__((constructor",
    "/tests", "score.json", "ctest", "exit(", "abort(", "kill(", "ptrace", "LD_PRELOAD",
]
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def scan(root):
    counts = {t: 0 for t in TOKENS}
    problems = []
    total = 0
    nfiles = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                problems.append("symlinked directory: " + os.path.join(dirpath, d))
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                problems.append("symlink: " + rel)
                continue
            if not os.path.isfile(p):
                problems.append("not a regular file: " + rel)
                continue
            if not fn.endswith(".hxx"):
                problems.append("unexpected file type: " + rel)
                continue
            nfiles += 1
            total += os.path.getsize(p)
            try:
                text = open(p, "r", encoding="utf-8", errors="replace").read()
            except OSError as e:
                problems.append("unreadable %s: %r" % (rel, e))
                continue
            for t in TOKENS:
                counts[t] += text.count(t)
    if total > MAX_TOTAL_BYTES:
        problems.append("include/ too large: %d bytes" % total)
    if nfiles == 0:
        problems.append("no .hxx files found")
    return counts, problems, nfiles, total


def main():
    mode, root = sys.argv[1], sys.argv[2]
    counts, problems, nfiles, total = scan(root)
    if mode == "baseline":
        json.dump({"counts": counts, "files": nfiles, "bytes": total}, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    baseline = json.load(open(sys.argv[3]))["counts"]
    exceeded = {t: [counts[t], baseline.get(t, 0)] for t in TOKENS if counts[t] > baseline.get(t, 0)}
    ok = not problems and not exceeded
    json.dump({"ok": ok, "files": nfiles, "bytes": total, "problems": problems, "token_exceeded": exceeded, "counts": counts}, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

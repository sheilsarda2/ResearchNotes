#!/usr/bin/env python3
"""Compute the C++ snippet run/compare plan from docs/snippets/snippets.toml.

Mirrors the selection logic of docs/snippets/compare_snippet_output.py (invoked with --no-py):
  * every snippet directory entry that has a .cpp file and is not in [opt_out.run] for "cpp" is RUN;
  * of those, the ones whose Rust counterpart also runs and that are not in [opt_out.compare] for
    "cpp" are COMPARED against the Rust output (Rust is the baseline, as upstream);
  * [extra_args] are passed verbatim with `$config_dir` resolved to <repo>/docs/snippets.

Usage: snippet_plan.py <repo_root> [--json out.json]
Prints a summary and writes the plan as JSON.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import tomllib

# Source fragments that docs/snippets/CMakeLists.txt does not compile into the `snippets` binary.
CMAKE_FILTERS = [
    r".*/concepts/static/*",
    r".*/migration/log_tick_enabled.*",
    r".*/migration/transactional_transforms/*",
    r".*/tutorials/custom-application-id.*",
    r".*/tutorials/custom-recording-id.*",
    r".*/tutorials/log-file.*",
    r".*/tutorials/log_line.*",
    r".*/tutorials/quick_start.*",
    r".*/tutorials/timelines_example.*",
]


def opt(table: dict, subdir: str, name: str) -> set[str]:
    for key in (subdir, f"{subdir}/{name}"):
        if key in table:
            return set(table[key])
    return set()


def build_plan(repo_root: str) -> dict:
    snippets_dir = os.path.join(repo_root, "docs", "snippets")
    cfg = tomllib.load(open(os.path.join(snippets_dir, "snippets.toml"), "rb"))
    opt_run = cfg["opt_out"]["run"]
    opt_cmp = cfg["opt_out"]["compare"]
    extra_args = cfg["extra_args"]
    root = os.path.join(snippets_dir, "all")

    examples: dict[tuple[str, str], set[str]] = {}
    for path in glob.glob(root + "/**", recursive=True):
        base = os.path.basename(path)
        if base == "__init__.py" or os.path.isdir(path):
            continue
        name, ext = os.path.splitext(base)
        if ext in (".cpp", ".rs", ".py"):
            subdir = os.path.relpath(os.path.dirname(path), root).replace("\\", "/")
            examples.setdefault((subdir, name), set()).add(ext)

    entries = []
    for (subdir, name), exts in sorted(examples.items()):
        if ".cpp" not in exts:
            continue
        cpp_path = f"{root}/{subdir}/{name}.cpp"
        if any(re.match(f, cpp_path) for f in CMAKE_FILTERS):
            continue
        run_out = opt(opt_run, subdir, name)
        if "cpp" in run_out:
            continue
        cmp_out = opt(opt_cmp, subdir, name)
        rust_runs = ".rs" in exts and "rust" not in run_out
        compare = rust_runs and "cpp" not in cmp_out
        args = [a.replace("$config_dir", snippets_dir) for a in opt(extra_args, subdir, name)]
        # keep declared order of extra args (opt() returns a set; re-read as list)
        for key in (subdir, f"{subdir}/{name}"):
            if key in extra_args:
                args = [a.replace("$config_dir", snippets_dir) for a in extra_args[key]]
        entries.append(
            {
                "key": f"{subdir}/{name}",
                "subdir": subdir,
                "name": name,
                "args": args,
                "compare": compare,
                "rust_runs": rust_runs,
            }
        )
    return {
        "repo_root": repo_root,
        "run_count": len(entries),
        "compare_count": sum(1 for e in entries if e["compare"]),
        "entries": entries,
    }


def main() -> None:
    repo_root = sys.argv[1]
    plan = build_plan(repo_root)
    out = None
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w") as f:
            json.dump(plan, f, indent=1)
    print(f"cpp snippets to run: {plan['run_count']}; compared against Rust: {plan['compare_count']}")
    if out:
        print(f"plan written to {out}")


if __name__ == "__main__":
    main()

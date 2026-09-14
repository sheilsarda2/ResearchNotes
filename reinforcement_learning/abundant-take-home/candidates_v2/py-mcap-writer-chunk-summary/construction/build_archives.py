#!/usr/bin/env python3
"""Build the task tarballs deterministically from the base-commit worktree.

  environment/upstream.tar.gz        pruned tree WITH the excision applied (agent start state)
  tests/image-source/pristine.tar.gz pruned pristine tree + full conformance corpus (verifier)
  tests/image-source/reference-src.tar.gz  Rust + Go sources for the reference readers (verifier builders)

Usage: build_archives.py WORKTREE EXCISED_PYTHON_MCAP_DIR TASK_DIR
LFS objects must already be smudged in WORKTREE (git lfs pull); tar is used rather
than `git archive` because git archive would emit LFS pointer files.
"""
import gzip
import hashlib
import io
import os
import sys
import tarfile
from pathlib import Path

MTIME = 1789415227  # 2026-09-09T11:47:07-04:00, commit time of aebd536b

wt, excised_pkg, task = (Path(p).resolve() for p in sys.argv[1:4])


def add_tree(entries, src_root, rel_root, exclude=()):
    for path in sorted(src_root.rglob("*")):
        rel = path.relative_to(src_root)
        if any(part == "__pycache__" or part.endswith(".pyc") or part.endswith(".egg-info") for part in rel.parts):
            continue
        if any(str(rel).startswith(e) for e in exclude):
            continue
        if path.is_dir():
            continue
        entries.append((str(Path(rel_root) / rel), path))


def write_tar(out_path, entries):
    entries = sorted(entries, key=lambda e: e[0])
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        dirs_done = set()
        for name, src in entries:
            parts = Path(name).parts
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in dirs_done:
                    ti = tarfile.TarInfo(d)
                    ti.type = tarfile.DIRTYPE
                    ti.mode = 0o755
                    ti.mtime = MTIME
                    ti.uid = ti.gid = 0
                    ti.uname = ti.gname = "root"
                    tf.addfile(ti)
                    dirs_done.add(d)
            data = src.read_bytes()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o755 if (os.access(src, os.X_OK) and src.suffix in (".sh", "")) else 0o644
            ti.mtime = MTIME
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(data))
    raw = buf.getvalue()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as gz:
            gz.write(raw)
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"{out_path.relative_to(task)}  files={len(entries)}  bytes={out_path.stat().st_size}  sha256={digest}")
    return digest


common = []
for f in ("LICENSE", "README.md"):
    common.append((f, wt / f))
add_tree(common, wt / "website" / "docs" / "spec", "website/docs/spec")
add_tree(common, wt / "testdata", "testdata")

# agent tree: excised python/mcap, one conformance file for the upstream CRC test
agent = list(common)
add_tree(agent, excised_pkg, "python/mcap")  # excised_pkg is .../python/mcap (package dir + tests)
agent.append(("python/README.md", wt / "python" / "README.md"))
for f in ("OneMessage.mcap", "OneMessage.json"):
    agent.append((f"tests/conformance/data/OneMessage/{f}", wt / "tests" / "conformance" / "data" / "OneMessage" / f))
write_tar(task / "environment" / "upstream.tar.gz", agent)

# verifier tree: pristine python/mcap + full corpus
pristine = list(common)
add_tree(pristine, wt / "python" / "mcap", "python/mcap")
pristine.append(("python/README.md", wt / "python" / "README.md"))
add_tree(pristine, wt / "tests" / "conformance" / "data", "tests/conformance/data")
write_tar(task / "tests" / "image-source" / "pristine.tar.gz", pristine)

# reference sources for Rust and Go readers
ref = [("Cargo.toml", wt / "Cargo.toml"), ("Cargo.lock", wt / "Cargo.lock"), ("LICENSE", wt / "LICENSE")]
add_tree(ref, wt / "rust", "rust", exclude=("mcap/tests/data",))
add_tree(ref, wt / "go", "go")
write_tar(task / "tests" / "image-source" / "reference-src.tar.gz", ref)

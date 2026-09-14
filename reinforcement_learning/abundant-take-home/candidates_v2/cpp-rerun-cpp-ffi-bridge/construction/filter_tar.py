#!/usr/bin/env python3
"""Filter a `git archive` tar stream: drop excluded prefixes/globs and the excised files.

Usage: git archive --format=tar <commit> | python3 filter_tar.py excised_files.txt > out.tar
Member order and headers are preserved so the output is deterministic for a given commit.
"""
import fnmatch
import io
import re
import sys
import tarfile

EXCLUDE_PREFIXES = [
    "tests/assets/rrd/",              # checked-in reference .rrd files (oracle leak) and other large recordings
    "tests/assets/gaussian_splats/",  # 32 MB, only used by an opted-out snippet
    "tests/assets/mcap/trossen_transfer_cube.mcap",
    "tests/python/",
    "examples/python/",
    "examples/notebook/",
    "rerun_notebook/",
    "rerun_js/",
]
EXCLUDE_GLOBS = [
    "crates/*/*/tests/snapshots/*",    # viewer snapshot PNGs
    "crates/*/*/tests/snapshots/*/*",
    # tests/rust/* are Cargo workspace members (root Cargo.toml [workspace] members): their manifests and sources must
    # stay or `cargo fetch/build --locked` fails to load the workspace. Only their LFS snapshot PNGs (43 MB) are dropped.
    "tests/rust/*/tests/snapshots/*",
    "tests/rust/*/tests/snapshots/*/*",
    "tests/assets/video/Big_Buck_Bunny_1080_1s_*",  # only the two 10 s AV1 clips are used by snippets
]


def workspace_member_patterns(root_cargo_toml: bytes) -> tuple[list, list]:
    """Return (members, exclude) globs from the root Cargo.toml [workspace] table (no tomllib on python < 3.11)."""
    text = root_cargo_toml.decode()
    ws = re.search(r"^\[workspace\]\n(.*?)(?=^\[)", text, re.S | re.M)
    body = ws.group(1) if ws else ""
    def arr(key: str) -> list:
        m = re.search(rf"^{key}\s*=\s*\[(.*?)\]", body, re.S | re.M)
        return re.findall(r'"([^"]+)"', m.group(1)) if m else []
    return arr("members"), arr("exclude")


def main() -> None:
    excised = {line.strip() for line in open(sys.argv[1]) if line.strip()}
    seen = set()
    root_cargo = None
    manifests_in, manifests_out = set(), set()
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as src, tarfile.open(
        fileobj=sys.stdout.buffer, mode="w|", format=tarfile.PAX_FORMAT
    ) as dst:
        for member in src:
            name = member.name
            if member.isfile() and name.endswith("Cargo.toml"):
                manifests_in.add(name)
            if name == "Cargo.toml":
                root_cargo = src.extractfile(member).read()
                dst.addfile(member, io.BytesIO(root_cargo))
                manifests_out.add(name)
                continue
            if any(name.startswith(p) or name == p.rstrip("/") for p in EXCLUDE_PREFIXES):
                continue
            if any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS):
                continue
            if name in excised:
                seen.add(name)
                continue
            if member.isfile():
                if name.endswith("Cargo.toml"):
                    manifests_out.add(name)
                dst.addfile(member, src.extractfile(member))
            else:
                dst.addfile(member)
    missing = excised - seen
    if missing:
        sys.stderr.write(f"ERROR: excised files not found in archive: {sorted(missing)}\n")
        sys.exit(1)
    # Every Cargo workspace member manifest present in the input must be present in the output.
    members, ws_exclude = workspace_member_patterns(root_cargo or b"")
    if not members:
        sys.stderr.write("ERROR: could not read [workspace] members from the root Cargo.toml\n")
        sys.exit(1)
    dropped_members = sorted(
        m for m in manifests_in - manifests_out
        if any(fnmatch.fnmatch(m[: -len("/Cargo.toml")], pat) for pat in members)
        and not any(fnmatch.fnmatch(m[: -len("/Cargo.toml")], pat) for pat in ws_exclude)
    )
    if dropped_members:
        sys.stderr.write(f"ERROR: workspace member manifests dropped by the filter: {dropped_members}\n")
        sys.exit(1)
    kept_members = sorted(
        m for m in manifests_out if any(fnmatch.fnmatch(m[: -len("/Cargo.toml")], pat) for pat in members)
    )
    sys.stderr.write(f"excised {len(seen)} files; {len(kept_members)} workspace member manifests kept\n")


if __name__ == "__main__":
    main()

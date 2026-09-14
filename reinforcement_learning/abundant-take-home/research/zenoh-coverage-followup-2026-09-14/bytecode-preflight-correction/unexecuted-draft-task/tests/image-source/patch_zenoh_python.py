#!/usr/bin/env python3
"""Point zenoh-python's `zenoh` and `zenoh-ext` git dependencies at the gold tree (image build only)."""
import pathlib
import re
import sys

manifest = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/zenoh-python/Cargo.toml")
gold = sys.argv[2] if len(sys.argv) > 2 else "/opt/gold"
text = manifest.read_text()
pattern = re.compile(
    r'^(zenoh(?:-ext)?) = \{ version = "[^"]+", git = "https://github\.com/eclipse-zenoh/zenoh\.git", branch = "main",',
    re.M,
)
new_text, n = pattern.subn(lambda m: f'{m.group(1)} = {{ path = "{gold}/{m.group(1)}",', text)
if n != 2:
    sys.exit(f"expected to rewrite 2 git dependencies, rewrote {n}")
manifest.write_text(new_text)
print(f"rewrote {n} dependencies in {manifest}")

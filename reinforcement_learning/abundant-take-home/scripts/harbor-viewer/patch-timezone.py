"""Patch the installed Harbor viewer's date rendering; safe to reapply."""

from pathlib import Path
import re
import sys


assets = Path(sys.argv[1]) / "assets"
module_import = (
    'import{formatTimestamp as harborPstFormat,parseTimestamp as harborUtcDate}'
    'from"./timezone.js";\n'
)
date_call = r"new Date\(([\w.$\[\]]+)\)"
local_format = date_call + (
    r'\.toLocaleString\((?:void 0,\{dateStyle:"short",timeStyle:"short"\})?\)'
)
pending = []
for route in ("home", "task", "trial"):
    # Match the hashed route bundle, excluding task-definition(s).
    bundles = [
        path for path in assets.glob(f"{route}-*.js")
        if re.fullmatch(rf"{route}-[\w-]{{8}}\.js", path.name)
    ]
    if len(bundles) != 1:
        raise SystemExit(f"Expected one Harbor {route} bundle; found {len(bundles)}")
    path = bundles[0]
    original = path.read_text()
    if original.startswith(module_import):
        continue
    include_zone = ",true" if route == "trial" else ""
    updated, count = re.subn(
        local_format, rf"harborPstFormat(\1{include_zone})", original
    )
    if count != 1:
        raise SystemExit(f"Expected one timestamp formatter in {path.name}; found {count}")
    # Normalize the same timestamps for running durations and timeline deltas.
    updated = re.sub(date_call, r"harborUtcDate(\1)", updated)
    if route != "trial":
        old_header = 'children:"Started"'
        if updated.count(old_header) != 1:
            raise SystemExit(f"Started header not found uniquely in {path.name}")
        updated = updated.replace(old_header, 'children:"Started (PST, UTC−8)"')
    pending.append((path, module_import + updated))

# Validate all route patterns before changing any installed bundles.
for path, content in pending:
    path.write_text(content)
print(f"[patch-harbor-viewer] PST timestamps installed ({len(pending)} bundles updated)")

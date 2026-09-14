#!/usr/bin/env bash
# Show reasoning effort, PST timestamps, and verifier check pass rates in the viewer.
# Idempotent; rerun after installing or upgrading Harbor, then restart the viewer.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_JS="$REPO_ROOT/scripts/harbor-viewer/effort-badge.js"
HARBOR_BIN="$(command -v harbor || true)"
HARBOR_PY=""
if [ -n "$HARBOR_BIN" ]; then
  HARBOR_PY="$(dirname "$(readlink -f "$HARBOR_BIN")")/python"
fi
if [ ! -x "${HARBOR_PY:-}" ]; then
  HARBOR_PY="${HOME}/.local/share/uv/tools/harbor/bin/python"
fi
HARBOR_PKG=""
if [ -x "${HARBOR_PY:-}" ]; then
  HARBOR_PKG="$("$HARBOR_PY" -c 'import harbor, pathlib; print(pathlib.Path(harbor.__file__).parent)')"
fi
if [ ! -d "$HARBOR_PKG" ]; then
  echo "[patch-harbor-viewer] harbor package not found; skip"
  exit 0
fi

STATIC="$HARBOR_PKG/viewer/static"
INDEX="$STATIC/index.html"
DEST_JS="$STATIC/assets/effort-badge.js"

if [ ! -f "$SRC_JS" ] || [ ! -f "$INDEX" ]; then
  echo "[patch-harbor-viewer] viewer files missing; skip"
  exit 0
fi

cp "$SRC_JS" "$DEST_JS"
cp "$REPO_ROOT/scripts/harbor-viewer/timezone.js" "$STATIC/assets/timezone.js"
"$HARBOR_PY" "$REPO_ROOT/scripts/harbor-viewer/patch-timezone.py" "$STATIC"
cp "$REPO_ROOT/scripts/harbor-viewer/failed_checks.py" "$HARBOR_PKG/viewer/failed_checks.py"
"$HARBOR_PY" "$REPO_ROOT/scripts/harbor-viewer/patch-failed-checks.py" "$HARBOR_PKG/viewer"
cp "$REPO_ROOT/scripts/harbor-viewer/effort_groups.py" "$HARBOR_PKG/viewer/effort_groups.py"
"$HARBOR_PY" "$REPO_ROOT/scripts/harbor-viewer/patch-effort-groups.py" "$HARBOR_PKG/viewer"
if ! grep -q 'effort-badge.js' "$INDEX"; then
  "$HARBOR_PY" - "$INDEX" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
html = path.read_text()
tag = '<script src="/assets/effort-badge.js"></script>'
if tag not in html:
    html = html.replace("</body>", tag + "</body>", 1)
    path.write_text(html)
PY
fi

echo "[patch-harbor-viewer] jobs page customizations installed in $STATIC"

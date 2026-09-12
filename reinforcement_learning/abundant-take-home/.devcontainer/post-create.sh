#!/usr/bin/env bash
# Runs once when the dev container is created. cwd = workspace root.
set -euo pipefail

UV_VERSION="0.12.5"          # latest release; bump deliberately and re-run the verification checklist
HARBOR_VERSION="0.15.0"      # must match the version the tasks were authored against
PYTHON_VERSION="3.14"        # latest stable; harbor requires >=3.12
WORKSPACE_DIR="$(pwd)"
MARKER="# >>> take-home env >>>"

echo "[post-create] Installing uv ${UV_VERSION}"
curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | sh
export PATH="$HOME/.local/bin:$PATH"

echo "[post-create] Installing harbor ${HARBOR_VERSION} (uv provides Python ${PYTHON_VERSION})"
uv tool install --python "${PYTHON_VERSION}" "harbor==${HARBOR_VERSION}"
harbor --version

# Zip extraction can drop exec bits.
chmod +x "$WORKSPACE_DIR"/scripts/*.sh "$WORKSPACE_DIR"/.devcontainer/*.sh 2>/dev/null || true

# Shell hook: every interactive shell loads .env and exports the token as
# ANTHROPIC_API_KEY, so ad-hoc scripts (python + anthropic SDK, curl) hit the
# gateway with zero extra config, and `harbor run` forwards it into trial
# containers natively for anthropic/* models. The gateway base URL reaches
# trials via each task's task.toml [environment.env] block.
# Idempotent via marker; workspace path baked in so folder renames don't matter.
write_hook() {
  local rc="$1"
  touch "$rc"
  grep -qF "$MARKER" "$rc" && return 0
  cat >> "$rc" <<EOF

$MARKER
if [ -f "${WORKSPACE_DIR}/.env" ]; then
  set -a; . "${WORKSPACE_DIR}/.env"; set +a
  [ -n "\${TAKE_HOME_TOKEN:-}" ] && export ANTHROPIC_API_KEY="\$TAKE_HOME_TOKEN"
fi
# <<< take-home env <<<
EOF
}
write_hook "$HOME/.bashrc"
write_hook "$HOME/.zshrc"

if [ ! -f "$WORKSPACE_DIR/.env" ]; then
  printf '\n[post-create] No .env yet — that is expected on first build.\n'
  printf '              Next: cp .env.example .env  (paste your thg_ token)\n'
fi
echo "[post-create] Done. Verify with: bash scripts/doctor.sh"

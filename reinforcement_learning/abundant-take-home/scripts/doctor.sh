#!/usr/bin/env bash
# Verify the take-home environment end to end. Safe to run any time.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GATEWAY_URL="https://take-home-automation.vercel.app"
EXPECTED_HARBOR="0.15.0"
FAILED=0

ok()   { printf '[ok]   %s\n' "$1"; }
fail() { printf '[FAIL] %s\n       fix: %s\n' "$1" "$2"; FAILED=1; }
warn() { printf '[warn] %s\n' "$1"; }

# 1. Inside the dev container?
if [ "${TAKE_HOME_DEVCONTAINER:-}" = "1" ]; then
  ok "running inside the dev container"
else
  fail "not inside the dev container" "open this folder in VS Code and use 'Reopen in Container'"
  echo "Aborting — the remaining checks only make sense inside the container."
  exit 1
fi

# 2. Docker daemon (docker-in-docker)
if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "docker daemon and compose v2 available"
else
  fail "docker not working inside the container" "make sure Docker Desktop is running, then 'Rebuild Container' (Cmd/Ctrl-Shift-P)"
fi

# 3. Harbor installed at the pinned version
if command -v harbor >/dev/null 2>&1; then
  HV="$(harbor --version 2>/dev/null | tr -d '[:space:]')"
  if [ "$HV" = "$EXPECTED_HARBOR" ]; then
    ok "harbor $HV on PATH"
  else
    fail "harbor version is '$HV', expected $EXPECTED_HARBOR" "rerun: bash .devcontainer/post-create.sh"
  fi
else
  fail "harbor not on PATH" "rerun: bash .devcontainer/post-create.sh, then open a new terminal"
fi

# 4. .env hygiene
if [ ! -f .env ]; then
  fail ".env missing" "cp .env.example .env  and paste your thg_ token"
else
  if grep -q $'\r' .env; then
    fail ".env has Windows (CRLF) line endings" "re-save with LF line endings — a trailing \\r makes the gateway return 401"
  fi
  set -a; . ./.env 2>/dev/null; set +a
  if [ -z "${TAKE_HOME_TOKEN:-}" ] || [ "${TAKE_HOME_TOKEN:-}" = "thg_paste_your_token_here" ]; then
    fail "TAKE_HOME_TOKEN is empty or still the placeholder" "paste your personal thg_ token into .env"
  else
    case "$TAKE_HOME_TOKEN" in
      thg_*) ok ".env present, token set" ;;
      *)     warn "token does not start with thg_ — double-check you pasted the right value" ;;
    esac
  fi
fi

# 5. Live gateway auth probe (1-token request, same wiring the agent uses)
if [ -n "${TAKE_HOME_TOKEN:-}" ] && [ "${TAKE_HOME_TOKEN:-}" != "thg_paste_your_token_here" ]; then
  out="$(mktemp)"
  code=$(curl -sS -o "$out" -w '%{http_code}' --max-time 30 \
    -X POST "$GATEWAY_URL/v1/messages" \
    -H "x-api-key: ${TAKE_HOME_TOKEN}" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d '{"model":"claude-sonnet-5","max_tokens":1,"messages":[{"role":"user","content":"ping"}]}' \
    2>/dev/null)
  case "$code" in
    200) ok "gateway auth probe succeeded" ;;
    401|403) fail "gateway rejected the token (HTTP $code)" "check .env for typos/whitespace; if it persists, contact us for a fresh token" ;;
    *) fail "gateway probe failed (HTTP ${code:-timeout})" "network or gateway issue — response: $(head -c 200 "$out" 2>/dev/null)" ;;
  esac
  rm -f "$out"
fi

# 6. Disk space (this is the docker-in-docker volume's filesystem)
avail_gb=$(df -Pk / | awk 'NR==2 {printf "%d", $4/1024/1024}')
if [ "${avail_gb:-0}" -lt 10 ]; then
  warn "only ${avail_gb} GB free — trials need up to 10 GB; reclaim with: docker system prune -af"
else
  ok "disk space: ${avail_gb} GB free"
fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "All checks passed — run the sample task with:"
  echo "  harbor run -p restaurant-weekly-cost-control-audit -a mini-swe-agent -m anthropic/claude-sonnet-5"
else
  echo "Some checks failed — see fixes above."
  exit 1
fi

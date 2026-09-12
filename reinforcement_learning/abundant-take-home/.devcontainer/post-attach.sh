#!/usr/bin/env bash
# Runs on every attach; must be fast and never fail the attach.
W="$(pwd)"
if [ ! -f "$W/.env" ] || ! grep -qE '^TAKE_HOME_TOKEN=thg_[A-Za-z0-9_-]' "$W/.env" \
   || grep -q 'thg_paste_your_token_here' "$W/.env"; then
  cat <<'MSG'
------------------------------------------------------------------
 One step left:
   1. cp .env.example .env      # then paste your thg_ token into it
   2. open a NEW terminal
   3. bash scripts/doctor.sh    # verifies everything end to end
------------------------------------------------------------------
MSG
else
  echo "Take-home ready.  Verify: bash scripts/doctor.sh"
  echo "Sample run: harbor run -p restaurant-weekly-cost-control-audit -a mini-swe-agent -m anthropic/claude-sonnet-5"
fi
exit 0

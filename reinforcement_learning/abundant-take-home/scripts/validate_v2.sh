#!/usr/bin/env bash
# Validate one candidates_v2 task with Harbor 0.15.0: oracle (expect 1) then nop (expect 0).
# Usage: scripts/validate_v2.sh candidates_v2/<task-id> [--skip-nop]
# Runs ONE trial at a time (this host's Docker VM is small). Records rewards, timings and image
# sizes into candidates_v2/validation/<task-id>.json and appends funnel stages to funnel.jsonl.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TASK="${1:?task dir}"; TASK="${TASK%/}"; TID="$(basename "$TASK")"
SKIP_NOP="${2:-}"
JOBS="$ROOT/candidates_v2/validation/jobs"; mkdir -p "$JOBS" "$ROOT/candidates_v2/validation"
HARBOR=(uv tool run --from harbor==0.15.0 harbor)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/candidates_v2/validation/$TID.json"

run_agent() {  # $1=agent name
  local agent="$1" job="$TID-$1-$STAMP" t0 t1 rc result reward
  t0=$(date +%s)
  "${HARBOR[@]}" run -p "$ROOT/$TASK" -a "$agent" -y -n 1 -o "$JOBS" --job-name "$job" \
      > "$JOBS/$job.log" 2>&1; rc=$?
  t1=$(date +%s)
  result=$(ls "$JOBS/$job"/*/result.json 2>/dev/null | head -1)
  reward=$(python3 -c "import json,sys;r=json.load(open(sys.argv[1]));v=(r.get('verifier_result') or {}).get('rewards') or {};print(v.get('reward'));e=r.get('exception_info');print(e.get('exception_type') if e else '')" "$result" 2>/dev/null | tr '\n' ' ')
  echo "$agent rc=$rc reward/exception=[$reward] wall=$((t1-t0))s job=$job"
  python3 - "$OUT" "$agent" "$job" "$rc" "$((t1-t0))" "$result" <<'PY'
import json,sys,pathlib
out,agent,job,rc,wall,result=sys.argv[1:]
p=pathlib.Path(out); d=json.loads(p.read_text()) if p.exists() else {}
rec=dict(job=job, exit_code=int(rc), wall_seconds=int(wall), result=result or None)
if result:
    r=json.load(open(result)); v=(r.get('verifier_result') or {}).get('rewards') or {}
    rec.update(reward=v.get('reward'), exception=(r.get('exception_info') or {}).get('exception_type'),
               task_checksum=r.get('task_checksum'),
               timings={k:r.get(k) for k in ('environment_setup','agent_execution','verifier') if r.get(k)})
d[agent]=rec; p.write_text(json.dumps(d,indent=1)+'\n')
PY
}

echo "== $TID: oracle"; run_agent oracle
if [ "$SKIP_NOP" != "--skip-nop" ]; then echo "== $TID: nop"; run_agent nop; fi

# image sizes (best effort: Harbor names images by task path hash; list recent images instead)
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}} {{.CreatedSince}}' 2>/dev/null | head -6 > "$ROOT/candidates_v2/validation/$TID.images.txt"

python3 - "$OUT" "$TID" "$ROOT/candidates_v2/funnel.jsonl" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); tid=sys.argv[2]; fun=open(sys.argv[3],'a')
o=d.get('oracle',{}); n=d.get('nop',{})
stage='built'
if o.get('reward')==1.0: stage='oracle_pass'
if o.get('reward')==1.0 and n.get('reward')==0.0: stage='nop_pass'
if o.get('reward')==0.0: stage='oracle_fail'   # harness or task defect; superseded by a later rerun line
fun.write(json.dumps(dict(task_id=tid, stage=stage, notes=f"oracle={o.get('reward')} exc={o.get('exception')} wall={o.get('wall_seconds')}s; nop={n.get('reward')} exc={n.get('exception')}"))+'\n')
print('funnel stage:',stage)
PY

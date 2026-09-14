#!/usr/bin/env bash
# Verifier for py-onnx-reference-rnn (Harbor separate offline verifier).
#
# Harbor re-materializes the [[artifacts]] entry /workspace/repo/onnx/reference into this
# container at the same path.  We snapshot it, rebuild a clean room from the excised
# baseline plus the submission, restore the pristine upstream tests, and score.
# Paths are overridable for local dry runs.
set -uo pipefail

REPO="${REPO:-/workspace/repo}"
LOG="${LOG:-/logs/verifier}"
TESTS="${TESTS:-/tests}"
EXCISED="${EXCISED:-/opt/excised/reference}"
SUBSRC="${SUBSRC:-$REPO/onnx/reference}"
PY="${PY:-python}"
SUB=/tmp/verifier_submission

export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$LOG"
printf '0\n' > "$LOG/reward.txt"
rm -f "$LOG/precheck.json" "$LOG/family.json" "$LOG/pytest_refeval.json" "$LOG/pytest_backend.json"

note() { echo "[verifier] $*"; }

# ---------------------------------------------------------------- 1. locate + snapshot submission
if [ ! -d "$SUBSRC" ]; then
  alt=$(find /logs/artifacts -maxdepth 4 -type d -path '*onnx_reference' 2>/dev/null | head -n1)
  [ -n "$alt" ] && SUBSRC="$alt"
fi
present=false
if [ -d "$SUBSRC" ]; then
  present=true
  rm -rf "$SUB" && cp -a "$SUBSRC" "$SUB"
  find "$SUB" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} + 2>/dev/null
fi
note "submission: $SUBSRC present=$present"

# ---------------------------------------------------------------- 2. anti-cheat (static)
hits_file=$(mktemp)
if $present; then
  # forbidden artefacts
  find "$SUB" -type f \( -name '*.so' -o -name '*.pyd' -o -name '*.dylib' -o -name '*.pth' -o -name 'conftest.py' \
      -o -name 'sitecustomize.py' -o -name 'usercustomize.py' -o -name '*.pkl' -o -name '*.npy' -o -name '*.npz' \
      -o -name '*.onnx' -o -name '*.pb' -o -name '*.json' -o -name 'pytest.ini' -o -name 'setup.cfg' \) \
      -exec sh -c 'printf "forbidden file: %s\n" "$@"' _ {} + >> "$hits_file"
  # forbidden code patterns (none occur in the pristine onnx/reference tree)
  grep -rnE --include='*.py' \
      -e '^[[:space:]]*(import|from)[[:space:]]+onnxruntime' \
      -e 'import_module\(' -e '__import__' -e 'importlib' -e '_pytest' -e 'atexit' -e '/logs' \
      -e 'subprocess' -e 'os\.system' -e 'os\.popen' -e 'os\.exec' -e 'ctypes' -e 'sys\.meta_path' -e 'sys\.modules' \
      -e 'onnx\.backend\.test' -e 'backend\.test\.case' -e 'socket' -e 'urllib' -e 'pickle' -e 'marshal' \
      -e 'exec\(' -e 'os\.environ' -e 'sys\.path' -e 'inspect\.' \
      "$SUB" 2>/dev/null | cut -c1-200 | sed 's/^/pattern: /' >> "$hits_file"
  nbytes=$(du -sk "$SUB" | cut -f1)
  [ "$nbytes" -gt 20480 ] && echo "submission larger than 20 MB ($nbytes KB)" >> "$hits_file"
fi
anti_ok=true; [ -s "$hits_file" ] && anti_ok=false
note "anti-cheat ok=$anti_ok"

# ---------------------------------------------------------------- 3. clean room
import_ok=false; import_err=""
if $present; then
  rm -rf "$REPO/onnx/reference"
  cp -a "$EXCISED" "$REPO/onnx/reference"
  cp -a "$SUB/." "$REPO/onnx/reference/"
  # pristine upstream tests (verbatim copies from the base commit)
  cp "$TESTS/upstream/case_node/"*.py "$REPO/onnx/backend/test/case/node/"
  cp "$TESTS/upstream/tests_python/"*.py "$REPO/tests/python/"
  find "$REPO/onnx" "$REPO/tests" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
  if out=$(cd "$REPO" && timeout 300 "$PY" -c 'import onnx, onnx.reference, onnx.reference.ops._op_list; from onnx.reference import ReferenceEvaluator; print(onnx.__version__)' 2>&1); then
    import_ok=true
  else
    import_err=$(printf '%s' "$out" | tail -c 1500)
  fi
fi
note "import ok=$import_ok"

"$PY" - "$LOG/precheck.json" "$present" "$anti_ok" "$import_ok" "$SUBSRC" "$hits_file" "$import_err" <<'PY'
import json, sys
out, present, anti_ok, import_ok, src, hits_file, import_err = sys.argv[1:8]
hits = open(hits_file).read().splitlines()
json.dump({"submission_present": present == "true", "submission_source": src, "anti_cheat_ok": anti_ok == "true",
           "anti_cheat_hits": hits[:100], "import_ok": import_ok == "true", "import_error": import_err or None},
          open(out, "w"), indent=2)
PY
rm -f "$hits_file"

# ---------------------------------------------------------------- 4. groups
if $present && $import_ok; then
  cd "$REPO" || exit 0
  EXP="$TESTS/harness/expected_counts.json"
  read -r n_node <<<"$("$PY" -c "import json;print(json.load(open('$EXP'))['family_node_cases'])")"
  note "family harness (node tests + onnxruntime differential)"
  timeout 1500 "$PY" "$TESTS/harness/family_tests.py" --out "$LOG/family.json" --expected-node-cases "$n_node" 2>&1 | tail -n 60

  read -r c1 p1 s1 d1 <<<"$("$PY" -c "import json;e=json.load(open('$EXP'))['reference_evaluator_test'];print(e['collected'],e['passed'],e['skipped'],' '.join(e['deselect_exact']))")"
  note "upstream tests/python/reference_evaluator_test.py"
  # shellcheck disable=SC2086
  timeout 900 "$PY" "$TESTS/harness/run_pytest.py" --out "$LOG/pytest_refeval.json" --expect-collected "$c1" --expect-passed "$p1" --expect-skipped "$s1" \
      $(for d in $d1; do printf -- '--deselect-exact %s ' "$d"; done) -- -q -p no:cacheprovider --junitxml="$LOG/refeval.xml" tests/python/reference_evaluator_test.py 2>&1 | tail -n 25

  read -r c2 p2 s2 <<<"$("$PY" -c "import json;e=json.load(open('$EXP'))['backend_reference_test_cpu'];print(e['collected'],e['passed'],e['skipped'])")"
  note "upstream tests/python/backend_reference_test.py (cpu)"
  timeout 1500 "$PY" "$TESTS/harness/run_pytest.py" --out "$LOG/pytest_backend.json" --expect-collected "$c2" --expect-passed "$p2" --expect-skipped "$s2" \
      -- -q -p no:cacheprovider -k cpu --junitxml="$LOG/backend.xml" tests/python/backend_reference_test.py 2>&1 | tail -n 25
fi

# ---------------------------------------------------------------- 5. score
"$PY" "$TESTS/harness/score.py" "$LOG"
exit 0

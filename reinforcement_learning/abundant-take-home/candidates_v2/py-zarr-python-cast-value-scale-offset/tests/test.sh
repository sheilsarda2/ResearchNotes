#!/usr/bin/env bash
# Clean-room verifier for py-zarr-python-cast-value-scale-offset.
#
# 1. Locate the submitted package directory (only /workspace/repo/src/zarr is collected).
# 2. Anti-cheat: refuse symlinks and any *new* occurrence (relative to the pristine tree) of
#    patterns that would let package code reach into the test process or the verifier.
# 3. Overlay the submission on the pristine tree, restore src/zarr/testing and tests/ from the
#    pristine copy, add the hidden PR-derived tests.
# 4. Groups: import | pr_tests (63) | upstream (505) | cast_value_matrix (3790 vs zarrs) |
#    foreign_metadata (12 vs zarrs) | scale_offset_reference (100 authored).
# 5. reward.txt = 1 only if every group is ok and every count is exact; score.json carries the
#    per-group attribution.
#
# Every path can be overridden through the environment so the same script can be dry-run
# outside a container.
set -uo pipefail

REPO=${REPO:-/workspace/repo}
PRISTINE=${PRISTINE:-/pristine}
TESTS=${TESTS:-/tests}
LOGS=${LOGS:-/logs}
ORACLE=${ORACLE:-/usr/local/bin/zarrs_oracle}
PYTHON=${PYTHON:-python}
SUBMISSION=${SUBMISSION:-}
WORK=${WORK:-/tmp/verifier-work}

EXPECTED_PR_TESTS=63
EXPECTED_UPSTREAM_TESTS=505
EXPECTED_CV_CASES=3790
EXPECTED_FM_CASES=12
EXPECTED_SO_CASES=100

# coreutils `timeout` is present in the verifier image; degrade gracefully for dry runs on hosts
# without it (macOS) so the same script can be exercised outside a container.
if ! command -v timeout >/dev/null 2>&1; then
  if command -v gtimeout >/dev/null 2>&1; then timeout() { gtimeout "$@"; }; else timeout() { shift; "$@"; }; fi
fi

VDIR="$LOGS/verifier"
mkdir -p "$VDIR"
printf '0\n' > "$VDIR/reward.txt"
rm -rf "$WORK"
mkdir -p "$WORK/groups"

log() { printf '[verifier] %s\n' "$*" | tee -a "$VDIR/verifier.log" >&2; }

group_result() {  # name ok(0/1) json-fragment
  local name=$1 ok=$2 extra=${3:-{\}}
  "$PYTHON" - "$WORK/groups/$name.json" "$ok" "$extra" <<'PY'
import json, sys
path, ok, extra = sys.argv[1], sys.argv[2] == "1", json.loads(sys.argv[3])
extra["ok"] = ok
json.dump(extra, open(path, "w"), indent=1)
PY
}

finish() {
  "$PYTHON" - "$WORK/groups" "$VDIR/score.json" "$VDIR/reward.txt" <<'PY'
import json, os, sys
gdir, score_path, reward_path = sys.argv[1:4]
groups = {}
for fn in sorted(os.listdir(gdir)):
    groups[fn[:-5]] = json.load(open(os.path.join(gdir, fn)))
required = ["submission", "anti_cheat", "import", "pr_tests", "upstream", "cast_value_matrix", "foreign_metadata", "scale_offset_reference"]
missing = [g for g in required if g not in groups]
ok = not missing and all(groups[g].get("ok") for g in required)
score = {"reward": int(ok), "groups": groups, "missing_groups": missing}
json.dump(score, open(score_path, "w"), indent=1)
open(reward_path, "w").write(f"{int(ok)}\n")
print(json.dumps({g: groups[g].get("ok") for g in groups}), file=sys.stderr)
print("REWARD", int(ok), file=sys.stderr)
PY
  local r
  r=$(cat "$VDIR/reward.txt")
  [ "$r" = "1" ] && exit 0 || exit 1
}

# ---------------------------------------------------------------------------------------------
# 1. locate the submission
# ---------------------------------------------------------------------------------------------
if [ -z "$SUBMISSION" ]; then
  # Harbor re-materializes [[artifacts]] at their source path inside the verifier container.
  for cand in "$REPO/src/zarr" "$LOGS/artifacts/submission/src/zarr" "$LOGS/artifacts/workspace/repo/src/zarr" "/submission/src/zarr"; do
    if [ -f "$cand/__init__.py" ]; then SUBMISSION=$cand; break; fi
  done
fi
if [ -z "$SUBMISSION" ] || [ ! -f "$SUBMISSION/__init__.py" ] || [ ! -d "$SUBMISSION/codecs" ]; then
  log "no submission found at $REPO/src/zarr or under $LOGS/artifacts"
  find "$LOGS/artifacts" -maxdepth 4 2>/dev/null | head -50 >> "$VDIR/verifier.log" || true
  group_result submission 0 '{"path": null, "detail": "submitted src/zarr not found"}'
  finish
fi
group_result submission 1 "{\"path\": \"$SUBMISSION\"}"
log "submission: $SUBMISSION"
# Harbor re-materializes the artifact in place; move it aside so the overlay below can rebuild
# $REPO/src/zarr from pristine + submission without deleting the submission first.
case "$SUBMISSION" in
  "$REPO"/*) mkdir -p "$WORK"; mv "$SUBMISSION" "$WORK/submission"; SUBMISSION="$WORK/submission"; log "moved in-place submission to $SUBMISSION";;
esac

# ---------------------------------------------------------------------------------------------
# 2. anti-cheat
# ---------------------------------------------------------------------------------------------
"$PYTHON" - "$SUBMISSION" "$PRISTINE/src/zarr" "$WORK/anti_cheat.json" <<'PY'
import json, os, re, sys
sub, pristine, out = sys.argv[1:4]
PATTERNS = ["_pytest", "atexit", "/logs", "reward", "score.json", "zarrs_oracle", "sitecustomize",
            "usercustomize", "conftest", "subprocess", "pytest_", "monkeypatch", "os.system(", "os.exec"]
SKIP_DIRS = {"testing", "__pycache__"}

def scan(root):
    counts = {p: 0 for p in PATTERNS}
    symlinks, non_py = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                symlinks.append(os.path.join(dirpath, d))
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                symlinks.append(p)
                continue
            if not fn.endswith((".py", ".pyi", ".typed", ".txt", ".md")):
                non_py.append(os.path.relpath(p, root))
            try:
                text = open(p, "rb").read().decode("utf-8", "replace")
            except OSError:
                continue
            for pat in PATTERNS:
                counts[pat] += text.count(pat)
    return counts, symlinks, non_py

s_counts, s_links, s_nonpy = scan(sub)
p_counts, _, _ = scan(pristine)
new = {p: (s_counts[p], p_counts[p]) for p in PATTERNS if s_counts[p] > p_counts[p]}
ok = not new and not s_links
json.dump({"ok": ok, "new_pattern_hits": new, "symlinks": s_links, "non_python_files": s_nonpy,
           "submission_counts": s_counts, "pristine_counts": p_counts}, open(out, "w"), indent=1)
print(json.dumps({"anti_cheat_ok": ok, "new_pattern_hits": new, "symlinks": s_links}), file=sys.stderr)
PY
cp "$WORK/anti_cheat.json" "$WORK/groups/anti_cheat.json"
if ! "$PYTHON" -c "import json,sys; sys.exit(0 if json.load(open('$WORK/anti_cheat.json'))['ok'] else 1)"; then
  log "anti-cheat check failed; scoring stops here"
  for g in import pr_tests upstream cast_value_matrix foreign_metadata scale_offset_reference; do
    group_result "$g" 0 '{"detail": "not run: anti-cheat failed"}'
  done
  finish
fi

# ---------------------------------------------------------------------------------------------
# 3. clean-room overlay
# ---------------------------------------------------------------------------------------------
rm -rf "$REPO/src/zarr"
cp -a "$SUBMISSION" "$REPO/src/zarr"
find "$REPO/src/zarr" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
TESTING_DIFFERS=0
if ! diff -rq -x '__pycache__' "$REPO/src/zarr/testing" "$PRISTINE/src/zarr/testing" >/dev/null 2>&1; then TESTING_DIFFERS=1; fi
rm -rf "$REPO/src/zarr/testing"
cp -a "$PRISTINE/src/zarr/testing" "$REPO/src/zarr/testing"
[ -f "$REPO/src/zarr/_version.py" ] || cp "$PRISTINE/src/zarr/_version.py" "$REPO/src/zarr/_version.py"
rm -rf "$REPO/tests"
cp -a "$PRISTINE/tests" "$REPO/tests"
cp "$PRISTINE/pyproject.toml" "$REPO/pyproject.toml"
cp "$TESTS/hidden/conftest.py" "$REPO/tests/test_codecs/conftest.py"
cp "$TESTS/hidden/test_cast_value.py" "$REPO/tests/test_codecs/test_cast_value.py"
cp "$TESTS/hidden/test_scale_offset.py" "$REPO/tests/test_codecs/test_scale_offset.py"
find "$REPO/tests" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
log "overlay done (src/zarr/testing differed from pristine: $TESTING_DIFFERS)"

cd "$REPO" || { group_result import 0 '{"detail": "cannot cd to repo"}'; finish; }

# ---------------------------------------------------------------------------------------------
# 4a. import group
# ---------------------------------------------------------------------------------------------
if timeout 120 "$PYTHON" - > "$VDIR/import.log" 2>&1 <<'PY'
import zarr
from zarr.codecs import CastValue, ScaleOffset
from zarr.codecs.cast_value import CastValue as _C, parse_scalar_map
from zarr.codecs.scale_offset import ScaleOffset as _S, _encode, _decode
from zarr.registry import get_codec_class
assert get_codec_class("cast_value") is _C, get_codec_class("cast_value")
assert get_codec_class("scale_offset") is _S, get_codec_class("scale_offset")
print("import ok", zarr.__version__)
PY
then
  IMPORT_OK=1
  group_result import 1 "{\"testing_dir_restored\": true, \"testing_dir_differed\": $TESTING_DIFFERS}"
else
  IMPORT_OK=0
  group_result import 0 "{\"detail\": \"import or registry lookup failed, see import.log\", \"testing_dir_differed\": $TESTING_DIFFERS}"
  # The feature groups cannot run without the new modules; the upstream regression group still
  # runs below so a review can tell "nothing implemented" from "existing behaviour broken".
  for g in pr_tests cast_value_matrix foreign_metadata scale_offset_reference; do
    group_result "$g" 0 '{"detail": "not run: import failed"}'
  done
fi

# helper: run pytest and summarise its junit xml
run_pytest_group() {  # name expected timeout_sec args...
  local name=$1 expected=$2 tmo=$3; shift 3
  local xml="$VDIR/$name.xml"
  timeout "$tmo" "$PYTHON" -m pytest -q -p no:cacheprovider --tb=short -rfEs \
    --junitxml="$xml" "$@" > "$VDIR/$name.log" 2>&1
  local rc=$?
  "$PYTHON" - "$xml" "$expected" "$rc" "$WORK/groups/$name.json" <<'PY'
import json, sys
import xml.etree.ElementTree as ET
xml, expected, rc, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
res = {"exit_code": rc, "expected": expected, "collected": None, "failures": None, "errors": None, "skipped": None}
try:
    root = ET.parse(xml).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    res["collected"] = sum(int(s.get("tests", 0)) for s in suites)
    res["failures"] = sum(int(s.get("failures", 0)) for s in suites)
    res["errors"] = sum(int(s.get("errors", 0)) for s in suites)
    res["skipped"] = sum(int(s.get("skipped", 0)) for s in suites)
    res["passed"] = res["collected"] - res["failures"] - res["errors"] - res["skipped"]
    ok = rc == 0 and res["collected"] == expected and res["failures"] == 0 and res["errors"] == 0 and res["skipped"] == 0
except Exception as e:  # noqa: BLE001
    res["parse_error"] = str(e)
    ok = False
res["ok"] = ok
json.dump(res, open(out, "w"), indent=1)
print(json.dumps({name: res for name in [out.rsplit("/", 1)[-1][:-5]]}), file=sys.stderr)
PY
}

# ---------------------------------------------------------------------------------------------
# 4b. hidden PR-derived tests
# ---------------------------------------------------------------------------------------------
if [ "$IMPORT_OK" = 1 ]; then
run_pytest_group pr_tests "$EXPECTED_PR_TESTS" 900 \
  tests/test_codecs/test_cast_value.py tests/test_codecs/test_scale_offset.py
fi

# ---------------------------------------------------------------------------------------------
# 4c. upstream regressions (codec suites), exact count, no skips / xfails
# ---------------------------------------------------------------------------------------------
run_pytest_group upstream "$EXPECTED_UPSTREAM_TESTS" 1500 \
  tests/test_codecs/test_blosc.py tests/test_codecs/test_codecs.py tests/test_codecs/test_crc32c.py \
  tests/test_codecs/test_endian.py tests/test_codecs/test_gzip.py tests/test_codecs/test_numcodecs.py \
  tests/test_codecs/test_sharding.py tests/test_codecs/test_transpose.py tests/test_codecs/test_vlen.py \
  tests/test_codecs/test_zstd.py tests/test_codec_entrypoints.py tests/test_codec_pipeline.py \
  tests/test_sync_codec_pipeline.py \
  --deselect 'tests/test_codecs/test_numcodecs.py::test_generic_bytes_codec[PCodec]' \
  --deselect 'tests/test_codecs/test_numcodecs.py::test_generic_bytes_codec[ZFPY]' \
  --deselect 'tests/test_codecs/test_sharding.py::test_delete_empty_shards[zip]'

# ---------------------------------------------------------------------------------------------
# 4d. differential groups
# ---------------------------------------------------------------------------------------------
run_diff_group() {  # name script expected extra-args...
  local name=$1 script=$2 expected=$3; shift 3
  timeout 1500 "$PYTHON" "$TESTS/differential/$script" --workdir "$WORK/$name" \
    --out "$VDIR/$name.json" --expect "$expected" "$@" > "$VDIR/$name.log" 2>&1
  local rc=$?
  "$PYTHON" - "$VDIR/$name.json" "$rc" "$expected" "$WORK/groups/$name.json" <<'PY'
import json, sys
path, rc, expected, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
try:
    summary = json.load(open(path))["summary"]
except Exception as e:  # noqa: BLE001
    summary = {"ok": False, "error": f"no results: {e}"}
summary["exit_code"] = rc
summary["expected"] = expected
summary["ok"] = bool(summary.get("ok")) and rc == 0
json.dump(summary, open(out, "w"), indent=1)
print(json.dumps(summary), file=sys.stderr)
PY
}

if [ "$IMPORT_OK" = 1 ]; then
run_diff_group cast_value_matrix cast_value_matrix.py "$EXPECTED_CV_CASES" --oracle "$ORACLE"
run_diff_group foreign_metadata foreign_metadata.py "$EXPECTED_FM_CASES" --oracle "$ORACLE"
run_diff_group scale_offset_reference scale_offset_reference.py "$EXPECTED_SO_CASES"
fi

finish

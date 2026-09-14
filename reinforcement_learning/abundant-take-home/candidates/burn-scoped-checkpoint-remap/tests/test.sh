#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
export CARGO_NET_OFFLINE=true CARGO_BUILD_JOBS=2 CARGO_INCREMENTAL=0
unset RUSTC_WRAPPER RUSTC_WORKSPACE_WRAPPER
# Submitted source timestamps may predate cached artifacts. Force this crate to rebuild.
cargo clean --manifest-path /tests/checks/Cargo.toml -p burn-store --offline > /logs/verifier/clean.log 2>&1
clean_status=$?
cargo test --locked --offline --manifest-path /tests/checks/Cargo.toml --test upstream_keyremapper -- --test-threads=1 \
  > /logs/verifier/upstream-tests.log 2>&1
upstream_status=$?
cargo test --locked --offline --manifest-path /tests/checks/Cargo.toml --test store_defaults -- --test-threads=1 \
  > /logs/verifier/store-defaults.log 2>&1
defaults_status=$?
cargo test --locked --offline --manifest-path /tests/checks/Cargo.toml --test scoped -- --test-threads=1 \
  > /logs/verifier/scoped-tests.log 2>&1
scoped_status=$?
status=$((clean_status || upstream_status || defaults_status || scoped_status))
cat /logs/verifier/upstream-tests.log /logs/verifier/store-defaults.log /logs/verifier/scoped-tests.log > /logs/verifier/cargo-tests.log
cat /logs/verifier/cargo-tests.log
python3 - "$status" <<'PY'
import json,pathlib,re,sys
root=pathlib.Path('/logs/verifier')
text=(root/'cargo-tests.log').read_text()
results=re.findall(r'test result: ok\. (\d+) passed; 0 failed; 0 ignored;',text)
expected=sum(p.read_text().count('#[test]') for p in pathlib.Path('/tests/checks/tests').glob('*.rs'))
passed=sum(map(int,results))
ok=int(sys.argv[1])==0 and passed==expected and expected>0
diag={'exit_code':int(sys.argv[1]),'passed':passed,'expected':expected,'reward':int(ok)}
(root/'diagnostics.json').write_text(json.dumps(diag,indent=2)+'\n')
(root/'reward.txt').write_text('1\n' if ok else '0\n')
print(json.dumps(diag))
PY

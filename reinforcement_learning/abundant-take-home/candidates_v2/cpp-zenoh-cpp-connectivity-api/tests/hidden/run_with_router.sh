#!/bin/sh
# Verifier replacement for upstream tests/run_with_router.sh.
#
# Upstream clones and builds zenohd per test and synchronises with fixed sleeps. In the
# verifier a single prebuilt zenohd (started by test.sh) serves the whole run; this wrapper
# only waits for the router port to accept connections, then execs the test binary with the
# locator as its first argument (the upstream calling convention).
TESTBIN="$1"
LOCATOR="${ZENOH_TEST_ROUTER:-tcp/127.0.0.1:27447}"
HOSTPORT="${LOCATOR#tcp/}"
HOST="${HOSTPORT%:*}"
PORT="${HOSTPORT##*:}"

python3 - "$HOST" "$PORT" <<'PY' || { echo "router $LOCATOR not reachable"; exit 97; }
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
deadline = time.time() + 30
while time.time() < deadline:
    try:
        socket.create_connection((host, port), timeout=1).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.2)
sys.exit(1)
PY

echo "------------------ Running test $TESTBIN (router $LOCATOR) -------------------"
exec "$TESTBIN" "$LOCATOR"

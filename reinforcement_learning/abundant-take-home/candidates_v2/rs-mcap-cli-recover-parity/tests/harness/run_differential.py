#!/usr/bin/env python3
"""Differential verifier for the Rust `mcap recover` command against the Go reference CLI.

For every manifest case:
  1. run the Rust CLI (file / stdin / stdout / remote modes),
  2. derive the expected record stream by running the Go CLI (`recover -a`) on the case's
     expectation input and dumping it with the Go conformance reader, optionally filtered,
  3. dump the Rust output with the same Go reader and compare schemas, channels, messages,
     attachments and metadata,
  4. check exit code, the `Recovered ...` stderr summary, the lossy marker, output validity
     (`mcap-go doctor`), header preservation and output chunk compression.

Writes a JSON report with per-group totals. Never imports or links anything produced by the
agent: the only agent artifact touched is the built binary under test.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcaplite as M  # noqa: E402

CASE_TIMEOUT = 120
BIG_TIMEOUT = 900


class Tools:
    def __init__(self, rust, go, dump, workdir):
        self.rust, self.go, self.dump_path, self.workdir = rust, go, dump, workdir
        self._go_cache = {}

    def run(self, argv, *, stdin=None, stdout=None, timeout=CASE_TIMEOUT, env=None, cwd=None):
        e = dict(os.environ)
        e.setdefault("NO_COLOR", "1")
        e["TERM"] = "dumb"
        if env:
            e.update(env)
        return subprocess.run(argv, stdin=stdin, stdout=stdout if stdout is not None else subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env=e, cwd=cwd)

    # ---- Go reference side -------------------------------------------------------
    def go_recover(self, input_path):
        """Go recover -a (decode chunks) -> output path; cached per input."""
        if input_path in self._go_cache:
            return self._go_cache[input_path]
        out = os.path.join(self.workdir, "go_out", hashlib.sha1(input_path.encode()).hexdigest() + ".mcap")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        p = self.run([self.go, "recover", "-a", input_path, "-o", out])
        if p.returncode != 0:
            raise RuntimeError(f"go recover failed on {input_path}: rc={p.returncode} {p.stderr.decode(errors='replace')[:500]}")
        self._go_cache[input_path] = out
        return out

    def dump(self, path):
        p = self.run([self.dump_path, path, "streamed"])
        if p.returncode != 0:
            raise RuntimeError(f"dump failed on {path}: {p.stdout.decode(errors='replace')[:300]} {p.stderr.decode(errors='replace')[:300]}")
        return json.loads(p.stdout)

    def doctor_ok(self, path):
        p = self.run([self.go, "doctor", path])
        return p.returncode == 0, (p.stdout + p.stderr).decode(errors="replace")[-800:]


def normalize(dump_json):
    """Reduce the Go streamed dump (data section only) to comparable structures."""
    schemas, channels, messages, attachments, metadata, header = {}, {}, [], [], [], None
    conflicts = []
    for rec in dump_json["records"]:
        t = rec["type"]
        f = {k: v for k, v in rec["fields"]}
        if t == "DataEnd":
            break
        if t == "Header":
            header = (f.get("profile", ""), f.get("library", ""))
        elif t == "Schema":
            cur = (f["name"], f["encoding"], tuple(f["data"]))
            prev = schemas.setdefault(f["id"], cur)
            if prev != cur:
                conflicts.append(("schema", f["id"]))
        elif t == "Channel":
            cur = (f["schema_id"], f["topic"], f["message_encoding"], tuple(sorted(f["metadata"].items())))
            prev = channels.setdefault(f["id"], cur)
            if prev != cur:
                conflicts.append(("channel", f["id"]))
        elif t == "Message":
            messages.append((f["channel_id"], f["sequence"], f["log_time"], f["publish_time"], tuple(f["data"])))
        elif t == "Attachment":
            attachments.append((f["log_time"], f["create_time"], f["name"], f["media_type"], tuple(f["data"])))
        elif t == "Metadata":
            metadata.append((f["name"], tuple(sorted(f["metadata"].items()))))
    return {"header": header, "schemas": schemas, "channels": channels, "messages": messages,
            "attachments": attachments, "metadata": metadata, "conflicts": conflicts}


def apply_filter(n, exp):
    drop_s = {str(x) for x in exp.get("drop_schema_ids", [])}
    drop_c = {str(x) for x in exp.get("drop_channel_ids", [])}
    for cid, (sid, *_r) in list(n["channels"].items()):
        if sid in drop_s:
            drop_c.add(cid)
    n["schemas"] = {k: v for k, v in n["schemas"].items() if k not in drop_s}
    n["channels"] = {k: v for k, v in n["channels"].items() if k not in drop_c}
    n["messages"] = [m for m in n["messages"] if m[0] not in drop_c]
    for idx in sorted(exp.get("drop_message_index", []), reverse=True):
        if idx < len(n["messages"]):
            del n["messages"][idx]
    for idx in sorted(exp.get("drop_metadata_index", []), reverse=True):
        if idx < len(n["metadata"]):
            del n["metadata"][idx]
    for idx in sorted(exp.get("drop_attachment_index", []), reverse=True):
        if idx < len(n["attachments"]):
            del n["attachments"][idx]
    return n


def plural(n, noun):
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def summary_line(n):
    return (f"Recovered {plural(len(n['messages']), 'message')}, {plural(len(n['attachments']), 'attachment')}, "
            f"and {plural(len(n['metadata']), 'metadata record')}.")


def diff_norm(a, b):
    out = []
    for k in ("header", "schemas", "channels", "messages", "attachments", "metadata"):
        if k == "header":
            continue
        if a[k] != b[k]:
            if isinstance(a[k], list):
                out.append(f"{k}: got {len(a[k])} expected {len(b[k])}")
            else:
                out.append(f"{k}: got ids {sorted(a[k])} expected {sorted(b[k])}")
    return "; ".join(out)


class HttpServer:
    def __init__(self, root):
        handler = lambda *args, **kw: http.server.SimpleHTTPRequestHandler(*args, directory=root, **kw)  # noqa: E731
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError("http server did not start")

    def stop(self):
        self.httpd.shutdown()


def poll_rss_anon(proc, interval=0.005):
    """Peak RssAnon (MiB) of a running child, sampled from /proc/<pid>/status."""
    peak = 0
    path = f"/proc/{proc.pid}/status"
    seen = False
    while proc.poll() is None:
        try:
            with open(path) as f:
                for line in f:
                    if line.startswith("RssAnon:"):
                        seen = True
                        kb = int(line.split()[1])
                        peak = max(peak, kb // 1024)
                        break
        except OSError:
            pass
        time.sleep(interval)
    return peak if seen else None


def run_case(c, T, fixtures, tmp, http_port, big_expect):
    res = {"id": c["id"], "group": c["group"], "ok": False, "reasons": []}
    inp = os.path.join(fixtures, c["input"])
    out = os.path.join(tmp, "rust_out.mcap")
    if os.path.exists(out):
        os.remove(out)
    mode = c["mode"]
    args = list(c["args"])
    exp_exit = c["expect_exit"]

    def fail(msg):
        res["reasons"].append(msg)

    # ---------------------------------------------------------------- run Rust CLI
    try:
        if mode == "raw":
            p = T.run([T.rust] + [a.replace("{input}", inp).replace("{out}", out) for a in args])
        elif mode == "raw_file":
            p = T.run([T.rust] + [a.replace("{input}", inp).replace("{out}", out) for a in args])
        elif mode == "file":
            timeout = BIG_TIMEOUT if c["group"] == "memory" else CASE_TIMEOUT
            if "max_rss_anon_mib" in c["checks"]:
                proc = subprocess.Popen([T.rust, "recover", inp, "-o", out] + args, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=dict(os.environ, NO_COLOR="1", TERM="dumb"))
                peak = poll_rss_anon(proc)
                so, se = proc.communicate(timeout=timeout)
                p = subprocess.CompletedProcess(proc.args, proc.returncode, so, se)
                res["peak_rss_anon_mib"] = peak
            else:
                p = T.run([T.rust, "recover", inp, "-o", out] + args, timeout=timeout)
        elif mode == "stdin":
            timeout = BIG_TIMEOUT if c["group"] == "memory" else CASE_TIMEOUT
            with open(inp, "rb") as fh:
                if "max_rss_anon_mib" in c["checks"]:
                    proc = subprocess.Popen([T.rust, "recover", "-o", out] + args, stdin=fh, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, env=dict(os.environ, NO_COLOR="1", TERM="dumb"))
                    peak = poll_rss_anon(proc)
                    so, se = proc.communicate(timeout=timeout)
                    p = subprocess.CompletedProcess(proc.args, proc.returncode, so, se)
                    res["peak_rss_anon_mib"] = peak
                else:
                    p = T.run([T.rust, "recover", "-o", out] + args, stdin=fh, timeout=timeout)
        elif mode == "stdout":
            # stdout is a pipe (non-seekable); the harness captures the bytes into a file
            proc = subprocess.Popen([T.rust, "recover", inp] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=dict(os.environ, NO_COLOR="1", TERM="dumb"))
            so, se = proc.communicate(timeout=CASE_TIMEOUT)
            with open(out, "wb") as fo:
                fo.write(so)
            p = subprocess.CompletedProcess(proc.args, proc.returncode, b"", se)
        elif mode in ("remote", "remote_noflag"):
            url = f"http://127.0.0.1:{http_port}/{c['input']}"
            extra = ["--allow-remote-scan"] if mode == "remote" else []
            p = T.run([T.rust, "recover", url, "-o", out] + args + extra)
        else:
            raise AssertionError(mode)
    except subprocess.TimeoutExpired:
        fail("timeout")
        return res
    stderr = p.stderr.decode(errors="replace")
    stdout_bytes = p.stdout or b""
    res["exit"] = p.returncode

    # ---------------------------------------------------------------- exit code
    if exp_exit == "nonzero":
        if p.returncode == 0:
            fail("expected non-zero exit")
    elif p.returncode != exp_exit:
        fail(f"exit {p.returncode} != expected {exp_exit}")

    checks = c["checks"]
    if checks.get("stdout_nonempty") and not stdout_bytes.strip():
        fail("expected non-empty stdout")
    for needle in checks.get("stderr_contains", []):
        if needle not in stderr:
            fail(f"stderr missing {needle!r}")
    if mode in ("file", "stdin", "remote") and stdout_bytes:
        fail("stdout must be empty when -o is given")

    if exp_exit in (1, "nonzero") or c["expect"]["kind"] == "none":
        res["ok"] = not res["reasons"]
        return res

    # ---------------------------------------------------------------- output validity
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        fail("no output file produced")
        return res
    with open(out, "rb") as f:
        out_bytes = f.read() if c["group"] != "memory" else f.read(1 << 20)

    ok, doc = T.doctor_ok(out)
    if not ok:
        fail("mcap-go doctor rejected output: " + doc.strip().splitlines()[-1] if doc.strip() else "mcap-go doctor rejected output")

    # ---------------------------------------------------------------- expected content
    exp = c["expect"]
    if exp["kind"] == "big":
        try:
            with open(out, "rb") as f:
                count, digest = M.message_payload_digest(f.read())
        except Exception as e:  # noqa: BLE001
            fail(f"could not walk output: {e}")
            count, digest = None, None
        if count != exp["count"]:
            fail(f"message count {count} != {exp['count']}")
        if big_expect and digest != big_expect:
            fail("message payload digest mismatch")
        line = f"Recovered {plural(exp['count'], 'message')}, 0 attachments, and 0 metadata records."
        if line not in stderr.splitlines():
            fail("stderr summary line missing or wrong")
        peak = res.get("peak_rss_anon_mib")
        if peak is None:
            fail("could not sample RssAnon")
        elif peak > checks["max_rss_anon_mib"]:
            fail(f"peak anonymous RSS {peak} MiB exceeds {checks['max_rss_anon_mib']} MiB")
        if "Recovery was lossy" in stderr:
            fail("unexpected lossy marker")
        res["ok"] = not res["reasons"]
        return res

    try:
        go_out = T.go_recover(os.path.join(fixtures, exp["input"]))
        expected = normalize(T.dump(go_out))
    except Exception as e:  # noqa: BLE001
        fail(f"reference failure: {e}")
        return res
    if exp["kind"] == "go_filtered":
        expected = apply_filter(expected, exp)
    try:
        got = normalize(T.dump(out))
    except Exception as e:  # noqa: BLE001
        fail(f"output not readable by reference reader: {e}")
        return res
    if got["conflicts"]:
        fail(f"conflicting duplicate definitions in output: {got['conflicts'][:3]}")
    d = diff_norm(got, expected)
    if d:
        fail("record stream differs: " + d)

    # ---------------------------------------------------------------- stderr contract
    line = summary_line(expected)
    if line not in stderr.splitlines():
        fail(f"stderr summary line missing/wrong; expected {line!r}")
    lossy = any(l.startswith("Recovery was lossy:") for l in stderr.splitlines())
    if exp_exit == 3 and not lossy:
        fail("exit 3 without a 'Recovery was lossy:' line")
    if exp_exit == 0 and lossy:
        fail("'Recovery was lossy:' printed on a clean recovery")

    # ---------------------------------------------------------------- header
    hdr = checks.get("header")
    if hdr:
        got_hdr = got["header"]
        if hdr == "input":
            with open(os.path.join(fixtures, c["input"]), "rb") as f:
                src = M.header_fields(f.read())
            if src is None:
                fail("could not read the input header for comparison")
            elif got_hdr != tuple(src):
                fail(f"output header {got_hdr} != input header {tuple(src)}")
        elif hdr == "default_profile":
            if got_hdr is None or got_hdr[0] != "":
                fail(f"expected empty default profile, got {got_hdr}")

    # ---------------------------------------------------------------- compression
    if "chunk_compression" in checks:
        comps = M.chunk_compressions(out_bytes)
        want = checks["chunk_compression"]
        if any(cc != want for cc in comps):
            fail(f"output chunk compressions {sorted(set(comps))} != {want!r}")
        if len(comps) < checks.get("min_chunks", 0):
            fail(f"output has {len(comps)} chunks, expected at least {checks['min_chunks']}")
    elif "min_chunks" in checks:
        comps = M.chunk_compressions(out_bytes)
        if len(comps) < checks["min_chunks"]:
            fail(f"output has {len(comps)} chunks, expected at least {checks['min_chunks']}")

    res["ok"] = not res["reasons"]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rust-bin", required=True)
    ap.add_argument("--go-bin", required=True)
    ap.add_argument("--dump-bin", required=True)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--only", default=None, help="substring filter on case id (debugging)")
    a = ap.parse_args()

    with open(os.path.join(a.fixtures, "manifest.json")) as f:
        manifest = json.load(f)
    cases = manifest["cases"]
    if a.only:
        cases = [c for c in cases if a.only in c["id"]]

    big_expect = None
    for c in cases:
        if c["expect"]["kind"] == "big":
            big_expect = c["expect"].get("sha256")

    workdir = tempfile.mkdtemp(prefix="recover-diff-")
    T = Tools(a.rust_bin, a.go_bin, a.dump_bin, workdir)
    server = HttpServer(a.fixtures)
    server.start()
    results = []
    groups = {}
    t0 = time.time()
    try:
        for c in cases:
            tmp = os.path.join(workdir, "case")
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            try:
                r = run_case(c, T, a.fixtures, tmp, server.port, big_expect)
            except Exception as e:  # noqa: BLE001
                r = {"id": c["id"], "group": c["group"], "ok": False, "reasons": [f"harness error: {e!r}"]}
            results.append(r)
            g = groups.setdefault(c["group"], {"total": 0, "passed": 0, "failed_ids": []})
            g["total"] += 1
            if r["ok"]:
                g["passed"] += 1
            elif len(g["failed_ids"]) < 25:
                g["failed_ids"].append(r["id"] + ": " + "; ".join(r["reasons"])[:300])
    finally:
        server.stop()
    report = {
        "expected_total": manifest["expected_total"],
        "ran": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "elapsed_sec": round(time.time() - t0, 1),
        "groups": groups,
        "all_passed": all(r["ok"] for r in results) and len(results) == manifest["expected_total"] and not a.only,
    }
    with open(a.report, "w") as f:
        json.dump(report, f, indent=1)
    with open(a.report.replace(".json", ".cases.json"), "w") as f:
        json.dump(results, f)
    print(json.dumps({k: v for k, v in report.items() if k != "groups"}))
    for g, v in groups.items():
        print(f"  {g}: {v['passed']}/{v['total']}")
        for fid in v["failed_ids"][:5]:
            print("     -", fid)
    shutil.rmtree(workdir, ignore_errors=True)
    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()

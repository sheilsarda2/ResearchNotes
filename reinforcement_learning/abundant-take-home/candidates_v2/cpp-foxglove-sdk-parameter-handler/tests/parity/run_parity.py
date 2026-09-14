#!/usr/bin/env python3
"""Cross-implementation parity for the parameter-handler feature.

Starts each server implementation (the Rust SDK reference server, the C++ driver, the C driver)
per scenario, drives it with one scripted Foxglove ws-protocol client, records a normalized
transcript per client connection, and requires the candidate transcripts to equal the reference
transcript exactly. The reference transcript is additionally sanity-checked so a broken harness
cannot pass silently.

Usage: run_parity.py --ref BIN --cpp BIN [--c BIN] --out DIR
Exit 0 iff every comparison and every sanity check passed. Summary JSON goes to DIR/summary.json.
"""

import argparse
import asyncio
import json
import os
import select
import subprocess
import sys
import time

import websockets

SUBPROTOCOL = "foxglove.sdk.v1"
RECV_TIMEOUT = 20.0
PORT_TIMEOUT = 60.0


def normalize(msg):
    op = msg.get("op")
    if op == "serverInfo":
        out = {"op": op, "name": msg.get("name"), "capabilities": sorted(msg.get("capabilities", []))}
        if "supportedEncodings" in msg:
            out["supportedEncodings"] = sorted(msg["supportedEncodings"])
        return out
    if op == "parameterValues":
        out = {"op": op, "parameters": msg.get("parameters", [])}
        if "id" in msg:
            out["id"] = msg["id"]
        return out
    if op == "status":
        out = {"op": op, "level": msg.get("level"), "message": msg.get("message")}
        if "id" in msg:
            out["id"] = msg["id"]
        return out
    return msg


class Conn:
    def __init__(self, name, ws):
        self.name = name
        self.ws = ws
        self.log = []

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def recv_op(self, op):
        """Receive until a message with `op` arrives; everything received is recorded."""
        deadline = time.monotonic() + RECV_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.log.append({"event": "timeout", "waiting_for": op})
                return None
            try:
                raw = await asyncio.wait_for(self.ws.recv(), remaining)
            except asyncio.TimeoutError:
                self.log.append({"event": "timeout", "waiting_for": op})
                return None
            except websockets.exceptions.ConnectionClosed as exc:
                self.log.append({"event": "closed", "code": exc.code, "waiting_for": op})
                return None
            if isinstance(raw, bytes):
                msg = {"event": "binary", "len": len(raw)}
            else:
                try:
                    msg = normalize(json.loads(raw))
                except ValueError:
                    msg = {"event": "unparseable", "text": raw[:200]}
            self.log.append(msg)
            if msg.get("op") == op:
                return msg


def connect(port):
    return websockets.connect(f"ws://127.0.0.1:{port}", subprotocols=[SUBPROTOCOL], max_size=None)


async def scenario_handler(port):
    async with connect(port) as ws_a:
        a = Conn("A", ws_a)
        await a.recv_op("serverInfo")
        await a.send({"op": "getParameters", "id": "g1", "parameterNames": ["foo", "bar", "missing", "unset_param"]})
        await a.recv_op("parameterValues")
        await a.send({"op": "getParameters", "id": "g2", "parameterNames": []})
        await a.recv_op("parameterValues")
        await a.send({"op": "getParameters", "parameterNames": ["count"]})
        await a.recv_op("parameterValues")
        await a.send({
            "op": "setParameters",
            "id": "s1",
            "parameters": [
                {"name": "foo", "type": "float64", "value": 2.5},
                {"name": "ro_locked", "value": "hack"},
                {"name": "newdict", "value": {"a": 1.0, "b": "x"}},
                {"name": "newblob", "type": "byte_array", "value": "AQID"},
                {"name": "newint", "value": 42},
                {"name": "newarr", "type": "float64_array", "value": [1.5, 2.5]},
                {"name": "newbool", "value": False},
            ],
        })
        await a.recv_op("parameterValues")
        await a.send({
            "op": "getParameters",
            "id": "g3",
            "parameterNames": ["foo", "ro_locked", "newdict", "newblob", "newint", "newarr", "newbool"],
        })
        await a.recv_op("parameterValues")

        async with connect(port) as ws_b:
            b = Conn("B", ws_b)
            await b.recv_op("serverInfo")
            await b.send({"op": "subscribeParameterUpdates", "parameterNames": ["foo", "bar"]})
            # Same-connection barrier: once this get is answered the subscription is registered.
            await b.send({"op": "getParameters", "id": "b1", "parameterNames": ["foo"]})
            await b.recv_op("parameterValues")

            # No request id: the requester must not receive an echo; subscribers get a broadcast.
            await a.send({
                "op": "setParameters",
                "parameters": [
                    {"name": "foo", "type": "float64", "value": 3.5},
                    {"name": "bar", "value": "BAZ"},
                    {"name": "zzz", "value": True},
                ],
            })
            await b.recv_op("parameterValues")
            await a.send({"op": "getParameters", "id": "g4", "parameterNames": ["foo", "bar", "zzz"]})
            await a.recv_op("parameterValues")

        # Dropped responder: generic error status.
        await a.send({"op": "setParameters", "id": "s2", "parameters": [{"name": "drop", "value": 1}]})
        await a.recv_op("status")
        # Completed from another thread.
        await a.send({"op": "getParameters", "id": "g5", "parameterNames": ["slow"]})
        await a.recv_op("parameterValues")
        # Handler exception (C++) / dropped responder (Rust, C): generic error status.
        await a.send({"op": "getParameters", "id": "g6", "parameterNames": ["throw"]})
        await a.recv_op("status")
        # The connection must still be usable afterwards.
        await a.send({"op": "getParameters", "id": "g7", "parameterNames": ["count"]})
        await a.recv_op("parameterValues")
    return {"A": a.log, "B": b.log}


async def scenario_precedence(port):
    async with connect(port) as ws_a:
        a = Conn("A", ws_a)
        await a.recv_op("serverInfo")
        await a.send({"op": "getParameters", "id": "p1", "parameterNames": ["foo"]})
        await a.recv_op("parameterValues")
        await a.send({"op": "setParameters", "id": "p2", "parameters": [{"name": "foo", "type": "float64", "value": 9.5}]})
        await a.recv_op("parameterValues")
        await a.send({"op": "getParameters", "id": "p3", "parameterNames": ["foo"]})
        await a.recv_op("parameterValues")
    return {"A": a.log}


async def scenario_legacy(port):
    async with connect(port) as ws_a:
        a = Conn("A", ws_a)
        await a.recv_op("serverInfo")
        await a.send({"op": "getParameters", "id": "l1", "parameterNames": ["foo", "bar"]})
        await a.recv_op("parameterValues")
        await a.send({"op": "setParameters", "id": "l2", "parameters": [{"name": "foo", "type": "float64", "value": 2.5}]})
        await a.recv_op("parameterValues")
        async with connect(port) as ws_b:
            b = Conn("B", ws_b)
            await b.recv_op("serverInfo")
            await b.send({"op": "subscribeParameterUpdates", "parameterNames": ["bar"]})
            await b.send({"op": "getParameters", "id": "b1", "parameterNames": ["bar"]})
            await b.recv_op("parameterValues")
            await a.send({"op": "setParameters", "parameters": [{"name": "bar", "value": "Q"}]})
            await b.recv_op("parameterValues")
            await a.send({"op": "getParameters", "id": "l3", "parameterNames": ["bar"]})
            await a.recv_op("parameterValues")
    return {"A": a.log, "B": b.log}


SCENARIOS = {
    "handler": scenario_handler,
    "precedence": scenario_precedence,
    "legacy": scenario_legacy,
}


def names_of(msg):
    return [p.get("name") for p in msg.get("parameters", [])] if msg else None


def reference_sanity(transcripts):
    """Structural expectations on the reference transcripts (harness self-check)."""
    problems = []
    h = transcripts.get("handler")
    if not h:
        problems.append("handler: missing")
    else:
        a = h["A"]
        if not a or a[0].get("op") != "serverInfo" or "parameters" not in a[0].get("capabilities", []):
            problems.append("handler: serverInfo must advertise parameters")
        by_id = {m.get("id"): m for m in a if m.get("op") == "parameterValues" and "id" in m}
        if names_of(by_id.get("g1")) != ["foo", "bar"]:
            problems.append(f"handler: g1 expected [foo, bar], got {names_of(by_id.get('g1'))}")
        if names_of(by_id.get("g2")) != ["arr", "bar", "blob", "count", "flag", "foo", "ints", "ro_locked"]:
            problems.append(f"handler: g2 unexpected {names_of(by_id.get('g2'))}")
        noid = [m for m in a if m.get("op") == "parameterValues" and "id" not in m]
        if len(noid) != 1 or names_of(noid[0]) != ["count"]:
            problems.append("handler: exactly one id-less parameterValues (the get for count) expected on A")
        statuses = [m for m in a if m.get("op") == "status"]
        if len(statuses) != 2 or any(s.get("level") != 2 or "failed to send a response" not in (s.get("message") or "") for s in statuses):
            problems.append(f"handler: expected two error statuses, got {statuses}")
        b = h["B"]
        bcast = [m for m in b if m.get("op") == "parameterValues" and "id" not in m]
        if len(bcast) != 1 or names_of(bcast[0]) != ["foo", "bar"]:
            problems.append(f"handler: B broadcast expected [foo, bar], got {[names_of(m) for m in bcast]}")
        if any(m.get("event") for m in a + b):
            problems.append("handler: timeouts or closes in reference transcript")
    p = transcripts.get("precedence")
    if not p:
        problems.append("precedence: missing")
    else:
        vals = {m.get("id"): m for m in p["A"] if m.get("op") == "parameterValues"}
        p1 = vals.get("p1")
        if not p1 or names_of(p1) != ["foo"] or p1["parameters"][0].get("value") != 1.5:
            problems.append(f"precedence: p1 must come from the handler, got {p1}")
        if any(m.get("event") for m in p["A"]):
            problems.append("precedence: timeouts or closes in reference transcript")
    l = transcripts.get("legacy")
    if not l:
        problems.append("legacy: missing")
    else:
        bcast = [m for m in l["B"] if m.get("op") == "parameterValues" and "id" not in m]
        if len(bcast) != 1 or names_of(bcast[0]) != ["bar"]:
            problems.append(f"legacy: B broadcast expected [bar], got {[names_of(m) for m in bcast]}")
        if any(m.get("event") for m in l["A"] + l["B"]):
            problems.append("legacy: timeouts or closes in reference transcript")
    return problems


class Driver:
    def __init__(self, cmd, scenario, log_path):
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen(
            cmd + ["--scenario", scenario],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            bufsize=1,
        )
        self.port = None
        deadline = time.monotonic() + PORT_TIMEOUT
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.proc.stdout], [], [], 1.0)
            if self.proc.poll() is not None and not ready:
                break
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    break
                if line.startswith("PORT="):
                    self.port = int(line.strip().split("=", 1)[1])
                    break

    def stop(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
        self.log.close()
        return self.proc.returncode


def run_impl(name, cmd, scenarios, out_dir):
    transcripts = {}
    for scenario in scenarios:
        log_path = os.path.join(out_dir, f"{name}-{scenario}.stderr.log")
        drv = Driver(cmd, scenario, log_path)
        if drv.port is None:
            drv.stop()
            transcripts[scenario] = {"error": "driver did not report a port"}
            continue
        try:
            transcripts[scenario] = asyncio.run(SCENARIOS[scenario](drv.port))
        except Exception as exc:  # noqa: BLE001 - recorded, compared, and reported
            transcripts[scenario] = {"error": f"{type(exc).__name__}: {exc}"}
        rc = drv.stop()
        if rc not in (0, None):
            transcripts[scenario] = {"error": f"driver exit code {rc}", "partial": transcripts[scenario]}
    with open(os.path.join(out_dir, f"{name}.transcripts.json"), "w") as fh:
        json.dump(transcripts, fh, indent=1, sort_keys=True)
    return transcripts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--cpp", required=True)
    ap.add_argument("--c")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    all_scenarios = ["handler", "precedence", "legacy"]
    ref = run_impl("reference", [args.ref], all_scenarios, args.out)
    sanity = reference_sanity(ref)

    summary = {"reference_sanity": sanity, "cpp": {}, "c": {}}
    cpp = run_impl("cpp", [args.cpp], all_scenarios, args.out)
    for s in all_scenarios:
        summary["cpp"][s] = cpp.get(s) == ref.get(s)
    if args.c:
        c = run_impl("c", [args.c], ["handler"], args.out)
        summary["c"]["handler"] = c.get("handler") == ref.get("handler")

    ok = not sanity and all(summary["cpp"].values()) and all(summary["c"].values())
    summary["ok"] = ok
    with open(os.path.join(args.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    print(json.dumps(summary, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

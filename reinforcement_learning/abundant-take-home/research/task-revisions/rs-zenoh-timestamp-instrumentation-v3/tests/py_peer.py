#!/usr/bin/env python3
"""Python reference peer for the timestamp-instrumentation parity check.

Same command line and JSON-lines protocol as tools/ts_peer (Rust). Runs on the
zenoh-python bindings (eclipse-zenoh/zenoh-python@f4bbce9, PR #739) compiled
against the upstream gold Rust tree inside the verifier image. Never shipped to
the agent environment.
"""
import argparse
import json
import os
import queue
import sys
import time

import zenoh
from zenoh import InterceptionPoint, TimestampInstrumentationBuilder

# `InterceptionPoint` is a pyo3 class with `__eq__` but no `__hash__` (unhashable), so the
# mapping is a comparison list rather than a dict.
POINT_NAMES = (
    (InterceptionPoint.SEND, "send"),
    (InterceptionPoint.ROUTE, "route"),
    (InterceptionPoint.RECEIVE, "receive"),
)


def point_name(point):
    for candidate, name in POINT_NAMES:
        if point == candidate:
            return name
    raise ValueError(f"unknown interception point {point!r}")


def emit(obj):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def attr(obj, name):
    """Read `name` whether the binding exposes it as a property or a method."""
    value = getattr(obj, name)
    return value() if callable(value) else value


def stack_json(stack):
    if stack is None:
        return None
    instr = attr(stack, "instrumentation")
    records = []
    for r in attr(stack, "records"):
        d = {"point": point_name(attr(r, "point")), "custom": bool(attr(r, "is_custom"))}
        ts = r.timestamp()
        if isinstance(ts, (bytes, bytearray, memoryview)):
            d["bytes"] = bytes(ts).hex()
        else:
            d["ts"] = str(ts)
        records.append(d)
    return {
        "instr": {
            "send": instr.is_instrumented(InterceptionPoint.SEND),
            "route": instr.is_instrumented(InterceptionPoint.ROUTE),
            "receive": instr.is_instrumented(InterceptionPoint.RECEIVE),
        },
        "records": records,
    }


def build_config(a):
    conf = zenoh.Config()
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("mode", json.dumps(a.mode))
    if a.listen:
        conf.insert_json5("listen/endpoints", json.dumps(a.listen))
    if a.connect:
        conf.insert_json5("connect/endpoints", json.dumps(a.connect))
    return conf


def instrumentation(a):
    if not (a.send or a.route or a.receive):
        return None
    return (
        TimestampInstrumentationBuilder()
        .set_send(a.send)
        .set_route(a.route)
        .set_receive(a.receive)
        .build()
    )


def wait_go():
    for line in sys.stdin:
        if line.strip() == "go":
            return
    # stdin closed without "go": proceed anyway.


def timeout_exit():
    emit({"event": "timeout"})
    sys.stdout.flush()
    os._exit(3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--role", required=True)
    p.add_argument("--listen", action="append", default=[])
    p.add_argument("--connect", action="append", default=[])
    p.add_argument("--mode", default="peer")
    p.add_argument("--ke", default="ts/interop")
    p.add_argument("--send", action="store_true")
    p.add_argument("--route", action="store_true")
    p.add_argument("--receive", action="store_true")
    p.add_argument("--callback", default=None)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--payload", default="payload")
    p.add_argument("--timeout", type=float, default=60.0)
    a = p.parse_args()

    conf = build_config(a)
    if a.callback:
        cb_bytes = bytes.fromhex(a.callback)
        session = zenoh.open(conf, timestamp_callback=lambda _ctx: cb_bytes)
    else:
        session = zenoh.open(conf)

    def ready():
        emit({"event": "ready", "zid": str(attr(session, "info").zid()), "locators": []})

    if a.role == "sub":
        q = queue.Queue()
        sub = session.declare_subscriber(a.ke, lambda s: q.put(s))
        ready()
        for _ in range(a.count):
            try:
                s = q.get(timeout=a.timeout)
            except queue.Empty:
                timeout_exit()
            kind = "Put" if attr(s, "kind") == zenoh.SampleKind.PUT else "Delete"
            payload = attr(s, "payload").to_string()
            emit({"event": "sample", "kind": kind, "payload": payload,
                  "stack": stack_json(attr(s, "timestamp_stack"))})
        sub.undeclare()
    elif a.role == "pub":
        publisher = session.declare_publisher(a.ke)
        ready()
        wait_go()
        for i in range(a.count):
            instr = instrumentation(a)
            if a.payload == "DELETE":
                publisher.delete(timestamp_instrumentation=instr)
            else:
                publisher.put(a.payload, timestamp_instrumentation=instr)
            emit({"event": "put", "i": i})
            time.sleep(0.1)
        publisher.undeclare()
    elif a.role == "queryable":
        q = queue.Queue()

        def on_query(query):
            st = stack_json(attr(query, "timestamp_stack"))
            if a.payload == "ERR":
                query.reply_err(b"error payload")
            else:
                query.reply(a.ke, a.payload)
            q.put(st)

        queryable = session.declare_queryable(a.ke, on_query)
        ready()
        for _ in range(a.count):
            try:
                st = q.get(timeout=a.timeout)
            except queue.Empty:
                timeout_exit()
            emit({"event": "query", "stack": st})
        time.sleep(0.3)
        queryable.undeclare()
    elif a.role == "get":
        ready()
        wait_go()
        instr = instrumentation(a)
        replies = session.get(
            a.ke,
            consolidation=zenoh.ConsolidationMode.NONE,
            timestamp_instrumentation=instr,
        )
        for reply in replies:
            ok = attr(reply, "ok")
            if ok is not None:
                emit({"event": "reply", "ok": True, "stack": stack_json(attr(ok, "timestamp_stack"))})
            else:
                err = attr(reply, "err")
                emit({"event": "reply", "ok": False, "stack": stack_json(attr(err, "timestamp_stack"))})
        emit({"event": "replies_done"})
    elif a.role == "router":
        ready()
        for _ in sys.stdin:
            pass
    else:
        raise SystemExit(f"unknown role {a.role}")
    session.close()


if __name__ == "__main__":
    main()

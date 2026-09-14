"""Reference connectivity dump through zenoh-python 1.8.0 (the cross-language oracle).

Runs under the verifier's pinned CPython (3.8 on x86_64, 3.11 on aarch64), so it stays
3.8-compatible. Emits the same JSON document as parity_dump.cxx for the verifier's router.
"""
import json
import os
import sys
import time

import zenoh


def enum_name(v):
    """Lower-case member name of a zenoh enum value. The stub types WhatAmI / Reliability /
    SampleKind as enum.Enum, but the pyo3 runtime objects have no `.name`; fall back to the
    textual form ("WhatAmI.ROUTER", "ROUTER" or "router" all map to "router")."""
    n = getattr(v, "name", None)
    if isinstance(n, str):
        return n.lower()
    for text in (str(v), repr(v)):
        text = text.strip()
        if text.startswith("<"):
            continue
        return text.rsplit(".", 1)[-1].strip().lower()
    return str(v).lower()


def attr_or_call(obj, name):
    v = getattr(obj, name)
    return v() if callable(v) else v


def wait_first(listener, budget_s=10.0):
    deadline = time.time() + budget_s
    while time.time() < deadline:
        ev = listener.try_recv()
        if ev is not None:
            return ev
        time.sleep(0.02)
    return listener.try_recv()


def transport_json(t):
    return {
        "zid": str(t.zid),
        "whatami": enum_name(t.whatami),
        "is_qos": bool(t.is_qos),
        "is_multicast": bool(t.is_multicast),
    }


def link_json(l):
    pr = l.priorities
    rel = l.reliability
    return {
        "zid": str(l.zid),
        "src": str(l.src),
        "dst": str(l.dst),
        "group": None if l.group is None else str(l.group),
        "auth_identifier": None if l.auth_identifier is None else str(l.auth_identifier),
        "mtu": int(l.mtu),
        "is_streamed": bool(l.is_streamed),
        "interfaces": [str(i) for i in l.interfaces],
        "priorities": None if pr is None else [int(pr[0]), int(pr[1])],
        "reliability": None if rel is None else enum_name(rel),
    }


def main():
    locator = os.environ.get("ZENOH_TEST_ROUTER") or "tcp/127.0.0.1:27447"
    conf = zenoh.Config()
    conf.insert_json5("mode", json.dumps("client"))
    conf.insert_json5("connect/endpoints", json.dumps([locator]))
    conf.insert_json5("scouting/multicast/enabled", "false")
    conf.insert_json5("scouting/gossip/enabled", "false")
    session = zenoh.open(conf)
    # The 1.8.0 stub declares `Session.info()` (and SessionInfo.zid/transports/links) as methods,
    # while the pyo3 runtime exposes `info` as a getter; accept either shape.
    info = attr_or_call(session, "info")

    transports = []
    deadline = time.time() + 10.0
    while time.time() < deadline:
        transports = list(attr_or_call(info, "transports"))
        if transports:
            break
        time.sleep(0.02)
    links = list(attr_or_call(info, "links"))

    out = {
        "zid": str(attr_or_call(info, "zid")),
        "transports": [transport_json(t) for t in transports],
        "links": [link_json(l) for l in links],
    }

    ll = info.declare_link_events_listener(history=True)
    ev = wait_first(ll)
    out["link_event"] = None if ev is None else {"kind": enum_name(ev.kind), "link": link_json(ev.link)}
    ll.undeclare()

    tl = info.declare_transport_events_listener(history=True)
    ev = wait_first(tl)
    out["transport_event"] = (
        None if ev is None else {"kind": enum_name(ev.kind), "transport": transport_json(ev.transport)}
    )
    tl.undeclare()

    session.close()
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

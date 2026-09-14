"""Field-by-field comparison of the C++ (submission) and Python (reference) connectivity dumps.

Both probes are clients of the same zenohd, so each must see exactly one transport (the
router) and one TCP link to it. Documented tolerances: the link's local endpoint (`src`)
carries an ephemeral port and is compared on its `tcp/127.0.0.1:` prefix only; `is_shm`
exists only on the C++ side (zenoh-python 1.8.0 does not expose it) and is ignored.
Writes a JSON report to stdout; exit code 0 iff every check passed.
"""
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    cpp_path, py_path = sys.argv[1], sys.argv[2]
    locator = os.environ.get("ZENOH_TEST_ROUTER") or "tcp/127.0.0.1:27447"
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        cpp = load(cpp_path)
    except Exception as e:  # noqa: BLE001
        cpp = None
        check("cpp_dump_parses", False, repr(e))
    try:
        py = load(py_path)
    except Exception as e:  # noqa: BLE001
        py = None
        check("py_dump_parses", False, repr(e))

    if cpp is not None and py is not None:
        check("cpp_dump_parses", True)
        check("py_dump_parses", True)
        check("cpp_own_zid_differs_from_py", str(cpp.get("zid", "")).lower() != str(py.get("zid", "")).lower())

        ct, pt = cpp.get("transports") or [], py.get("transports") or []
        check("transport_count_1", len(ct) == 1 and len(pt) == 1, "cpp=%d py=%d" % (len(ct), len(pt)))
        cl, pl = cpp.get("links") or [], py.get("links") or []
        check("link_count_1", len(cl) == 1 and len(pl) == 1, "cpp=%d py=%d" % (len(cl), len(pl)))

        def cmp_transport(name, a, b):
            if not a or not b:
                check(name, False, "missing")
                return
            check(name + ".zid", str(a.get("zid", "")).lower() == str(b.get("zid", "")).lower(), "%s vs %s" % (a.get("zid"), b.get("zid")))
            check(name + ".whatami_router", a.get("whatami") == "router" and b.get("whatami") == "router", "%s vs %s" % (a.get("whatami"), b.get("whatami")))
            for f in ("is_qos", "is_multicast"):
                check(name + "." + f, a.get(f) == b.get(f), "%r vs %r" % (a.get(f), b.get(f)))

        def cmp_link(name, a, b, tzid):
            if not a or not b:
                check(name, False, "missing")
                return
            check(name + ".zid", str(a.get("zid", "")).lower() == str(b.get("zid", "")).lower() == str(tzid).lower(), "%s vs %s (transport %s)" % (a.get("zid"), b.get("zid"), tzid))
            check(name + ".dst_is_router", a.get("dst") == locator and b.get("dst") == locator, "%s vs %s" % (a.get("dst"), b.get("dst")))
            check(name + ".src_prefix", str(a.get("src", "")).startswith("tcp/127.0.0.1:") and str(b.get("src", "")).startswith("tcp/127.0.0.1:"), "%s vs %s" % (a.get("src"), b.get("src")))
            check(name + ".group_none", a.get("group") is None and b.get("group") is None, "%r vs %r" % (a.get("group"), b.get("group")))
            check(name + ".auth_identifier_none", a.get("auth_identifier") is None and b.get("auth_identifier") is None, "%r vs %r" % (a.get("auth_identifier"), b.get("auth_identifier")))
            for f in ("mtu", "is_streamed", "interfaces", "priorities", "reliability"):
                check(name + "." + f, a.get(f) == b.get(f), "%r vs %r" % (a.get(f), b.get(f)))
            check(name + ".is_streamed_true", a.get("is_streamed") is True)

        tzid = ct[0].get("zid") if ct else None
        cmp_transport("transport0", ct[0] if ct else None, pt[0] if pt else None)
        cmp_link("link0", cl[0] if cl else None, pl[0] if pl else None, tzid)

        check("cpp_links_filtered_equals_links", cpp.get("links_filtered_count") == len(cl), "%r vs %d" % (cpp.get("links_filtered_count"), len(cl)))

        ce, pe = cpp.get("link_event"), py.get("link_event")
        check("link_event_present", bool(ce) and bool(pe))
        if ce and pe:
            check("link_event.kind_put", ce.get("kind") == "put" and pe.get("kind") == "put", "%s vs %s" % (ce.get("kind"), pe.get("kind")))
            cmp_link("link_event.link", ce.get("link"), pe.get("link"), tzid)
        ce, pe = cpp.get("transport_event"), py.get("transport_event")
        check("transport_event_present", bool(ce) and bool(pe))
        if ce and pe:
            check("transport_event.kind_put", ce.get("kind") == "put" and pe.get("kind") == "put", "%s vs %s" % (ce.get("kind"), pe.get("kind")))
            cmp_transport("transport_event.transport", ce.get("transport"), pe.get("transport"))

        check("cpp_link_event_filtered_by_transport", (cpp.get("link_event_filtered_count") or 0) >= 1, repr(cpp.get("link_event_filtered_count")))
        check("cpp_background_listener_fired", (cpp.get("background_events") or 0) >= 1, repr(cpp.get("background_events")))

    ok = all(c["ok"] for c in checks) and len(checks) > 0
    json.dump({"ok": ok, "checks": checks}, sys.stdout, indent=1)
    sys.stdout.write("\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Scoring helper for the cpp-rosbag2-mixed-serialization-playback verifier.

Keeps /logs/verifier/score.json (per-group attribution) and writes reward.txt on `finalize`.

  score.py record <group> <0|1> <detail>
  score.py gtests <expected_counts.json> <xml_root>
  score.py differential <sqlite3.json> <mcap.json>
  score.py cli <cli_play.json>
  score.py finalize
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

LOG = os.environ.get("VERIFIER_LOG_DIR", "/logs/verifier")
SCORE_PATH = os.path.join(LOG, "score.json")
REQUIRED_GROUPS = ["anticheat", "overlay", "build", "gtests", "differential", "cli_play"]

PROTO = "/camera/video_compressed"
CHATTER = "/chatter"
N = 5


def load():
    if os.path.exists(SCORE_PATH):
        with open(SCORE_PATH) as f:
            return json.load(f)
    return {"groups": {}, "reward": 0}


def save(state):
    os.makedirs(LOG, exist_ok=True)
    with open(SCORE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def record(group, ok, detail, extra=None):
    state = load()
    entry = {"ok": bool(ok), "detail": detail}
    if extra is not None:
        entry["results"] = extra
    state["groups"][group] = entry
    save(state)
    print(f"[score] {group}: {'PASS' if ok else 'FAIL'} - {detail}")


# ----------------------------------------------------------------------------- gtests
def parse_gtest_xml(path):
    root = ET.parse(path).getroot()
    total = run = failed = skipped = 0
    for case in root.iter("testcase"):
        total += 1
        status = case.get("status", "run")
        result = case.get("result", "completed")
        has_failure = any(child.tag in ("failure", "error") for child in case)
        is_skipped = result == "skipped" or any(child.tag == "skipped" for child in case)
        if status != "run" or is_skipped:
            skipped += 1
            continue
        run += 1
        if has_failure:
            failed += 1
    return {"total": total, "run": run, "failed": failed, "skipped": skipped}


def gtests(expected_path, xml_root):
    with open(expected_path) as f:
        expected = json.load(f)
    results = {}
    all_ok = True
    for pkg, tests in expected["counts"].items():
        for name, want in tests.items():
            key = f"{pkg}/{name}"
            candidates = sorted(glob.glob(os.path.join(xml_root, pkg, f"{name}*.xml")))
            if not candidates:
                results[key] = {"ok": False, "reason": "no xml (executable missing, crashed or timed out)", "expected": want}
                all_ok = False
                continue
            try:
                got = parse_gtest_xml(candidates[0])
            except ET.ParseError as exc:
                results[key] = {"ok": False, "reason": f"unparsable xml: {exc}", "expected": want}
                all_ok = False
                continue
            ok = got["total"] == want and got["run"] == want and got["failed"] == 0 and got["skipped"] == 0
            results[key] = {"ok": ok, "expected": want, **got}
            all_ok = all_ok and ok
    n_ok = sum(1 for r in results.values() if r["ok"])
    record("gtests", all_ok, f"{n_ok}/{len(results)} suites match expected case counts with no failures or skips", results)


# ----------------------------------------------------------------------------- differential
def _msgs(entry):
    return [(m["topic"], m["recv"], m["format"], m["data_hex"]) for m in entry.get("messages", [])]


def differential(sqlite_path, mcap_path):
    checks = {}

    def check(name, cond, detail=""):
        checks[name] = {"ok": bool(cond), "detail": detail}

    try:
        with open(sqlite_path) as f:
            a = json.load(f)
        with open(mcap_path) as f:
            b = json.load(f)
    except Exception as exc:  # noqa: BLE001
        record("differential", False, f"harness output missing or invalid: {exc}")
        return

    rmw = a.get("rmw_format")
    check("rmw_format_agrees", rmw and rmw == b.get("rmw_format"), f"{rmw} / {b.get('rmw_format')}")

    # Scenario-by-scenario equality between the two storage plugins (message sequences carry topic,
    # recv timestamp, serialization format and payload bytes).
    for scen in ["raw", "requested_rmw", "requested_unknown", "uniform_raw", "uniform_requested_rmw",
                 "only_proto_raw", "only_proto_requested_rmw", "message_compressed", "file_compressed",
                 "message_compressed_requested_rmw_filtered"]:
        sa, sb = a.get(scen), b.get(scen)
        check(f"{scen}.both_present", sa is not None and sb is not None)
        if sa is None or sb is None:
            continue
        check(f"{scen}.equal_between_plugins", sa == sb,
              "" if sa == sb else "sqlite3 and mcap dumps differ")

    # Contract expectations, evaluated on both dumps.
    for label, d in (("sqlite3", a), ("mcap", b)):
        raw = d.get("raw", {})
        check(f"{label}.raw.opens", raw.get("open_ok") is True)
        check(f"{label}.raw.topic_formats", raw.get("topics") == {CHATTER: rmw, PROTO: "protobuf"}, str(raw.get("topics")))
        check(f"{label}.raw.no_undeliverable", raw.get("undeliverable") == [])
        msgs = _msgs(raw)
        check(f"{label}.raw.count", len(msgs) == 2 * N, f"{len(msgs)} messages")
        check(f"{label}.raw.every_message_tagged",
              all((t == CHATTER and fmt == rmw) or (t == PROTO and fmt == "protobuf") for t, _, fmt, _ in msgs))
        check(f"{label}.raw.recv_order", [m[1] for m in msgs] == sorted(m[1] for m in msgs))

        req = d.get("requested_rmw", {})
        check(f"{label}.requested_rmw.opens", req.get("open_ok") is True)
        check(f"{label}.requested_rmw.undeliverable", req.get("undeliverable") == [PROTO], str(req.get("undeliverable")))
        first = req.get("first_message", {})
        check(f"{label}.requested_rmw.first_is_chatter_in_rmw_format",
              first.get("topic") == CHATTER and first.get("format") == rmw)
        check(f"{label}.requested_rmw.second_read_throws", req.get("second_read_throws") is True)
        filt = _msgs({"messages": req.get("filtered_messages", [])})
        check(f"{label}.requested_rmw.filtered_count", len(filt) == N, f"{len(filt)}")
        check(f"{label}.requested_rmw.filtered_all_chatter_rmw", all(t == CHATTER and fmt == rmw for t, _, fmt, _ in filt))
        raw_chatter = [m for m in msgs if m[0] == CHATTER]
        check(f"{label}.requested_rmw.filtered_bytes_equal_raw", [m[3] for m in filt] == [m[3] for m in raw_chatter])

        check(f"{label}.requested_unknown.open_throws", d.get("requested_unknown", {}).get("open_throws") is True)

        uni = d.get("uniform_requested_rmw", {})
        check(f"{label}.uniform_requested_rmw.opens", uni.get("open_ok") is True)
        check(f"{label}.uniform_requested_rmw.no_undeliverable", uni.get("undeliverable") == [])
        umsgs = _msgs(uni)
        check(f"{label}.uniform_requested_rmw.count_and_format", len(umsgs) == N and all(fmt == rmw for _, _, fmt, _ in umsgs))
        check(f"{label}.uniform_raw.count_and_format",
              len(_msgs(d.get("uniform_raw", {}))) == N and all(fmt == rmw for _, _, fmt, _ in _msgs(d.get("uniform_raw", {}))))

        op = d.get("only_proto_raw", {})
        opm = _msgs(op)
        check(f"{label}.only_proto_raw.count_and_format", len(opm) == N and all(fmt == "protobuf" for _, _, fmt, _ in opm))
        check(f"{label}.only_proto_raw.no_undeliverable", op.get("undeliverable") == [])
        check(f"{label}.only_proto_requested_rmw.open_throws", d.get("only_proto_requested_rmw", {}).get("open_throws") is True)

        for comp in ("message_compressed", "file_compressed"):
            c = d.get(comp, {})
            check(f"{label}.{comp}.opens", c.get("open_ok") is True)
            cm = _msgs(c)
            check(f"{label}.{comp}.matches_raw", cm == msgs, f"{len(cm)} messages")
            check(f"{label}.{comp}.no_undeliverable", c.get("undeliverable") == [])
        mc = d.get("message_compressed_requested_rmw_filtered", {})
        mcm = _msgs(mc)
        check(f"{label}.message_compressed_requested_rmw_filtered.undeliverable", mc.get("undeliverable") == [PROTO])
        check(f"{label}.message_compressed_requested_rmw_filtered.messages", mcm == raw_chatter)

    ok = all(c["ok"] for c in checks.values())
    n_ok = sum(1 for c in checks.values() if c["ok"])
    record("differential", ok, f"{n_ok}/{len(checks)} differential and contract checks passed", checks)


# ----------------------------------------------------------------------------- cli
def cli(path):
    checks = {}

    def check(name, cond, detail=""):
        checks[name] = {"ok": bool(cond), "detail": detail}

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        record("cli_play", False, f"cli output missing or invalid: {exc}")
        return

    expected_values = list(range(N))
    spec = {
        # case: (rc must be zero, must mention the protobuf topic (None: not checked), values that
        #        must be received)
        "requested_undecodable": (False, True, None),
        "default_play": (True, True, expected_values),
        "explicit_exclude": (True, False, expected_values),
        "select_playable": (True, False, expected_values),
        "nothing_playable": (False, True, None),
        # Uniform protobuf bag: open() fails on the missing protobuf converter (C4) before the
        # Player's topic resolution runs, so the error names the format, not the topic.
        "only_undecodable_bag": (False, None, None),
        "uniform_baseline": (True, False, expected_values),
    }
    for storage in ("sqlite3", "mcap"):
        cases = data.get(storage, {})
        for case, (rc_zero, mentions, values) in spec.items():
            r = cases.get(case)
            if r is None:
                check(f"{storage}.{case}.present", False, "case missing")
                continue
            rc = r.get("rc")
            check(f"{storage}.{case}.exit_code", (rc == 0) if rc_zero else (rc is not None and rc != 0), f"rc={rc}")
            if mentions is not None:
                check(f"{storage}.{case}.mentions_undecodable_topic", bool(r.get("mentions_proto")) == mentions,
                      f"mentions={r.get('mentions_proto')}")
            if values is not None:
                check(f"{storage}.{case}.received_values", r.get("values") == values, f"values={r.get('values')}")
            else:
                check(f"{storage}.{case}.nothing_received", r.get("values") in ([], None), f"values={r.get('values')}")
    # The two storage plugins must produce the same observable behaviour case by case.
    for case in spec:
        a = data.get("sqlite3", {}).get(case, {})
        b = data.get("mcap", {}).get(case, {})
        same = (
            (a.get("rc") == 0) == (b.get("rc") == 0)
            and bool(a.get("mentions_proto")) == bool(b.get("mentions_proto"))
            and a.get("values") == b.get("values")
        )
        check(f"both_plugins_agree.{case}", same)

    ok = all(c["ok"] for c in checks.values())
    n_ok = sum(1 for c in checks.values() if c["ok"])
    record("cli_play", ok, f"{n_ok}/{len(checks)} CLI playback checks passed", checks)


# ----------------------------------------------------------------------------- finalize
def finalize():
    state = load()
    groups = state["groups"]
    missing = [g for g in REQUIRED_GROUPS if g not in groups]
    for g in missing:
        groups[g] = {"ok": False, "detail": "group did not run"}
    reward = 1 if all(groups[g]["ok"] for g in REQUIRED_GROUPS) else 0
    state["reward"] = reward
    state["required_groups"] = REQUIRED_GROUPS
    save(state)
    with open(os.path.join(LOG, "reward.txt"), "w") as f:
        f.write(f"{reward}\n")
    summary = ", ".join(f"{g}={'ok' if groups[g]['ok'] else 'FAIL'}" for g in REQUIRED_GROUPS)
    print(f"[score] reward={reward} ({summary})")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "record":
        record(argv[2], argv[3] == "1", argv[4] if len(argv) > 4 else "")
    elif cmd == "gtests":
        gtests(argv[2], argv[3])
    elif cmd == "differential":
        differential(argv[2], argv[3])
    elif cmd == "cli":
        cli(argv[2])
    elif cmd == "finalize":
        finalize()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

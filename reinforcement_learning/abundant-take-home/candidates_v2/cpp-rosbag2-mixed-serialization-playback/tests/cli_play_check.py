#!/usr/bin/env python3
"""`ros2 bag play` behaviour on mixed-serialization-format bags, for both storage plugins.

    cli_play_check.py --bags <dir with sqlite3/ and mcap/ subdirs> --out <json>

For every storage plugin and case, runs `ros2 bag play` (from the rebuilt overlay) with an rclpy
subscriber on /chatter already up, records the exit code, whether the output mentions the
protobuf topic, and the int32 values actually received. Waiting is bounded: the player process
has a hard timeout, and after it exits the subscriber gets at most 10 s to drain. Ordering is
barrier-based, not timing-decided: the subscriber exists before the player starts, the player
waits 2 s after creating its publishers (--delay) before publishing, publishes with the recorded
reliable + transient-local QoS, and waits for acknowledgements (--wait-for-all-acked).

Cases per storage (bag names are produced by the differential harness):
  requested_undecodable   mixed, --topics /camera/video_compressed      expect failure naming the topic
  default_play            mixed, no filters                            expect success, warning naming the topic, 5 msgs
  explicit_exclude        mixed, --exclude-topics /camera/video_compressed  expect success, no mention, 5 msgs
  select_playable         mixed, --topics /chatter                     expect success, no mention, 5 msgs
  nothing_playable        mixed, --exclude-topics /chatter             expect failure naming the topic: the
                          protobuf topic is the only implicitly selected one, its automatic exclusion
                          leaves nothing to play (contract C6, third bullet)
  only_undecodable_bag    only_proto, no filters                       expect failure, nothing received:
                          a uniform protobuf bag opened in the rmw format needs a protobuf converter,
                          which does not exist here, so the reader's open() throws (contract C4);
                          that error names the format, not the topic, so no mention is required
  uniform_baseline        uniform, no filters                          expect success, 5 msgs
"""
import argparse
import json
import subprocess
import threading
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from test_msgs.msg import BasicTypes

PROTO = "/camera/video_compressed"
CHATTER = "/chatter"
EXPECTED = 5
# --delay 2: the player sleeps 2 s after creating its publishers and before the first publish, so the
# already-running subscriber is matched (loopback unicast discovery, no multicast in the offline
# container) before the messages go out. --wait-for-all-acked only waits for readers matched at that
# point, so without the barrier a player could publish and exit before the subscriber is discovered.
COMMON = ["--disable-keyboard-controls", "--progress-bar-update-rate", "0", "--delay", "2", "--wait-for-all-acked", "5000"]
PLAY_TIMEOUT = 180.0
DRAIN_TIMEOUT = 10.0


class Collector(Node):
    def __init__(self, name):
        super().__init__(name)
        self.values = []
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=50,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.sub = self.create_subscription(BasicTypes, CHATTER, self._on_msg, qos)

    def _on_msg(self, msg):
        self.values.append(int(msg.int32_value))


def run_case(bag, extra, index):
    node = Collector(f"cli_play_check_{index}")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            executor.spin_once(timeout_sec=0.05)

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    # Give the subscription a moment to be created before the player process starts.
    time.sleep(0.5)

    cmd = ["ros2", "bag", "play", bag, *COMMON, *extra]
    rc = None
    out = ""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=PLAY_TIMEOUT)
        rc, out = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")) + \
              ((exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")) + \
              "\n[cli_play_check] player timed out"

    deadline = time.monotonic() + DRAIN_TIMEOUT
    while len(node.values) < EXPECTED and time.monotonic() < deadline:
        time.sleep(0.1)
    stop.set()
    thread.join(timeout=5)
    executor.remove_node(node)
    node.destroy_node()

    return {
        "cmd": cmd,
        "rc": rc,
        "mentions_proto": PROTO in out,
        "values": list(node.values),
        "output_tail": out[-3000:],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bags", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rclpy.init()
    results = {}
    index = 0
    try:
        for storage in ("sqlite3", "mcap"):
            root = f"{args.bags}/{storage}"
            cases = {
                "requested_undecodable": (f"{root}/mixed", ["--topics", PROTO]),
                "default_play": (f"{root}/mixed", []),
                "explicit_exclude": (f"{root}/mixed", ["--exclude-topics", PROTO]),
                "select_playable": (f"{root}/mixed", ["--topics", CHATTER]),
                "nothing_playable": (f"{root}/mixed", ["--exclude-topics", CHATTER]),
                "only_undecodable_bag": (f"{root}/only_proto", []),
                "uniform_baseline": (f"{root}/uniform", []),
            }
            results[storage] = {}
            for name, (bag, extra) in cases.items():
                index += 1
                print(f"[cli_play_check] {storage}/{name}: ros2 bag play {bag} {' '.join(extra)}", flush=True)
                r = run_case(bag, extra, index)
                print(f"[cli_play_check]   rc={r['rc']} mentions_proto={r['mentions_proto']} values={r['values']}", flush=True)
                results[storage][name] = r
    finally:
        rclpy.shutdown()
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()

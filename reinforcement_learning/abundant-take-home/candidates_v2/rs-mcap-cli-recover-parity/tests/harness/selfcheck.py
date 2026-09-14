#!/usr/bin/env python3
"""Image-build self-check of the reference tooling (fails the verifier image build on error).

1. Every manifest case input exists.
2. For every pristine conformance corpus file, `mcap-go recover -a` + the Go conformance
   reader reproduce exactly the record stream listed in the corpus' expected JSON. This
   validates the oracle chain (Go CLI, Go reader, normalizer) independently of any Rust code.
3. `mcap-go doctor` accepts the Go recover outputs (validates the doctor tool itself).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_differential import normalize  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--go-bin", required=True)
    ap.add_argument("--dump-bin", required=True)
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(a.fixtures, "manifest.json")))
    missing = [c["id"] for c in manifest["cases"] if not os.path.exists(os.path.join(a.fixtures, c["input"]))]
    if missing:
        sys.exit(f"missing case inputs: {missing[:10]} (+{max(0, len(missing) - 10)})")
    print(f"manifest ok: {manifest['expected_total']} cases, groups={manifest['groups']}")

    corpus = os.path.join(a.repo, "tests/conformance/data")
    tmp = tempfile.mkdtemp()
    checked = 0
    doctored = 0
    for g in sorted(os.listdir(corpus)):
        for name in sorted(os.listdir(os.path.join(corpus, g))):
            if not name.endswith(".mcap"):
                continue
            src = os.path.join(corpus, g, name)
            out = os.path.join(tmp, "go.mcap")
            p = subprocess.run([a.go_bin, "recover", "-a", src, "-o", out], capture_output=True)
            if p.returncode != 0:
                sys.exit(f"go recover failed on {src}: {p.stderr.decode(errors='replace')}")
            d = subprocess.run([a.dump_bin, out, "streamed"], capture_output=True)
            if d.returncode != 0:
                sys.exit(f"dump failed on go output for {src}: {d.stdout.decode(errors='replace')}")
            got = normalize(json.loads(d.stdout))
            exp = normalize(json.load(open(src[:-5] + ".json")))
            for k in ("schemas", "channels", "messages", "attachments", "metadata"):
                if got[k] != exp[k]:
                    sys.exit(f"oracle mismatch on {src} [{k}]: got {got[k]!r}\nexpected {exp[k]!r}")
            # The Go writer always stamps its own `library` string on the output header (it is
            # not a recovery reference for that field; the differential runner checks the Rust
            # header against the *input*), so only the profile is compared here.
            if got["header"] is None or got["header"][0] != exp["header"][0]:
                sys.exit(f"oracle header profile mismatch on {src}: {got['header']} vs {exp['header']}")
            checked += 1
            if checked % 25 == 0:
                doc = subprocess.run([a.go_bin, "doctor", out], capture_output=True)
                if doc.returncode != 0:
                    sys.exit(f"doctor rejected go recover output for {src}: {(doc.stdout + doc.stderr).decode(errors='replace')[-500:]}")
                doctored += 1
    print(f"oracle self-check ok: {checked} corpus files matched expected JSON, {doctored} doctor runs ok")
    # a corrupted-CRC file must NOT pass doctor (proves doctor validates CRCs)
    bad = [c for c in manifest["cases"] if c["group"] == "diff_bad_crc" and os.path.exists(os.path.join(a.fixtures, c["input"]))]
    if bad:
        doc = subprocess.run([a.go_bin, "doctor", os.path.join(a.fixtures, bad[0]["input"])], capture_output=True)
        if doc.returncode == 0:
            sys.exit("doctor accepted a chunk with a corrupted CRC; the validity check would be toothless")
        print("doctor rejects corrupted CRC: ok")


if __name__ == "__main__":
    main()

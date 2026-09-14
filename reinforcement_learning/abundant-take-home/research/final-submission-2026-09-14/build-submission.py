"""Add the user's final PDF to the previously verified submission contents."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PACKAGING = ROOT / "research/takehome-presentation-2026-09-14/packaging"
BASE_ZIP = PACKAGING / "review-pack-99-002.zip"
BASE_PLAN = PACKAGING / "final-plan-99-002.json"
BASE_RECEIPT = PACKAGING / "review-pack-99-002.verification.json"
PDF = ROOT / "Take home challenge results - Sheil Sarda 091426.pdf"
DESTINATION = OUT / "abundant-take-home-sheil-sarda-20260914.zip"
PARTIAL = DESTINATION.with_suffix(".zip.partial")
RECEIPT = OUT / "submission-verification.json"
EXPECTED_BASE_SHA = "75004f604d72ee81e866b4d4e99e915e3ff1b9b393eeeb5bb87287448b4296cd"
EXPECTED_PDF_SHA = "ded09d029c504a762ecfff347ced3fd884f7cdda0b0068518838b61298269506"
EXPECTED_SAMPLES = {
    "rs-rerun-chunk-optimizer",
    "rs-zenoh-timestamp-instrumentation-v4-validation",
    "rs-burn-store-pytorch-reader-v4",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stream_sha(stream):
    digest = hashlib.sha256()
    count = 0
    for chunk in iter(lambda: stream.read(1048576), b""):
        count += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), count


def file_ref(path):
    require(path.is_file() and not path.is_symlink(), "Expected a regular source file")
    with path.open("rb") as source:
        digest, size = stream_sha(source)
    return {"path": str(path.relative_to(ROOT)), "sha256": digest, "bytes": size}


def preserved_originals():
    repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    prefix = ROOT.relative_to(repo).as_posix()
    files = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "c1ae968", "--", prefix], text=True).splitlines()
    require(len(files) == 36, "Original file inventory changed")
    for name in files:
        require((repo / name).read_bytes() == subprocess.check_output(["git", "-C", str(repo), "show", "c1ae968:" + name]), "Original supplied file changed: " + name)
    return {"commit": "c1ae968", "files": len(files), "unchanged": True}


require(not any(path.exists() for path in (DESTINATION, PARTIAL, RECEIPT)), "Use a new output name instead of replacing a completed or partial package")
before = preserved_originals()
base, report = file_ref(BASE_ZIP), file_ref(PDF)
require(base["sha256"] == EXPECTED_BASE_SHA, "Reviewed base ZIP changed")
require(report["sha256"] == EXPECTED_PDF_SHA, "User-finalized PDF changed")
plan = json.loads(BASE_PLAN.read_text())
prior = json.loads(BASE_RECEIPT.read_text())
canonical_plan = json.dumps({k: v for k, v in plan.items() if k != "plan_sha256"}, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
require(hashlib.sha256(canonical_plan).hexdigest() == plan["plan_sha256"] == prior["plan_sha256"], "Frozen plan digest mismatch")
require(prior["zip"]["sha256"] == base["sha256"] and prior["zip_bytes_verified"] and prior["copied_bytes_verified"], "Base ZIP has no matching completed verification")
expected = {}
for entry in plan["entries"]:
    require(entry["kind"] != "supplied_report", "Base package must have no earlier report")
    for name, details in entry["tree"]["files"].items():
        destination = str(PurePosixPath(entry["destination"]) / name) if entry["tree"]["is_directory"] else entry["destination"]
        require(destination not in expected, "Duplicate destination in frozen plan")
        expected[destination] = details
require(len(expected) == 5580, "Unexpected base file inventory")
report_name = "report/" + PDF.name
with zipfile.ZipFile(BASE_ZIP) as archive:
    original_names = archive.namelist()
    require(len(original_names) == len(set(original_names)), "Duplicate base ZIP members")
    require({name for name in original_names if not name.endswith("/")} == set(expected), "Base ZIP and frozen plan inventory differ")
    require(not any(name.startswith("report/") and not name.endswith("/") for name in original_names), "Base ZIP already contains a report")
    require({name.split("/")[0] for name in original_names} == {"samples", "jobs", "archive", "report"}, "Unexpected ZIP roots")
    require(not any(name.lower().endswith((".ppt", ".pptx", ".pptm")) for name in original_names), "PowerPoint file present in base ZIP")

print(json.dumps({"stage": "source_checks_passed", "base_files": len(expected), "report": report}), flush=True)
# Copy the reviewed container, then append one file. Existing compressed payloads
# and their metadata stay intact; the ZIP central directory is rewritten.
shutil.copyfile(BASE_ZIP, PARTIAL)
with zipfile.ZipFile(PARTIAL, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as archive:
    archive.write(PDF, report_name)

expected[report_name] = report
verified_bytes = 0
with zipfile.ZipFile(PARTIAL) as archive:
    names = archive.namelist()
    require(len(names) == len(set(names)), "Duplicate final ZIP members")
    require(names == original_names + [report_name], "Unexpected member addition or reorder")
    require({name for name in names if not name.endswith("/")} == set(expected), "Final ZIP inventory differs")
    require(not any(name.lower().endswith((".ppt", ".pptx", ".pptm")) for name in names), "PowerPoint file present in final ZIP")
    for name, details in expected.items():
        with archive.open(name) as source:
            digest, size = stream_sha(source)
        require(digest == details["sha256"] and size == details["bytes"], "Packaged bytes differ: " + name)
        verified_bytes += size
    samples = {name.split("/")[1] for name in expected if name.startswith("samples/")}
    require(samples == EXPECTED_SAMPLES, "Unexpected submitted samples")
    discarded = {name.split("/")[1] for name in expected if name.startswith("archive/")}
    require(len(discarded) == 34, "Unexpected discarded-task inventory")
    results = sorted(name for name in expected if name.startswith("jobs/") and len(PurePosixPath(name).parts) == 4 and name.endswith("/result.json"))
    require(len(results) == 27, "Expected 27 retained-task trial results")
    for result in results:
        trial = str(PurePosixPath(result).parent)
        require(trial + "/agent/trajectory.json" in expected, "Missing captured trajectory")
        require(any(name.startswith(trial + "/verifier/") for name in expected), "Missing raw verifier output")

require(file_ref(PDF) == report, "Final PDF changed during assembly")
require(file_ref(BASE_ZIP) == base, "Reviewed ZIP changed during assembly")
after = preserved_originals()
os.replace(PARTIAL, DESTINATION)
receipt = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "state": "submission_zip_complete",
    "zip": file_ref(DESTINATION),
    "base_zip": base,
    "base_plan": file_ref(BASE_PLAN),
    "base_verification": file_ref(BASE_RECEIPT),
    "report": {**report, "zip_path": report_name, "user_designated_final": True, "byte_identical": True, "reexported": False},
    "roots": ["samples", "jobs", "archive", "report"],
    "samples": sorted(samples), "raw_trial_count": len(results),
    "archived_task_directories": len(discarded), "file_count": len(expected),
    "verified_uncompressed_bytes": verified_bytes,
    "all_frozen_sample_job_archive_bytes_preserved": True,
    "all_member_hashes_and_sizes_verified": True,
    "powerpoint_files": [], "extra_report_files": [],
    "originals_before": before, "originals_after": after,
    "uploaded": False,
}
RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt), flush=True)

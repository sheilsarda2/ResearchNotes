"""Check the revised deck against frozen data and the delivered source deck."""
import collections
import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import xml.etree.ElementTree as ET
import zipfile

BASE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
CWD = BASE.parents[1]
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            value.update(chunk)
    return value.hexdigest()


def tables(archive, slide):
    root = ET.fromstring(archive.read(f"ppt/slides/slide{slide}.xml"))
    return [
        [["".join(node.text or "" for node in cell.findall(".//a:t", NS))
          for cell in row.findall("a:tc", NS)] for row in table.findall("a:tr", NS)]
        for table in root.findall(".//a:tbl", NS)
    ]


def chart(archive):
    names = [name for name in archive.namelist()
             if re.search(r"/charts/chart\d+\.xml$", name)]
    assert len(names) == 1
    root = ET.fromstring(archive.read(names[0]))
    return list(zip(
        [v.text for v in root.findall(".//c:cat//c:pt/c:v", NS)],
        [float(v.text) for v in root.findall(".//c:val//c:pt/c:v", NS)],
    ))


def workbook(archive):
    names = [name for name in archive.namelist() if name.endswith(".xlsx")]
    assert len(names) == 1
    with zipfile.ZipFile(io.BytesIO(archive.read(names[0]))) as book:
        root = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        rows = root.findall("s:sheetData/s:row", NS)[1:]
        return [(row.find("s:c/s:is/s:t", NS).text,
                 float(row.findall("s:c", NS)[1].find("s:v", NS).text)) for row in rows]


def all_text(archive):
    return "\n".join(" ".join(node.text or "" for node in ET.fromstring(archive.read(name)).findall(".//a:t", NS))
                     for name in archive.namelist()
                     if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", name))


baseline = json.loads((OUT / "before.json").read_text())
for record in baseline["preserved_artifacts"]:
    assert sha(CWD / record["path"]) == record["sha256"], record["path"]
repo = CWD.parents[1]
for record in baseline["originals"]:
    assert sha(repo / record["path"]) == record["sha256"], record["path"]

data = json.loads((BASE / "data-snapshots/99-20260914T1319/results.json").read_text())
first = [row for row in data["trials"] if row["first_counted_result"]]
assert len(first) == 99
successes = [row for row in first if row["reward"] == 1]
assert len(successes) == 76
assert sum(row["assistant_steps"] > 75 for row in successes) == 64
task_order = list(data["tasks"])
assert len(task_order) == 11
old_path = BASE / "output/takehome-all-completed-tasks-99.pptx"
new_path = BASE / "output/takehome-all-completed-tasks-99-revised.pptx"
with zipfile.ZipFile(old_path) as old, zipfile.ZipFile(new_path) as new:
    slide_names = [name for name in new.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
    assert len(slide_names) == 19
    executive = " ".join(node.text or "" for node in ET.fromstring(new.read("ppt/slides/slide1.xml")).findall(".//a:t", NS))
    assert "Executive summary" in executive
    for required in ("Rerun", "Zenoh", "Burn", "99", "76", "64", "75"):
        assert required in executive, required
    for original_slide in (3, 4, 5):
        assert tables(old, original_slide) == tables(new, original_slide + 1), original_slide
    grid_rows = tables(new, 5)[0][1:] + tables(new, 6)[0][1:]
    checked = []
    statuses = collections.Counter()
    for task, row in zip(task_order, grid_rows):
        expected = []
        for model in ("fable-5-1", "opus-5", "sonnet-5"):
            for effort in ("medium", "high", "max"):
                trials = [r for r in first if (r["task"], r["model"], r["effort"]) == (task, model, effort)]
                assert len(trials) == 1
                trial = trials[0]
                status = "V" if trial["status"] == "verifier_timeout" else "A" if trial["status"] == "timeout" else "P" if trial["reward"] == 1 else "F"
                marker = "*" if trial["trial"].endswith(("__uuqa2f6", "__6ooqjT5")) else ""
                expected.append(f"{status} ({trial['assistant_steps']}){marker}")
                statuses[status] += 1
        assert row[1:] == expected, task
        checked.append({"task": task, "label": row[0], "cells": 9})
    coverage_rows = tables(new, 4)[0][1:]
    for task, row in zip(task_order, coverage_rows):
        expected = []
        for model in ("fable-5-1", "opus-5", "sonnet-5"):
            trials = [r for r in first if r["task"] == task and r["model"] == model]
            expected.append(f"{sum(r['reward'] == 1 for r in trials)}/{len(trials)}")
        assert row[1:] == expected + ["9/9"], task
    chart_values = chart(new)
    assert chart_values == chart(old) == workbook(new) == workbook(old)
    label_to_task = {row[0]: task for task, row in zip(task_order, coverage_rows)}
    for label, median in chart_values:
        task = label_to_task[label]
        assert median == statistics.median(r["assistant_steps"] for r in successes if r["task"] == task)
    assert len(chart_values) == 11
    old_size = ET.fromstring(old.read("ppt/presentation.xml")).find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
    new_size = ET.fromstring(new.read("ppt/presentation.xml")).find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz").attrib
    assert old_size == new_size
    original_text, revised_text = all_text(old), all_text(new)
    urls = lambda value: {url.rstrip(".,;") for url in re.findall(r'https?://[^\s<>()"]+', value)}
    assert urls(original_text) <= urls(revised_text), urls(original_text) - urls(revised_text)
    hashes = lambda value: set(re.findall(r"\b[0-9a-f]{64}\b", value))
    assert hashes(original_text) <= hashes(revised_text), hashes(original_text) - hashes(revised_text)
    for quote in ("All existing tests for the three crates pass, plus my own smoke tests",
                  "Everything is green with both feature configurations.",
                  "poor self-verification, self-reported infeasibility, and premature termination",
                  "patches across multiple files and substantial code modifications."):
        assert quote in revised_text, quote

result = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": True,
          "deck": str(new_path.relative_to(CWD)), "deck_sha256": sha(new_path),
          "slides": 19, "executive_summary_first": True, "all99_cells": checked,
          "statuses": dict(statuses), "all11_coverage_rows_match": True,
          "all11_chart_and_workbook_values_match": True, "slide_size_preserved": new_size,
          "original_source_urls_and_hashes_preserved": True, "exact_quotes_preserved": True,
          "original_files_preserved": len(baseline["originals"]),
          "prior_artifacts_preserved": len(baseline["preserved_artifacts"]),
          "visual_review": "Recorded separately after inspecting the final renders."}
(OUT / "numeric-review.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))

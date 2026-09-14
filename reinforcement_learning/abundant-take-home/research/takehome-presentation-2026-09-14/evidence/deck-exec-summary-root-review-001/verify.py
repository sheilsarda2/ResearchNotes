"""Verify the executive-summary reorder against the reviewed 19-slide deck."""
from pathlib import Path
import datetime
import hashlib
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

BASE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
CWD = BASE.parents[1]
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}
ORDER = [1, 11, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_nodes(root):
    return [node.text or "" for node in root.findall(".//a:t", NS)]


def normalized_slide(archive, number):
    root = ET.fromstring(archive.read(f"ppt/slides/slide{number}.xml"))
    footer_shapes = []
    for shape in root.findall(".//p:sp", NS):
        offset = shape.find("p:spPr/a:xfrm/a:off", NS)
        if offset is not None and offset.attrib == {"x": "11191875", "y": "6429375"}:
            footer_shapes.append(shape)
    assert len(footer_shapes) == 1
    assert text_nodes(footer_shapes[0]) == [str(number)]
    footer_shapes[0].find(".//a:t", NS).text = "SLIDE_NUMBER"
    return root


previous = BASE / "output/takehome-all-completed-tasks-99-revised.pptx"
current = BASE / "output/takehome-all-completed-tasks-99-exec-summary.pptx"
assert sha(previous) == "ff4c43bd4ffbf4dbbab7c805ec794b401590c4218d05df2143552b191dc815ad"
assert sha(BASE / ".build/build-all-tasks-deck-revised.mjs") == "b807201f33f43cffc8f40d513cc3ca790a9ff4c0aa8f39b373cd679056a7434f"
assert sha(BASE / "data-snapshots/99-20260914T1319/results.json") == "e780a2045232d215c26ad0893cf1df818e70a0e6caf9321d026c16d87cff2a9d"
previous_numeric = BASE / "evidence/deck-revision-root-review-001/numeric-review.json"
numeric = json.loads(previous_numeric.read_text())
assert numeric["passed"] and numeric["deck_sha256"] == sha(previous)
checked = []
with zipfile.ZipFile(previous) as old, zipfile.ZipFile(current) as new:
    assert len([n for n in new.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]) == 19
    for new_number, old_number in enumerate(ORDER, 1):
        old_root, new_root = normalized_slide(old, old_number), normalized_slide(new, new_number)
        old_text, new_text = text_nodes(old_root), text_nodes(new_root)
        if new_number == 2:
            assert old_text[0] == "Recommended Harbor samples"
            assert new_text[0] == "Executive summary: task selection"
            new_text[0] = old_text[0]
        assert old_text == new_text, (old_number, new_number, "slide text")
        for query in (".//a:tbl", ".//p:spPr", ".//p:graphicFrame/p:xfrm"):
            assert [ET.tostring(node) for node in old_root.findall(query, NS)] == [ET.tostring(node) for node in new_root.findall(query, NS)], (old_number, new_number, query)
        old_notes = ET.fromstring(old.read(f"ppt/notesSlides/notesSlide{old_number}.xml"))
        new_notes = ET.fromstring(new.read(f"ppt/notesSlides/notesSlide{new_number}.xml"))
        assert text_nodes(old_notes) == text_nodes(new_notes), (old_number, new_number, "notes")
        checked.append({"previous_slide": old_number, "current_slide": new_number,
                        "title": text_nodes(new_root)[0], "text_notes_tables_geometry_preserved": True})
    for name in old.namelist():
        if re.search(r"/charts/chart\d+\.xml$", name):
            assert old.read(name) == new.read(name), name
        if name.endswith(".xlsx"):
            with zipfile.ZipFile(io.BytesIO(old.read(name))) as old_book, zipfile.ZipFile(io.BytesIO(new.read(name))) as new_book:
                assert old_book.namelist() == new_book.namelist()
                for member in old_book.namelist():
                    assert old_book.read(member) == new_book.read(member), member
    assert len(ET.fromstring(new.read("ppt/slides/slide2.xml")).findall(".//a:tbl", NS)) == 1
    assert len(ET.fromstring(new.read("ppt/slides/slide11.xml")).findall(".//c:chart", NS)) == 1
repo = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
prefix = CWD.relative_to(repo).as_posix()
originals = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "c1ae968", "--", prefix], text=True).splitlines()
assert len(originals) == 36
for name in originals:
    assert (repo / name).read_bytes() == subprocess.check_output(["git", "-C", str(repo), "show", "c1ae968:" + name]), name
result = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "passed": True,
          "deck": str(current.relative_to(CWD)), "deck_sha256": sha(current),
          "previous_deck_sha256": sha(previous), "slide_order": ORDER, "slides": checked,
          "all99_results_preserved": True, "chart_xml_and_embedded_workbook_member_bytes_preserved": True,
          "workbook_container_note": "ZIP creation timestamps changed. All member names and uncompressed bytes are identical.",
          "prior_numeric_review": {"path": str(previous_numeric.relative_to(CWD)), "sha256": sha(previous_numeric)},
          "original_files_preserved": len(originals), "native_powerpoint_opened": False}
(OUT / "content-review.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({"passed": True, "slides": 19, "originals": 36, "deck_sha256": sha(current)}))

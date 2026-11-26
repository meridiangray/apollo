"""sourcedrop: dropped files become page-flow source profiles; the original is never touched; refusals say why."""
import os
import stat
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader, PdfWriter

import compose
import sourcedrop as sd

POLLARD = sd.PAGEFLOW.parent / "sources" / "pollard-damage-assessment.pdf"
PROSE = "The Harbour Board met on 4 May 1971 and appointed Ana Reyes as secretary. " * 80


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    return tmp_path / "sources"


@pytest.fixture
def pdf(tmp_path):
    if not POLLARD.is_file():
        pytest.skip("pollard pdf not present")
    r, w = PdfReader(str(POLLARD)), PdfWriter()
    for i in (2, 3, 4):
        w.add_page(r.pages[i])
    out = tmp_path / "in" / "Pollard excerpt.pdf"
    out.parent.mkdir()
    with out.open("wb") as f:
        w.write(f)
    return out


def blank_pdf(path, n=2, title=None):
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(200, 200)
    if title:
        w.add_metadata({"/Title": title})
    with Path(path).open("wb") as f:
        w.write(f)
    return Path(path)


def test_pdf_profile_shape_and_original_untouched(pdf, folder):
    before = pdf.read_bytes()
    r = sd.add(pdf)
    assert r["slug"] == "pollard-excerpt" and r["needs"] == []
    p = r["profile"]
    assert p["pages"] == {"from": 1, "to": 3} and p["archive"] == {"name": p["title"], "prefix": "pollard-excerpt"}
    assert "context" not in p and p["l6_ignore"] == [] and p["group"]["title"] == p["title"]
    assert p["file"].endswith("/files/pollard-excerpt.pdf") and p["file"].startswith("/")
    assert Path(p["file"]).read_bytes() == before == pdf.read_bytes()
    assert p["dropped"]["text_layer"] in ("full", "partial") and len(p["dropped"]["page_chars"]) == 3
    assert p["brief"].strip() == compose.load_context("general")["brief"].strip()
    assert yaml.safe_load((folder / "pollard-excerpt.yaml").read_text()) == p
    assert "publisher" not in p


def test_title_from_metadata_else_tidied_file_name(tmp_path):
    a = sd.add(blank_pdf(tmp_path / "a.pdf", title="Port Records 1971"))
    assert a["profile"]["title"] == "Port Records 1971"
    b = sd.add(blank_pdf(tmp_path / "harbour_notes-1971.pdf", title="Microsoft Word - draft.doc"))
    assert b["profile"]["title"] == "Harbour notes 1971"
    assert sd.add(blank_pdf(tmp_path / "x.pdf"), title="Mine")["profile"]["title"] == "Mine"


def test_no_text_layer_is_recorded_with_an_ocr_note(tmp_path, folder):
    r = sd.add(blank_pdf(tmp_path / "scan.pdf", 3))
    assert r["profile"]["dropped"]["text_layer"] == "none"
    assert "OCR" in (folder / "scan.yaml").read_text()


def test_text_and_markdown_are_read_in_parts(tmp_path):
    t = tmp_path / "Board Minutes.txt"
    t.write_text(PROSE, encoding="utf-8")
    p = sd.add(t)["profile"]
    assert p["part_chars"] == 3500 and p["pages"]["from"] == 1 and p["pages"]["to"] >= 2
    assert Path(p["file"]).read_text(encoding="utf-8") == PROSE
    m = tmp_path / "notes.md"
    m.write_text("# The Eastmere quay\n\n" + PROSE, encoding="utf-8")
    assert sd.add(m)["profile"]["title"] == "The Eastmere quay"


def test_html_is_converted_and_the_txt_kept_beside_the_copy(tmp_path, folder):
    h = tmp_path / "page.html"
    h.write_text("<html><head><title>Harbour Board</title></head><body><article><h1>Harbour Board</h1><p>"
                 + PROSE + "</p></article></body></html>", encoding="utf-8")
    p = sd.add(h)["profile"]
    assert p["file"].endswith("/files/page.txt") and "Harbour Board met on 4 May 1971" in Path(p["file"]).read_text()
    assert (folder / "files" / "page.html").is_file() and p["part_chars"] == 3500
    assert p["dropped"]["files"] == ["page.html", "page.txt"]


def fake_pandoc(tmp_path, monkeypatch, present=True):
    exe = tmp_path / "bin" / "pandoc"
    exe.parent.mkdir(exist_ok=True)
    exe.write_text("#!/bin/sh\necho 'Annual report text of the Harbour Board.'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(sd.shutil, "which", lambda n: str(exe) if present and n == "pandoc" else None)


def test_docx_is_refused_without_pandoc(tmp_path, monkeypatch):
    d = tmp_path / "report.docx"
    d.write_bytes(b"PK fake")
    fake_pandoc(tmp_path, monkeypatch, present=False)
    with pytest.raises(ValueError, match="pandoc"):
        sd.add(d)
    assert sd.listing() == []


def test_docx_with_pandoc(tmp_path, monkeypatch):
    d = tmp_path / "report.docx"
    d.write_bytes(b"PK fake")
    fake_pandoc(tmp_path, monkeypatch)
    r = sd.add(d)
    assert "Annual report" in Path(r["profile"]["file"]).read_text() and r["profile"]["dropped"]["format"] == "docx"


@pytest.mark.parametrize("name", ["a.xlsx", "noext", "b.exe"])
def test_other_formats_are_refused(tmp_path, name, folder):
    f = tmp_path / name
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="not a format"):
        sd.add(f)
    assert not folder.exists() or not list(folder.rglob("*.*"))


def test_other_refusals(tmp_path):
    with pytest.raises(ValueError, match="no such file"):
        sd.add(tmp_path / "gone.pdf")
    e = tmp_path / "e.txt"
    e.write_text("  \n")
    with pytest.raises(ValueError, match="no text"):
        sd.add(e)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        sd.add(bad)


def test_a_refused_file_leaves_nothing_behind(tmp_path, folder):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(Exception):
        sd.add(bad)
    assert not folder.exists() or not any(folder.rglob("*.*"))


def test_clashes_get_a_number_and_built_in_names_are_not_taken(tmp_path):
    t = tmp_path / "doc.txt"
    t.write_text(PROSE)
    assert [sd.add(t)["slug"] for _ in range(3)] == ["doc", "doc-2", "doc-3"]
    p = tmp_path / "pollard.txt"
    p.write_text(PROSE)
    assert sd.add(p)["slug"] == "pollard-2"


def test_a_source_needs_nothing_and_gets_the_general_brief(tmp_path):
    t = tmp_path / "doc.txt"
    t.write_text(PROSE)
    r = sd.add(t)
    assert r["needs"] == [] and "context" not in r["profile"]
    assert r["profile"]["brief"].strip() == compose.load_context("general")["brief"].strip()
    assert sd.listing()[0]["needs"] == [] and "context" not in sd.listing()[0]
    with pytest.raises(ValueError, match="cannot edit"):
        sd.update("doc", context="place")


def test_given_brief_is_kept_and_an_emptied_one_returns_to_the_default(tmp_path):
    t = tmp_path / "doc.txt"
    t.write_text(PROSE)
    assert sd.add(t, brief="Keep dates.")["profile"]["brief"] == "Keep dates.\n"
    assert sd.update("doc", brief="")["profile"]["brief"].strip() == compose.load_context("general")["brief"].strip()


def test_update_validates(pdf):
    s = sd.add(pdf)["slug"]
    u = sd.update(s, title="  New   name ", pages="2-3")["profile"]
    assert u["title"] == "New name" and u["archive"] == {"name": "New name", "prefix": s}
    assert u["pages"] == {"from": 2, "to": 3}
    for bad in ("0-2", "3-2", "1-9", "x"):
        with pytest.raises(ValueError, match="pages"):
            sd.update(s, pages=bad)
    with pytest.raises(ValueError, match="cannot edit"):
        sd.update(s, file="/etc/passwd")
    with pytest.raises(ValueError, match="empty"):
        sd.update(s, title=" ")
    with pytest.raises(ValueError, match="no dropped source"):
        sd.update("nope", title="x")
    assert sd.load(s)["pages"] == {"from": 2, "to": 3}


def test_remove_deletes_only_its_own_files(tmp_path, folder):
    t = tmp_path / "a.txt"
    t.write_text(PROSE)
    sd.add(t)
    sd.add(t)                              # a-2
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    p = yaml.safe_load((folder / "a.yaml").read_text())
    p["dropped"]["files"] = ["a.txt", "../../outside.txt"]  # a tampered profile cannot reach out of files/
    (folder / "a.yaml").write_text(yaml.safe_dump(p))
    out = sd.remove("a")
    assert out["removed"] == ["a.yaml", "a.txt"] and outside.read_text() == "keep" and t.is_file()
    assert (folder / "a-2.yaml").is_file() and (folder / "files" / "a-2.txt").is_file()
    for bad in ("../x", "a/b", "", "A"):
        with pytest.raises(ValueError):
            sd.remove(bad)
    with pytest.raises(ValueError, match="no dropped source"):
        sd.remove("a")


def test_listing(tmp_path, pdf, folder):
    sd.add(pdf)
    t = tmp_path / "t.txt"
    t.write_text(PROSE)
    sd.add(t)
    (folder / "hand.yaml").write_text("title: Hand written\nfile: /x\n")        # not dropped: not listed
    rows = {r["slug"]: r for r in sd.listing()}
    assert set(rows) == {"pollard-excerpt", "t"}
    assert rows["pollard-excerpt"]["page_count"] == 3 and rows["pollard-excerpt"]["bytes"] > 0
    assert rows["pollard-excerpt"]["text_layer"] and rows["t"]["page_count"] >= 2 and rows["t"]["needs"] == []


def test_a_dropped_profile_composes_through_the_extra_folder(tmp_path, folder, monkeypatch):
    t = tmp_path / "board.txt"
    t.write_text(PROSE)
    sd.add(t)
    monkeypatch.setenv("PAGEFLOW_SOURCES_EXTRA", str(folder))
    cfg = compose.compose("smoke", "board", name="x", root=tmp_path)
    assert cfg["source"]["file"].endswith("/files/board.txt") and cfg["brief"].startswith("A document about a case, a person")
    assert cfg["pages"]["from"] == 1 and cfg["archive"]["prefix"] == "board"

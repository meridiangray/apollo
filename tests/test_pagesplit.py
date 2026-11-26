"""One page split: Apollo's page getter and the Text tab give the pages the Basic (page-flow) run saw, cut from hydra's text."""
import json
import shutil

import pytest

import pagetext
import sourcedrop
import triage
from conftest import TESTING

PARA = "Mr. Rockefeller bought the Standard works at Cleveland in 1872 and paid for them in stock, as the books show."
BOOK = "\n\n\n".join(f"{PARA} Section {i}." for i in range(900))      # blank-line runs: hydra's text is not the raw file


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    f = tmp_path / "in" / "Oil book.txt"
    f.parent.mkdir()
    f.write_text(BOOK, encoding="utf-8")
    return sourcedrop.add(f)["slug"], f


def basic_pages(f, size=3500):
    from hydra_mcp import files
    return triage.parts_of(files.read(f), size)


def test_the_pages_are_page_flows_slices_of_hydras_text(book):
    slug, f = book
    want = basic_pages(f)
    assert len(want) > 15 and "".join(want.values()) != BOOK           # hydra's text differs from the raw file
    got = pagetext.source_pages(slug, "/a/arch", range(36 - 30, 51 - 30))
    assert got == {n: want[n] for n in range(6, 21)}
    assert pagetext.source_pages(slug, None, [1, 9999]) == {1: want[1]}
    assert pagetext.source_pages(slug) == want


def test_the_text_tab_shows_the_same_pages(book):
    slug, f = book
    want = basic_pages(f)
    for n in (1, 7, len(want)):
        got = pagetext.page(slug, n)
        assert got["ok"] and got["kind"] == "parts" and got["total"] == len(want) and got["text"] == want[n]


def test_page_40_of_tarbell_holds_the_quotes_basic_found(tmp_path, monkeypatch):
    run = TESTING / "runs" / "2026-11-25-read-tarbell-standard-oil" / "pageflow" / "pages" / "40.mentions.json"
    src = TESTING.parent / "apollo" / "sources" / "files" / "tarbell-standard-oil.txt"
    if not (run.is_file() and src.is_file()):
        pytest.skip("the Tarbell run or file is not here")
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    copy = tmp_path / "in" / "tarbell-standard-oil.txt"
    copy.parent.mkdir()
    shutil.copy(src, copy)
    slug = sourcedrop.add(copy)["slug"]
    pages = pagetext.source_pages(slug)
    seen = triage.window(pages, 40)                                  # what the model saw: the page and 400 characters each side
    plain = str.maketrans({0x2019: "'", 0x2018: "'", 0x201c: '"', 0x201d: '"', 0x2014: "-"})              # the model writes straight quotes, the book curly ones and dashes
    text = " ".join((seen["before"] + pages[40] + seen["after"]).translate(plain).split())   # quotes are one line, the text wrapped
    quotes = [m["quote"] for m in json.loads(run.read_text(encoding="utf-8"))["mentions"] if m.get("quote")]
    assert quotes and all(" ".join(q.split()) in text for q in quotes)

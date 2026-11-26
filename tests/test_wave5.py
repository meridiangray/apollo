"""Wave 5: Wikipedia refusals (no network), the portrait card's facts, the empty Draft never builds, imported collections."""
import json
import pytest

import archives
import sourcedrop as sd
import tools
import wikipedia


def test_wikipedia_parse_title_and_address():
    assert wikipedia.parse("en", "Al Waleed bin Talal") == ("en", "Al Waleed bin Talal")
    assert wikipedia.parse("en", "https://ar.wikipedia.org/wiki/%D9%85") [0] == "ar"
    for bad in ("", "https://example.com/wiki/X"):
        with pytest.raises(ValueError):
            wikipedia.parse("en", bad)
    with pytest.raises(ValueError):
        wikipedia.parse("English!", "X")


def test_wikipedia_refuses_plainly(monkeypatch):
    import urllib.error

    def blocked(*a, **k):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(wikipedia.urllib.request, "urlopen", blocked)
    with pytest.raises(ValueError, match="network"):
        wikipedia.fetch("en", "Anything")


def test_scanned_ranges():
    assert sd.scanned_ranges([5, 0, 0, 3, 0, 7]) == "2-3, 5"
    assert sd.scanned_ranges([1, 2]) == ""
    assert sd.scanned_ranges(None) == ""


def test_dropped_text_carries_web_facts(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "s"))
    f = tmp_path / "x.txt"
    f.write_text("The Harbour Board met on 4 May 1971. " * 60, encoding="utf-8")
    got = sd.add(f, title="Harbour", extra={"publisher": "Wikipedia", "url": "https://en.wikipedia.org/wiki/X", "language": "en", "retrieved": "2026-11-25"})
    row = next(r for r in sd.listing() if r["slug"] == got["slug"])
    assert (row["publisher"], row["language"], row["url"], row["retrieved"]) == ("Wikipedia", "en", "https://en.wikipedia.org/wiki/X", "2026-11-25")
    assert row["added"]


def test_empty_archive_is_never_stale(tmp_path, monkeypatch):
    arch = tmp_path / "archives" / "draft"
    (arch / "collections").mkdir(parents=True)
    (arch / "hydra.yaml").write_text("name: Draft\nprefix: draft\n", encoding="utf-8")
    monkeypatch.setattr(tools, "allowed_archive", lambda a: arch)
    assert tools.archive_stale(str(arch)) is False


def test_imported_collection_is_labelled_not_a_run(tmp_path):
    archives.track_import("mapsaud", str(tmp_path / "a"), ["mapsaud-alwaleed"])
    t = archives.tracked(str(tmp_path / "a"))["mapsaud-alwaleed"]
    assert t["imported"] == "mapsaud" and t["run"] is None

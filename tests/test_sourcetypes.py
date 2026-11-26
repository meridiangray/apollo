"""Source types (Document, Photo, Video, Audio, Web) and the photo, video and audio files a drop now keeps."""
import pytest

import sourcedrop as sd


@pytest.mark.parametrize("fmt,publisher,url,want", [
    ("pdf", None, None, "document"), ("docx", None, None, "document"), ("txt", None, None, "document"), ("md", None, None, "document"),
    ("html", None, None, "web"), ("htm", None, None, "web"), ("txt", "Wikipedia", None, "web"),
    ("txt", None, "https://en.wikipedia.org/wiki/X", "web"),
    ("jpg", None, None, "photo"), ("jpeg", None, None, "photo"), ("png", None, None, "photo"), ("tif", None, None, "photo"),
    ("tiff", None, None, "photo"), ("webp", None, None, "photo"), ("gif", None, None, "photo"),
    ("mp4", None, None, "video"), ("mov", None, None, "video"), ("webm", None, None, "video"), ("mkv", None, None, "video"),
    ("mp3", None, None, "audio"), ("wav", None, None, "audio"), ("m4a", None, None, "audio"), ("flac", None, None, "audio"),
])
def test_a_source_type_follows_its_format(fmt, publisher, url, want):
    assert sd.source_type(fmt, publisher, url) == want


@pytest.fixture
def sources(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "s"))
    return tmp_path


def test_a_photo_a_video_and_a_sound_are_kept_with_their_type(sources):
    for name, want in (("harbour.JPG", "photo"), ("launch.mp4", "video"), ("minutes.flac", "audio")):
        f = sources / name
        f.write_bytes(b"not really media")
        got = sd.add(f)
        assert got["needs"] == [] and got["profile"]["dropped"]["media"] == want
    rows = {r["slug"]: r for r in sd.listing()}
    assert {r["type"] for r in rows.values()} == {"photo", "video", "audio"}
    assert all(r["kind"] == "media" and r["page_count"] is None and r["needs"] == [] for r in rows.values())


def test_media_stay_out_of_the_flows_source_folder_and_take_only_a_title(sources):
    f = sources / "harbour.png"
    f.write_bytes(b"\x89PNG")
    slug = sd.add(f)["slug"]
    assert not list(sd.folder().glob("*.yaml")) and (sd.media_dir() / f"{slug}.yaml").is_file()
    assert sd.update(slug, title="The harbour")["profile"]["title"] == "The harbour"
    with pytest.raises(ValueError, match="only a title"):
        sd.update(slug, brief="x")
    assert sd.thumb(slug, sources / "thumbs") == sd.files_dir() / f"{slug}.png"        # a photo is its own picture
    sd.remove(slug)
    assert sd.listing() == [] and not (sd.files_dir() / f"{slug}.png").exists()


def test_a_text_source_is_a_document_and_a_fetched_page_is_web(sources):
    f = sources / "minutes.txt"
    f.write_text("The Harbour Board met on 4 May 1971. " * 60, encoding="utf-8")
    sd.add(f)
    h = sources / "page.html"
    h.write_text("<html><title>A page</title><body><p>" + "Words here. " * 60 + "</p></body></html>", encoding="utf-8")
    sd.add(h)
    assert {r["format"]: r["type"] for r in sd.listing()} == {"txt": "document", "html": "web"}


def test_a_format_that_is_not_kept_is_refused(sources):
    f = sources / "program.exe"
    f.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="not a format"):
        sd.add(f)


def test_the_cards_facts_are_edited_in_place_and_checked(sources, tmp_path):
    f = tmp_path / "in" / "Harbour minutes.txt"
    f.parent.mkdir()
    f.write_text("The Harbour Board met on 4 May 1971.", encoding="utf-8")
    slug = sd.add(f)["slug"]
    sd.update(slug, publisher=" Harbour  Board ", language="en", url="https://example.org/a", retrieved="2026-11-25")
    row = next(r for r in sd.listing() if r["slug"] == slug)
    assert (row["publisher"], row["language"], row["url"], row["retrieved"]) == ("Harbour Board", "en", "https://example.org/a", "2026-11-25")
    sd.update(slug, url="", publisher="")
    row = next(r for r in sd.listing() if r["slug"] == slug)
    assert not row["url"] and not row["publisher"]
    for bad in ({"language": "English"}, {"url": "ftp://x"}, {"retrieved": "yesterday"}, {"publisher": "p" * 400}):
        with pytest.raises(ValueError):
            sd.update(slug, **bad)

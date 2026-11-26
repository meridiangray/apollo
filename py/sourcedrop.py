"""sourcedrop.py: take a file the owner drops in and make it a source Apollo can run.

    add(path, title=, brief=)            copy the file into sources/files/, write sources/<slug>.yaml  -> dict
    update(slug, **fields)               edit title, brief, pages (validated)                          -> dict
    remove(slug)                         delete that profile and its copied files, nothing else          -> dict
    listing()                            every dropped source: fields, size, pages, text layer          -> list

A dropped source is a page-flow source profile in the same shape as pageflow/sources/<name>.yaml, with an absolute
`file:`. pageflow's compose.py reads it through PAGEFLOW_SOURCES_EXTRA (see `folder()`). The original is only read.
A format that cannot be read is refused with `ValueError` whose text says why.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml

import settings

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
PAGEFLOW = Path(os.environ.get("PAGEFLOW_DIR") or HERE.parent / "hydra-mcp-testing" / "pageflow")
HYDRA_SRC = HERE.parent / "hydra-git" / "src"
PART_CHARS = 3500
TEXT_FORMATS = {".txt", ".md"}
HTML_FORMATS = {".html", ".htm"}
IMAGE_FORMATS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif"}
VIDEO_FORMATS = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_FORMATS = {".mp3", ".wav", ".m4a", ".flac"}
MEDIA_FORMATS = IMAGE_FORMATS | VIDEO_FORMATS | AUDIO_FORMATS    # kept as sources with a type, never read by a flow (no text layer)
FORMATS = {".pdf"} | TEXT_FORMATS | HTML_FORMATS | {".docx"} | MEDIA_FORMATS
TYPES = ("document", "photo", "video", "audio", "web")
FIELDS = ("title", "brief", "pages", "publisher", "language", "url", "retrieved")
FACTS = {"publisher": None, "language": r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", "url": r"https?://\S+", "retrieved": r"\d{4}-\d{2}-\d{2}"}   # the facts the card edits
MEANINGLESS_TITLE = re.compile(r"^(untitled|document\d*|microsoft word.*|.*\.(docx?|pdf|indd|tex|qxd|ppt|pptx|txt))$", re.I)


def folder() -> Path:
    """Where dropped sources live (APOLLO_SOURCES overrides, for tests): the profiles, and files/ beside them."""
    return settings.folder("folders.sources", HERE / "sources")            # APOLLO_SOURCES, then Settings


def files_dir() -> Path:
    return folder() / "files"


def media_dir() -> Path:
    """The profiles of photos, videos and audio: apart from the others, so no flow's source list sees them."""
    return folder() / "media"


def source_type(fmt, publisher=None, url=None) -> str:
    """A source's type from its format: pdf, docx, txt and md are a document; html, a Wikipedia article and any fetched page are web;
    then photo, video and audio by their own formats."""
    ext = "." + str(fmt or "").lower().lstrip(".")
    if ext in IMAGE_FORMATS:
        return "photo"
    if ext in VIDEO_FORMATS:
        return "video"
    if ext in AUDIO_FORMATS:
        return "audio"
    if ext in HTML_FORMATS or url or re.search(r"wikipedia", str(publisher or ""), re.I):
        return "web"
    return "document"


# ---- small helpers -------------------------------------------------------------------------------------------------

def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:60] or "source"


def tidy_name(stem: str) -> str:
    """A file name as a title: separators to spaces, whitespace collapsed; a name all in lower case gets its first
    letter raised. Nothing is added."""
    t = re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()
    return t[:1].upper() + t[1:] if t and t == t.lower() else t


def meaningful(title) -> str | None:
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(t) < 4 or MEANINGLESS_TITLE.match(t) or re.fullmatch(r"[\d\W_]+", t):
        return None
    return t


def posix(p: Path) -> str:
    """The path as the driver (WSL) sees it: E:\\a\\b becomes /mnt/e/a/b; a path already POSIX is kept."""
    s = str(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    return f"/mnt/{m.group(1).lower()}/{m.group(2).replace(chr(92), '/')}" if m else s.replace("\\", "/")


def native(p: str) -> Path:
    """A profile's `file:` as a path this process can open (the reverse of `posix` when run on Windows)."""
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    return Path(f"{m.group(1).upper()}:/{m.group(2)}") if m and os.name == "nt" else Path(p)


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parts_count(text: str) -> int:
    return len(_module("_sd_triage", PAGEFLOW / "triage.py").parts_of({"text": text}, PART_CHARS))


def default_brief() -> str | None:
    """The page flow's one general context's brief: what a source with no brief of its own gets."""
    p = PAGEFLOW / "contexts" / "general.yaml"
    return ((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("brief") or "").strip() or None if p.is_file() else None


def taken() -> set[str]:
    names = {p.stem for p in folder().glob("*.y*ml")} if folder().is_dir() else set()
    names |= {p.stem for p in media_dir().glob("*.y*ml")} if media_dir().is_dir() else set()
    names |= {p.stem for p in (PAGEFLOW / "sources").glob("*.y*ml")}
    if files_dir().is_dir():
        names |= {p.stem for p in files_dir().iterdir()}
    return names


def free_slug(base: str) -> str:
    used, slug, n = taken(), base, 1
    while slug in used:
        n += 1
        slug = f"{base}-{n}"
    return slug


# ---- reading each format -------------------------------------------------------------------------------------------

def pdf_facts(path: Path) -> dict:
    """{title, publisher, pages, chars: [characters of text per page]} with pypdfium2, else pypdf, else poppler."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(path))
        meta = {k: (doc.get_metadata_value(k) or "") for k in ("Title", "Publisher")}
        chars = []
        for i in range(len(doc)):
            tp = doc[i].get_textpage()
            chars.append(len((tp.get_text_range() or "").strip()))
        return {"title": meta["Title"], "publisher": meta["Publisher"], "pages": len(doc), "chars": chars}
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path))
        meta = r.metadata or {}
        return {"title": str(meta.get("/Title") or ""), "publisher": str(meta.get("/Publisher") or ""),
                "pages": len(r.pages), "chars": [len((pg.extract_text() or "").strip()) for pg in r.pages]}
    except ImportError:
        pass
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=60)
    if info.returncode:
        raise ValueError(f"cannot read the PDF: {info.stderr.strip() or 'pdfinfo failed'}")
    kv = dict(re.findall(r"^(\w+):\s*(.*)$", info.stdout, re.M))
    n = int(kv.get("Pages") or 0)
    chars = []
    for i in range(1, n + 1):
        out = subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), str(path), "-"], capture_output=True, text=True,
                             timeout=60)
        chars.append(len(out.stdout.strip()))
    return {"title": kv.get("Title", ""), "publisher": kv.get("Publisher", ""), "pages": n, "chars": chars}


def text_layer(chars: list[int]) -> str:
    """hydra's rule (hydra_mcp.profile): none when no text at all, partial when under 80% of the pages have any."""
    filled = sum(1 for c in chars if c > 0)
    return "none" if not filled else "partial" if filled < len(chars) * 0.8 else "full"


def html_to_text(raw: str) -> tuple[str, str | None, str | None]:
    """(text, headline, site name) with hydra's own extraction (read-only import of hydra.api.webtext)."""
    if str(HYDRA_SRC) not in sys.path:
        sys.path.append(str(HYDRA_SRC))
    try:
        from hydra.api.webtext import html_text
    except Exception as e:
        raise ValueError(f"HTML needs hydra's text extraction, which cannot be imported ({e})") from e
    text, headline, _ = html_text(raw)
    m = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', raw, re.I)
    if not text.strip():
        raise ValueError("no readable text found in the HTML file")
    return text, meaningful(headline), (m.group(1).strip() if m else None)


def docx_to_text(path: Path) -> str:
    cmd, exe = None, shutil.which("pandoc")
    if exe:
        cmd = [exe, "-t", "plain", "--wrap=none", str(path)]
    elif os.name == "nt" and shutil.which("wsl"):
        probe = subprocess.run(["wsl", "-d", "Debian", "--", "sh", "-c", "command -v pandoc"], capture_output=True, text=True)
        if probe.returncode == 0 and probe.stdout.strip():
            cmd = ["wsl", "-d", "Debian", "--", "pandoc", "-t", "plain", "--wrap=none", posix(path)]
    if not cmd:
        raise ValueError("a .docx needs pandoc, which is not installed; save the document as PDF or text and drop that")
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if out.returncode or not out.stdout.strip():
        raise ValueError(f"pandoc could not convert the .docx: {out.stderr.strip() or 'no text came out'}")
    return out.stdout


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("the file is not plain text")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("cannot decode the file as text")
    if not text.strip():
        raise ValueError("the file has no text")
    return text


def md_heading(text: str) -> str | None:
    m = re.search(r"^#\s+(.+?)\s*#*\s*$", text, re.M)
    return meaningful(m.group(1)) if m else None


# ---- the profile ---------------------------------------------------------------------------------------------------

def profile_path(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        raise ValueError(f"not a dropped source name: {slug!r}")
    m = media_dir() / f"{slug}.yaml"
    return m if m.is_file() else folder() / f"{slug}.yaml"


def load(slug: str) -> dict:
    p = profile_path(slug)
    if not p.is_file():
        raise ValueError(f"no dropped source {slug!r}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def needs_of(prof: dict) -> list[str]:
    return []                                                # nothing is required before a run: the context is the page flow's one general one


def save(slug: str, prof: dict, note: str | None = None) -> Path:
    p = media_dir() / f"{slug}.yaml" if (prof.get("dropped") or {}).get("media") else profile_path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    head = f"# Dropped source {slug}: written by Apollo's sourcedrop; edit it from the Sources tab.\n"
    if note:
        head += f"# {note}\n"
    p.write_text(head + yaml.safe_dump(prof, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8", newline="\n")
    return p


def result(slug: str, prof: dict) -> dict:
    return {"slug": slug, "path": str(profile_path(slug)), "profile": prof, "needs": needs_of(prof)}


def add(path, *, title: str | None = None, brief: str | None = None, extra: dict | None = None) -> dict:
    """Take the file at `path` in. Returns {slug, path, profile, needs}; `needs` is always empty (a source needs no
    context). Raises ValueError, with the reason, for a file it refuses."""
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"no such file: {src}")
    ext = src.suffix.lower()
    if ext not in FORMATS:
        raise ValueError(f"{ext or 'a file with no extension'} is not a format Apollo reads; "
                         f"drop one of {', '.join(sorted(FORMATS))}")
    if ext in MEDIA_FORMATS:
        return _add_media(src, ext, title)
    found_title, publisher, text, info = None, None, None, {}
    extra = {k: str(v).strip() for k, v in (extra or {}).items() if v and k in ("publisher", "url", "language", "retrieved")}
    if ext == ".pdf":
        f = pdf_facts(src)
        if not f["pages"]:
            raise ValueError("the PDF has no pages")
        layer = text_layer(f["chars"])
        found_title, publisher = meaningful(f["title"]), meaningful(f["publisher"])
        info = {"text_layer": layer, "page_chars": f["chars"]}
    elif ext in TEXT_FORMATS:
        text = read_text_file(src)
        found_title = md_heading(text) if ext == ".md" else None
    elif ext in HTML_FORMATS:
        text, found_title, publisher = html_to_text(read_text_file(src))
    else:
        text = docx_to_text(src)
    title = (title or "").strip() or found_title or tidy_name(src.stem)
    publisher = extra.get("publisher") or publisher

    slug = free_slug(slugify(src.stem))
    fd = files_dir()
    fd.mkdir(parents=True, exist_ok=True)
    copy = fd / f"{slug}{ext}"
    shutil.copy2(src, copy)
    copied = [copy.name]
    runfile = copy
    if text is not None and ext != ".txt" and ext != ".md":
        runfile = fd / f"{slug}.txt"                        # the converted text sits beside the original copy
        runfile.write_text(text, encoding="utf-8", newline="\n")
        copied.append(runfile.name)

    prof: dict = {"file": posix(runfile.resolve()), "title": title}
    if publisher:
        prof["publisher"] = publisher
    if ext == ".pdf":
        prof["pages"] = {"from": 1, "to": f["pages"]}
    else:
        parts = max(1, parts_count(text))
        info = {"parts": parts}
        prof["part_chars"] = PART_CHARS
        prof["pages"] = {"from": 1, "to": parts}
    prof["archive"] = {"name": title, "prefix": slug}
    default = brief is None or not str(brief).strip()
    chosen = default_brief() if default else str(brief).strip()
    if chosen:
        prof["brief"] = chosen + "\n"
    prof["group"] = {"name": "all", "title": title}
    prof["l6_ignore"] = []
    where = {k: extra[k] for k in ("url", "language", "retrieved") if k in extra}
    prof["dropped"] = {"original": src.name, "format": ext.lstrip("."), "files": copied,
                       "bytes": copy.stat().st_size, "brief_default": bool(default and chosen), **info}
    prof["dropped"]["added"] = datetime.now().isoformat(timespec="seconds")
    prof["dropped"].update(where)
    note = "The PDF has no text layer: run OCR on it first." if info.get("text_layer") == "none" else \
        "The PDF has a partial text layer: OCR the pages without text before a run." if info.get("text_layer") == "partial" else None
    save(slug, prof, note)
    return result(slug, prof)


def _add_media(src: Path, ext: str, title: str | None) -> dict:
    """A photo, video or audio file: copied in and listed with its type; it has no text, so no context, brief or pages."""
    title = (title or "").strip() or tidy_name(src.stem)
    slug = free_slug(slugify(src.stem))
    files_dir().mkdir(parents=True, exist_ok=True)
    copy = files_dir() / f"{slug}{ext}"
    shutil.copy2(src, copy)
    prof = {"file": posix(copy.resolve()), "title": title,
            "dropped": {"original": src.name, "format": ext.lstrip("."), "files": [copy.name], "bytes": copy.stat().st_size,
                        "media": source_type(ext), "added": datetime.now().isoformat(timespec="seconds")}}
    save(slug, prof)
    return result(slug, prof)


def update(slug: str, **fields) -> dict:
    """Edit a dropped source: title, brief, pages ('A-B', or {from, to}) and the facts. Anything else is refused.
    An emptied brief brings back the default brief."""
    bad = sorted(set(fields) - set(FIELDS))
    if bad:
        raise ValueError(f"cannot edit {', '.join(bad)}; editable: {', '.join(FIELDS)}")
    prof = load(slug)
    d = prof.setdefault("dropped", {})
    if d.get("media") and set(fields) - {"title"}:
        raise ValueError(f"a {d['media']} has only a title to edit")
    if "title" in fields:
        t = re.sub(r"\s+", " ", str(fields["title"] or "")).strip()
        if not t:
            raise ValueError("the title cannot be empty")
        prof["title"] = t
        prof["archive"] = {**(prof.get("archive") or {}), "name": t}
        prof["group"] = {**(prof.get("group") or {}), "title": t}
    for k, pat in FACTS.items():                                                  # publisher, language, address, date: empty clears
        if k in fields:
            v = " ".join(str(fields[k] or "").split())
            if len(v) > 300 or (v and pat and not re.fullmatch(pat, v)):
                raise ValueError(f"{k}: {'a language code such as en' if k == 'language' else 'an address starting http' if k == 'url' else 'a date as YYYY-MM-DD' if k == 'retrieved' else 'at most 300 characters'}")
            if k == "publisher":
                prof["publisher"] = v
                if not v:
                    prof.pop("publisher", None)
            elif v:
                d[k] = v
            else:
                d.pop(k, None)
    if "brief" in fields:
        b = str(fields["brief"] or "").strip()
        if b:
            prof["brief"], d["brief_default"] = b + "\n", False
        else:
            prof.pop("brief", None)
            d["brief_default"] = False
            b2 = default_brief()
            if b2:
                prof["brief"], d["brief_default"] = b2 + "\n", True
    if "pages" in fields:
        total = len(d["page_chars"]) if d.get("page_chars") else d.get("parts") or 1
        pg = fields["pages"]
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", pg) if isinstance(pg, str) else None
        try:
            a, b = (int(m.group(1)), int(m.group(2) or m.group(1))) if m else (int(pg["from"]), int(pg["to"]))
        except (TypeError, KeyError, ValueError):
            raise ValueError("pages: A-B, e.g. 3-5") from None
        if not 1 <= a <= b <= total:
            raise ValueError(f"pages: A-B within 1-{total}")
        prof["pages"] = {"from": a, "to": b}
    save(slug, prof)
    return result(slug, prof)


def remove(slug: str) -> dict:
    """Delete the profile and the files it lists in sources/files/. Nothing outside the sources folder is touched."""
    prof = load(slug)
    fd = files_dir().resolve()
    gone = []
    for name in (prof.get("dropped") or {}).get("files") or []:
        f = (fd / Path(name).name).resolve()
        if f.parent == fd and f.is_file():
            f.unlink()
            gone.append(f.name)
    profile_path(slug).unlink()
    return {"slug": slug, "removed": [f"{slug}.yaml"] + gone}


def listing() -> list[dict]:
    """Each dropped source (profiles in sources/ that carry a `dropped` block), by name: slug, title, context, brief,
    pages, format, original, bytes, page count, text_layer, needs, brief_default, kind (pages | parts)."""
    out = []
    paths = (sorted(folder().glob("*.yaml")) if folder().is_dir() else []) + (sorted(media_dir().glob("*.yaml")) if media_dir().is_dir() else [])
    for p in paths:
        prof = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        d = prof.get("dropped")
        if not d:
            continue
        total = len(d["page_chars"]) if d.get("page_chars") else d.get("parts")
        out.append({"slug": p.stem, "title": prof.get("title"), "brief": prof.get("brief"),
                    "pages": prof.get("pages"), "page_count": total, "format": d.get("format"), "original": d.get("original"),
                    "bytes": d.get("bytes"), "text_layer": d.get("text_layer"), "file": prof.get("file"),
                    "publisher": prof.get("publisher"), "needs": needs_of(prof), "brief_default": bool(d.get("brief_default")),
                    "kind": "pages" if d.get("page_chars") else "parts" if d.get("parts") else "media",
                    "type": source_type(d.get("format"), prof.get("publisher"), d.get("url")), "scanned": scanned_ranges(d.get("page_chars")),
                    "url": d.get("url"), "language": d.get("language"), "retrieved": d.get("retrieved"),
                    "added": d.get("added") or datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")})
    return out


def scanned_ranges(chars) -> str:
    """The pages with no text of their own, as ranges ("3-5, 9"); empty when every page has text or the source has no pages."""
    bad = [i + 1 for i, c in enumerate(chars or []) if not c]
    out, i = [], 0
    while i < len(bad):
        j = i
        while j + 1 < len(bad) and bad[j + 1] == bad[j] + 1:
            j += 1
        out.append(str(bad[i]) if i == j else f"{bad[i]}-{bad[j]}")
        i = j + 1
    return ", ".join(out)


def thumb(slug: str, out_dir: Path) -> Path | None:
    """The first page of a dropped PDF as a small PNG under `out_dir` (pdftoppm), made once and kept; None for any other format."""
    try:
        prof = load(slug)
    except ValueError:                                       # a built-in source: the page flow's own PDF, read only
        import tools
        src = tools.source_file(slug)
        if src.suffix.lower() != ".pdf":
            return None
    else:
        d = prof.get("dropped") or {}
        if d.get("media") == "photo":                        # a photo is its own picture
            f = files_dir() / (d.get("files") or [""])[0]
            return f if f.is_file() else None
        if d.get("format") != "pdf":
            return None
        src = files_dir() / (d.get("files") or [""])[0]
        if not src.is_file():
            src = native(prof.get("file") or "")
    if not src.is_file():
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{slug}.png"
    if target.is_file() and target.stat().st_mtime >= src.stat().st_mtime:
        return target
    base = out_dir / f"{slug}.tmp"
    p = subprocess.run([shutil.which("pdftoppm") or "pdftoppm", "-png", "-f", "1", "-l", "1", "-scale-to-x", "420", "-scale-to-y", "-1",
                        "-singlefile", str(src), str(base)], capture_output=True, text=True, timeout=120)
    made = Path(str(base) + ".png")
    if p.returncode or not made.is_file():
        return None
    made.replace(target)
    return target


def needs(slug: str) -> list[str] | None:
    """What a dropped source still needs before it can run (none today), or None when `slug` is no dropped source."""
    p = folder() / f"{slug}.yaml"
    if not p.is_file():
        return None
    prof = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return needs_of(prof) if prof.get("dropped") else None

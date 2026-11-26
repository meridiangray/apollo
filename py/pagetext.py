"""pagetext.py: one page of a source's text, for the window's Text tab.

    page(source, n)   -> {ok, source, page, total, kind: pages | parts, text, layer: text | ocr | proofread | none}

A PDF's page comes from `pdftotext` in reading order (one page at a time); a text source (a dropped TXT, MD, HTML or DOCX, already
turned into text) is cut into parts by the page flow's own split over hydra's text (`source_pages`), and a part stands for a page. Each source's text is cached
under state/pagetext/<source>/, keyed by the file's size and time, so a changed file is read again. A page with no text layer
that OCR has read (state/tools/<source>/ocr.json) shows the OCR text, `layer` ocr.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import runfiles

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def _profile(source: str) -> dict:
    import yaml
    import sourcedrop
    for d in (runfiles.PAGEFLOW / "sources", sourcedrop.folder()):
        f = d / f"{source}.yaml"
        if f.is_file():
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    raise ValueError(f"no source {source!r}")


def _file(prof: dict) -> Path:
    import sourcedrop
    rel = prof.get("file")
    if not rel:
        raise ValueError("the source names no file")
    p = sourcedrop.native(str(rel)) if str(rel).startswith("/mnt/") else Path(rel)
    return p if p.is_absolute() else runfiles.TESTING / p


def _stamp(f: Path) -> str:
    st = f.stat()
    return f"{st.st_size}:{st.st_mtime_ns}:hydra-text"       # parts are cut from hydra's text; older caches cut the raw file


def _pdf_total(pdf: Path) -> int | None:
    exe = os.environ.get("APOLLO_PDFINFO") or shutil.which("pdfinfo") or "pdfinfo"
    try:
        out = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def _pdf_page(pdf: Path, n: int) -> str:
    exe = os.environ.get("APOLLO_PDFTOTEXT") or shutil.which("pdftotext") or "pdftotext"
    p = subprocess.run([exe, "-f", str(n), "-l", str(n), str(pdf), "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if p.returncode != 0:
        raise ValueError((p.stderr.strip().splitlines() or [f"pdftotext exited {p.returncode}"])[-1][:200])
    return p.stdout.replace("\f", "").strip("\n")


def source_pages(source: str, archive=None, pages=None) -> dict[int, str]:
    """{page: text} of `source` exactly as the page-flow driver sees it: hydra's own text of the file, split by
    pageflow/triage.py `split_pages` (a PDF's pages, else parts of the source's `part_chars`). `pages`: the page numbers
    wanted (any iterable of ints), default all; a page the source lacks is left out. `archive`: the archive a run works
    in; the text is the source file's, the same in every archive."""
    import triage                                            # pageflow/triage.py (runfiles puts pageflow on sys.path)
    from hydra_mcp import files
    prof = _profile(source)
    f = _file(prof)
    if not f.is_file():
        raise ValueError(f"the source's file is missing: {f.name}")
    got, _ = triage.split_pages(files.read(f), prof.get("part_chars"))
    return got if pages is None else {n: got[n] for n in pages if n in got}


def _ocr_text(source: str, n: int) -> str | None:
    try:
        rec = json.loads((state_dir() / "tools" / source / "ocr.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for p in rec.get("pages") or []:
        if p.get("page") == n and str(p.get("text") or "").strip():
            return p["text"]
    return None


def page(source: str, n: int, raw: bool = False) -> dict:
    """`raw`: the page as the file gives it (text layer, else OCR), whatever was proofread: what Proofread itself starts from."""
    if not SLUG.fullmatch(source or ""):
        return {"ok": False, "why": "a source name"}
    if n < 1:
        return {"ok": False, "why": "a page number from 1"}
    try:
        prof = _profile(source)
        f = _file(prof)
        if not f.is_file():
            return {"ok": False, "why": f"the source's file is missing: {f.name}"}
        cache = state_dir() / "pagetext" / source
        meta_f = cache / "meta.json"
        try:
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if meta.get("stamp") != _stamp(f):
            shutil.rmtree(cache, ignore_errors=True)
            meta = {"stamp": _stamp(f)}
        kind = "pages" if f.suffix.lower() == ".pdf" else "parts"
        cache.mkdir(parents=True, exist_ok=True)
        if kind == "pages":
            if "total" not in meta:
                meta["total"] = _pdf_total(f)
            total = meta["total"]
            if total and n > total:
                return {"ok": False, "why": f"the source has {total} pages"}
            cf = cache / f"{n}.txt"
            if cf.is_file():
                text = cf.read_text(encoding="utf-8")
            else:
                text = _pdf_page(f, n)
                cf.write_text(text, encoding="utf-8", newline="\n")
        else:
            pf = cache / "parts.json"
            if pf.is_file():
                parts = json.loads(pf.read_text(encoding="utf-8"))
            else:
                got = source_pages(source)
                parts = [got[k] for k in sorted(got)]
                pf.write_text(json.dumps(parts, ensure_ascii=False), encoding="utf-8")
            total = len(parts)
            if n > total:
                return {"ok": False, "why": f"the source has {total} pages"}
            text = parts[n - 1]
        meta_f.write_text(json.dumps(meta), encoding="utf-8")
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "why": str(e)}
    layer = "text" if text.strip() else "none"
    if layer == "none" and kind == "pages":
        ocr = _ocr_text(source, n)
        if ocr:
            text, layer = ocr, "ocr"
    out = {"ok": True, "source": source, "page": n, "total": total, "kind": kind, "text": text, "layer": layer}
    if kind == "pages" and not raw:                          # an accepted Proofread comes before OCR and the text layer
        import proofread
        got = proofread.accepted(source, n)
        if got and str(got.get("text") or "").strip():
            out.update({"text": got["text"], "layer": "proofread", "was": layer,
                        "proofread": {k: got.get(k) for k in ("model", "at", "changes", "calls", "cost_usd")}})
    return out

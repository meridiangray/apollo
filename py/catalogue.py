"""catalogue.py: an agent reads a source's first pages and writes a library catalogue record of it (MARC 21 fields, each citing
the page it was read from), for the Sources card.

    begin(source, model)        start a job in its own process; returns at once
    get(source)                 the job, the record waiting for Accept, the accepted record and its check against Apollo's profile
    act(source, action)         accept | reject the waiting record, or revert the accepted one
    export(source, fmt)         the accepted record as MARCXML or CSL-JSON: {ok, filename, mime, text}
    run(source, model)          the job's own process: render the pages, ask, store the record

Files, all under state/catalogue/ (the source and the page flow's profiles are never written):

    <slug>.json            the accepted record: {source, record, pages, model, at, accepted, calls, cost_usd}
    <slug>.pending.json    the record waiting for Accept or Reject
    <slug>.job.json        the running or last job
    <slug>/f<N>.png       the front pages as images (pdftoppm), kept for the next try

The call is the page flow's own `Models.ask(..., image=...)` (`claude -p`, no tools). The driver's call takes one image: this module
hands it a list of page images joined in one string and gives its module the matching `image_message`. Every call lands in the
DuckDB log (`model_calls`, origin catalogue; `tool_runs`, tool catalogue). Subjects and names are proposals; nothing leaves Apollo.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import proofread
import runfiles
import toolargs

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
MAX_PAGES = 4
TEXT_CHARS = 6000
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
DEFAULT_MODEL = "haiku"

# (key, tag, name): the record's fields, in MARC 21 order as a library shows them
FIELDS = [("title", "245", "Title"), ("author", "110/100", "Author"), ("imprint", "264", "Imprint"), ("extent", "300", "Extent"),
          ("note", "500", "Note"), ("access", "506", "Classification"), ("declass", "506/540", "Declassified"), ("identifier", "024/035", "Identifier"), ("language", "546", "Language"),
          ("summary", "520", "Summary"), ("subject", "600/610/650/651", "Subjects")]
KINDS = {"title": ("title", "responsibility"), "author": ("corporate", "personal"), "imprint": ("place", "issuer", "date"),
         "declass": ("authority", "date", "iscap", "document"), "subject": ("600", "610", "650", "651")}
KEYS = [f[0] for f in FIELDS]
# tool args: the "Fields" choice, as the (key, kind) pairs it keeps; a kind of None keeps every kind of that key
GROUPS = {"all": None, "title": [("title", None), ("author", None)], "dates": [("imprint", "date")], "identifiers": [("identifier", None)],
          "publisher": [("imprint", "place"), ("imprint", "issuer")], "subjects": [("subject", None)], "notes": [("note", None), ("access", None), ("declass", None)]}

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["fields"],
          "properties": {"fields": {"type": "array", "maxItems": 40, "items": {
              "type": "object", "additionalProperties": False, "required": ["key", "value", "page"],
              "properties": {"key": {"type": "string", "enum": KEYS}, "kind": {"type": "string"},
                             "value": {"type": "string"}, "page": {"type": "integer"}}}}}}

PROMPT = """You are cataloguing a document: writing the library catalogue record (MARC 21) of it from its first pages. {what}

Give one item for each thing the pages state. Each item has: key, kind (where the key has kinds), value, page (the number of the page it was read from, as labelled {where}).
Keys:
- title (245): kind "title" (title proper and subtitle, as printed) and kind "responsibility" (the statement of responsibility, as printed)
- author (110/100): kind "corporate" (a body named as the author) or "personal"
- imprint (264): kind "place", "issuer" (the issuing body or publisher) and "date" (as printed)
- extent (300): the number of pages or parts the document says it has, e.g. "165 pages"
- note (500): general notes printed on the pages, e.g. a declassification stamp, a copy number, a date of release
- access (506): the classification marking as stamped, with its handling caveat, e.g. "Top Secret Umbra"
- declass (540): the release facts, often in a stamp on page 1. kind "authority" (the declassifying authority in short form, e.g. "Interagency Security Classification Appeals Panel, E.O. 13526, section 5.3(b)(3)"), "date" (the declassification or release date, as printed), "iscap" (the ISCAP case number only, e.g. "2007-010") and "document" (the document number within the case, e.g. "1"). The MORI DocID is an identifier, not a declass item. Give a declass item only for what a stamp or page states
- identifier (024/035): kind is the identifier's type as printed (for example "MORI DocID", "ISBN", "report number"), value is the number only
- language (546): the language of the text
- summary (520): ONE paragraph, in your own words, of what the document is and covers, from what these pages show
- subject: kind "600" (a person), "610" (a body), "650" (a topic) or "651" (a place), value the name as printed. These are proposals for a cataloguer; a name on a page does not identify anyone.

Rules:
- Use only what the pages show. If the pages do not state something, leave it out: no item, no guess, no value from memory.
- Copy values as printed in spelling and numbers, but never in ALL CAPITALS: a value printed in capitals is written in normal case. Titles, names of people, bodies and places in title case ("The History of the Standard Oil Company", "Ida M. Tarbell", "Central Intelligence Agency"); notes and statements in sentence case; acronyms stay as printed (CIA, NFOIO, ISCAP); classification markings as words ("Top Secret Umbra"). Only the summary is in your words.
- Every item names the page it was read from.
- Do not wrap values in quotes or code marks.
{extra}{text}"""


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def folder() -> Path:
    return state_dir() / "catalogue"


def _slug(source: str) -> str:
    if not SLUG.fullmatch(source or ""):
        raise ValueError("a source name")
    return source


def model_key(model: str | None) -> str:
    key = str(model or DEFAULT_MODEL).lower()
    for k in MODELS:
        if k in key:
            return k
    raise ValueError("model: haiku, sonnet or opus")


def model_id(key: str) -> str:
    """The model's id: the page flow's recipe names Haiku and Sonnet; Opus is the constant."""
    try:
        import yaml
        rec = yaml.safe_load((runfiles.PAGEFLOW / "recipes" / "full.yaml").read_text(encoding="utf-8")) or {}
        got = (rec.get("models") or {}).get(key)
        if got:
            return str(got)
    except Exception:                                    # noqa: BLE001 - the constant stands
        pass
    return MODELS[key]


# ---- files -------------------------------------------------------------------------------------------------------------

def _read(path: Path) -> dict | None:
    got = runfiles.read_json(path)
    return got if isinstance(got, dict) else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def marks() -> dict[str, str]:
    """Each catalogued source's state, from the files: `pending` (a record waits for Accept) or `accepted`. Others are not listed."""
    out: dict[str, str] = {}
    for f in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        if f.name.endswith((".job.json", ".pending.json")):
            continue
        out[f.stem] = "accepted"
    for f in folder().glob("*.pending.json") if folder().is_dir() else []:
        out[f.name[:-len(".pending.json")]] = "pending"
    return out


def accepted(source: str) -> dict | None:
    return _read(folder() / f"{_slug(source)}.json")


def pending(source: str) -> dict | None:
    return _read(folder() / f"{_slug(source)}.pending.json")


def _job(source: str) -> dict | None:
    job = _read(folder() / f"{_slug(source)}.job.json")
    if job and job.get("status") == "running":
        young = not job.get("pid") and time.time() - (job.get("started") or 0) < 15
        if not young and not proofread._pid_alive(job.get("pid")):
            job = {**job, "status": "failed", "error": job.get("error") or "the job's process ended without a result"}
    return job


# ---- the source ----------------------------------------------------------------------------------------------------------

def _profile(source: str) -> dict:
    import pagetext
    return pagetext._profile(source)


def is_pdf(source: str) -> bool:
    import tools
    return tools.source_file(source).suffix.lower() == ".pdf"


def available(pdf: bool) -> dict:
    exe = proofread.claude_path()
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)) and not shutil.which(exe):
        return {"ok": False, "why": "claude is not installed in WSL"}
    if pdf and not (os.environ.get("APOLLO_PDFTOPPM") or shutil.which("pdftoppm")):
        return {"ok": False, "why": "pdftoppm is not installed in WSL"}
    return {"ok": True, "why": ""}


def front_images(source: str, pdf: Path, pages: str = "1-4", limit: int = MAX_PAGES, dpi: int = 110) -> list[tuple[int, Path]]:
    """The pages asked for (the first ones by default) as images, [(page, png)], at most `limit`, under state/catalogue/<source>/."""
    import pagetext
    total = pagetext._pdf_total(pdf) or max(limit, 4)
    exe = os.environ.get("APOLLO_PDFTOPPM") or shutil.which("pdftoppm") or "pdftoppm"
    out = []
    for n in (toolargs.pages(pages, total) or list(range(1, total + 1)))[:limit]:
        png = folder() / source / (f"f{n}.png" if dpi == 110 else f"f{n}-{dpi}.png")
        if not (png.is_file() and png.stat().st_mtime >= pdf.stat().st_mtime):
            png.parent.mkdir(parents=True, exist_ok=True)
            p = subprocess.run([exe, "-r", str(dpi), "-f", str(n), "-l", str(n), "-png", "-singlefile", str(pdf), str(png.with_suffix(""))],
                               capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
            if p.returncode != 0 or not png.is_file():
                raise ValueError("pdftoppm: " + ((p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1][:200]))
        out.append((n, png))
    if not out:
        raise ValueError("those pages are not in the document")
    return out


def opening_text(source: str, pages: str = "1-4", limit: int = MAX_PAGES) -> tuple[str, list[int]]:
    """The parts asked for (the first ones by default) of a text source, up to TEXT_CHARS: (text with each part labelled, the numbers)."""
    import pagetext
    text, nums = "", []
    want = None if "end" in pages else toolargs.pages(pages)
    for n in (want or list(range(1, limit + 1)))[:limit]:
        if len(text) >= TEXT_CHARS:
            break
        got = pagetext.page(source, n, raw=True)
        if not got.get("ok"):
            break
        text += f"\n[Part {n}]\n{got.get('text') or ''}\n"
        nums.append(n)
        if n >= (got.get("total") or 1):
            break
    return text[:TEXT_CHARS + 200], nums


# ---- asking --------------------------------------------------------------------------------------------------------------

def _multi_message(images: str, prompt: str) -> str:
    """The driver's `image_message` for several pages: `images` is "N=path|N=path"; each image follows its label."""
    content = []
    for part in images.split("|"):
        n, path = part.split("=", 1)
        content.append({"type": "text", "text": f"Page {n}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.b64encode(Path(path).read_bytes()).decode("ascii")}})
    content.append({"type": "text", "text": prompt})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


def ask_model(prompt: str, key: str, images: list[tuple[int, Path]], label: str, schema: dict | None = None) -> tuple[dict, dict]:
    """(answer, row): the model's structured answer and the call's log row (ms, tokens, cost_usd, model)."""
    mod = proofread._models_module()
    mod.image_message = _multi_message                   # this copy of the driver module only
    rows: list[dict] = []
    mid = model_id(key)
    m = mod.Models({"agent": mid}, {"agent": 1}, claude=proofread.claude_path(), timeout=300, log=rows.append)
    t = time.time()
    arg = "|".join(f"{n}={p}" for n, p in images) if images else None
    try:
        ans = m.ask("agent", prompt, schema or SCHEMA, label, image=arg)
    except Exception as e:                               # noqa: BLE001 - the row still says what happened
        row = rows[-1] if rows else {"ms": int((time.time() - t) * 1000), "ok": False, "why": str(e)[:300]}
        e.row = {**row, "model": mid, "label": label}
        raise
    row = rows[-1] if rows else {"ms": int((time.time() - t) * 1000), "ok": True}
    return ans, {**row, "model": mid, "label": label, "cost_usd": m.tokens["agent"]["cost_usd"]}


def clean(ans: dict, sent: list[int], fields: str = "all") -> list[dict]:
    """The answer's items that are usable: a known key (in the fields asked for), a value, a page that was sent. Nothing else is kept."""
    out, seen = [], set()
    keep = GROUPS[fields]
    for it in ans.get("fields") or []:
        key = str(it.get("key") or "")
        value = " ".join(str(it.get("value") or "").split())
        kind = str(it.get("kind") or "").strip()
        page = it.get("page")
        if key not in KEYS or not value or page not in sent:
            continue
        if keep is not None and not any(key == k and (kd is None or kd == kind) for k, kd in keep):
            continue
        if key in KINDS and kind not in KINDS[key]:
            kind = ""
        if key not in ("identifier", "title", "author", "imprint", "subject", "declass"):
            kind = ""
        sig = (key, kind, value.lower())
        if sig in seen:
            continue
        seen.add(sig)
        out.append({"key": key, "kind": kind, "value": value[:1500], "page": page})
    return sorted(out, key=_order)


def _order(x: dict) -> tuple:
    """The record's reading order: the fields as FIELDS lists them, a release's facts in their own order."""
    return (KEYS.index(x["key"]), (KINDS["declass"] + ("",)).index(x["kind"]) if x["key"] == "declass" and x["kind"] in KINDS["declass"] + ("",) else 0)


def _log(source: str, row: dict, prompt: str) -> None:
    try:
        import runlog
        runlog.log_model_call(source, row, prompt, origin="catalogue")
    except Exception:                                    # noqa: BLE001 - the log is a copy
        pass


def _extra(a: dict) -> str:
    """The prompt's added lines: the fields asked for, and the cataloguer's note."""
    out = ""
    if a["fields"] != "all":
        keys = ", ".join(sorted({k for k, _ in GROUPS[a["fields"]]}))
        out += f"Give only these keys: {keys}.\n"
        if a["fields"] == "dates":
            out += 'For imprint give only kind "date".\n'
        elif a["fields"] == "publisher":
            out += 'For imprint give only kinds "place" and "issuer".\n'
    if a["note"]:
        out += f"Note from the cataloguer: {a['note']}\n"
    return out


def catalogue_source(source: str, key: str, args: dict | None = None) -> dict:
    """Ask once; write and return the pending record. Raises ValueError with a sentence."""
    import tools
    a = toolargs.clean("catalogue", args)
    f = tools.source_file(source)
    if f.suffix.lower() == ".pdf":
        if not f.is_file():
            raise ValueError(f"the source's file is missing: {f.name}")
        images = front_images(source, f, a["pages"], a["max_pages"], a["dpi"])
        sent = [n for n, _ in images]
        what, where, text = f"{len(sent)} page image{'s' if len(sent) != 1 else ''} of the PDF {'is' if len(sent) == 1 else 'are'} attached.", "above each image (the PDF's own page numbers)", ""
        kind = "pdf"
    else:
        body, sent = opening_text(source, a["pages"], a["max_pages"])
        if not body.strip():
            raise ValueError("the source has no text to read")
        images, kind = [], "text"
        what, where = "The opening text of the document follows.", "[Part N]"
        text = f"\nOpening text:\n<<<{body}\n>>>"
    prompt = PROMPT.format(what=what, where=where, extra=_extra(a), text=text)
    label = f"catalogue {source}"
    try:
        ans, row = ask_model(prompt, key, images, label)
    except Exception as e:                               # noqa: BLE001
        _log(source, getattr(e, "row", {"label": label, "ok": False, "why": str(e)[:300], "model": model_id(key)}), prompt)
        raise ValueError(f"the model gave no answer: {str(e)[:200]}") from None
    items = clean(ans, sent, a["fields"])
    _log(source, {**row, "answer": json.dumps(ans, ensure_ascii=False), "ok": True}, prompt)
    if not items:
        raise ValueError("the pages gave no catalogue fields")
    rec = {"source": source, "kind": kind, "record": items, "pages": sent, "model": row.get("model"), "tier": key, "args": a,
           "at": datetime.now().isoformat(timespec="seconds"), "calls": 1, "cost_usd": row.get("cost_usd"), "ms": row.get("ms")}
    _write(folder() / f"{source}.pending.json", rec)
    return rec


BRIEF_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["brief"], "properties": {"brief": {"type": "string"}}}
BRIEF_PROMPT = """You are writing the working brief for a document that will be read page by page to extract facts. {what}

Write 8 to 15 plain lines (no headings, no bullets, no filler) that say: what the document is; its period and places; the main people and bodies; what to extract from it; and the pitfalls to expect (OCR noise, redactions, page headers and footers, stamps, anything the pages show). Use only what the pages and the catalogue record show; leave out what they do not state.
{record}{text}"""


def brief_draft(slug: str, model: str = "claude-haiku-5-5") -> str:
    """A draft brief for the source, written by a model from its first pages and its catalogue record: 8 to 15 plain lines. Nothing is
    saved; the owner edits it and saves. Raises ValueError with a sentence."""
    _slug(slug)
    key = model_key(model)
    body, _ = opening_text(slug, "1-4", MAX_PAGES)
    images: list[tuple[int, Path]] = []
    if not body.strip():
        import tools
        f = tools.source_file(slug)
        if f.suffix.lower() != ".pdf" or not f.is_file():
            raise ValueError("the source has no text to read")
        images = front_images(slug, f)
    rec = (pending(slug) or accepted(slug) or {}).get("record") or []
    lines = [f"{i['key']}{' ' + i['kind'] if i.get('kind') else ''}: {i['value']}" for i in rec if not i.get("rejected")]
    prompt = BRIEF_PROMPT.format(
        what=f"{len(images)} page images of it are attached." if images else "The opening text of the document follows.",
        record=("\nCatalogue record:\n" + "\n".join(lines)[:3000] + "\n") if lines else "",
        text=f"\nOpening text:\n<<<{body}\n>>>" if body.strip() else "")
    label = f"brief {slug}"
    try:
        ans, row = ask_model(prompt, key, images, label, BRIEF_SCHEMA)
    except Exception as e:                               # noqa: BLE001
        _log(slug, getattr(e, "row", {"label": label, "ok": False, "why": str(e)[:300], "model": model_id(key)}), prompt)
        raise ValueError(f"the model gave no answer: {str(e)[:200]}") from None
    _log(slug, {**row, "answer": json.dumps(ans, ensure_ascii=False), "ok": True}, prompt)
    brief = "\n".join(ln.rstrip() for ln in str(ans.get("brief") or "").strip().splitlines() if ln.strip())
    if not brief:
        raise ValueError("the model gave no brief")
    return brief


# ---- the job ---------------------------------------------------------------------------------------------------------------

def begin(source: str, model: str | None = None, args: dict | None = None) -> dict:
    _slug(source)
    a = toolargs.clean("catalogue", {**(args or {}), **({"agent": model_key(model)} if model else {})})   # tool args
    key = a["agent"]
    pdf = is_pdf(source)
    av = available(pdf)
    if not av["ok"]:
        raise ValueError(av["why"])
    old = _job(source)
    if old and old.get("status") == "running":
        return {"ok": True, "started": False, "source": source}
    job = {"status": "running", "source": source, "model": model_id(key), "tier": key, "args": a, "started": time.time(), "pid": None, "error": None}
    path = folder() / f"{source}.job.json"
    _write(path, job)
    log = open(folder() / f"{source}.job.log", "ab")
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve().with_name("apollo.py")), "cataloguerun", "--source", source, "--model", key, "--args", json.dumps(a)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    log.close()
    return {"ok": True, "started": True, "source": source, "pid": p.pid}


def run(source: str, model: str | None = None, args: dict | None = None) -> int:
    key = model_key(model)
    path = folder() / f"{source}.job.json"
    job = _read(path) or {"source": source, "model": model_id(key), "tier": key, "started": time.time()}
    job.update({"status": "running", "pid": os.getpid()})
    _write(path, job)
    rec = None
    try:
        rec = catalogue_source(source, key, args)
        job.update({"status": "done", "ended": time.time(), "error": None})
    except ValueError as e:
        job.update({"status": "failed", "ended": time.time(), "error": str(e)[:300]})
    except Exception as e:                               # noqa: BLE001 - the file says why
        job.update({"status": "failed", "ended": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
    _write(path, job)
    try:
        import runlog
        runlog.log_tool_run("catalogue", source, {
            "status": job["status"], "started": job.get("started"), "ended": job.get("ended"), "done": 1 if rec else 0, "total": 1,
            "error": job.get("error"), "source": source, "pages_arg": "-".join(map(str, (rec["pages"][0], rec["pages"][-1]))) if rec and rec["pages"] else None,
            "model": job.get("model"), "calls": 1, "cost_usd": (rec or {}).get("cost_usd")})
    except Exception:                                    # noqa: BLE001
        pass
    return 0 if job["status"] == "done" else 1


# ---- the check ---------------------------------------------------------------------------------------------------------------

def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").casefold()).strip()


def _first(items: list[dict], key: str, kinds: tuple = ()) -> dict | None:
    return next((i for i in items if i["key"] == key and (not kinds or i["kind"] in kinds)), None)


LANGS = {"en": "english", "ar": "arabic", "fr": "french", "de": "german", "es": "spanish", "it": "italian", "ru": "russian",
         "fa": "persian", "tr": "turkish", "ur": "urdu", "he": "hebrew", "zh": "chinese", "ja": "japanese", "pt": "portuguese"}


def held(source: str) -> dict:
    """What Apollo holds of the source: title, publisher, pages, language (the profile's own, nothing is written)."""
    import tools
    prof = _profile(source)
    d = prof.get("dropped") or {}
    total = len(d["page_chars"]) if d.get("page_chars") else d.get("parts")
    if total is None:
        try:
            import pagetext
            f = tools.source_file(source)
            total = pagetext._pdf_total(f) if f.suffix.lower() == ".pdf" else None
        except (ValueError, OSError):
            total = None
    lang = prof.get("language") or d.get("language")
    return {"title": prof.get("title"), "publisher": prof.get("publisher"), "pages": total,
            "language": LANGS.get(str(lang).lower(), lang) if lang else None}


def check(source: str, items: list[dict]) -> list[dict]:
    """The record against what Apollo holds: [{field, held, record, differs}], for each field both can give (or only one)."""
    h = held(source)
    title = _first(items, "title", ("title",)) or _first(items, "title")
    issuer = _first(items, "imprint", ("issuer",)) or _first(items, "author", ("corporate",)) or _first(items, "author")
    ext = _first(items, "extent")
    lang = _first(items, "language")
    date = _first(items, "imprint", ("date",))
    n = re.search(r"\d[\d,]*", ext["value"]) if ext else None
    rec_pages = int(n.group(0).replace(",", "")) if n else None
    rows = [("Title", h["title"], title and title["value"], lambda a, b: _norm(a) in _norm(b) or _norm(b) in _norm(a)),
            ("Publisher", h["publisher"], issuer and issuer["value"], lambda a, b: _norm(a) in _norm(b) or _norm(b) in _norm(a)),
            ("Pages", h["pages"], rec_pages, lambda a, b: int(a) == int(b)),
            ("Language", h["language"], lang and lang["value"], lambda a, b: _norm(a) in _norm(b) or _norm(b) in _norm(a)),
            ("Date", None, date and date["value"], None)]
    out = []
    for name, have, got, same in rows:
        if have in (None, "") and got in (None, ""):
            continue
        differs = bool(have not in (None, "") and got not in (None, "") and same and not same(have, got))
        out.append({"field": name, "held": have if have not in ("",) else None, "record": got, "differs": differs})
    return out


# ---- the basic record: what the pages state, read without a model ------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\b"
_DATES = [re.compile(p, re.I) for p in (r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b", rf"\b(\d{{1,2}})\s+({_MON})\.?,?\s+(\d{{4}})\b",
                                         rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")]
_PRINTED = re.compile(rf"(?:\b\d{{1,2}}\s+)?\b{_MON}\.?,?\s+(?:\d{{1,2}},?\s+)?\d{{4}}\b", re.I)
_STAMP = re.compile(r"approved\s+for\s+release|release\s+date|date\s+of\s+release|date\s+released|declassified\s+(?:on|in\s+part)|released\s+on", re.I)
_MARK = re.compile(r"(?:TOP SECRET|SECRET|CONFIDENTIAL|RESTRICTED|UNCLASSIFIED|(?:FOR )?OFFICIAL USE ONLY)(?:[ /,-]+[A-Z][A-Z0-9]*){0,5}\.?")
_IDENT = re.compile(r"\b(ISBN(?:-1[03])?|MORI\s+Doc\s*I?D|Doc[il]D|Report\s+(?:No\.?|Number)|Document\s+(?:No\.?|Number))\s*[:#]?\s*(\d[0-9A-Za-z\-/.]{3,})", re.I)
_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with", "from"}
_STOP = {"en": "the and of to in is that was for with as by on at this from it be are".split(), "fr": "le la les des et est une dans que pour par sur au aux du ce qui".split(),
         "de": "der die das und ist nicht mit von den zu ein eine auf dem sich".split(), "es": "el la los las de y que en un una por con para es del se".split()}
BASIC_TIER = "basic"


def _day(y: int, m: int, d: int) -> str | None:
    try:
        return datetime(y, m, d).date().isoformat()
    except ValueError:
        return None


def _iso(text: str) -> str | None:
    """The first date in the text as YYYY-MM-DD (2012/03/15, 15 Mar 2012, March 15, 2012 and 3/15/2012 are read), else None."""
    best = None
    for i, rx in enumerate(_DATES):
        for m in rx.finditer(text):
            g = m.groups()
            if i == 0:
                got = _day(int(g[0]), int(g[1]), int(g[2]))
            elif i == 1:
                got = _day(int(g[2]), MONTHS[g[1][:3].lower()], int(g[0]))
            elif i == 2:
                got = _day(int(g[2]), MONTHS[g[0][:3].lower()], int(g[1]))
            else:
                got = _day(int(g[2]), int(g[0]), int(g[1]))
            if got:
                if best is None or m.start() < best[0]:
                    best = (m.start(), got)
                break
    return best[1] if best else None


def release_date(pages: list[str]) -> tuple[str, int] | None:
    """The release date the pages' stamps and footers give ("Approved for Release 2012/03/15", "Release Date: ..."): the date the
    most pages carry wins (a tie goes to the first seen). (YYYY-MM-DD, the first page it is on), or None."""
    seen: dict[str, list[int]] = {}
    for n, text in enumerate(pages, 1):
        for d in {_iso(text[m.end():m.end() + 80]) for m in _STAMP.finditer(text)} - {None}:
            seen.setdefault(d, []).append(n)
    if not seen:
        return None
    day = max(seen, key=lambda d: (len(seen[d]), -seen[d][0]))
    return day, seen[day][0]


def decap(text: str) -> str:
    """Capitals read in normal case ("THE HISTORY OF THE STANDARD OIL COMPANY" is "The History of the Standard Oil Company"); text
    that has lower case already is left alone."""
    text = " ".join(str(text or "").split())
    if not text or any(c.islower() for c in text):
        return text
    return " ".join(w if i and w in _SMALL else w[:1].upper() + w[1:] for i, w in enumerate(text.lower().split(" ")))


def _pdf_pages(pdf: Path, limit: int = 400) -> list[str]:
    """Every page's text layer from one pdftotext call (pages split at the form feed); [] when it cannot be read."""
    exe = os.environ.get("APOLLO_PDFTOTEXT") or shutil.which("pdftotext") or "pdftotext"
    try:
        p = subprocess.run([exe, "-l", str(limit), str(pdf), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    out = p.stdout.split("\f")
    return out[:-1] if len(out) > 1 and not out[-1].strip() else out


def _text_pages(source: str, limit: int = 60) -> list[str]:
    import pagetext
    out = []
    for n in range(1, limit + 1):
        got = pagetext.page(source, n, raw=True)
        if not got.get("ok"):
            break
        out.append(got.get("text") or "")
        if n >= (got.get("total") or 1):
            break
    return out


def _guess_language(text: str) -> str | None:
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    if len(words) < 80:
        return None
    score = sorted(((sum(w in set(sw) for w in words), k) for k, sw in _STOP.items()), reverse=True)
    return LANGS[score[0][1]].capitalize() if score[0][0] >= 15 and score[0][0] >= 2 * score[1][0] else None


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _title_and_author(first: str, held_title: str | None) -> tuple[str | None, str | None]:
    """The title as the first page prints it (normal case) and the author's name only, from a "By ..." line."""
    lines = _lines(first)[:25]
    by = next((i for i, ln in enumerate(lines) if re.match(r"by\s+\S", ln, re.I)), None)
    author = None
    if by is not None:
        who = re.split(r",|;|\bauthor of\b|\beditor\b|\btranslat|\bwith\b|\billustrated\b|\bedited\b", lines[by][2:].strip(), maxsplit=1, flags=re.I)[0]
        who = " ".join(who.split()).strip(" .")
        if 2 <= len(who) <= 60 and not re.search(r"\d", who) and len(who.split()) <= 6:
            author = decap(who)
    title = next((ln for ln in lines if held_title and _norm(ln) == _norm(held_title)), None)
    if title is None and by:
        prev = [ln for ln in lines[max(0, by - 2):by] if len(ln) <= 120]
        if len(prev) == 1 or (prev and all(ln == ln.upper() for ln in prev)):
            title = " ".join(prev)
    return decap(title or held_title or "") or None, author


def basic(source: str) -> dict:
    """The essentials read without a model, written as the waiting record (pending) the way the model's catalogue writes it: title,
    author when a "By ..." line names one, publisher, date, extent, classification marking, release date, labelled identifiers and
    language. No network, no model; safe to call twice. An accepted record, a record the model proposed, a running job, and any
    field the owner edited or rejected are never overwritten. Returns {ok, source, written, why, record}."""
    _slug(source)
    acc, old, job = accepted(source), pending(source), _job(source)
    running = bool(job and job.get("status") == "running")
    if acc or running or (old and old.get("tier") != BASIC_TIER):
        return {"ok": True, "source": source, "written": False, "why": "accepted" if acc else "busy" if running else "waiting",
                "record": (acc or old or {}).get("record") or []}
    import tools
    f = tools.source_file(source)
    pdf = f.suffix.lower() == ".pdf"
    if pdf and not f.is_file():
        raise ValueError(f"the source's file is missing: {f.name}")
    pages = _pdf_pages(f) if pdf else _text_pages(source)
    h = held(source)
    items: list[dict] = []

    def add(key: str, kind: str, value, page) -> None:
        value = " ".join(str(value or "").split())
        if value:
            items.append({"key": key, "kind": kind, "value": value[:1500], "page": page})
    title, author = _title_and_author(pages[0] if pages else "", h.get("title"))
    add("title", "title", title, 1 if pages else None)
    add("author", "personal", author, 1)
    add("imprint", "issuer", h.get("publisher"), None)
    for n, text in enumerate(pages[:3], 1):
        stamps = [m.end() for m in _STAMP.finditer(text)]
        dm = next((m for m in _PRINTED.finditer(text) if not any(0 <= m.start() - e <= 80 for e in stamps)), None)
        if dm:
            add("imprint", "date", dm.group(0), n)
            break
    total = h.get("pages") or len(pages)
    if total:
        add("extent", "", f"{total} {'pages' if pdf else 'parts'}", None)
    for n, text in enumerate(pages[:3], 1):
        mk = next((ln for ln in _lines(text) if len(ln) <= 60 and _MARK.fullmatch(ln)), None)
        if mk:
            add("access", "", mk, n)
            break
    rel = release_date(pages)
    if rel:
        add("declass", "date", rel[0], rel[1])
    seen = set()
    for n, text in enumerate(pages[:3], 1):
        for m in _IDENT.finditer(text):
            kind = "MORI DocID" if re.match(r"mori|doc[il]d", m.group(1), re.I) else " ".join(m.group(1).split()).title().replace("Isbn", "ISBN")
            if (kind, m.group(2)) not in seen:
                seen.add((kind, m.group(2)))
                add("identifier", kind, m.group(2), n)
    add("language", "", h.get("language") or _guess_language("\n".join(pages[:6])), None)
    keep = [i for i in (old or {}).get("record") or [] if i.get("edited") or i.get("rejected")]     # the owner's choices stay as they are
    items = sorted([i for i in items if not any(k["key"] == i["key"] and k["kind"] == i["kind"] for k in keep)] + keep, key=_order)
    if not items:
        raise ValueError("the source gave no basic fields")
    rec = {"source": source, "kind": "pdf" if pdf else "text", "record": items, "pages": [], "model": None, "tier": BASIC_TIER, "args": {},
           "at": datetime.now().isoformat(timespec="seconds"), "calls": 0, "cost_usd": 0, "ms": 0}
    _write(folder() / f"{source}.pending.json", rec)
    return {"ok": True, "source": source, "written": True, "why": "", "record": items}


# ---- reading and deciding ------------------------------------------------------------------------------------------------------

def get(source: str) -> dict:
    """{ok, available, why, pdf, fields, job, pending, accepted, check}: the card's view of the Catalogue for a source."""
    _slug(source)
    try:
        pdf = is_pdf(source)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    av = available(pdf)
    acc, pen = accepted(source), pending(source)
    return {"ok": True, "available": av["ok"], "why": av["why"], "pdf": pdf, "max_pages": MAX_PAGES,
            "fields": [{"key": k, "tag": t, "name": n, "kinds": list(KINDS.get(k, ()))} for k, t, n in FIELDS], "job": _job(source), "pending": pen, "accepted": acc,
            "check": check(source, (pen or acc or {}).get("record") or []) if (pen or acc) else []}


def mark(source: str, index: int, rejected: bool) -> dict:
    """Mark one field of the waiting record rejected (or take the mark off). Accept then keeps only the fields not rejected."""
    _slug(source)
    p = pending(source)
    if not p:
        raise ValueError("nothing is waiting")
    items = list(p.get("record") or [])
    if not isinstance(index, int) or not 0 <= index < len(items):
        raise ValueError("no such field")
    items[index] = {k: v for k, v in items[index].items() if k != "rejected"} | ({"rejected": True} if rejected else {})
    _write(folder() / f"{source}.pending.json", {**p, "record": items})
    return {"ok": True, "source": source}


def act(source: str, action: str) -> dict:
    _slug(source)
    f = folder()
    if action == "accept":
        p = pending(source)
        if not p:
            return {"ok": False, "why": "nothing is waiting"}
        kept = [i for i in p.get("record") or [] if not i.get("rejected")]
        if not kept:
            return {"ok": False, "why": "every field is rejected"}
        _write(f / f"{source}.json", {**{k: p.get(k) for k in ("source", "kind", "record", "pages", "model", "tier", "at", "calls", "cost_usd")},
                                      "record": kept, "accepted": datetime.now().isoformat(timespec="seconds")})
        (f / f"{source}.pending.json").unlink(missing_ok=True)
    elif action == "reject":
        (f / f"{source}.pending.json").unlink(missing_ok=True)
    elif action == "revert":
        (f / f"{source}.json").unlink(missing_ok=True)
    else:
        return {"ok": False, "why": "accept, reject or revert"}
    try:
        import runlog
        now = time.time()
        runlog.log_tool_run("catalogue-act", f"{source}-{int(now * 1000)}", {"status": action, "started": now, "ended": now, "source": source})
    except Exception:                                    # noqa: BLE001
        pass
    return {"ok": True, "source": source, "action": action}


def edit(source: str, index: int, value: str, pending: bool = False) -> dict:
    """Change the text of one field of the accepted record (its place in the list), or of the record waiting for Accept
    (`pending`), or remove the field when the text is empty. An edited waiting field loses its rejection mark."""
    _slug(source)
    rec = globals()["pending"](source) if pending else accepted(source)
    if not rec:
        raise ValueError("nothing is waiting to edit" if pending else "no accepted record to edit")
    items = list(rec.get("record") or [])
    if not isinstance(index, int) or not 0 <= index < len(items):
        raise ValueError("no such field")
    text = _text(items[index]["key"], items[index].get("kind") or "", value)
    if text:
        items[index] = {**{k: v for k, v in items[index].items() if k != "rejected"}, "value": text, "edited": True}
    else:
        del items[index]
    name = f"{source}.pending.json" if pending else f"{source}.json"
    _write(folder() / name, {**rec, "record": items, "edited": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True, "source": source}


def _text(key: str, kind: str, value) -> str:
    """A field's text as the record keeps it: lines trimmed, at most 1500 characters, a date as YYYY, YYYY-MM or YYYY-MM-DD."""
    text = "\n".join(" ".join(ln.split()) for ln in str(value or "").splitlines()).strip()
    if len(text) > 1500:
        raise ValueError("at most 1500 characters")
    if text and kind == "date" and key in ("imprint", "declass"):
        m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
        try:
            if not m:
                raise ValueError
            datetime(int(m[1]), int(m[2] or 1), int(m[3] or 1))
        except ValueError:
            raise ValueError("a date is YYYY, YYYY-MM or YYYY-MM-DD") from None
    return text


def add(source: str, key: str, kind: str, value: str, pending: bool = False) -> dict:
    """Add a field (key, kind, text) to the accepted record, or to the one waiting for Accept (`pending`), marked edited as a hand edit is.
    A field of that key and kind that is already there is an error, unless it is a rejected waiting one, which the new text replaces."""
    _slug(source)
    rec = globals()["pending"](source) if pending else accepted(source)
    if not rec:
        raise ValueError("nothing is waiting to add to" if pending else "no accepted record to add to")
    kind = kind or ""
    if key not in KEYS:
        raise ValueError(f"no such field; one of {', '.join(KEYS)}")
    if key != "identifier" and kind not in KINDS.get(key, ("",)) + ("",) or len(kind) > 40:
        raise ValueError("no such kind of that field")
    text = _text(key, kind, value)
    if not text:
        raise ValueError("give the field's text")
    items = [i for i in rec.get("record") or [] if not (i["key"] == key and (i.get("kind") or "") == kind and i.get("rejected"))]
    if any(i["key"] == key and (i.get("kind") or "") == kind for i in items):
        raise ValueError("that field is already there")
    items = sorted(items + [{"key": key, "kind": kind, "value": text, "page": None, "edited": True}], key=_order)
    name = f"{source}.pending.json" if pending else f"{source}.json"
    _write(folder() / name, {**rec, "record": items, "edited": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True, "source": source}


# ---- export ----------------------------------------------------------------------------------------------------------------------

def _of(items: list[dict], key: str, *kinds: str) -> list[str]:
    return [i["value"] for i in items if i["key"] == key and (not kinds or i["kind"] in kinds)]


def marcxml(items: list[dict], source: str) -> str:
    """The record as MARCXML (one record; leader and 008 are for the cataloguer to complete)."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<record xmlns="http://www.loc.gov/MARC21/slim">',
           "  <leader>00000nam a2200000 a 4500</leader>"]

    def df(tag: str, ind1: str, ind2: str, subs: list[tuple[str, str]]) -> None:
        subs = [(c, v) for c, v in subs if v]
        if subs:
            out.append(f'  <datafield tag="{tag}" ind1="{ind1}" ind2="{ind2}">')
            out.extend(f'    <subfield code="{c}">{escape(v)}</subfield>' for c, v in subs)
            out.append("  </datafield>")
    for i in items:
        if i["key"] == "identifier":
            df("024", "8", " ", [("a", i["value"]), ("2", i["kind"] or "")])
    corp, pers = _of(items, "author", "corporate"), _of(items, "author", "personal") or _of(items, "author", "")
    if pers:
        df("100", "1", " ", [("a", pers[0])])
    elif corp:
        df("110", "2", " ", [("a", corp[0])])
    title = (_of(items, "title", "title") or _of(items, "title", ""))[:1]
    resp = _of(items, "title", "responsibility")[:1]
    if title:
        df("245", "1" if (pers or corp) else "0", "0", [("a", title[0]), ("c", resp[0] if resp else "")])
    df("264", " ", "1", [("a", (_of(items, "imprint", "place") or [""])[0]), ("b", (_of(items, "imprint", "issuer") or [""])[0]),
                         ("c", (_of(items, "imprint", "date") or [""])[0])])
    for v in _of(items, "extent")[:1]:
        df("300", " ", " ", [("a", v)])
    for v in _of(items, "note"):
        df("500", " ", " ", [("a", v)])
    for v in _of(items, "access"):
        df("506", " ", " ", [("a", v)])
    for i in items:
        if i["key"] == "declass":
            df("540", " ", " ", [("a", f"Declassified {i['kind']}: {i['value']}" if i["kind"] else f"Declassified: {i['value']}")])
    for v in _of(items, "summary")[:1]:
        df("520", " ", " ", [("a", v)])
    for v in _of(items, "language")[:1]:
        df("546", " ", " ", [("a", v)])
    for i in items:
        if i["key"] == "subject" and i["kind"] in KINDS["subject"]:
            df(i["kind"], {"600": "1", "610": "2"}.get(i["kind"], " "), "4", [("a", i["value"])])
    df("887", " ", " ", [("a", f"Catalogued by an agent from the first pages of {source}; subjects and names are proposals.")])
    out.append("</record>")
    return "\n".join(out) + "\n"


def csl(items: list[dict], source: str) -> str:
    """The record as CSL-JSON (a list of one item)."""
    title = (_of(items, "title", "title") or _of(items, "title", ""))[:1]
    resp = _of(items, "title", "responsibility")[:1]
    authors = [{"literal": v} for v in _of(items, "author")] or ([{"literal": resp[0]}] if resp else [])
    ids = [f"{i['kind']} {i['value']}".strip() for i in items if i["key"] == "identifier"]
    rel = [f"Declassified {i['kind']}: {i['value']}".replace("Declassified : ", "Declassified: ") for i in items if i["key"] == "declass"]
    notes = _of(items, "note") + _of(items, "access") + rel + ids
    ext = re.search(r"\d[\d,]*", (_of(items, "extent") or [""])[0])
    rec: dict = {"id": source, "type": "report", "title": title[0] if title else source}
    if authors:
        rec["author"] = authors
    for k, v in (("publisher", (_of(items, "imprint", "issuer") or [""])[0]), ("publisher-place", (_of(items, "imprint", "place") or [""])[0]),
                 ("abstract", (_of(items, "summary") or [""])[0]), ("language", (_of(items, "language") or [""])[0]),
                 ("note", "; ".join(notes)), ("number", ids[0] if ids else ""),
                 ("number-of-pages", ext.group(0).replace(",", "") if ext else "")):
        if v:
            rec[k] = v
    date = (_of(items, "imprint", "date") or [""])[0]
    if date:
        rec["issued"] = {"raw": date}
    return json.dumps([rec], ensure_ascii=False, indent=2) + "\n"


def export(source: str, fmt: str) -> dict:
    acc = accepted(source)
    if not acc:
        return {"ok": False, "why": "accept a record first"}
    if fmt == "marcxml":
        return {"ok": True, "filename": f"{source}.marc.xml", "mime": "application/marcxml+xml; charset=utf-8", "text": marcxml(acc["record"], source)}
    if fmt == "csl":
        return {"ok": True, "filename": f"{source}.csl.json", "mime": "application/json; charset=utf-8", "text": csl(acc["record"], source)}
    return {"ok": False, "why": "marcxml or csl"}

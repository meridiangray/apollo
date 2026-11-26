"""proofread.py: Haiku reads a page's image beside its text and writes the text again, properly. For the Text tab.

    begin(source, pages)        start a job in its own process (pages "7" or "7-9", at most 20); returns at once
    get(source, page=None)      the job, the pages that wait for a decision, the accepted pages; with a page, its pending text
    act(source, page, action)   accept | reject a pending page, or revert an accepted one
    accepted(source, n)         the accepted proofread of a page, or None (pagetext shows it before OCR and the text layer)
    run(source, pages)          the job's own process: render, ask, store a pending page for each

Files, all under state/proofread/<source>/ (the source file itself is never written):

    p<N>.json          the accepted text: {source, page, text, model, at, changes, calls, cost_usd}
    pending-<N>.json   a proposal waiting for Accept or Reject: the old text, the new, the diff and the changes
    p<N>.png           the page as an image (pdftoppm -r 150), kept for the next try
    job.json           the running or last job: {status, pages, done, total, calls, cost_usd, error, errors, pid, started, ended}

The model call is the page flow's own: pageflow/models.py `Models.ask(..., image=PNG)` (`claude -p`, stream-json, no tools).
Every call lands in the DuckDB log (`model_calls`, and one `tool_runs` row for the job).
"""
from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import runfiles

HERE = Path(__file__).resolve().parent
MAX_PAGES = 20
DEFAULT_MODEL = "claude-haiku-5-5"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
REDACTED = "[REDACTED]"          # hydra's mark for a redaction (pageflow/triage.py REDACTED_LINE, hydra's source profile)
LOST = "~"                       # hydra's mark for lost or unreadable letters (records/reading.py): one ~ for up to three

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["text", "changes"],
          "properties": {"text": {"type": "string"},
                         "changes": {"type": "array", "maxItems": 8, "items": {"type": "string"}}}}

PROMPT = f"""You are proofreading one page of a document. The page image is attached. Below is the text a machine read from \
this page (its text layer, or OCR). It may hold errors.

Write the page's text again from the image, transcribed faithfully in reading order.

- Transcribe what the page shows and nothing else. Do not summarise, explain, correct the author, modernise or add words.
- Keep the original spelling, punctuation, capitals and numbers as printed.
- Keep the page's own lines: end a line of your text exactly where the printed line ends, one printed line to one line of text.
  Never join lines into paragraphs. A blank line only where the page leaves a gap. A word broken by a hyphen at the end of a
  line stays broken the way the page prints it.
- Keep running headers, footers, page numbers and classification marks that are printed on the page.
- Use the machine text only to help you read the image. Where they disagree, the image is right.
- A redacted or blacked-out area, and a stamp like (b)(1) that stands for one: write {REDACTED} where it stands in the line. Never
  guess what is under a redaction, and if the machine text has words where the image shows a black box, replace them with {REDACTED}.
- A word or part of a word you cannot read in the image: write {LOST} for it ({LOST} stands for up to three lost letters, or for
  one whole unreadable word). Never guess.
- Do not wrap the text in quotes or code marks.

In `changes`, list in short phrases what you changed against the machine text, with counts, for example "fixed 12 OCR errors",
"2 redactions marked", "1 illegible word marked". An empty list means nothing needed changing.

Machine text of page {{page}}:
<<<
{{text}}
>>>"""


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def folder(source: str) -> Path:
    if not SLUG.fullmatch(source or ""):
        raise ValueError("a source name")
    return state_dir() / "proofread" / source


def claude_path() -> str:
    """`claude` lives in ~/.local/bin, which a bare `sh -c` PATH in WSL lacks: name it by path."""
    if os.environ.get("APOLLO_CLAUDE"):
        return os.environ["APOLLO_CLAUDE"]
    p = Path.home() / ".local" / "bin" / "claude"
    return str(p) if p.exists() else (shutil.which("claude") or "claude")


def available() -> dict:
    """{ok, why}: `claude` can be run here, and so can pdftoppm."""
    exe = claude_path()
    if not (os.path.isfile(exe) and os.access(exe, os.X_OK)) and not shutil.which(exe):
        return {"ok": False, "why": "claude is not installed in WSL"}
    if not (os.environ.get("APOLLO_PDFTOPPM") or shutil.which("pdftoppm")):
        return {"ok": False, "why": "pdftoppm is not installed in WSL"}
    return {"ok": True, "why": ""}


def model_id() -> str:
    try:
        import yaml
        rec = yaml.safe_load((runfiles.PAGEFLOW / "recipes" / "full.yaml").read_text(encoding="utf-8")) or {}
        return str((rec.get("models") or {}).get("haiku") or DEFAULT_MODEL)
    except Exception:                                    # noqa: BLE001 - the constant stands
        return DEFAULT_MODEL


def tier_model(tier: str | None) -> str:
    """The model id for Haiku, Sonnet or Opus (None: Haiku, the default)."""
    if not tier:
        return model_id()
    import catalogue
    return catalogue.model_id(catalogue.model_key(tier))


def source_pdf(source: str) -> Path:
    """The source's PDF, or ValueError: a text source (a dropped TXT, MD, HTML, DOCX) has no page image to read."""
    import tools
    f = tools.source_file(source)
    if f.suffix.lower() != ".pdf":
        raise ValueError("this source has no PDF")
    if not f.is_file():
        raise ValueError(f"the source's file is missing: {f.name}")
    return f


def parse_pages(pages: str) -> list[int]:
    m = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", pages or "")
    if not m:
        raise ValueError("pages: N or A-B")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if a < 1 or b < a:
        raise ValueError("pages: A-B with 1 <= A <= B")
    if b - a + 1 > MAX_PAGES:
        raise ValueError(f"at most {MAX_PAGES} pages at a time")
    return list(range(a, b + 1))


# ---- files -----------------------------------------------------------------------------------------------------------

def _read(path: Path) -> dict | None:
    got = runfiles.read_json(path)
    return got if isinstance(got, dict) else None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def accepted(source: str, n: int) -> dict | None:
    try:
        return _read(folder(source) / f"p{int(n)}.json")
    except ValueError:
        return None


def pending(source: str, n: int) -> dict | None:
    return _read(folder(source) / f"pending-{int(n)}.json")


def _numbers(source: str, pattern: str, prefix: str) -> list[int]:
    out = []
    for f in folder(source).glob(pattern) if folder(source).is_dir() else []:
        m = re.fullmatch(prefix + r"(\d+)\.json", f.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


# ---- the diff --------------------------------------------------------------------------------------------------------

def diff(old: str, new: str) -> list[dict]:
    """The two texts as segments [{op: eq | del | ins, text}], by words and the spaces between them."""
    a, b = re.findall(r"\s+|\S+", old), re.findall(r"\s+|\S+", new)
    out: list[dict] = []

    def add(op, toks):
        text = "".join(toks)
        if not text:
            return
        if out and out[-1]["op"] == op:
            out[-1]["text"] += text
        else:
            out.append({"op": op, "text": text})
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            add("eq", a[i1:i2])
        else:
            add("del", a[i1:i2])
            add("ins", b[j1:j2])
    return out


# ---- asking ----------------------------------------------------------------------------------------------------------

def _models_module():
    spec = importlib.util.spec_from_file_location("pageflow_models", runfiles.PAGEFLOW / "models.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render(pdf: Path, source: str, n: int) -> Path:
    """The page as a PNG at 150 dpi, under state/proofread/<source>/ (made once)."""
    out = folder(source) / f"p{n}.png"
    if out.is_file() and out.stat().st_mtime >= pdf.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    exe = os.environ.get("APOLLO_PDFTOPPM") or shutil.which("pdftoppm") or "pdftoppm"
    p = subprocess.run([exe, "-r", "150", "-f", str(n), "-l", str(n), "-png", "-singlefile", str(pdf), str(out.with_suffix(""))],
                       capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    if p.returncode != 0 or not out.is_file():
        raise ValueError("pdftoppm: " + ((p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1][:200]))
    return out


def ask_model(prompt: str, schema: dict, image: Path, label: str, model: str | None = None) -> tuple[dict, dict]:
    """(answer, row): Haiku's structured answer to the prompt with the page image, and the call's log row (ms, tokens, cost_usd,
    model). The page flow's `Models.ask(which, prompt, schema, label, image=...)`."""
    mod = _models_module()
    rows: list[dict] = []
    mid = model or model_id()
    m = mod.Models({"haiku": mid}, {"haiku": 1}, claude=claude_path(), timeout=300, log=rows.append)
    t = time.time()
    try:
        ans = m.ask("haiku", prompt, schema, label, image=str(image))
    except Exception as e:                               # noqa: BLE001 - the row still says what happened
        row = rows[-1] if rows else {"ms": int((time.time() - t) * 1000), "ok": False, "why": str(e)[:300]}
        e.row = {**row, "model": mid, "label": label}
        raise
    row = rows[-1] if rows else {"ms": int((time.time() - t) * 1000), "ok": True}
    return ans, {**row, "model": mid, "label": label, "cost_usd": m.tokens["haiku"]["cost_usd"]}


def _log(source: str, row: dict, prompt: str) -> None:
    try:
        import runlog
        runlog.log_model_call(source, row, prompt)
    except Exception:                                    # noqa: BLE001 - the log is a copy
        pass


def proofread_page(source: str, pdf: Path, n: int, model: str | None = None) -> dict:
    """One page: render it, ask, and write the pending proposal. Returns it. Raises ValueError with a sentence on failure."""
    import pagetext
    base = pagetext.page(source, n, raw=True)
    if not base.get("ok"):
        raise ValueError(base.get("why") or "the page could not be read")
    old = base.get("text") or ""
    image = render(pdf, source, n)
    prompt = PROMPT.format(page=n, text=old if old.strip() else "(none: the page has no text layer)")
    label = f"proofread p{n}"
    try:
        ans, row = ask_model(prompt, SCHEMA, image, label, **({"model": model} if model else {}))
    except Exception as e:                               # noqa: BLE001
        _log(source, getattr(e, "row", {"label": label, "ok": False, "why": str(e)[:300], "model": model or model_id()}) | {"page": n}, prompt)
        raise ValueError(f"the model gave no answer: {str(e)[:200]}") from None
    new = str(ans.get("text") or "").strip("\n")
    _log(source, {**row, "page": n, "answer": json.dumps(ans, ensure_ascii=False), "ok": True}, prompt)
    if not new.strip():
        raise ValueError("the model returned no text")
    rec = {"source": source, "page": n, "old": old, "text": new, "layer": base.get("layer"), "diff": diff(old, new),
           "changes": [str(c) for c in ans.get("changes") or []][:8], "model": row.get("model"),
           "at": datetime.now().isoformat(timespec="seconds"), "calls": 1, "cost_usd": row.get("cost_usd"), "ms": row.get("ms")}
    _write(folder(source) / f"pending-{n}.json", rec)
    return rec


# ---- the job ---------------------------------------------------------------------------------------------------------

def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return False
    return stat.rsplit(")", 1)[-1].split()[0] not in ("Z", "X")


def _job(source: str) -> dict | None:
    job = _read(folder(source) / "job.json")
    if job and job.get("status") == "running":
        young = not job.get("pid") and time.time() - (job.get("started") or 0) < 15
        if not young and not _pid_alive(job.get("pid")):
            job = {**job, "status": "failed", "error": job.get("error") or "the job's process ended without a result"}
    return job


def get(source: str, page: int | None = None) -> dict:
    """The Text tab's view of Proofread: {ok, available, why, job, pending: [pages], accepted: [pages], page: {pending, accepted}}."""
    try:
        source_pdf(source)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    av = available()
    out = {"ok": True, "available": av["ok"], "why": av["why"], "max_pages": MAX_PAGES, "job": _job(source),
           "pending": _numbers(source, "pending-*.json", "pending-"), "accepted": _numbers(source, "p*.json", "p")}
    if page:
        acc = accepted(source, page)
        out["page"] = {"pending": pending(source, page),
                       "accepted": None if not acc else {k: acc.get(k) for k in ("model", "at", "changes", "calls", "cost_usd")}}
    return out


def begin(source: str, pages: str, tier: str | None = None) -> dict:
    """Start the job in its own process and return at once. Refuses when `claude` is missing, the source has no PDF, more than
    20 pages are asked for, or a job is running for the source."""
    source_pdf(source)
    av = available()
    if not av["ok"]:
        raise ValueError(av["why"])
    nums = parse_pages(pages)
    old = _job(source)
    if old and old.get("status") == "running":
        return {"ok": True, "started": False, "source": source}
    job = {"status": "running", "source": source, "pages": nums, "pages_arg": pages, "done": 0, "total": len(nums), "calls": 0,
           "cost_usd": 0.0, "errors": {}, "error": None, "pid": None, "started": time.time(), "model": tier_model(tier)}
    path = folder(source) / "job.json"
    _write(path, job)
    log = open(path.with_suffix(".log"), "ab")
    p = subprocess.Popen([sys.executable, str(HERE / "apollo.py"), "proofreadrun", "--source", source, "--pages", pages] + (["--model", tier] if tier else []),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    log.close()
    return {"ok": True, "started": True, "source": source, "pid": p.pid}


def run(source: str, pages: str, tier: str | None = None) -> int:
    """The job's own process: each page in turn; a page that fails is noted and the rest go on."""
    path = folder(source) / "job.json"
    job = _read(path) or {"source": source, "pages": parse_pages(pages), "total": len(parse_pages(pages)), "calls": 0,
                          "cost_usd": 0.0, "errors": {}, "done": 0, "started": time.time(), "model": tier_model(tier)}
    job.update({"status": "running", "pid": os.getpid()})
    _write(path, job)
    try:
        pdf = source_pdf(source)
        for n in job["pages"]:
            try:
                rec = proofread_page(source, pdf, n, *([job.get("model")] if tier else []))
                job["calls"] += 1
                job["cost_usd"] = round(float(job["cost_usd"]) + float(rec.get("cost_usd") or 0), 5)
            except ValueError as e:
                job["errors"][str(n)] = str(e)
                if "the model gave no answer" in str(e):
                    job["calls"] += 1
            job["done"] += 1
            _write(path, job)
        failed = len(job["errors"]) == job["total"]
        job.update({"status": "failed" if failed else "done", "ended": time.time(),
                    "error": next(iter(job["errors"].values())) if failed else None})
    except Exception as e:                               # noqa: BLE001 - the file says why
        job.update({"status": "failed", "ended": time.time(), "error": f"{type(e).__name__}: {e}"[:300]})
    _write(path, job)
    try:
        import runlog
        runlog.log_tool_run("proofread", source, {
            "status": job["status"], "started": job.get("started"), "ended": job.get("ended"), "done": job["done"],
            "total": job["total"], "error": job.get("error"), "source": source, "pages_arg": job.get("pages_arg") or pages,
            "model": job.get("model"), "calls": job["calls"], "cost_usd": job["cost_usd"], "errors": job["errors"]})
    except Exception:                                    # noqa: BLE001
        pass
    return 0 if job["status"] == "done" else 1


def act(source: str, page: int, action: str) -> dict:
    """accept: the pending text becomes the page's text; reject: the proposal is dropped; revert: the accepted text is dropped
    (the page shows OCR or its text layer again). Nothing outside state/proofread/<source>/ is touched."""
    source_pdf(source)
    f = folder(source)
    if action == "accept":
        p = pending(source, page)
        if not p:
            return {"ok": False, "why": "nothing is waiting for this page"}
        _write(f / f"p{page}.json", {"source": source, "page": page, "text": p["text"], "model": p.get("model"), "at": p.get("at"),
                                     "changes": p.get("changes") or [], "calls": p.get("calls", 1), "cost_usd": p.get("cost_usd"),
                                     "accepted": datetime.now().isoformat(timespec="seconds")})
        (f / f"pending-{page}.json").unlink(missing_ok=True)
    elif action == "reject":
        (f / f"pending-{page}.json").unlink(missing_ok=True)
    elif action == "revert":
        (f / f"p{page}.json").unlink(missing_ok=True)
    else:
        return {"ok": False, "why": "accept, reject or revert"}
    return {"ok": True, "source": source, "page": page, "action": action}

"""The Tools group of Configure: OCR, Text, Check. None of them uses a model.

Each runs in its own process (`apollo.py toolrun`, started by `toolstart`) and writes its progress and result to one file,
`state/tools/<key>/<tool>.json`, which `toolget` reads (quick: the window's worker answers it). The key is the source's
name (OCR, Text) or the archive's run and folder name (Check). The result stays until the next run of that tool.

  toolstart --tool ocr --source S [--pages A-B] [--lang auto|CODE]  OCR venv's ocr-pdf.py, one page after another
  toolstart --tool text --source S [--pages A-B]                   pdftotext -layout, one page after another
  toolstart --tool check --archive PATH                            hydra-ctl check (read only)
  toolstart --tool build --archive PATH                            hydra-ctl build (hydra skips a fresh archive); Apollo
                                                                   starts it itself, never a button
  toolget   --tool T (--source S | --archive PATH)                 the file's JSON, with `running` made honest

Check and Build take only archives under the roots Apollo's own runs use (hydra-mcp-testing/archives, apollo/dry/archives
and apollo/archives, the Draft's); anything else is refused here, whatever the window sends. Nothing is written beside a
source.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import runfiles
import settings
import toolargs

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
TOOLS = ("ocr", "text", "check", "build")
HOME = Path.home()
OCR_PYTHON = HOME / ".venvs" / "ocr" / "bin" / "python"
OCR_SCRIPT = Path("/mnt/e/Documents/development/stable/Archive Workflow/recipes/ocr-pdf.py")
INSTALL_HINT = "install-ocr-venv.sh"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
MAX_ITEMS = 300                                    # check findings kept in the result


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def tools_dir() -> Path:
    return state_dir() / "tools"


def _ocr_python() -> Path:
    return Path(os.environ.get("APOLLO_OCR_PYTHON") or OCR_PYTHON)


def _ocr_script() -> Path:
    return Path(os.environ.get("APOLLO_OCR_SCRIPT") or OCR_SCRIPT)


def _hydra_ctl() -> list[str]:
    if os.environ.get("APOLLO_HYDRA_CTL"):
        return [os.environ["APOLLO_HYDRA_CTL"]]
    py = Path(os.environ.get("APOLLO_RUN_PYTHON") or HOME / ".venvs" / "hydra" / "bin" / "python")
    ctl = py.parent / "hydra-ctl"
    return [str(ctl)] if ctl.exists() else [str(py), "-m", "hydra"]


def ocr_ready() -> bool:
    return _ocr_python().exists() and _ocr_script().exists()


_LANGS: dict = {}


def ocr_langs() -> list[dict]:
    """[{code, name}] for the OCR's Language select: Auto first, then what `ocr-pdf.py --langs` lists (English and Arabic
    when the script cannot say). Cached per script file."""
    script = _ocr_script()
    try:
        stamp = (str(script), script.stat().st_mtime)
    except OSError:
        stamp = (str(script), 0)
    if stamp not in _LANGS:
        out = [{"code": "en", "name": "English"}, {"code": "ar", "name": "Arabic"}]
        try:
            p = subprocess.run([str(_ocr_python()), str(script), "--langs"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            got = json.loads(p.stdout)
            if isinstance(got, list) and got:
                out = [{"code": x["code"], "name": x.get("name") or x["code"]} for x in got if isinstance(x, dict) and x.get("code")]
        except (OSError, ValueError, subprocess.TimeoutExpired, KeyError):
            pass
        _LANGS.clear()
        _LANGS[stamp] = [{"code": "auto", "name": "Auto"}] + out
    return _LANGS[stamp]


def archive_roots() -> list[Path]:
    """The folders whose archives Check may open: the live runs', the dry runs' and Apollo's own (the Draft)."""
    env = os.environ.get("APOLLO_ROOT")
    live = Path(env) if env else runfiles.TESTING
    dry = (Path(env) / "dry") if env else HERE / "dry"
    own = settings.folder("folders.archives", HERE / "archives")
    return [(live / "archives").resolve(), (dry / "archives").resolve(), own.resolve()]


def allowed_archive(path: str) -> Path:
    """The archive as a resolved path, or ValueError. Under an allowed root, and an existing folder."""
    if not path or not isinstance(path, str):
        raise ValueError("an archive is needed")
    p = Path(path).resolve()
    if not any(r == p or r in p.parents for r in archive_roots()) or p in archive_roots():
        raise ValueError("only archives of Apollo's own runs (hydra-mcp-testing/archives, apollo/dry/archives or apollo/archives)")
    if not p.is_dir():
        raise ValueError(f"no archive at {p}")
    return p


def source_file(name: str) -> Path:
    """The source's PDF (or text) file, from pageflow/sources/<name>.yaml."""
    import yaml
    if not SLUG.fullmatch(name or ""):
        raise ValueError("a source name")
    import sourcedrop
    f = next((d / f"{name}.yaml" for d in (runfiles.PAGEFLOW / "sources", sourcedrop.folder())
              if (d / f"{name}.yaml").is_file()), None)
    if f is None:
        raise ValueError(f"no source {name!r}")
    spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    rel = spec.get("file")
    if not rel:
        raise ValueError(f"source {name!r} names no file")
    p = Path(rel)
    return p if p.is_absolute() else runfiles.TESTING / p


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s).strip("-") or "x"


def key_of(tool: str, source: str | None, archive: Path | None) -> str:
    if tool in ("ocr", "text"):
        if not SLUG.fullmatch(source or ""):
            raise ValueError("a source name")
        return source
    return _slug(f"{archive.parent.name}-{archive.name}")


def job_file(key: str, tool: str) -> Path:
    return tools_dir() / key / f"{tool}.json"


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return False
    return stat.rsplit(")", 1)[-1].split()[0] not in ("Z", "X")


def _save(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _args(tool, source, archive, pages, lang) -> tuple[str, Path | None, list[int] | None, str]:
    """The key, the archive and the page numbers named (None: all, or `end` still to resolve). A tool's other arguments: toolargs."""
    if tool not in TOOLS:
        raise ValueError(f"tool: one of {', '.join(TOOLS)}")
    if lang != "auto" and not re.fullmatch(r"[a-z]{2,3}", lang or ""):
        raise ValueError("--lang: auto, or a language code from the OCR's list")
    nums = None
    if pages:
        try:
            nums = toolargs.pages(pages) if "end" not in pages.lower() else None
        except ValueError as e:
            raise ValueError(f"--{e}") from None
    arch = allowed_archive(archive) if tool in ("check", "build") else None
    return key_of(tool, source, arch), arch, nums, lang


def _langs_of(tool: str) -> dict:
    return {"langs": ocr_langs()} if tool == "ocr" and ocr_ready() else {}


def get(tool: str, source: str | None = None, archive: str | None = None) -> dict:
    """The tool's last job for that source or archive: {ok, tool, key, status, ...}, or status `none`."""
    if tool not in TOOLS:
        return {"ok": False, "why": f"tool: one of {', '.join(TOOLS)}"}
    try:
        key = key_of(tool, source, allowed_archive(archive) if tool in ("check", "build") else None)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    rec = _read(job_file(key, tool))
    if not rec:
        return {"ok": True, "tool": tool, "key": key, "status": "none", "ocr_ready": ocr_ready(), **_langs_of(tool)}
    young = not rec.get("pid") and time.time() - (rec.get("started") or 0) < 15      # begun, its process not yet at work
    if rec.get("status") == "running" and not young and not _pid_alive(rec.get("pid")):
        rec = {**rec, "status": "failed", "error": rec.get("error") or "the tool's process ended without a result"}
    return {"ok": True, **rec, "ocr_ready": ocr_ready(), **_langs_of(tool)}


def begin(tool: str, source: str | None = None, archive: str | None = None, pages: str | None = None, lang: str = "auto",
          args: dict | None = None) -> dict:
    """Start the tool in its own process and return at once. A tool already running for the same key is not started twice.
    `args` are the tool's declared arguments (toolargs.ARGS); they win over `pages` and `lang`."""
    if tool in ("ocr", "text"):                                                  # tool args
        if lang != "auto" and not re.fullmatch(r"[a-z]{2,3}", lang or ""):
            raise ValueError("--lang: auto, or a language code from the OCR's list")
        a = toolargs.clean(tool, {**({"pages": pages} if pages else {}), **(args or {})})
        pages, lang = a["pages"] or None, (a["lang"] if "lang" in (args or {}) else lang) if tool == "ocr" else "auto"
    else:
        a = {}
    key, arch, rng, lang = _args(tool, source, archive, pages, lang)
    if tool == "ocr" and not ocr_ready():
        raise ValueError(f"OCR is not installed here: run {INSTALL_HINT}")
    if tool in ("ocr", "text"):
        f = source_file(source)
        if f.suffix.lower() != ".pdf":
            raise ValueError("this source is not a PDF")
        if not f.is_file():
            raise ValueError(f"the source's file is missing: {f}")
    old = get(tool, source, archive)
    if old.get("status") == "running":
        return {"ok": True, "tool": tool, "key": key, "started": False}
    spec = {"tool": tool, "source": source, "archive": str(arch) if arch else None, "pages": pages or None, "lang": lang, "dpi": a.get("dpi")}
    rec = {"tool": tool, "key": key, "status": "running", "started": time.time(), "done": 0, "total": len(rng) if rng and tool not in ("check", "build") else None,
           "source": source, "archive": spec["archive"], "pages_arg": pages or None, "lang": lang if tool == "ocr" else None, "args": a or None, "pid": None}
    path = job_file(key, tool)
    _save(path, rec)
    cmd = [sys.executable, str(Path(__file__).resolve().with_name("apollo.py")), "toolrun", "--tool", tool, "--key", key, "--spec", json.dumps(spec)]
    log = open(path.with_suffix(".log"), "ab")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    log.close()
    return {"ok": True, "tool": tool, "key": key, "started": True, "pid": p.pid}


# ---- the process ----------------------------------------------------------------------------------------------------

def _hydra_env() -> dict:
    env = {**os.environ}
    env.setdefault("HYDRA_CONFIG_DIR", str(tools_dir() / ".hydra-config"))      # never the owner's own hydra settings
    return env


def _page_count(pdf: Path) -> int | None:
    exe = os.environ.get("APOLLO_PDFINFO") or shutil.which("pdfinfo") or "pdfinfo"
    try:
        out = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def _pages_for(pdf: Path, pages: str | None) -> list[int] | None:
    if pages:
        return toolargs.pages(pages, _page_count(pdf) if "end" in pages.lower() else None)
    n = _page_count(pdf)
    return list(range(1, n + 1)) if n else None


def _finish(path: Path, rec: dict, **more) -> None:
    rec = {**rec, **more}
    rec["ended"] = time.time()
    _save(path, rec)


def _run_ocr(path: Path, rec: dict, spec: dict) -> None:
    pdf = source_file(spec["source"])
    pages = _pages_for(pdf, spec["pages"])
    rec["total"] = len(pages) if pages else rec.get("total")
    _save(path, rec)
    lang = spec["lang"] if spec["lang"] != "auto" or "--langs" in _ocr_script().read_text(encoding="utf-8") else "en"   # an older script has no auto
    cmd = [str(_ocr_python()), str(_ocr_script()), str(pdf), "--lang", lang, "--json"]
    if spec["pages"] and pages:                                                  # tool args: a list or `end` goes as plain numbers
        cmd += ["--pages", toolargs.compact(pages)]
    if spec.get("dpi"):
        cmd += ["--dpi", str(spec["dpi"])]
    out_dir = path.parent
    raw = open(out_dir / "ocr-raw.jsonl", "w", encoding="utf-8")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    rec["child"] = p.pid
    got = []
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        raw.write(line + "\n")
        raw.flush()
        try:
            one = json.loads(line)
        except ValueError:
            continue
        if "detected" in one:                                  # --lang auto: the language it found, before the pages
            rec["detected"] = one["detected"]
            _save(path, rec)
            continue
        lines = [x.get("text", "") for x in one.get("lines", [])]
        got.append({"page": one.get("page"), "text": "\n".join(lines), "lines": len(lines)})
        rec.update({"done": len(got), "pages": got})
        _save(path, rec)
    code = p.wait()
    raw.close()
    err = (p.stderr.read() or "").strip().splitlines()
    if code != 0:
        _finish(path, rec, status="failed", error=(err[-1] if err else f"ocr exited {code}")[:300])
    else:
        _finish(path, rec, status="done", total=len(got))


def _run_text(path: Path, rec: dict, spec: dict) -> None:
    pdf = source_file(spec["source"])
    pages = _pages_for(pdf, spec["pages"])
    if not pages:
        _finish(path, rec, status="failed", error="the page count is unknown: give pages A-B")
        return
    rec["total"] = len(pages)
    import pagetext                                           # the Text tab's cache first: a page read before is not read again
    got = []
    for n in pages:
        one = pagetext.page(spec["source"], n, raw=True)
        if not one.get("ok"):
            _finish(path, rec, status="failed", error=str(one.get("why") or "the page could not be read")[:300])
            return
        text = str(one.get("text") or "")
        got.append({"page": n, "text": text, "lines": len([x for x in text.splitlines() if x.strip()])})
        rec.update({"done": len(got), "pages": got})
        _save(path, rec)
    _finish(path, rec, status="done")


def _hydra(args: list[str], archive: str) -> subprocess.CompletedProcess:
    return subprocess.run(_hydra_ctl() + args + [archive], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=900, env=_hydra_env(), stdin=subprocess.DEVNULL)


def _counts(items: list[dict]) -> dict:
    return {lv: sum(1 for i in items if i.get("level") == lv) for lv in ("error", "warning", "info")}


def _run_check(path: Path, rec: dict, spec: dict) -> None:
    _hydra(["build"], spec["archive"])                          # hydra's own freshness check: a build of a fresh archive does nothing
    p = _hydra(["check", "--format", "json"], spec["archive"])
    try:
        items = json.loads(p.stdout)
        if not isinstance(items, list):
            raise ValueError("not a list")
    except ValueError:
        _finish(path, rec, status="failed", error=((p.stderr or p.stdout).strip().splitlines() or ["check gave no answer"])[-1][:300])
        return
    order = {"error": 0, "warning": 1, "info": 2}
    items.sort(key=lambda i: order.get(i.get("level"), 3))
    _finish(path, rec, status="done", counts=_counts(items), total_items=len(items),
            items=[{k: i.get(k) for k in ("level", "code", "where", "message", "file")} for i in items[:MAX_ITEMS]])


def build_now(archive: str) -> subprocess.CompletedProcess:
    """hydra-ctl build on an archive of Apollo's own, in this process; hydra itself skips an archive that is fresh."""
    arch = allowed_archive(archive)
    p = _hydra(["build"], str(arch))
    key = key_of("build", None, arch)
    if p.returncode == 0 and not (_read(job_file(key, "build")) or {}).get("status") == "running":
        _save(job_file(key, "build"), {"tool": "build", "key": key, "status": "done", "archive": str(arch), "ended": time.time()})
    return p


def _run_build(path: Path, rec: dict, spec: dict) -> None:
    p = build_now(spec["archive"])
    if p.returncode != 0:
        _finish(path, rec, status="failed", error=((p.stderr or p.stdout).strip().splitlines() or [f"build exited {p.returncode}"])[-1][:300])
    else:
        _finish(path, rec, status="done")


def archive_stale(archive: str) -> bool:
    """True when the archive's records or manifest changed since its last build here, or it was never built by Apollo."""
    p = allowed_archive(archive)
    records = sorted((p / "collections").glob("*.json"))
    if not records:
        return False                                         # nothing to build from (an empty Draft): a build would only fail, forever
    rec = _read(job_file(key_of("build", None, p), "build"))
    if rec and rec.get("status") == "running":
        return False
    files = [p / "hydra.yaml", *records]
    try:
        newest = max(f.stat().st_mtime for f in files if f.exists())
    except ValueError:
        return False
    if not rec or rec.get("status") not in ("done", "failed"):
        return True
    return newest > (rec.get("ended") or 0)                  # a failed build is tried again only after the archive changed


def build_if_stale(archive: str) -> dict:
    """Start a background build of the archive when it is stale and none is running; {building, started}."""
    try:
        if not archive_stale(archive):
            return {"building": get("build", archive=archive).get("status") == "running", "started": False}
        out = begin("build", None, archive)
        return {"building": True, "started": bool(out.get("started"))}
    except ValueError:
        return {"building": False, "started": False}


def run(tool: str, key: str, spec: dict) -> int:
    """The tool's own process. Re-checks the archive against the allowed roots: this is the gate, not the window."""
    path = job_file(key, tool)
    rec = _read(path) or {"tool": tool, "key": key}
    rec["pid"] = os.getpid()
    _save(path, rec)
    try:
        if tool in ("check", "build"):
            spec = {**spec, "archive": str(allowed_archive(spec.get("archive")))}
        {"ocr": _run_ocr, "text": _run_text, "check": _run_check, "build": _run_build}[tool](path, rec, spec)
    except Exception as e:                                                       # noqa: BLE001 - the file says why
        _finish(path, _read(path) or rec, status="failed", error=f"{type(e).__name__}: {e}"[:300])
        _log_result(tool, key, path)
        return 1
    _log_result(tool, key, path)
    return 0


def _log_result(tool: str, key: str, path: Path) -> None:
    """The tool's result joins the DuckDB log (runlog.py); a log that is busy or broken never changes how the tool ended."""
    try:
        import runlog
        rec = _read(path)
        if rec:
            runlog.log_tool_run(tool, key, rec)
    except Exception:                                                            # noqa: BLE001
        pass


# ---- Ready: "is this ready for a run?" for the Configure bar --------------------------------------------------------

def _span(nums: list[int]) -> str:
    """[4, 5, 6, 9] -> "4–6, 9"."""
    out, i = [], 0
    nums = sorted(nums)
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(out)


def archive_findings(archive: str) -> list[dict]:
    """hydra-ctl check on an archive of Apollo's own runs, built first (hydra builds only a stale one). [{level, text}]."""
    p = allowed_archive(archive)
    _hydra(["build"], str(p))
    got = _hydra(["check", "--format", "json"], str(p))
    try:
        items = json.loads(got.stdout)
        if not isinstance(items, list):
            raise ValueError
    except ValueError:
        return [{"level": "error", "text": "The archive could not be checked: " + ((got.stderr or got.stdout).strip().splitlines() or ["no answer"])[-1][:160]}]
    return [{"level": i.get("level"), "text": f"{i.get('where') or i.get('code')}: {i.get('message')}"} for i in items if i.get("level") in ("error", "warning")]


def ready(source: str, pages: str | None, recipe: str, archive: str | None, analyse, forbidden=()) -> dict:
    """The run's preflight without any model call, for the selected source and pages, plus the archive's check when one is
    picked: {ok, level: ready | warnings | not-ready, errors, warnings, sentence, items: [{level, text}]}. `analyse` is the
    manager's own page analysis (text layer, words, redaction per page)."""
    items = []

    def add(level, text):
        items.append({"level": level, "text": text})
    try:
        f = source_file(source)
    except ValueError as e:
        add("error", str(e))
        f = None
    if f is not None:
        if not f.is_file():
            add("error", f"The source file is missing: {f.name}.")
        else:
            got = analyse(source, pages, recipe)
            if not got.get("ok", True) and got.get("why"):
                add("error", f"The source could not be read: {got['why']}")
            else:
                rng = None
                if pages:
                    a, _, b = pages.partition("-")
                    rng = (int(a), int(b or a))
                rows = [p for p in got.get("pages") or [] if rng is None or rng[0] <= p.get("page", 0) <= rng[1]]
                bare = [p["page"] for p in rows if p.get("needs_ocr")]
                if bare:
                    add("error", f"No text layer on page{'s' if len(bare) > 1 else ''} {_span(bare)}.")
                skipped = [p["page"] for p in rows if p.get("status") in ("thin", "redacted") and not p.get("needs_ocr")]
                if skipped:
                    add("warning", f"Thin or redacted, so skipped: page{'s' if len(skipped) > 1 else ''} {_span(skipped)}.")
    try:
        import hydra  # noqa: F401
        import hydra_mcp  # noqa: F401
    except Exception as e:                                                      # noqa: BLE001 - the answer says why
        add("error", f"Hydra cannot be loaded: {e}")
    try:
        hits = sorted({p.name for p in (runfiles.PAGEFLOW / "prompts").glob("*.md") if any(w in p.read_text(encoding="utf-8") for w in forbidden)})
        if hits:
            add("error", f"Prompt templates hold forbidden words: {', '.join(hits)}.")
    except OSError:
        pass
    if archive:
        items += archive_findings(archive)
    errors = sum(i["level"] == "error" for i in items)
    warnings = sum(i["level"] == "warning" for i in items)
    level = "not-ready" if errors else "warnings" if warnings else "ready"
    first = next((i["text"] for i in items if i["level"] == "error"), "")
    sentence = (f"Not ready: {first[0].lower() + first[1:]}" if level == "not-ready"
                else f"{'Ready' if level == 'ready' else 'Ready with warnings'}: {errors} errors, {warnings} warning{'s' if warnings != 1 else ''}")
    if level == "not-ready" and not first.endswith("."):
        sentence += "."
    return {"ok": True, "level": level, "errors": errors, "warnings": warnings, "sentence": sentence, "items": items}


# ---- tool args: the panel's fields and its sentence ---------------------------------------------------------------------

def about(tool: str, source: str | None = None, args: dict | None = None) -> dict:
    """{ok, tool, args: the declared fields, values: what `args` comes to, about: the sentence, problem: why the values are wrong}.
    Answers for the tools that take a source (ocr, text, catalogue, proofread) and for check and build."""
    decl = toolargs.declarations(tool)
    pdf = True
    if source:
        try:
            pdf = source_file(source).suffix.lower() == ".pdf"
        except ValueError:
            pass
    try:
        values = toolargs.clean(tool, args)
    except ValueError as e:
        return {"ok": True, "tool": tool, "args": decl, "values": toolargs.clean(tool, None), "about": "", "problem": str(e)}
    return {"ok": True, "tool": tool, "args": decl, "values": values, "about": toolargs.describe(tool, values, pdf), "problem": ""}

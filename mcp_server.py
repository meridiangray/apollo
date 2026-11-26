"""Apollo's MCP server (stdio): start and watch Apollo runs as tools.

It runs `apollo.py` as a subprocess, the way the Go server does, and returns its JSON. It imports nothing of Apollo's.
The window reads the same state, so a run started here shows there within seconds. Archive and entity work stays in
hydra's MCP; Proofread, OCR and adding or removing sources stay in the window.
"""
import functools
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
APOLLO = HERE / "apollo.py"
HCFG = "/home/manager/hcfg_apollo"
MAX_ROWS = 40                     # list items one tab returns
MAX_CHARS = 30000                 # characters one tab returns
TABS = ("run", "found", "questions", "calls", "report")
FLOWS = ("read", "stubs", "places", "enrich", "wiki")

mcp = FastMCP("apollo")


class Failed(Exception):
    def __init__(self, why: str, do: str = "Check the arguments, or run `apollo.py --help` in WSL."):
        self.out = {"error": "apollo failed", "why": why, "do": do}


def _env() -> dict:
    env = os.environ.copy()
    if "HYDRA_CONFIG_DIR" not in env and Path(HCFG).is_dir():
        env["HYDRA_CONFIG_DIR"] = HCFG
    return env


def _cli(*args: str) -> dict | list:
    """One apollo.py command; its JSON, or Failed with the error in {error, why, do} shape."""
    try:
        p = subprocess.run([sys.executable, str(APOLLO), *args], capture_output=True, text=True, timeout=120,
                           env=_env(), cwd=str(HERE))
    except subprocess.TimeoutExpired:
        raise Failed("apollo.py took over 120 seconds", "Try again; if it repeats, look at the window.")
    out = None
    try:
        out = json.loads(p.stdout) if p.stdout.strip() else None
    except ValueError:
        pass
    if out is not None and not (isinstance(out, dict) and out.get("ok") is False):
        if p.returncode == 0 or isinstance(out, list) or out.get("ok"):
            return out
    if isinstance(out, dict) and out.get("ok") is False:
        raise Failed(str(out.get("why") or out.get("error") or "refused"), "Fix what it says and call again.")
    err = (p.stderr or p.stdout or "").strip()
    raise Failed(err[-1500:] or f"apollo.py exited {p.returncode}",
                 "If this is a traceback, apollo.py or its modules are stale or broken: restart this server and tell the owner.")


def _guard(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Failed as e:
            return e.out
    return wrapper


def _cut(view: dict) -> dict:
    """Cap a tab's size. Says what was cut. The CLI cannot page a tab: use query() for the rest."""
    cut = {}
    for k, v in list(view.items()):
        if isinstance(v, list) and len(v) > MAX_ROWS:
            cut[k] = {"shown": MAX_ROWS, "of": len(v)}
            view[k] = v[:MAX_ROWS]
    while len(json.dumps(view, ensure_ascii=False)) > MAX_CHARS:
        big = max((k for k, v in view.items() if isinstance(v, list) and len(v) > 1),
                  key=lambda k: len(json.dumps(view[k], ensure_ascii=False)), default=None)
        if big is None:
            break
        n = len(view[big]) // 2
        cut[big] = {"shown": n, "of": cut.get(big, {}).get("of", len(view[big]))}
        view[big] = view[big][:n]
    if cut:
        view["cut"] = {"lists": cut, "note": "Cut to keep this small. The CLI has no paging; use query() on the log for the rest."}
    return view


@mcp.tool()
@_guard
def runs() -> dict:
    """List every run Apollo knows: id, flow, source, status, pages, dry, started."""
    rows = _cli("ls", "--json")
    return {"runs": [{"id": r.get("id"), "flow": r.get("flow"), "source": r.get("source"), "status": r.get("status"),
                      "pages": r.get("pages", (r.get("progress") or {}).get("pages")), "dry": r.get("dry"),
                      "started": r.get("started")} for r in rows]}


@mcp.tool()
@_guard
def run(id: str, tab: str = "run") -> dict:
    """One run's status and one tab of its rows. tab is run, found, questions, calls or report. id may be any unique part of a run id. Big tabs are cut and say so."""
    if tab not in TABS:
        raise Failed(f"no tab {tab!r}", f"Use one of: {', '.join(TABS)}.")
    status = _cli("status", id, "--json")
    keep = ("id", "name", "kind", "flow", "source", "status", "detail", "alive", "dry", "started", "ended", "elapsed_s",
            "archive", "progress", "cost", "calls", "questions", "report")
    return {"status": {k: status[k] for k in keep if k in status}, "tab": tab, "view": _cut(_cli("view", id, "--tab", tab, "--json"))}


@mcp.tool()
@_guard
def sources() -> dict:
    """The built-in and dropped sources, with what each still needs before it can run."""
    return _cli("sourcelist")


def _ready(source: str, pages: str, archive: str) -> dict:
    if archive == "draft":                                    # ready wants the Draft's path, as the window sends it
        archive = _cli("configs", "--json")["draft"]["path"]
    args = ["ready", "--source", source, "--json"] + (["--archive", archive] if archive != "new" else [])
    if pages:
        args += ["--pages", pages]
    return _cli(*args)


@mcp.tool()
@_guard
def ready(source: str, pages: str = "", flow: str = "read", archive: str = "draft") -> dict:
    """Check a run is ready, with no model call: the source, its pages and the archive. pages is A-B. Only the read flow works on a source."""
    if flow != "read":
        return {"ok": True, "note": f"The {flow} flow works on an archive and has no source check."}
    return _ready(source, pages, archive)


@mcp.tool()
@_guard
def start(flow: str, source: str = "", pages: str = "", archive: str = "draft", dry: bool = False, models: str | None = None,
          cost_cap: str | None = None, after: str | None = None, confirm: bool = False) -> dict:
    """Start a run. flow is read (needs source and pages A-B), stubs, places, enrich or wiki (these work on the archive). dry means no model calls. models is role=model,... ; cost_cap is US dollars or none; after is a workflow to run when a read ends. Without confirm it only previews: what would run, whether it calls models, the archive and the Ready result, and starts nothing. Set confirm=true only after the person agrees to the preview."""
    if flow not in FLOWS:
        raise Failed(f"no flow {flow!r}", f"Use one of: {', '.join(FLOWS)}.")
    if flow == "read" and not source:
        raise Failed("the read flow needs a source", "Call sources() and pass one.")
    args = ["start", "--flow", flow, "--archive", archive, "--json"]
    if source:
        args += ["--source", source]
    for flag, val in (("--pages", pages), ("--models", models), ("--budget", cost_cap), ("--after", after)):
        if val:
            args += [flag, str(val)]
    if dry:
        args.append("--dry")
    if not confirm:
        out = {"preview": True, "flow": flow, "source": source or None, "pages": pages or None, "archive": archive,
               "dry": dry, "models": models, "cost_cap": cost_cap, "after": after,
               "model_calls": "no" if dry else "yes",
               "started": False, "do": "Show this to the person. Call again with confirm=true only after they agree."}
        if flow == "read":
            try:
                out["ready"] = _ready(source, pages, archive)
            except Failed as e:                               # a preview still shows what is wrong
                out["ready"] = e.out
        return out
    got = _cli(*args)
    return {"started": True, "id": got.get("id"), "dry": got.get("dry"), "archive": got.get("archive"), "run_folder": got.get("run_folder")}


@mcp.tool()
@_guard
def stop(id: str) -> dict:
    """Stop a running run. It ends as stopped."""
    return _cli("stop", id, "--json")


@mcp.tool()
@_guard
def clear(id: str, confirm: bool = False) -> dict:
    """Delete a finished run for good: its folder, its archive and its log rows. Refused while it runs. Without confirm it only previews what it is. Set confirm=true only after the person agrees."""
    st = _cli("status", id, "--json")
    if st.get("alive"):
        raise Failed(f"{st.get('id')} is still running", "Stop it first, then clear it.")
    if not confirm:
        return {"preview": True, "id": st.get("id"), "status": st.get("status"), "flow": st.get("flow"), "source": st.get("source"),
                "would_delete": [st.get("run_folder"), st.get("archive")], "cleared": False,
                "do": "Show this to the person. Call again with confirm=true only after they agree. It cannot be undone."}
    return _cli("clear", id, "--json")


@mcp.tool()
@_guard
def query(sql: str) -> dict:
    """Run one read-only SELECT or WITH query on Apollo's DuckDB log. Tables: runs, events, model_calls, hydra_calls, found, pages, questions, tool_runs, collections. Returns columns, rows and whether it was truncated."""
    return _cli("logdb", "--sql", sql)


if __name__ == "__main__":
    mcp.run()

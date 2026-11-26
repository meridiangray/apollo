"""history.py: Action History, every action the person or an agent took, newest first, read from the DuckDB log.

    rows(kind=None, limit=400)    {ok, rows: [...], counts: {kind: n}}

A row: {at, kind, what, source, pages, model, result, detail, secs, run}. `kind` is run, tool, source or toast. The sources of the
rows: `runs` (started, and stopped), `tool_runs` (tools, source changes, toasts, clears). `events` holds a run's own trace, which is
the run's page and not an action of the person's: it is not listed. Clicking a row opens `run` when the run still exists, else `source`.
"""
from __future__ import annotations

import json
from datetime import datetime

import runlog

KINDS = ("run", "tool", "source", "toast")
TOOLS = {"analyse": "Analyse", "ocr": "OCR", "text": "Text", "ready": "Ready", "check": "Check", "build": "Build",
         "proofread": "Proofread", "catalogue": "Catalogue", "wikipedia": "Wikipedia add"}
SOURCE_WORD = {"add": "Source added", "remove": "Source removed", "hide": "Source hidden", "unhide": "Source shown"}


def _iso(v) -> str | None:
    if v in (None, ""):
        return None
    return str(v).replace(" ", "T")[:19]


def _secs(a, b) -> float | None:
    try:
        return round((datetime.fromisoformat(str(b)[:26].replace(" ", "T")) - datetime.fromisoformat(str(a)[:26].replace(" ", "T"))).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _query(sql: str) -> list[dict]:
    got = runlog.sql(sql, limit=runlog.MAX_ROWS * 5)
    if not got.get("ok"):
        return []
    return [dict(zip(got["columns"], r)) for r in got["rows"]]


def _detail(raw) -> dict:
    try:
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def rows(kind: str | None = None, limit: int = 400) -> dict:
    out: list[dict] = []
    for r in _query("SELECT id, flow, name, source, pages, model, status, detail, started, ended, dry FROM runs"):
        what = (r["flow"] or r["name"] or r["id"]) + (" (dry)" if r["dry"] else "")
        out.append({"at": _iso(r["started"]), "kind": "run", "what": f"Run started: {what}", "source": r["source"], "pages": r["pages"],
                    "model": r["model"], "result": r["status"], "detail": None if r["detail"] in (None, "", "running", r["status"]) else r["detail"], "secs": _secs(r["started"], r["ended"]), "run": r["id"]})
        if r["status"] == "stopped" and r["ended"]:
            out.append({"at": _iso(r["ended"]), "kind": "run", "what": f"Run stopped: {what}", "source": r["source"], "pages": r["pages"],
                        "model": r["model"], "result": "stopped", "detail": None, "secs": None, "run": r["id"]})
    for r in _query("SELECT id, tool, key, source, pages, status, started, ended, error, model, detail FROM tool_runs"):
        tool, d = r["tool"], _detail(r["detail"])
        if isinstance(d.get("detail"), dict):
            d = {**d, **d["detail"]}
        row = {"at": _iso(r["started"] or r["ended"]), "source": r["source"], "pages": r["pages"], "model": r["model"], "result": r["status"],
               "detail": r["error"], "secs": _secs(r["started"], r["ended"]), "run": None}
        if tool == "notice":
            out.append({**row, "kind": "toast", "what": "Toast", "source": None, "result": r["status"], "detail": r["error"]})
        elif tool == "clear":
            out.append({**row, "kind": "run", "what": f"Run cleared: {d.get('name') or r['key']}", "result": "cleared", "detail": None})
        elif tool == "source":
            out.append({**row, "kind": "source", "what": SOURCE_WORD.get(d.get("action"), "Source"), "source": r["source"] or r["key"], "result": d.get("action")})
        elif tool == "catalogue-act":
            out.append({**row, "kind": "tool", "what": {"accept": "Catalogue accepted", "reject": "Catalogue rejected", "revert": "Catalogue reverted"}.get(r["status"], "Catalogue"), "result": r["status"]})
        elif tool in TOOLS:
            out.append({**row, "kind": "tool", "what": TOOLS[tool], "result": r["status"] if tool != "ready" else (r["status"] or "").replace("-", " ")})
    out = [r for r in out if r["at"]]
    out.sort(key=lambda r: r["at"], reverse=True)
    counts = {k: sum(1 for r in out if r["kind"] == k) for k in KINDS}
    if kind in KINDS:
        out = [r for r in out if r["kind"] == kind]
    return {"ok": True, "rows": out[:max(1, min(int(limit), 2000))], "counts": counts, "total": sum(counts.values())}

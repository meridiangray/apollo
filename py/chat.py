"""chat.py — the chat bubble's conversation (Hydra's chat window, brought into Apollo): one model answers, reading the archive
through hydra's read-only MCP tools. Hydra's chat keeps its sessions on the machine (hydra machine/chatstore.py); this mirrors
that, one JSON line per message, under the state folder:

    state/chat/<session>.jsonl          the conversation: {at, who: me|reply, text, model, context?, cost_usd?}
    state/chat/jobs/<session>-<n>.json  one reply on its way: {status: running|done|failed, text, tools, error, ...}
    state/chat/mcp.json                 hydra-mcp's client config for `claude` (the session's own, written each time)

    begin(req, run_facts)  -> {ok, id, session}   writes the job and starts `apollo.py chatrun` (returns at once)
    get(id)                -> {ok, id, status, text, error, tools, model}
    run(id)                -> exit code           `claude -p` with the model, the hydra read tools, the transcript on stdin

The page holds the conversation (gone on reload); every request carries it whole, so the reply reads the same thread the
person sees. No canned replies and no suggested questions: the model answers what was typed."""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# hydra-mcp's core tools that read and never write (the archive, its sources and its schema)
READ_TOOLS = ("guide", "details", "find_entities", "get_records", "explain_entity", "paths", "sources", "read_source",
              "source_profile", "source_outline", "source_names", "read_file", "collections", "describe_schema",
              "properties", "vocabularies", "keys", "check")
MAX_TEXT = 4000                                          # characters kept of one message (Hydra's chatstore keeps as many)
MAX_MSGS = 50
TIMEOUT = 600                                            # seconds one reply may take, tools included
BUDGET_USD = 2                                           # one reply's dollar cap (claude --max-budget-usd)
VIEWS = ("runs", "configure", "sources", "collections", "history")
_SESSION = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
_JOB = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}-\d{1,4}$")

SYSTEM = """You are the chat inside Apollo, the app that runs Hydra's reading flows and agent sessions over sources and archives.
Answer the person's question directly, in plain text, briefly. When the answer is in an archive, read it with the hydra tools:
they are read-only here, so change nothing and offer no edits. Quote only text you read through read_source or read_file.
A name alone never proves two records are one entity. When you do not know, say so."""


# ---- places ---------------------------------------------------------------------------------------------------------------

def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def folder() -> Path:
    return state_dir() / "chat"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def _append(session: str, row: dict) -> None:
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **row}
    with (d / f"{session}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def log(session: str) -> list[dict]:
    """The session's messages as written (the mirror of Hydra's chatstore; the page never reads it back)."""
    f = folder() / f"{session}.jsonl"
    out = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("who") in ("me", "reply"):
                out.append(m)
    except OSError:
        pass
    return out


# ---- the request ----------------------------------------------------------------------------------------------------------

def clean(req: dict) -> dict:
    """The request as the run reads it: {session, model, messages: [{role, text}], context}. Raises ValueError."""
    if not isinstance(req, dict):
        raise ValueError("a JSON object: messages, model, context")
    model = str(req.get("model") or "sonnet").lower()
    if model not in MODELS:
        raise ValueError("model: haiku, sonnet or opus")
    msgs = []
    for m in (req.get("messages") or [])[-MAX_MSGS:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            raise ValueError("each message: role user or assistant, and text")
        text = str(m.get("text") or "").strip()[:MAX_TEXT]
        if text:
            msgs.append({"role": m["role"], "text": text})
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message is the person's")
    session = str(req.get("session") or "")
    if session and not _SESSION.match(session):
        raise ValueError("not a chat session")
    ctx = req.get("context") if isinstance(req.get("context"), dict) else {}
    keep = {}
    for k in ("view", "run", "source", "archive"):
        v = ctx.get(k)
        if isinstance(v, str) and v.strip() and len(v) <= 500 and not any(ord(c) < 32 for c in v):
            keep[k] = v.strip()
    if keep.get("view") not in VIEWS:
        keep.pop("view", None)
    return {"session": session, "model": model, "messages": msgs, "context": keep}


def _archive_ok(p: str) -> bool:
    try:
        return (Path(p) / "hydra.yaml").is_file()
    except OSError:
        return False


def context_lines(ctx: dict, run_facts=None) -> list[str]:
    """What Apollo shows the person, as lines for the model: the view, the run and source picked, the archives to read."""
    out, archives = [], []
    view = {"runs": "the Runs view", "configure": "the New run view", "sources": "the Sources view",
            "collections": "the Collections view", "history": "the History view"}.get(ctx.get("view", ""))
    if view:
        out.append(f"The person has {view} open.")
    rid = ctx.get("run")
    if rid:
        facts = None
        if run_facts:
            try:
                facts = run_facts(rid)
            except Exception:                            # noqa: BLE001 - a run that cannot be read is only named
                facts = None
        if facts:
            bits = [f"{k} {facts[k]}" for k in ("flow", "status", "source", "model") if facts.get(k)]
            out.append(f"Selected run: {rid}" + (f" ({', '.join(bits)})" if bits else "") + ".")
            if facts.get("archive"):
                archives.append(("the selected run's archive", str(facts["archive"])))
        else:
            out.append(f"Selected run: {rid}.")
    if ctx.get("source"):
        out.append(f"Selected source: {ctx['source']}.")
    if ctx.get("archive"):
        archives.append(("the archive open in Apollo", ctx["archive"]))
    try:
        import archives as arc
        archives.append(("the Draft archive", str(arc.draft_path())))
    except Exception:                                    # noqa: BLE001
        pass
    seen = set()
    for label, path in archives:
        if path in seen or not _archive_ok(path):
            continue
        seen.add(path)
        out.append(f"{label[0].upper()}{label[1:]}: {path}")
    if seen:
        out.append("Pass the archive's path as `archive` on every hydra call.")
    return out


def prompt_text(msgs: list[dict]) -> str:
    """The thread for `claude -p`: the earlier messages as a transcript, then the person's last message."""
    if len(msgs) == 1:
        return msgs[0]["text"]
    who = {"user": "Person", "assistant": "You"}
    past = "\n\n".join(f"{who[m['role']]}: {m['text']}" for m in msgs[:-1])
    return f"The conversation so far:\n\n{past}\n\nThe person now says:\n\n{msgs[-1]['text']}"


def command(model: str, mcp_path: Path, system: str) -> list[str]:
    import proofread
    allowed = ["ToolSearch", *(f"mcp__hydra__{t}" for t in READ_TOOLS)]
    return [proofread.claude_path(), "-p", "--model", MODELS[model], "--mcp-config", str(mcp_path), "--strict-mcp-config",
            "--tools", "ToolSearch", "--allowedTools", *allowed, "--append-system-prompt", system,
            "--max-budget-usd", f"{BUDGET_USD:g}", "--output-format", "stream-json", "--verbose"]


# ---- the job --------------------------------------------------------------------------------------------------------------

def _new_session() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}"


def begin(req: dict, run_facts=None) -> dict:
    r = clean(req)
    session = r["session"] or _new_session()
    jobs = folder() / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    n = 1 + sum(1 for _ in jobs.glob(f"{session}-*.json"))
    jid = f"{session}-{n}"
    system = "\n".join([SYSTEM, "", *context_lines(r["context"], run_facts)]).strip()
    job = {"status": "running", "id": jid, "session": session, "model": r["model"], "messages": r["messages"],
           "context": r["context"], "system": system, "started": time.time(), "pid": None, "text": "", "tools": [], "error": None}
    _write(jobs / f"{jid}.json", job)
    _append(session, {"who": "me", "text": r["messages"][-1]["text"], "model": MODELS[r["model"]], "context": r["context"]})
    out = open(jobs / f"{jid}.log", "ab")
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve().with_name("apollo.py")), "chatrun", "--id", jid],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    out.close()
    return {"ok": True, "id": jid, "session": session, "pid": p.pid}


def _alive(pid) -> bool:
    if not pid:
        return True                                      # not started yet: the run writes its pid first
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
    except (OSError, ValueError):
        return False
    return stat.rsplit(")", 1)[-1].split()[0] not in ("Z", "X")


def get(jid: str) -> dict:
    if not _JOB.match(str(jid or "")):
        raise ValueError("not a chat reply")
    path = folder() / "jobs" / f"{jid}.json"
    job = _read(path)
    if not job:
        raise ValueError("no such chat reply")
    if job.get("status") == "running" and job.get("pid") and not _alive(job["pid"]):
        job.update({"status": "failed", "error": "the chat ended without a reply"})
        _write(path, job)
    return {"ok": True, "id": jid, "status": job.get("status"), "text": job.get("text") or "", "error": job.get("error"),
            "tools": job.get("tools") or [], "model": job.get("model")}


def _lines(cmd: list[str], cwd: Path, stdin_text: str, err_path: Path):
    """`claude`'s stream-json output, a line at a time; the process is ours to end when the reply runs over TIMEOUT."""
    with open(err_path, "ab") as err:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                             text=True, encoding="utf-8", errors="replace")
        timer = threading.Timer(TIMEOUT, p.kill)
        timer.start()
        try:
            try:
                p.stdin.write(stdin_text)
                p.stdin.close()
            except OSError:
                pass
            for line in p.stdout:
                yield line
            p.wait()
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
                p.wait()
    if p.returncode:
        yield json.dumps({"type": "_exit", "code": p.returncode})


def run(jid: str) -> int:
    path = folder() / "jobs" / f"{jid}.json"
    job = _read(path)
    if not job:
        return 2
    job.update({"pid": os.getpid()})
    _write(path, job)
    d = folder()
    try:
        import runfiles
        client = json.loads((runfiles.TESTING / "clients" / "claude-code-hydra-linux.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        client = {"mcpServers": {}}
    mcp_path = d / "mcp.json"
    _write(mcp_path, client)
    cmd = command(job["model"], mcp_path, job.get("system") or SYSTEM)
    result, last, cost, code, err_text = None, "", None, 0, ""
    try:
        for line in _lines(cmd, d, prompt_text(job["messages"]), d / "jobs" / f"{jid}.err"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            if kind == "assistant":
                changed = False
                for b in ((ev.get("message") or {}).get("content") or []):
                    if b.get("type") == "text" and str(b.get("text") or "").strip():
                        last = str(b["text"]).strip()
                        changed = True
                    elif b.get("type") == "tool_use" and b.get("name"):
                        job["tools"] = [*job.get("tools", []), str(b["name"]).removeprefix("mcp__hydra__")][-40:]
                        changed = True
                if changed:
                    job["partial"] = last
                    _write(path, job)
            elif kind == "result":
                cost = ev.get("total_cost_usd")
                if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                    err_text = str(ev.get("result") or ev.get("subtype") or "the model gave no answer")[:300]
                else:
                    result = str(ev.get("result") or "").strip() or last
            elif kind == "_exit":
                code = ev.get("code") or 1
    except OSError as e:
        err_text = f"claude could not start: {e}"[:300]
    if result:
        job.update({"status": "done", "text": result, "error": None})
    else:
        why = err_text
        if not why:
            try:
                tail = [ln.strip() for ln in (d / "jobs" / f"{jid}.err").read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            except OSError:
                tail = []
            why = (tail[-1] if tail else f"claude exited {code}" if code else "the model gave no answer")[:300]
        job.update({"status": "failed", "error": why})
    job.update({"ended": time.time(), "cost_usd": cost})
    _write(path, job)
    if result:
        _append(job["session"], {"who": "reply", "text": result[:MAX_TEXT * 4], "model": MODELS[job["model"]],
                                 "tools": job.get("tools") or [], "cost_usd": cost})
    return 0 if result else 1


# ---- the command (apollo.py chatstart | chatget | chatrun) ----------------------------------------------------------------

def main(a, run_facts=None) -> int:
    import base64
    if a.cmd == "chatrun":
        return run(a.id)
    try:
        if a.cmd == "chatstart":
            raw = base64.urlsafe_b64decode(a.req64 + "=" * (-len(a.req64) % 4))
            out = begin(json.loads(raw.decode("utf-8")), run_facts)
        else:
            out = get(a.id)
    except (ValueError, OSError) as e:                   # a bad request; the reply's process could not start
        out = {"ok": False, "why": str(e)[:300]}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 2

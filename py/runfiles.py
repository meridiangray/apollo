"""runfiles.py: what a run's files say, for the Apollo CLI and the app. Reads only; no GUI, no process, no writes.

A run is a folder. A page-flow run writes `pageflow/progress.json`, `trace.jsonl`, `found.jsonl`, `hydra.jsonl` (the
driver's hydra calls: tool, ms, ok), `models.jsonl` and `state.json` (pageflow/progress.py, pageflow.py). An MCP agent
session writes `transcript.jsonl` (`claude -p --output-format stream-json`). Either may have an archive, whose
`.hydra/tasks/<id>.md` logs the task's steps, `.hydra/tasks/*.calls.jsonl` every hydra call, and
`.hydra/review/questions.yaml` the `ask_person` cards; and a `report.md` in the run folder.

The Run tab's line and its folding of repeats are pageflow/progress.py's `line` and `fold` (one implementation); the
formatting functions here turn rows into (text, tag) pieces a window or a terminal shows.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
TESTING = Path(os.environ.get("APOLLO_TESTING") or HERE.parent / "hydra-mcp-testing")   # the one setting: where hydra-mcp-testing is
PAGEFLOW = TESTING / "pageflow"
if str(PAGEFLOW) not in sys.path:
    sys.path.insert(0, str(PAGEFLOW))

from progress import CATEGORIES, STATUS_COLOURS, fold, line  # noqa: E402,F401 - shared with the progress window


# ---- reading files -------------------------------------------------------------------------------------------------

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def tail_text(path, lines: int = 300, max_bytes: int = 120_000) -> list[str]:
    """The last `lines` lines of a text file (read from its last `max_bytes`)."""
    try:
        size = Path(path).stat().st_size
        with Path(path).open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-lines:]


class Tail:
    """A JSONL file read a piece at a time: `read()` gives the rows added since the last read (whole lines only)."""

    def __init__(self, path):
        self.path, self.pos, self.rows = Path(path), 0, []

    def read(self) -> list:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:                                  # the file was replaced: start again
            self.pos, self.rows = 0, []
        if size == self.pos:
            return []
        new = []
        with self.path.open("rb") as f:
            f.seek(self.pos)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return []
        for ln in data[:end + 1].splitlines():
            try:
                row = json.loads(ln.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(row, dict):
                new.append(row)
        self.pos += end + 1
        self.rows += new
        return new

    def all(self) -> list:
        self.read()
        return self.rows


# ---- an MCP agent session's transcript (stream-json) ---------------------------------------------------------------

def short_tool(name: str) -> str:
    return name.removeprefix("mcp__hydra__")


def arg_summary(args: dict, width: int = 60) -> str:
    """The few arguments that say what a call was about (never the archive path)."""
    if not isinstance(args, dict):
        return ""
    keys = [k for k in ("id", "step", "name", "path", "collection", "query", "topic", "file", "pattern", "command")
            if args.get(k) not in (None, "", [], {})]
    s = " ".join(f"{k}={str(args[k])[:40]}" for k in keys[:3])
    return s if len(s) <= width else s[:width - 1] + "…"


class Session:
    """The rows, calls and cost of a `claude -p` stream-json transcript, fed event by event."""

    def __init__(self):
        self.rows, self.calls, self.pending = [], [], {}
        self.began = None
        self.cost, self.turns, self.result, self.model, self.archive = None, None, None, None, None
        self.usage = {"input": 0, "output": 0}

    def _row(self, now, verb, obj="", descriptor="", kind="info", who="agent"):
        if self.began is None:
            self.began = now
        self.rows.append({"t": round(now - self.began, 1), "at": time.strftime("%H:%M:%S", time.localtime(now)),
                          "stage": "session", "verb": verb, "object": obj, "descriptor": descriptor, "who": who,
                          "kind": kind})

    def feed(self, events: list, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for ev in events:
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.model = ev.get("model") or self.model
                self._row(now, "Ran", f"session {ev.get('model') or ''}".strip(), "Session", "phase", "driver")
            elif t == "assistant":
                msg = ev.get("message") or {}
                u = msg.get("usage") or {}
                self.usage["input"] += int(u.get("input_tokens") or 0)
                self.usage["output"] += int(u.get("output_tokens") or 0)
                for c in msg.get("content") or []:
                    if c.get("type") == "text" and (c.get("text") or "").strip():
                        self._row(now, "Said", c["text"].strip().splitlines()[0][:200], "Text")
                    elif c.get("type") == "tool_use":
                        name, args = c.get("name") or "?", c.get("input") or {}
                        if isinstance(args, dict) and args.get("archive") and name.startswith("mcp__hydra"):
                            self.archive = self.archive or str(args["archive"])
                        call = {"at": time.strftime("%H:%M:%S", time.localtime(now)), "tool": short_tool(name),
                                "ms": None, "ok": None, "error": None, "hydra": name.startswith("mcp__hydra"),
                                "_t": now}
                        self.calls.append(call)
                        self.pending[c.get("id")] = call
                        self._row(now, "Called", arg_summary(args), short_tool(name))
            elif t == "user":
                content = (ev.get("message") or {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if c.get("type") != "tool_result":
                        continue
                    call = self.pending.pop(c.get("tool_use_id"), None)
                    err = bool(c.get("is_error"))
                    if call is not None:
                        call["ok"] = not err
                        call["ms"] = int((now - call["_t"]) * 1000) if now > call["_t"] else None
                        if err:
                            call["error"] = _text_of(c.get("content"))[:300]
                    if err:
                        self._row(now, "Failed", _text_of(c.get("content"))[:120], call["tool"] if call else "Tool",
                                  "fail")
            elif t == "result":
                self.result = ev
                self.cost = ev.get("total_cost_usd")
                self.turns = ev.get("num_turns")
                bad = ev.get("is_error") or ev.get("subtype") not in (None, "success")
                self._row(now, "Ran", f"session {'stopped: ' + str(ev.get('subtype')) if bad else 'done'}", "Session",
                          "stop" if bad else "end", "driver")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    return str(content or "")


# ---- an archive: tasks, call log, questions ------------------------------------------------------------------------

def task_summary(path, stamps: bool = False) -> dict | None:
    """One `.hydra/tasks/<id>.md`: its front matter, its steps ticked of all, and the last log line."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    meta = {}
    if text.startswith("---"):
        parts = text.split("\n---", 2)
        try:
            import yaml
            meta = yaml.safe_load(parts[0].lstrip("-\n")) or {}
        except Exception:                                    # noqa: BLE001 - a half-written file reads as no meta
            meta = {}
        body = parts[1] if len(parts) > 1 else ""
    else:
        body = text
    steps = re.findall(r"(?m)^- \[([ x\-])\] ", body)
    log = body.split("## Log", 1)[1].strip().splitlines() if "## Log" in body else []
    found = re.findall(r"(?m)^- (\d{4}-\d\d-\d\dT[\d:]+Z?) ", "\n".join(log))
    seen = {"created": found[0] if found else None, "touched": found[-1] if found else None} if stamps else {}
    return {"id": meta.get("id") or Path(path).stem, "title": meta.get("title") or "", "status": meta.get("status"), **seen,
            "holder": meta.get("holder"), "model": meta.get("model"),
            "steps_done": sum(s in "x-" for s in steps), "steps_total": len(steps),
            "last": log[-1].lstrip("- ").strip() if log else ""}


def question_status(q: dict) -> str:
    return "answered" if q.get("answer") else "dismissed" if q.get("dismissed") else "open"


def epoch(stamp) -> float | None:
    """A log time ("2026-11-25T20:53:52Z", UTC; or a naive local one) as epoch seconds."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def in_window(stamp, window) -> bool:
    """Does a log time fall inside a run's (start, end) window (epoch seconds, 2 s slack)? No window: everything."""
    if not window:
        return True
    t = epoch(stamp)
    return t is not None and window[0] - 2 <= t <= window[1] + 2


def load_questions(archive) -> list[dict]:
    if not archive:
        return []
    p = Path(archive) / ".hydra" / "review" / "questions.yaml"
    try:
        import yaml
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001 - no file, or one being written
        return []
    rows = (doc or {}).get("questions") if isinstance(doc, dict) else None
    return [q for q in rows or [] if isinstance(q, dict) and q.get("id")]


def window_tasks(archive, window) -> set[str]:
    """The ids of the archive's tasks created inside a run's window (a workflow run's own tasks)."""
    mine = set()
    if not archive or not window:
        return mine
    for f in (Path(archive) / ".hydra" / "tasks").glob("*.md"):
        m = re.search(r"^- (\S+) created", _read_text(f), re.M)
        if m and in_window(m.group(1), window):
            mine.add(f.stem)
    return mine


def workflow_changes(archive, window, also=()) -> list[dict]:
    """What a workflow run changed, as the archive records it: [{kind, label, ids}]. Its tasks are the archive's tasks created
    inside the run's window; `.hydra/review/links.json` lists what each applied, and the questions asked in the window are its
    cards. Nothing recorded: []."""
    if not archive or not window:
        return []
    root = Path(archive) / ".hydra"
    mine = window_tasks(archive, window)
    links = read_json(root / "review" / "links.json", {}) or {}
    applied = [str(x) for t in sorted(mine) for x in ((links.get(t) or {}).get("applied") or [])]
    asked = [q["id"] for q in load_questions(archive) if in_window(q.get("asked"), window) or q["id"] in also]
    out = []
    if applied:
        out.append({"kind": "applied", "label": f"{len(applied)} link{'s' if len(applied) != 1 else ''} applied", "ids": applied})
    if asked:
        out.append({"kind": "asked", "label": f"{len(asked)} question{'s' if len(asked) != 1 else ''} asked", "ids": asked})
    return out


def _read_text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def archive_calls(archive) -> list[dict]:
    """hydra's own call log of an archive (every task's and the untasked), in time order."""
    rows = []
    for f in sorted((Path(archive) / ".hydra" / "tasks").glob("*.calls.jsonl")) if archive else []:
        rows += Tail(f).all()
    rows.sort(key=lambda r: str(r.get("at") or ""))
    return [{"at": str(r.get("at") or "")[11:19] or str(r.get("at") or ""), "tool": r.get("tool"), "ms": r.get("ms"),
             "ok": r.get("ok", True), "error": r.get("error"), "_stamp": r.get("at"), "_args": r.get("args"),
             "_step": r.get("step"), "_note": r.get("note") if isinstance(r.get("note"), dict) else None} for r in rows]


# ---- one run -------------------------------------------------------------------------------------------------------

class RunFiles:
    """A run's files, read incrementally: keep one per run and call its methods as often as needed."""

    def __init__(self, folder, kind: str = "pageflow", archive: str | None = None):
        self.folder, self.kind, self._archive = Path(folder), kind, archive
        self.driven = kind in ("pageflow", "engine")          # a driver writes pageflow/ (an engine run: its progress too)
        self.pf = self.folder / "pageflow"
        self._trace, self._found = Tail(self.pf / "trace.jsonl"), Tail(self.pf / "found.jsonl")
        self._hydra, self._models = Tail(self.pf / "hydra.jsonl"), Tail(self.pf / "models.jsonl")
        self._transcript = Tail(self.folder / "transcript.jsonl")
        self.session = Session()
        self.window = None          # (start, end) epoch seconds of a run that shares its archive: only its own calls and questions

    def _feed(self) -> None:
        new = self._transcript.read()
        if new:
            self.session.feed(new)

    def progress(self) -> dict | None:
        return read_json(self.pf / "progress.json") if self.driven else None

    def state(self) -> dict:
        if self.kind == "engine":                            # the engine keeps its state beside its stage: <run>/<flow>/
            got = next((f for f in sorted(self.folder.glob("*/state.json")) if f.parent.name != "pageflow"), None)
            return (read_json(got, {}) or {}) if got else {}
        return read_json(self.pf / "state.json", {}) or {}

    def archive(self) -> str | None:
        if self._archive:
            return self._archive
        if self.driven:
            return self.state().get("archive")
        self._feed()
        return self.session.archive

    def trace(self) -> list[dict]:
        if self.driven:
            return self._trace.all()
        self._feed()
        return self.session.rows

    def found(self) -> list[dict]:
        return self._found.all() if self.driven else []

    def found_rows(self) -> list[dict]:
        """What the Found tab and the page boxes read: a page-flow run's found rows as written; an engine run's decisions as the
        entities they touched (engineentities.rows), each with its `did`."""
        if self.kind == "engine":
            import engineentities
            return engineentities.rows(self)
        return self.found()

    @property
    def iso_from_archive(self) -> bool:
        """An engine run has no Read pass's mentions: a place's country comes from the archive's place record."""
        return self.kind == "engine"

    def models(self) -> list[dict]:
        """models.jsonl: every model call (its answer too, for runs made since the raw log)."""
        return self._models.all() if self.driven else []

    def hydra_rows(self) -> list[dict]:
        """hydra.jsonl as written: tool, args, ms, ok, text."""
        return self._hydra.all() if self.driven else []

    def mcp_rows(self) -> list[dict]:
        """An agent session's calls as hydra wrote them to the archive (`.hydra/tasks/*.calls.jsonl`) inside the run's
        window: tool, args, ms, ok, step and the call's `note` where it has one."""
        a = self.archive()
        return [c for c in (archive_calls(a) if a else []) if in_window(c.get("_stamp"), self.window)]

    def driver_log(self, lines: int = 300) -> list[str]:
        return tail_text(self.folder / "driver.log", lines)

    def transcript_events(self) -> list[dict]:
        return self._transcript.all()

    def calls(self) -> list[dict]:
        if self.driven:
            return [{"at": r.get("at"), "tool": r.get("tool"), "ms": r.get("ms"), "ok": r.get("ok", True),
                     "error": None if r.get("ok", True) else r.get("text")} for r in self._hydra.all()]
        a = self.archive()
        got = [c for c in (archive_calls(a) if a else []) if in_window(c.get("_stamp"), self.window)]
        if got:
            return [{k: v for k, v in c.items() if not k.startswith("_")} for c in got]
        self._feed()
        return [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.session.calls]

    def model_calls(self) -> int:
        return len(self._models.all())

    def swept(self) -> set[str]:
        """The questions the run's own links sweep says it asked (its transcript): a question another run had already asked
        is the same question, so it keeps that run's time and falls outside this run's window."""
        out: set[str] = set()
        for ev in self._transcript.all():
            body = (ev.get("message") or {}).get("content")
            for b in body if isinstance(body, list) else []:
                got = b.get("content") if isinstance(b, dict) and b.get("type") == "tool_result" else None
                text = got if isinstance(got, str) else "".join(x.get("text", "") for x in got or [] if isinstance(x, dict))
                if '"asked"' not in text:
                    continue
                try:
                    asked = json.loads(text).get("asked")
                except (ValueError, AttributeError):
                    continue
                if isinstance(asked, dict):
                    out |= {v for v in asked.values() if isinstance(v, str) and v.startswith("ask-")}
        return out

    def questions(self) -> list[dict]:
        """The archive's questions; a run with a window: those asked inside it (`asked`; one with no time is no run's) and
        those its own sweep reports asking. Each carries its `passage` (answering.passages)."""
        mine = self.swept() if self.window else set()
        qs = [q for q in load_questions(self.archive()) if in_window(q.get("asked"), self.window) or q.get("id") in mine]
        try:
            import answering
            return answering.attach(self.archive(), qs)
        except Exception:                                    # noqa: BLE001 - the passage is a help, never a reason to lose a question
            return qs

    def tasks(self) -> list[dict]:
        a = self.archive()
        if not a:
            return []
        out = [task_summary(f, bool(self.window)) for f in sorted((Path(a) / ".hydra" / "tasks").glob("*.md"))]
        out = [t for t in out if t]
        if not self.window:
            return out
        w = self.window                                      # a run that shares its archive: only tasks it touched
        def mine(t):
            a0, a1 = epoch(t.get("created")), epoch(t.get("touched"))
            return a0 is not None and a1 is not None and a0 <= w[1] + 2 and a1 >= w[0] - 2
        return [{k: v for k, v in t.items() if k not in ("created", "touched")} for t in out if mine(t)]

    def report(self) -> str | None:
        try:
            return (self.folder / "report.md").read_text(encoding="utf-8")
        except OSError:
            return None

    def cost(self) -> dict:
        """{usd, haiku: [used, cap], sonnet: [used, cap], turns}: a page-flow run's from state.json, a session's from
        its transcript's result (None until it ends)."""
        if self.driven:
            m = self.state().get("models") or {}
            used, caps, tokens = m.get("used") or {}, m.get("caps") or m.get("budgets") or {}, m.get("tokens") or {}  # caps: the run's own, kept when limits are off
            usd = round(sum(float((v or {}).get("cost_usd") or 0) for v in tokens.values()), 4) if tokens else 0.0
            if not m:                                        # no state yet: nothing counted
                return {"usd": usd, "haiku": None, "sonnet": None, "turns": None}
            return {"usd": usd, "haiku": [used.get("haiku", 0), caps.get("haiku")],
                    "sonnet": [used.get("sonnet", 0), caps.get("sonnet")], "turns": None}
        self._feed()
        return {"usd": self.session.cost, "haiku": None, "sonnet": None, "turns": self.session.turns}


# ---- formatting: (text, tag) pieces for a window or a terminal -----------------------------------------------------

def run_lines(rows: list[dict], width: int = 90) -> list[tuple[str, str, str]]:
    """The Run tab: newest first, repeats folded, as (time, text, tag) with tag fail | phase | ''."""
    out = []
    for r in reversed(fold(rows)):
        tag = "fail" if r.get("kind") in ("fail", "escalated", "stop") else "phase" if r.get("kind") == "phase" else ""
        out.append((r.get("at", ""), line(r, width), tag))
    return out


def tail_lines(rows: list[dict], n: int = 20, width: int = 100) -> list[str]:
    """The last `n` lines, oldest first, for `apollo.py tail`."""
    return [f"{at}  {text}" for at, text, _ in reversed(run_lines(rows, width)[:n])]


def _found_by_page(rows: list[dict]) -> tuple[dict, dict]:
    """One record per (schema, thing): a later state wins, the first page found stays, `cited` sticks; and those
    records by page (0: no page)."""
    best = {}
    for r in rows:
        k = (r.get("schema"), r.get("thing"))
        old = best.get(k)
        best[k] = {**(old or {}), **{x: v for x, v in r.items() if v is not None},
                   "page": (old or {}).get("page") or r.get("page"),
                   "state": "cited" if "cited" in ((old or {}).get("state"), r.get("state")) else r.get("state")}
    by_page = {}
    for r in best.values():
        by_page.setdefault(r.get("page") or 0, []).append(r)
    return best, by_page


def found_lines(rows: list[dict], quotes: bool = False) -> tuple[list[list[tuple[str, str]]], str]:
    """The Found tab: one line per record (a later state wins, the first page found stays), grouped by page; and the
    count line. Each line is a list of (text, tag) pieces."""
    best, by_page = _found_by_page(rows)
    out = []
    for page in sorted(by_page):
        recs = sorted(by_page[page], key=lambda r: (r.get("order", 0), r.get("schema", "")))
        cited = sum(r.get("state") == "cited" for r in recs)
        out.append([((f"p. {page}" if page else "no page") + f"  ·  {len(recs)} found, {cited} cited", "where")])
        for r in recs:
            cat = CATEGORIES.get(r.get("schema"), ("Entities", "#6b7280"))[0]
            dashed = r.get("state") != "cited"
            tag = "dashed" if dashed else cat
            out.append([("   " + ("○ " if dashed else "● "), tag), (f"{r.get('schema', ''):<14}", tag),
                        (f"{r.get('thing', '')}", "")])
            if quotes and r.get("quote"):
                q = r["quote"] if len(r["quote"]) < 160 else r["quote"][:159] + "…"
                out.append([(f"{'':>19}“{q}”", "quote")])
        out.append([("", "")])
    count = f"{len(best)} records on {len([p for p in by_page if p])} pages  ·  ● cited  ○ proposed"
    return out, count


def call_rows(calls: list[dict], slowest: int = 5) -> list[dict]:
    """The Calls tab's rows in order, the `slowest` few (by ms) marked `slow`."""
    timed = sorted((c for c in calls if isinstance(c.get("ms"), (int, float))), key=lambda c: -c["ms"])
    slow = {id(c) for c in timed[:slowest]}
    return [{**c, "slow": id(c) in slow} for c in calls]


def call_line(c: dict) -> str:
    ms = f"{c['ms']:>7} ms" if isinstance(c.get("ms"), (int, float)) else "      … ms"
    ok = "ok" if c.get("ok") else ("…" if c.get("ok") is None else "ERROR " + str(c.get("error") or "")[:80])
    return f"{str(c.get('at') or ''):>8}  {str(c.get('tool') or ''):<26} {ms}  {ok}"


def calls_head(calls: list[dict]) -> str:
    errs = sum(c.get("ok") is False for c in calls)
    timed = [c for c in calls if isinstance(c.get("ms"), (int, float))]
    top = max(timed, key=lambda c: c["ms"]) if timed else None
    return f"{len(calls)} calls · {errs} errors" + (f" · slowest {top['tool']} {top['ms']} ms" if top else "")


def question_lines(qs: list[dict]) -> list[tuple[str, str]]:
    """The Questions tab: each `ask_person` card as (text, tag) lines, open ones first."""
    out = []
    for q in sorted(qs, key=lambda q: question_status(q) != "open"):
        st = question_status(q)
        head = f"[{st}] {q['id']}" + (f" · {q['kind']}" if q.get("kind") else "") + \
               (f" · p. {q['page']}" if q.get("page") else "") + \
               (f" · {', '.join(map(str, q['records']))}" if q.get("records") else "")
        out.append((head, st))
        out.append(("  " + str(q.get("question") or ""), ""))
        if q.get("quote"):
            out.append((f"  “{q['quote'][:300]}”", "quote"))
        if q.get("options"):
            out.append(("  options: " + " / ".join(map(str, q["options"])), "dim"))
        if q.get("suggestion"):
            out.append(("  suggestion: " + str(q["suggestion"]), "dim"))
        a = q.get("answer") or {}
        if a:
            out.append(("  answer: " + " — ".join(str(x) for x in (a.get("choice"), a.get("text")) if x), "answered"))
        out.append(("", ""))
    return out


def report_lines(md: str | None) -> list[tuple[str, str]]:
    """report.md as plain text: headings tagged h1 | h2 | h3, tables as rows of cells, emphasis and code marks gone."""
    if not md:
        return []
    out = []
    for raw in md.splitlines():
        s = raw.rstrip()
        if re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*", s):
            continue                                        # a table's rule line
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            out.append((_plain(m.group(2)), f"h{len(m.group(1))}"))
            continue
        if s.lstrip().startswith("|"):
            cells = [_plain(c.strip()) for c in s.strip().strip("|").split("|")]
            out.append(("  " + "  │  ".join(cells), "table"))
            continue
        s = re.sub(r"^(\s*)[-*] ", r"\1• ", s)
        out.append((_plain(s), ""))
    return out


def _plain(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)


def bar_text(fraction, width: int = 10) -> str:
    """A text progress bar for the runs list: `██████░░░░ 62%`."""
    if fraction is None:
        return ""
    f = max(0.0, min(float(fraction), 1.0))
    n = int(round(f * width))
    return "█" * n + "░" * (width - n) + f" {f:.0%}"


def elapsed_text(seconds) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def cost_text(cost: dict | None, budget_usd=None) -> str:
    """`$0.42 / $5` for a session; `$0.42 · H 37/80 S 1/6` for a page-flow run (caps of None: no limit)."""
    if not cost:
        return ""
    usd = cost.get("usd")
    out = "" if usd is None else f"${usd:.2f}"
    if budget_usd is not None:
        out += f" / ${budget_usd:g}"
    for k, letter in (("haiku", "H"), ("sonnet", "S")):
        v = cost.get(k)
        if v:
            out += f" · {letter} {v[0]}/{'∞' if v[1] is None else v[1]}"
    return out.strip(" ·")


# ---- structured: each tab as rows with fields (`apollo.py view --json`, for Apollo's web app) ---------------------

def run_rows(rows: list[dict], limit: int = 2000) -> list[dict]:
    """The Run tab: newest first, repeats folded (`count`), at most `limit` rows. `tag`: fail | phase | ''."""
    out = []
    for r in reversed(fold(rows)):
        if len(out) >= limit:
            break
        kind = r.get("kind") or "info"
        out.append({"at": r.get("at", ""), "verb": r.get("verb", ""), "count": r.get("fold", 1), "n": r.get("n"),
                    "page": r.get("page"), "object": r.get("object") or "", "more": r.get("more"),
                    "descriptor": r.get("descriptor") or "",
                    "who": None if r.get("who") in (None, "", "driver") else r.get("who"), "kind": kind,
                    "tag": "fail" if kind in ("fail", "escalated", "stop") else "phase" if kind == "phase" else ""})
    return out


def found_groups(rows: list[dict]) -> dict:
    """The Found tab: {groups: [{page, found, cited, records: [{schema, category, thing, state, cited, quote}]}],
    total, on_pages}: the records, and the pages they are on; pages in order (0: no page), records as found_lines
    orders them."""
    best, by_page = _found_by_page(rows)
    groups = []
    for page in sorted(by_page):
        recs = sorted(by_page[page], key=lambda r: (r.get("order", 0), r.get("schema", "")))
        groups.append({"page": page, "found": len(recs), "cited": sum(r.get("state") == "cited" for r in recs),
                       "records": [{"schema": r.get("schema") or "", "thing": r.get("thing") or "",
                                    "category": CATEGORIES.get(r.get("schema"), ("Entities", ""))[0],
                                    "state": r.get("state") or "", "cited": r.get("state") == "cited",
                                    "quote": r.get("quote") or ""} for r in recs]})
    return {"groups": groups, "total": len(best), "on_pages": len([p for p in by_page if p])}


def _option_list(v) -> list[str]:
    """A question's options as a list: a list as written, a text as its " / " or line separated choices."""
    if isinstance(v, str):
        v = [x for x in re.split(r"\n|\s+/\s+", v) if x.strip()]
    return [str(x) for x in v or []]


def question_title(q: dict, labels: dict) -> str:
    """What to call a question: the names of the records it is about ("A ↔ B"), else the start of its text."""
    named = [labels[r] for r in map(str, q.get("records") or []) if r in labels]
    if named:
        return " ↔ ".join(named[:3])
    text = " ".join(str(q.get("question") or "").split())
    return text if len(text) <= 80 else text[:79].rstrip() + "…"


def question_rows(qs: list[dict], names: dict | None = None, extra: list[dict] | None = None) -> dict:
    """The Questions tab: {rows: [{id, title, status, kind, page, records, labels, question, quote, options, choices,
    suggestion, answer, asked, group}], open, answered, dismissed}, open ones first, then `extra` (the build's
    own questions, answering.build_rows). `options` is a list; `choices` is what one click answers ({choice, label}: the
    option's number for hydra's answer, the words on the button); `labels` names each record id (from `names`: {record
    id: name}); `title` is the human name of the card; `answer` is {choice, text, by, at} once answered or dismissed."""
    import answering
    names = names or {}
    rows = []
    for q in sorted(qs, key=lambda q: question_status(q) != "open"):
        st = question_status(q)
        a = q.get("answer") if isinstance(q.get("answer"), dict) else ({"text": q["answer"]} if q.get("answer") else None)
        if st == "dismissed":
            d = q.get("dismissed") if isinstance(q.get("dismissed"), dict) else {}
            a = {"choice": "Dismissed", "by": answering.you(d.get("by")), "at": d.get("at")}
        recs = [str(x) for x in q.get("records") or []]
        labels = {r: names[r] for r in recs if r in names}
        opts = _option_list(q.get("options"))
        rows.append({"id": q["id"], "title": question_title(q, labels), "status": st, "kind": q.get("kind"),
                     "page": q.get("page"), "records": recs, "labels": labels, "question": str(q.get("question") or ""),
                     "quote": str(q.get("quote") or ""), "options": opts,
                     "choices": [{"choice": str(i), "label": o, **({"none": True} if answering.is_none(o) else {})} for i, o in enumerate(opts, 1)],
                     "passage": q.get("passage"),
                     "suggestion": None if q.get("suggestion") is None else str(q["suggestion"]),
                     "answer": None if not a else {"choice": a.get("choice"), "text": a.get("text"), "by": answering.you(a.get("by")), "at": a.get("at")},
                     "asked": q.get("asked"), "group": "asked"})
    rows += extra or []
    counts = {s: sum(r["status"] == s for r in rows) for s in ("open", "answered", "dismissed")}
    return {"rows": rows, **counts}


def calls_view(calls: list[dict], slowest: int = 5, limit: int = 2000) -> dict:
    """The Calls tab: {rows: [{at, tool, ms, ok, error, slow}], total, errors, slowest: [{tool, ms, at}]}, the rows in
    order (the last `limit`), the `slowest` few by ms marked `slow`."""
    rows = call_rows(calls, slowest)
    timed = sorted((c for c in calls if isinstance(c.get("ms"), (int, float))), key=lambda c: -c["ms"])[:slowest]
    return {"rows": [{"at": c.get("at"), "tool": c.get("tool"), "ms": c.get("ms"), "ok": c.get("ok"),
                      "error": None if c.get("ok") is not False else str(c.get("error") or ""), "slow": c["slow"]}
                     for c in rows[-limit:]],
            "total": len(calls), "errors": sum(c.get("ok") is False for c in calls),
            "slowest": [{"tool": c.get("tool"), "ms": c["ms"], "at": c.get("at")} for c in timed]}


def report_blocks(md: str | None) -> list[dict]:
    """report.md as blocks: {type: h1 | h2 | h3, text}, {type: p | li, text}, {type: table, rows: [[cell]]} (the first
    row the header), {type: gap}; emphasis and code marks gone."""
    out = []
    for raw in (md or "").splitlines():
        s = raw.rstrip()
        if re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*", s):
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            out.append({"type": f"h{len(m.group(1))}", "text": _plain(m.group(2))})
        elif s.lstrip().startswith("|"):
            cells = [_plain(c.strip()) for c in s.strip().strip("|").split("|")]
            if out and out[-1]["type"] == "table":
                out[-1]["rows"].append(cells)
            else:
                out.append({"type": "table", "rows": [cells]})
        elif re.match(r"^\s*[-*] ", s):
            out.append({"type": "li", "text": _plain(re.sub(r"^\s*[-*] ", "", s)), "indent": len(s) - len(s.lstrip())})
        elif not s.strip():
            if out and out[-1]["type"] != "gap":
                out.append({"type": "gap"})
        else:
            out.append({"type": "p", "text": _plain(s)})
    while out and out[-1]["type"] == "gap":
        out.pop()
    return out


# ---- the window's richer surfaces: the page cells and heatmap, the Route, the Report's measures ---------------------

SKIPPED = ("empty", "redacted", "out-of-scope")
PASS_NAMES = {"preflight": "Preflight", "setup": "Setup", "triage": "Triage", "A": "Mentions", "B": "Roster",
              "CT": "Facts and ties", "I": "Islands"}
TARGET_NAMES = {"ties_per_entity": "Ties", "persons_no_tie": "Untied", "events_dated_involved": "Dated",
                "places_sited": "Sited", "duplicates": "Dupes", "notes_full_sentences": "Notes",
                "content_pages_with_record": "Pages"}


PLACE_SCHEMAS = {"Place", "Address", "Feature", "GeoLink"}       # what counts as a place found on a page


def page_places(found: list[dict]) -> dict:
    """{page: places}: the place records (and geo links) found on each page."""
    _, by_page = _found_by_page(found)
    return {p: sum(1 for r in rs if (r.get("schema") or "") in PLACE_SCHEMAS) for p, rs in by_page.items() if p}


def page_counts(found: list[dict]) -> dict:
    """{page: (records, quotes)}: the records found on each page (one per schema and thing) and how many carry a quote."""
    _, by_page = _found_by_page(found)
    return {p: (len(rs), sum(bool(r.get("quote")) for r in rs)) for p, rs in by_page.items() if p}


def cells(progress: dict | None, found: list[dict]) -> list[list]:
    """[[page, status, records]] for every page the run works (the pages strip)."""
    counts = page_counts(found)
    out = []
    for k, v in ((progress or {}).get("pages") or {}).items():
        try:
            p = int(k)
        except (TypeError, ValueError):
            continue
        if v != "out-of-scope":
            out.append([p, v, counts.get(p, (0, 0))[0]])
    return sorted(out)


def content_pages(progress: dict | None) -> list[int]:
    """The pages that are read (not skipped as empty or redacted), in order."""
    out = []
    for k, v in ((progress or {}).get("pages") or {}).items():
        try:
            if v not in SKIPPED:
                out.append(int(k))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def page_groups(found: list[dict]) -> dict:
    """{page: {group: count}}: the records found on each page by entity category (runview.TYPE_GROUPS), none left out."""
    from runview import type_group
    _, by_page = _found_by_page(found)
    out = {}
    for p, rs in by_page.items():
        if not p:
            continue
        g = {}
        for r in rs:
            k = type_group(r.get("schema") or "")
            g[k] = g.get(k, 0) + 1
        out[p] = g
    return out


def heat(progress: dict | None, state: dict, found: list[dict], questions: list[dict] | None = None) -> list[dict]:
    """One row per page for the heatmap: page, status, records, quotes, places, groups ({category: records}), calls, seconds,
    problems ({pass: count}); `asked`, the questions put to the person on the page (from `questions`), when there are any."""
    counts, places, groups = page_counts(found), page_places(found), page_groups(found)
    asked: dict = {}
    for q in questions or []:
        try:
            asked[int(q.get("page"))] = asked.get(int(q.get("page")), 0) + 1
        except (TypeError, ValueError):
            continue
    sp = state.get("pages") or {}
    out = []
    for page, status, _ in cells(progress, found):
        s = sp.get(str(page)) or {}
        rec, quo = counts.get(page, (0, 0))
        problems = {k: len(v) for k, v in (s.get("fails") or {}).items() if v}
        out.append({"page": page, "status": status, "records": rec, "quotes": quo, "places": places.get(page, 0),
                    "groups": groups.get(page, {}),
                    "calls": s.get("calls") or 0,
                    "seconds": round(float(s.get("seconds") or 0), 1), "problems": problems,
                    **({"asked": asked[page]} if asked.get(page) else {}),
                    **({"reason": "gave up: " + str(s["gave_up"])} if s.get("gave_up") else {})})
    return out


def _span(pages: list[int]) -> str:
    return f"{pages[0]}-{pages[-1]}" if pages else ""


def route(trace: list[dict], progress: dict | None, flush: int = 10, finished: bool = False) -> dict:
    """The run's Route from its trace's phases: Preflight, Setup, Triage, Mentions, Roster, one node per chunk of
    facts and ties (the content pages, `flush` at a time), Islands, Close. {nodes: [{key, label, kind, pages, state}],
    now: index of the node in hand | None}. A node is done once a later one has started."""
    phases = [r for r in trace if r.get("kind") == "phase"]
    stages = [r.get("stage") for r in phases]
    content = content_pages(progress)
    planned = max(1, -(-len(content) // flush)) if content else 0
    started = {"preflight": "preflight" in stages, "setup": "setup" in stages, "triage": "triage" in stages,
               "A": "A" in stages, "B": "B" in stages}
    chunks = 0                                               # C phases in a row are one chunk's start
    saves = 0
    prev = None
    for st in stages:
        if st == "C" and prev != "C":
            chunks += 1
        if st == "save":
            saves += 1
        prev = st
    islands = "I" in stages
    close = any(r.get("stage") == "close" for r in trace)
    nchunks = max(planned, chunks)
    nodes = [{"key": k, "label": PASS_NAMES[k], "kind": "pass", "pages": ""} for k in ("preflight", "setup", "triage", "A", "B")]
    for i in range(nchunks):
        sl = content[i * flush:(i + 1) * flush]
        nodes.append({"key": f"chunk{i + 1}", "label": _span(sl) or f"chunk {i + 1}", "kind": "chunk", "pages": _span(sl)})
    nodes += [{"key": "I", "label": "Islands", "kind": "pass", "pages": ""}, {"key": "close", "label": "Close", "kind": "pass", "pages": ""}]
    started.update({"I": islands, "close": close})
    reached = [started[n["key"]] if n["kind"] == "pass" else False for n in nodes]
    for i in range(nchunks):
        reached[5 + i] = i < chunks
    last = max((i for i, r in enumerate(reached) if r), default=-1)
    now = None if finished or last < 0 else last
    for i, n in enumerate(nodes):
        n["state"] = "done" if (finished or i < last) else "now" if i == last else "todo"
    return {"nodes": nodes, "now": now, "chunk": {"at": min(chunks, nchunks), "of": nchunks} if nchunks else None,
            "group": [5, 5 + nchunks] if nchunks else None}


def measures(state: dict, calls: list[dict], progress: dict | None) -> dict:
    """The Report's numbers: targets (richness bars), seconds by pass, hydra calls by tool, the slowest calls, cost per
    model, records saved."""
    run = ((state.get("richness") or {}).get("run")) or {}
    targets = []
    for k, label in TARGET_NAMES.items():
        t = run.get(k)
        if isinstance(t, dict):
            targets.append({"key": k, "label": label, "value": t.get("value"), "bar": t.get("bar"), "ok": t.get("ok")})
    passes = [{"key": k, "label": PASS_NAMES.get(k, k), "seconds": float(v.get("seconds") or 0), "status": v.get("status")}
              for k, v in (state.get("passes") or {}).items() if isinstance(v, dict)]
    by = {}
    for c in calls:
        t = by.setdefault(c.get("tool") or "?", [0, 0.0])
        t[0] += 1
        t[1] += float(c.get("ms") or 0) / 1000
    tools = sorted(({"tool": k, "calls": v[0], "seconds": round(v[1], 1)} for k, v in by.items()), key=lambda r: -r["seconds"])
    slow = sorted((c for c in calls if isinstance(c.get("ms"), (int, float))), key=lambda c: -c["ms"])[:5]
    m = state.get("models") or {}
    tokens, used, caps = m.get("tokens") or {}, m.get("used") or {}, m.get("budgets") or {}
    cost = [{"model": k, "label": k.capitalize(), "usd": round(float((tokens.get(k) or {}).get("cost_usd") or 0), 2),
             "used": used.get(k, 0), "cap": caps.get(k)} for k in ("haiku", "sonnet")]
    counts = (run.get("_counts") or {}) if run else {}
    return {"targets": targets, "passes": passes, "wall_seconds": state.get("wall_seconds"), "tools": tools[:8], "tools_total": len(tools),
            "slowest": [{"tool": c.get("tool"), "seconds": round(c["ms"] / 1000, 1), "at": c.get("at")} for c in slow],
            "cost": cost, "usd": round(sum(c["usd"] for c in cost), 2), "records": counts.get("records")}

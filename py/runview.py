"""runview.py: what is actually going on in a run, as plain data for Apollo's page. Reads only (runfiles' files).

The Now sentence, the live Flow card and the run's configuration, the Found tab by entity, what a log row stands for
when it is unfolded (its records, its model call, its problems), and the Raw tab (driver log, hydra calls, model calls,
a session's transcript). Everything is additive JSON beside runfiles' builders.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from runfiles import PLACE_SCHEMAS, SKIPPED, _text_of, fold, run_rows, short_tool

# What a run does, as the window's Flow select names it: key, label. `read` is the page flow; the rest are hydra workflows.
FLOWS = [("read", "Basic"), ("mark", "Mark"), ("fit", "Fit"), ("assemble", "Assemble"), ("refine", "Refine"), ("timeline", "Timeline"), ("places", "Places"), ("stubs", "Stubs"),
         ("enrich", "Enrich"), ("wikidata", "Wikidata"), ("finance", "Finance"), ("wiki", "Curate")]
FLOW_LABEL = dict(FLOWS)
FLOW_DOMAIN = {"read": "Records", "mark": "Records", "fit": "Records", "assemble": "Graph", "refine": "Graph", "timeline": "Time", "places": "Geo", "stubs": "Profiles",
               "enrich": "Profiles", "wikidata": "Profiles", "finance": "Finance", "wiki": "Curate"}
# a flow renamed: its old runs show as the new one (2026-11-26: Links became Wikidata; its merges went to Assemble)
RENAMED = {"links": "wikidata"}
# the names runs were recorded under before the one-word ones: an old record's `flow` may hold either
OLD_LABELS = {"Read the source": "read", "Read": "read", "Wiki": "wiki", "Suggest links": "wikidata", "Links": "wikidata", "Place names": "places",
              "Fill stubs": "stubs"}
GRADES = ("haiku", "sonnet", "opus")
# What New run offers per flow: needs, `after` (the flow whose finished run on the archive it waits for), and the model grades the flow can use with their {default, max} agent counts.
# Read (the page flow): Haiku readers work pages at once, Sonnet agents work the hard pages at once; no Opus role.
# Every other flow is an engine flow (pageflow/engine.py): it judges with one model, and the person picks its grade (the
# strongest given an agent).
_ENGINE = {"haiku": {"default": 0, "max": 1}, "sonnet": {"default": 1, "max": 1}, "opus": {"default": 0, "max": 1}}
FLOW_SPEC = {
    "read": {"needs": "source", "grades": {"haiku": {"default": 4, "max": 8}, "sonnet": {"default": 2, "max": 4}}},
    **{k: {"needs": "archive", "after": "read", "grades": dict(_ENGINE)}
       for k in ("assemble", "timeline", "places", "stubs", "enrich", "finance", "wikidata", "wiki")},
    # Mark: Haiku reads a page at a time; Fit decides the unknowns Mark set aside (Sonnet), once a Mark run is done
    "mark": {"needs": "archive", "after": "read", "grades": {**_ENGINE, "haiku": {"default": 1, "max": 1}, "sonnet": {"default": 0, "max": 1}}},
    "fit": {"needs": "archive", "after": "mark", "grades": dict(_ENGINE)},
    # Refine: a final review of the finished set, Opus unless the person picks another grade
    "refine": {"needs": "archive", "after": "assemble", "grades": {**_ENGINE, "sonnet": {"default": 0, "max": 1},
                                                                   "opus": {"default": 1, "max": 1}}},
}


# The stages of an engine run (pageflow/engine.py), once, for Configure's Flow tab and a run's: key, label, the engine phase
# that marks it in hand (None: it follows its neighbour), who does it (`engine`: the flow's grade judges), note.
ENGINE_STEPS = (
    ("gather", "Gather", "gather", "code", "Collects the pages and entries the flow works from."),
    ("judge", "Judge", "judge", "engine", "Judges them in chunks, one chunk at a time."),
    ("check", "Check", None, "code", "Checks each judgement against its pages before it is kept."),
    ("apply", "Apply", "apply", "code", "Writes the kept judgements to the archive once."),
    ("report", "Report", "report", "code", "Writes the report."))


def engine_steps(flow: str) -> list[dict]:
    """The Flow tab's steps for an engine flow, before a run: the same five for every engine flow."""
    return [{"key": f"{flow}-{k}", "label": lab, "who": who, "role": flow if who == "engine" else None, "per": "chunk" if k == "judge" else "once",
             "note": note, "prompts": [], "switch": False, "budget": None} for k, lab, _ph, who, note in ENGINE_STEPS]


def engine_flow(flow: str, progress: dict | None, status: str) -> list[dict]:
    """An engine run's Flow card: engine_steps with a `state` each from the run's progress stage (check is done once apply starts)."""
    p = progress or {}
    order = [k for k, *_ in ENGINE_STEPS]
    here = next((k for k, _l, ph, *_ in ENGINE_STEPS if ph and ph == p.get("stage")), None)
    at = order.index(here) if here else -1
    live = status in ("running", "starting", "stopping")
    out = []
    for i, s in enumerate(engine_steps(flow)):
        st = "done" if status == "done" or i < at else "now" if i == at and live else "skipped" if i == at else "next"
        det = f"chunk {p.get('done') or 0} of {p['total']}" if st == "now" and s["label"] == "Judge" and p.get("total") else ""
        out.append({**s, "state": st, "detail": det})
    return out


def flow_grades(flow: str) -> dict:
    """A flow's grades as FLOW_SPEC gives them, each default replaced by the Settings page's (apollo.json), kept in bounds."""
    import settings
    low = 1 if flow == "read" else 0
    return {g: {**lim, "default": min(lim["max"], max(low, settings.value(f"agents.{flow}.{g}", lim["default"])))}
            for g, lim in ((FLOW_SPEC.get(flow) or {}).get("grades") or {}).items()}


def flow_catalogue() -> list[dict]:
    """The flows New run lists, in the order of their domains: key, name (and `label`, the same), domain, needs, grades."""
    return [{"key": k, "name": name, "label": name, "domain": FLOW_DOMAIN[k], "needs": FLOW_SPEC[k]["needs"], "after": FLOW_SPEC[k].get("after"),
             "grades": flow_grades(k)} for k, name in FLOWS]


def flow_key(rec: dict) -> str | None:
    """A run's flow as its key (see FLOWS). Old records: a page flow is a read, a renamed flow its new key, a workflow session
    is named by its workflow or its `workflow-<key>` name, and an old display name maps back; a brief session has none."""
    f = rec.get("flow")
    if f in FLOW_LABEL:
        return f
    if f in RENAMED:
        return RENAMED[f]
    if f in OLD_LABELS:
        return OLD_LABELS[f]
    nm = str(rec.get("name") or "")
    name = rec.get("workflow") or (nm.removeprefix("workflow-") if nm.startswith("workflow-") else "")
    name = RENAMED.get(name, name)
    if name in FLOW_LABEL:
        return name
    return "read" if rec.get("kind") == "pageflow" else None


def flow_label(rec: dict) -> str | None:
    """A run's flow in words (one word): see flow_key."""
    k = flow_key(rec)
    return FLOW_LABEL[k] if k else None


# ---- the Now sentence ---------------------------------------------------------------------------------------------------

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
STAGE_SENTENCES = {"preflight": "Checking that the document and the tools are ready.",
                   "setup": "Setting up a fresh archive for the run.",
                   "triage": "Choosing which pages are worth reading.",
                   "B": "Merging the names into one roster for the whole document.",
                   "save": "Saving the records to the archive.",
                   "I": "Working out which names belong to the same person.",
                   "close": "Running the final checks and writing the report."}


def gave_up_pages(state: dict) -> list[int]:
    return sorted(int(n) for n, p in (state.get("pages") or {}).items() if str(n).isdigit() and (p or {}).get("status") == "gave-up")


def gave_up_text(state: dict) -> str:
    """`gave up on p. 42` / `gave up on pp. 42, 57`: pages that hit their own call or time cap (the run went on)."""
    g = gave_up_pages(state)
    return "" if not g else f"gave up on p. {g[0]}" if len(g) == 1 else f"gave up on pp. {', '.join(map(str, g))}"


def _num(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _span_text(pages: list[int], unit: str = "page") -> str:
    if len(pages) == 1:
        return f"{unit} {pages[0]}"
    return f"{unit}s {pages[0]}–{pages[-1]}"


def duration_text(seconds) -> str:
    s = int(seconds or 0)
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60} min"
    if s >= 60:
        return f"{round(s / 60)} min"
    return f"{s} s"


def now_line(kind: str, status: str, detail: str, progress: dict | None, state: dict, records: int, parallel: int | None,
             elapsed, last_page: int | None, calls: int = 0, workflow: str | None = None, pages_done: int = 0) -> str:
    """One plain sentence on what the run is doing, or how it ended."""
    unit = "page"                                          # owner: Page 40, not Part 40
    live = status in ("running", "starting")
    if kind != "pageflow":
        what = f"the {workflow} steps" if workflow else "its task"
        if live or status == "stopping":
            return f"A Claude Code session is working through {what}. {calls} hydra calls so far."
        if status == "done":
            return f"Finished: the session made {calls} hydra calls in {duration_text(elapsed)}."
        return f"Stopped: {_why(detail, state, last_page, unit)}"
    p = progress or {}
    recs = (f" {records} records so far." if records else "") + (f" {gave_up_text(state).capitalize()}." if gave_up_text(state) else "")
    if live:
        stage = p.get("stage") or ""
        pages = {}
        for k, v in (p.get("pages") or {}).items():
            if str(k).isdigit():
                pages[int(k)] = v
        doing = sorted(k for k, v in pages.items() if v == "doing")
        content = len([1 for v in pages.values() if v not in SKIPPED and v != "out-of-scope"])
        if stage in ("A", "C", "T", "CT"):
            verb = "Listing the names on" if stage == "A" else "Writing the records and ties for"
            if doing:
                par = f", {_num(parallel)} at a time" if parallel and parallel > 1 else ""
                return f"{verb} {_span_text(doing, unit)} of {content}{par}.{recs}"
            return f"{verb} the {unit}s: {pages_done} of {content} done.{recs}"
        return STAGE_SENTENCES.get(stage, f"{p.get('label') or 'Starting'}.") + recs
    if status == "stopping":
        return "Stopping: the run is finishing the step in hand."
    if status == "done":
        return f"Finished: {records} records from {pages_done} {unit}s in {duration_text(elapsed)}."
    return f"Stopped: {_why(detail, state, last_page, unit)}"


def _why(detail: str, state: dict, last_page, unit: str) -> str:
    d = (detail or "").strip()
    where = f" on {unit} {last_page}" if last_page else ""
    if d == "stopped-budget":
        stop = str(state.get("stop") or "")
        if stop.startswith("spent"):
            return f"the cost cap was reached, so it wrote the report{where}."
        m = state.get("models") or {}
        used, caps = m.get("used") or {}, m.get("budgets") or {}
        for k in ("haiku", "sonnet"):
            if caps.get(k) is not None and used.get(k, 0) >= caps[k]:
                return f"the {k.capitalize()} safety limit on model calls was reached{where}."
        return f"a budget was used up{where}."
    table = {"stopped-flush-failed": "saving to the archive failed", "stopped-interrupted": "the run was interrupted",
             "stopped-hydra-refused": "hydra refused a call", "stopped-design-fault": "a pass kept failing its checks",
             "stopped-error": "the driver hit an error", "preflight-failed": "a check before the run failed"}
    if d in table:
        return table[d] + where + "."
    if d.startswith("signal"):
        return "it was stopped from Apollo" + where + "."
    if "wrapper is gone" in d:
        return "the run ended without saying why."
    return (d.replace("-", " ") or "the run ended") + where + "."


# ---- the live Flow card and the run's configuration ------------------------------------------------------------------

def flow_steps(steps: list[dict], rt: dict, trace: list[dict], state: dict, finished: bool, cfg: dict) -> list[dict]:
    """The Flow card's steps with a `state` each (done | now | next | skipped) and a short `detail` for the one in hand."""
    nodes = {n["key"]: n for n in rt.get("nodes") or []}
    chunks = [n for n in rt.get("nodes") or [] if n["kind"] == "chunk"]
    esc = bool(state.get("escalations")) or any(r.get("verb") == "Escalated" for r in trace)
    rated = any(r.get("descriptor") == "Rate" for r in trace)
    keys = {"Preflight": "preflight", "Setup": "setup", "Triage": "triage", "Names": "A", "Roster": "B",
            "Identity": "I", "Close": "close"}
    took = {"Preflight": "preflight", "Setup": "setup", "Triage": "triage", "Names": "A", "Roster": "B", "Records and ties": "CT",
            "Identity": "I"}
    passes = state.get("passes") or {}
    out = []
    for s in steps:
        label, st, det = s["label"], "next", ""
        if keys.get(label) in nodes:
            st = {"done": "done", "now": "now", "todo": "next"}[nodes[keys[label]]["state"]]
        elif label == "Records and ties":
            ss = [c["state"] for c in chunks]
            st = "done" if ss and all(x == "done" for x in ss) else "now" if "now" in ss else "next"
            if st == "now":
                det = f"chunk {ss.index('now') + 1} of {len(ss)}"
        elif label == "Hard pages":
            st = "done" if esc else ("skipped" if finished else "next")
            det = "used" if esc else ""
            if gave_up_text(state):
                det = (det + ", " if det else "") + gave_up_text(state)
        elif label == "Lag check":
            st = "skipped" if s.get("on") is False else ("done" if finished else "next")
        elif label == "Rate":
            st = "skipped" if not cfg.get("rubric") else ("done" if rated or finished else "next")
        if s.get("on") is False:
            st = "skipped"
        if finished and st == "now":
            st = "done"
        secs = (passes.get(took.get(label)) or {}).get("seconds") if st == "done" else None
        out.append({**s, "state": st, "detail": det, "secs": float(secs) if isinstance(secs, (int, float)) else None})
    return out


def session_flow(steps: list[dict], tasks: list[dict], status: str) -> list[dict]:
    """A workflow session's Flow card: the session itself, then the workflow's steps ticked as the archive's task shows."""
    done = sum(t.get("steps_done") or 0 for t in tasks)
    live = status in ("running", "starting", "stopping")
    out = []
    for i, s in enumerate(steps):
        if i == 0:
            st = "done" if status == "done" else "now" if live else "skipped"
        else:
            st = "done" if (i <= done or status == "done") else "now" if (i == done + 1 and live) else "next"
        out.append({**s, "state": st, "detail": ""})
    return out


def model_word(model_id) -> str:
    m = str(model_id or "")
    for k in ("haiku", "sonnet", "opus"):
        if k in m:
            ver = re.search(r"(\d+)-(\d+)", m)
            return f"{k.capitalize()} {ver.group(1)}.{ver.group(2)}" if ver else k.capitalize()
    return m.replace("claude-", "")


def archive_row(path) -> dict | None:
    """The Archive row: its name and its run's folder name (`alwaleed · 2026-11-25-dry-full-alwaleed`), the whole path as `title`."""
    if not path:
        return None
    p = Path(str(path))
    run = p.parent.name
    return {"label": "Archive", "value": p.name if not run or run == p.name else f"{p.name} · {run}", "title": str(path)}


def config_rows(cfg: dict, rec: dict, state: dict) -> list[dict]:
    """The run's configuration as used, plain labelled rows [{label, value}], from its run-config.yaml."""
    if not cfg:
        return []
    rows = []

    def add(label, value):
        if value not in (None, "", []):
            rows.append({"label": label, "value": str(value)})
    src = cfg.get("source") or {}
    unit = "Pages"                                         # owner: Page 40, not Part 40
    add("Flow", flow_label(rec))
    add("Runner", "Claude Code")
    add("Source", src.get("title") or cfg.get("source_slug"))
    pg = cfg.get("pages") or {}
    if pg.get("from") is not None:
        add(unit, f"{pg['from']}–{pg['to']}")
    m, b = cfg.get("models") or {}, cfg.get("budgets") or {}
    if cfg.get("budget_usd") is not None:
        add("Cost cap", f"${float(cfg['budget_usd']):g}")
    add("Reader", model_word(m.get("haiku")))
    add("Hard pages", model_word(m.get("sonnet")))
    add("Reader calls", b.get("haiku"))
    add("Hard-page calls", b.get("sonnet"))
    add("Escalations", b.get("escalations"))
    add("Calls a page", b.get("calls_per_page"))
    add("Seconds a page", b.get("seconds_per_page"))
    add("Haiku agents", cfg.get("parallel"))
    add("Sonnet agents", cfg.get("sonnet_parallel"))
    add("Save every", f"{cfg['flush_pages']} pages" if cfg.get("flush_pages") else None)
    add("Ties batch", cfg.get("ties_batch"))
    add("Lag check", "On" if (cfg.get("lag") or {}).get("enabled") else "Off")
    add("Rating", "On" if cfg.get("rubric") else "Off")
    ar = archive_row(rec.get("archive"))
    if ar:
        rows.append(ar)
    add("Dry", "Yes" if cfg.get("stub_models") or rec.get("dry") else "No")
    return rows


def session_config_rows(rec: dict) -> list[dict]:
    rows = [("Flow" if rec.get("kind") == "engine" else "Workflow", FLOW_LABEL.get(rec.get("flow")) or str(rec.get("workflow") or str(rec.get("name") or "").removeprefix("workflow-")).capitalize()),
            ("Model", model_word(rec.get("model"))), ("Runner", None if rec.get("kind") == "engine" else "Claude Code")]   # an engine run has no session
    out = [{"label": a, "value": str(b)} for a, b in rows if b]
    ar = archive_row(rec.get("archive"))
    if ar:
        out.append(ar)
    return out + [{"label": "Dry", "value": "Yes" if rec.get("dry") else "No"}]


# ---- the Found tab by entity -----------------------------------------------------------------------------------------

TYPE_GROUPS = [("people", "People", {"Person"}), ("bodies", "Bodies", {"PublicBody", "Organization", "Company"}),
               ("places", "Places", PLACE_SCHEMAS), ("events", "Events", {"Event", "Payment"}),
               ("ties", "Ties", {"Employment", "Membership", "Representation", "UnknownLink", "Family", "Associate", "Ownership", "Directorship"}),
               ("posts", "Positions", {"Position", "Occupancy"})]


def type_group(schema: str) -> str:
    for key, _, schemas in TYPE_GROUPS:
        if schema in schemas:
            return key
    return "other"


TIE_ENDS = ("member", "employee", "person", "subject", "agent", "owner", "holder", "director", "payer", "source",
            "organization", "employer", "relative", "associate", "object", "client", "asset", "post", "beneficiary", "target",
            "entity", "location")                         # a GeoLink: the entity, then the place
_NAMES_MEMO: dict = {}


_RECS_MEMO: dict = {}


def archive_records(archive) -> list[dict]:
    """Every record of the archive's collections ({id, schema, properties}), read again only when a collection file changes."""
    root = Path(str(archive or ""))
    files = sorted((root / "collections").glob("*.json")) if root.is_dir() else []
    key = (str(root), tuple((f.name, f.stat().st_mtime_ns) for f in files))
    if _RECS_MEMO.get("key") == key:
        return _RECS_MEMO["recs"]
    recs = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recs += [r for r in (data if isinstance(data, list) else data.get("records") or []) if isinstance(r, dict) and r.get("id")]
    _RECS_MEMO.update(key=key, recs=recs)
    return recs


def archive_names(archive) -> dict:
    """{record id: what to call it} from the archive's collections: an entity's name, a tie as "A → B" by its two ends'
    names. Read again only when a collection file changes."""
    root = Path(str(archive or ""))
    recs = archive_records(archive)
    key = (str(root), id(recs))
    if _NAMES_MEMO.get("key") == key:
        return _NAMES_MEMO["names"]
    names = {r["id"]: ((r.get("properties") or {}).get("name") or [None])[0] for r in recs}
    prefix = Path(str(archive)).name + ":"

    def pick(i: str) -> str:
        return names.get(i) or names.get(i.removeprefix(prefix)) or i
    for r in recs:
        if names.get(r["id"]):
            continue
        p = r.get("properties") or {}
        ends = [v[0] for k in TIE_ENDS for v in [p.get(k)] if isinstance(v, list) and v and isinstance(v[0], str)][:2]
        if len(ends) == 2:
            names[r["id"]] = f"{pick(ends[0])} → {pick(ends[1])}"
    names = {k: v for k, v in names.items() if v}
    for k, v in list(names.items()):                              # references carry the archive's prefix ("draft:place-paris")
        names.setdefault(f"{Path(str(archive)).name}:{k}", v)
    _NAMES_MEMO.update(key=key, names=names, recs=recs)         # recs kept so its id stays this list's
    return names


def named(rows: list[dict], archive) -> list[dict]:
    """The found rows with each `thing` that is a record id (or "id → id") said by name, so a tie proposed as
    "person-x → company-y" and saved as "p41-t1" reads, and counts, as one: "X → Y"."""
    names = archive_names(archive)
    if not names:
        return rows
    def say(t: str) -> str:
        if t in names:
            return names[t]
        if " → " in t:
            a, b = t.split(" → ", 1)
            return f"{names.get(a.strip(), a.strip())} → {names.get(b.strip(), b.strip())}"
        return t
    return [{**r, "thing": say(r.get("thing") or "")} for r in rows]


DID_WORDS = {"merge": "Merged", "apart": "Kept apart", "alias": "Alias", "rename": "Renamed", "retype": "Retyped", "prop": "Changed",
             "date": "Dated", "order": "Ordered", "bind": "Bound", "none": "No match", "link": "Placed", "tie": "Tied", "stake": "Held",
             "payment": "Paid", "accept": "Accepted", "reject": "Rejected", "leave": "Left", "ask": "Asked", "answer": "Answered"}


def found_entities(rows: list[dict], quote_max: int = 400) -> dict:
    """{entities: [{key, schema, group, thing, records, pages, cited, recs: [{page, state, quote, did?}], did?}], types: [{key,
    label, count}], dids: [{key, label, count}], entity_count}. One entity is one (schema, thing); its records are where it was
    found (a page and its quote, the same quote on a page once); `cited` once any of them is. A row that says what a run did to
    the entity (`did`: {kind, verb, other?, text?, why?}, an engine run's decisions) keeps it, on the record and in `did` (each once,
    with `run` when the row names its run); `dids` counts the decision kinds, for the one filter on them."""
    ents = {}
    for r in rows:
        e = ents.setdefault((r.get("schema") or "", r.get("thing") or ""), {"seen": {}, "recs": [], "did": {}})
        did = r.get("did")
        if did:
            sig = (did.get("kind"), did.get("verb"), (did.get("other") or {}).get("name"), did.get("text"), r.get("page") or 0, r.get("run"))
            e["did"].setdefault(sig, {**did, "page": r.get("page") or 0, **({"run": r["run"]} if r.get("run") else {})})
        sig = (r.get("page") or 0, (r.get("quote") or "")[:80], (did or {}).get("kind"), (did or {}).get("verb"), (did or {}).get("text"), r.get("run"))
        if sig in e["seen"]:
            if r.get("state") == "cited":
                e["seen"][sig]["state"] = "cited"
            continue
        rec = {"page": r.get("page") or 0, "state": r.get("state") or "", "quote": (r.get("quote") or "")[:quote_max]}
        if did:
            rec["did"] = e["did"][(did.get("kind"), did.get("verb"), (did.get("other") or {}).get("name"), did.get("text"), r.get("page") or 0, r.get("run"))]
        e["seen"][sig] = rec
        e["recs"].append(rec)
    order = {key: i for i, (key, _, _) in enumerate(TYPE_GROUPS)}
    out = []
    for (schema, thing), e in ents.items():
        recs = sorted(e["recs"], key=lambda x: (x["page"] or 10 ** 9, x["quote"]))
        pages = sorted({x["page"] for x in recs if x["page"]})
        item = {"key": f"{schema}|{thing}", "schema": schema, "group": type_group(schema), "thing": thing,
                "records": len(recs), "pages": pages, "cited": any(x["state"] == "cited" for x in recs), "recs": recs}
        if e["did"]:
            item["did"] = sorted(e["did"].values(), key=lambda d: (d["page"] or 10 ** 9, d.get("kind") or ""))
        out.append(item)
    out.sort(key=lambda e: (order.get(e["group"], 99), e["pages"][0] if e["pages"] else 10 ** 9, e["thing"].lower()))
    types = []
    for key, label, _ in TYPE_GROUPS + [("other", "Other", set())]:
        n = sum(1 for e in out if e["group"] == key)
        if n:
            types.append({"key": key, "label": label, "count": n})
    kinds = {}
    for e in out:
        for k in {d.get("kind") for d in e.get("did") or []}:
            kinds[k] = kinds.get(k, 0) + 1
    dids = [{"key": k, "label": DID_WORDS.get(k, str(k).capitalize()), "count": n} for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])]
    return {"entities": out, "types": types, "dids": dids, "entity_count": len(out)}


# ---- the log's rows unfold: which records, which model call, which problems ---------------------------------------------

def _lead_int(text) -> int | None:
    m = re.match(r"\s*(\d+)", str(text or ""))
    return int(m.group(1)) if m else None


def _model_key(row: dict):
    m = re.match(r"^(\S+) p(\d+)\b", row.get("label") or "")
    return (m.group(1), int(m.group(2)), row.get("model")) if m else None


def trace_links(trace: list[dict], found: list[dict], models: list[dict]) -> dict:
    """{trace index: {recs: [found row indexes], call: model call n, kind}} for the rows that stand for several things:
    Found (the records that page's answer gave, in the order written), Added and Cited (a saved batch, the cited
    records in the order saved), Asked (the model call that answered it)."""
    proposed, cited = {}, []
    for i, r in enumerate(found):
        if r.get("state") == "cited":
            cited.append(i)
        else:
            proposed.setdefault(r.get("page") or 0, []).append(i)
    by_model = {}
    for j, m in enumerate(models):
        k = _model_key(m)
        if k and m.get("ok", True):
            by_model.setdefault(k, []).append(m.get("n") or j + 1)
    pp, mp, cp, batch, links = {}, {}, 0, [], {}
    for i, r in enumerate(trace):
        verb, page = r.get("verb"), r.get("page")
        if verb == "Found" and page:
            n = _lead_int(r.get("object"))
            got, start = proposed.get(page, []), pp.get(page, 0)
            take = got[start:start + n] if n else got[start:]
            pp[page] = start + len(take)
            links[i] = {"recs": take, "kind": "found"}
        elif verb == "Added" and r.get("descriptor") == "Records":
            batch = cited[cp:cp + (r.get("n") or 0)]
            cp += len(batch)
            links[i] = {"recs": batch, "kind": "added"}
        elif verb == "Cited":
            links[i] = {"recs": batch, "kind": "cited"}
        elif verb == "Asked" and page and r.get("who") in ("haiku", "sonnet"):
            ps = (r.get("object") or "").split()
            key = (ps[0], page, r.get("who")) if ps else None
            lst, at = by_model.get(key) or [], mp.get(key, 0)
            if at < len(lst):
                links[i] = {"call": lst[at], "kind": "asked"}
                mp[key] = at + 1
    return links


COLLECTION_SCHEMA = {"actors": "Person", "people": "Person", "bodies": "PublicBody", "places": "Place", "events": "Event"}


def row_schema(r: dict, by_page: dict) -> str | None:
    """The type a log row's subject has when no record is linked to it, from the row's own words: a place is a Place, the
    roster and identity checks are about people, a collection is its type, an escalated page takes the commonest type its
    records have. None when the row names no entity (its verb's icon stays)."""
    verb, desc, obj = r.get("verb"), r.get("descriptor"), str(r.get("object") or "").strip().lower()
    if desc in ("OSM", "Place") or verb in ("Placed", "Flagged"):           # Flagged: geo-context, a place set against its source
        return "Place"
    if desc in ("Roster", "Identity"):
        return "Person"
    if desc in ("Collection", "Quote") and obj in COLLECTION_SCHEMA:
        return COLLECTION_SCHEMA[obj]
    if verb == "Escalated" and r.get("page"):
        kinds = by_page.get(r["page"]) or {}
        return max(kinds, key=kinds.get) if kinds else None
    return None


def run_rows_linked(rows: list[dict], links: dict, found: list[dict], limit: int = 2000) -> list[dict]:
    """run_rows, with `i` (the row's index in the trace), `open` (it can unfold), `nrecs`, `schema` (the commonest type
    among its records, for the icon) and `call`."""
    marked = [{**r, "_i": i} for i, r in enumerate(rows)]
    base = run_rows(marked, limit)
    folded = list(reversed(fold(marked)))[:limit]
    by_page = {}
    for x in found:
        if x.get("schema"):
            kinds = by_page.setdefault(x.get("page"), {})
            kinds[x["schema"]] = kinds.get(x["schema"], 0) + 1
    out = []
    for r, f in zip(base, folded):
        i, span = f["_i"], f.get("fold", 1)
        ids = [x for j in range(i, i + span) for x in (links.get(j, {}).get("recs") or [])]
        call = (links.get(i) or {}).get("call")
        opens = bool(ids) or call is not None or r["verb"] in ("Escalated", "Ran", "Failed", "Skipped") or bool(f.get("detail"))
        kinds = {}
        for x in ids:
            kinds[found[x].get("schema")] = kinds.get(found[x].get("schema"), 0) + 1
        top = max(kinds, key=kinds.get) if kinds else row_schema(r, by_page)
        out.append({**r, "i": i, "open": opens, "nrecs": len(ids), "schema": top, "call": call})
    return out


def model_call(m: dict, answer_max: int = 20000) -> dict:
    ans = m.get("answer")
    return {"n": m.get("n"), "at": m.get("at"), "model": m.get("model"), "label": m.get("label"), "ms": m.get("ms"),
            "ok": m.get("ok", True), "tokens": m.get("tokens"), "why": m.get("why"), "prompt_chars": m.get("prompt_chars"),
            "prompt_sha": m.get("prompt_sha"), "answer": None if ans is None else str(ans)[:answer_max],
            "answer_chars": None if ans is None else len(str(ans)), "stub": bool(m.get("stub"))}


def phase_did(i: int, trace: list[dict]) -> str:
    """What a step did, in a sentence, from the trace rows between its start and the next step's."""
    end = next((j for j in range(i + 1, len(trace)) if trace[j].get("kind") == "phase"), len(trace))
    seg = trace[i + 1:end]
    asked = sum(1 for r in seg if r.get("verb") == "Asked")
    pages = {r["page"] for r in seg if r.get("page")}
    found = sum(_lead_int(r.get("object")) or 0 for r in seg if r.get("verb") == "Found")
    added = sum(r.get("n") or 0 for r in seg if r.get("verb") == "Added" and r.get("descriptor") == "Records")
    cited = sum(r.get("n") or 0 for r in seg if r.get("verb") == "Cited")
    esc = sum(1 for r in seg if r.get("verb") == "Escalated")
    failed = sum(1 for r in seg if r.get("kind") == "fail")
    t_end = trace[end]["t"] if end < len(trace) else trace[-1]["t"]
    secs = round(t_end - trace[i]["t"])
    bits = []
    if asked:
        bits.append(f"asked a model {asked} time{'s' if asked != 1 else ''}" +
                    (f" on {len(pages)} page{'s' if len(pages) != 1 else ''}" if pages else ""))
    if found:
        bits.append(f"found {found} records")
    if added:
        bits.append(f"saved {added} records" + (f" and quoted {cited}" if cited else ""))
    if esc:
        bits.append(f"escalated {esc} page{'s' if esc != 1 else ''}")
    if failed:
        bits.append(f"hit {failed} problem{'s' if failed != 1 else ''}")
    if not bits:
        return f"Took {duration_text(secs)}."
    return ", ".join(bits).capitalize() + f". Took {duration_text(secs)}."


def row_detail(i: int, trace: list[dict], found: list[dict], models: list[dict], links: dict, state: dict) -> dict:
    """What one log row stands for: {recs: [{schema, thing, page, state, quote}], recs_total, call, problems, did, detail}.
    A run of folded rows is read from its first."""
    if not 0 <= i < len(trace):
        return {}
    r, lk = trace[i], links.get(i, {})
    span = 1
    while (i + span < len(trace) and not trace[i + span].get("page") and not r.get("page")
           and (trace[i + span].get("verb"), trace[i + span].get("who"), trace[i + span].get("descriptor")) ==
           (r.get("verb"), r.get("who"), r.get("descriptor")) and trace[i + span].get("kind") == r.get("kind") == "info"):
        span += 1
    ids = [x for j in range(i, i + span) for x in (links.get(j, {}).get("recs") or [])]
    out = {"verb": r.get("verb"), "object": r.get("object"), "detail": r.get("detail") or "", "recs_total": len(ids),
           "recs": [{"schema": found[x].get("schema") or "", "thing": found[x].get("thing") or "",
                     "page": found[x].get("page") or 0, "state": found[x].get("state") or "",
                     "quote": (found[x].get("quote") or "")[:500]} for x in ids[:300]]}
    if lk.get("call") is not None:
        m = next((m for j, m in enumerate(models) if (m.get("n") or j + 1) == lk["call"]), None)
        if m:
            out["call"] = model_call(m)
    if r.get("verb") == "Escalated":
        probs = []
        for e in state.get("escalations") or []:
            if e.get("page") == r.get("page") and e.get("pass") == (r.get("object") or "").split(" ")[0]:
                probs = [str(p) for p in e.get("problems") or []]
        if not probs:
            for j in range(i - 1, -1, -1):
                t = trace[j]
                if t.get("page") == r.get("page") and t.get("verb") == "Asked" and t.get("detail"):
                    probs = [p.strip() for p in t["detail"].split(";") if p.strip()]
                    break
        out["problems"] = probs
    if r.get("kind") == "phase":
        out["did"] = phase_did(i, trace)
    return out


# ---- the Raw tab: what the files hold, unprocessed ---------------------------------------------------------------------

def transcript_rows(events: list[dict], limit: int = 400) -> list[dict]:
    """A session's stream-json events as rows {n, type, text}: said, called (tool and arguments), came back (cut)."""
    out = []
    for n, ev in enumerate(events):
        t = ev.get("type")
        content = (ev.get("message") or {}).get("content")
        items = content if isinstance(content, list) else ([{"type": "text", "text": content}] if content else [])
        parts = []
        if t == "system":
            parts.append(f"{ev.get('subtype') or 'system'} {ev.get('model') or ''}".strip())
        elif t in ("assistant", "user"):
            for c in items:
                ct = c.get("type")
                if ct == "text":
                    parts.append(str(c.get("text") or ""))
                elif ct == "tool_use":
                    parts.append(f"{short_tool(c.get('name') or '?')} {json.dumps(c.get('input') or {}, ensure_ascii=False)[:600]}")
                elif ct == "tool_result":
                    parts.append(("error: " if c.get("is_error") else "") + _text_of(c.get("content")))
        elif t == "result":
            parts.append(f"{ev.get('subtype') or ''} {ev.get('result') or ''}".strip())
        kind = {"assistant": "said", "user": "back", "system": "start", "result": "end"}.get(t, str(t))
        if t == "assistant" and any(c.get("type") == "tool_use" for c in items):
            kind = "call"
        out.append({"n": n, "type": kind, "text": "\n".join(parts)[:3000]})
    return out[-limit:]


def call_row(c: dict) -> dict:
    """One archive call row for the Calls tab: {at, tool, ms, ok, args, text, step, note}. `note` (what the call did: did,
    counts, refs, refs_more, lint) is kept only when hydra wrote one; `step` is the task step's text, else None."""
    n = c.get("_note") if isinstance(c.get("_note"), dict) else None
    step = (n or {}).get("step") or c.get("_step")
    out = {"at": c.get("_stamp"), "tool": c.get("tool"), "ms": c.get("ms"), "ok": c.get("ok", True),
           "args": json.dumps(c.get("_args") or {}, ensure_ascii=False)[:600], "text": str(c.get("error") or "")[:600],
           "step": step if isinstance(step, str) and step else None}
    if n:
        out["note"] = {k: n[k] for k in ("did", "counts", "refs", "refs_more", "lint") if n.get(k)}
    return out


def _name_note_ids(rows: list[dict], archive) -> None:
    """Say each record id a note names (refs, lint) by name: `names` {id: name} on the row, only for ids the archive knows."""
    ids = {i for r in rows for i in [*((r.get("note") or {}).get("refs") or []),
                                     *(x.get("id") for x in (r.get("note") or {}).get("lint") or [] if isinstance(x, dict))] if isinstance(i, str)}
    if not ids:
        return
    names, where = archive_names(archive), _record_homes(archive)
    for r in rows:
        n = r.get("note")
        if not n:
            continue
        mine = {*(n.get("refs") or []), *(x.get("id") for x in n.get("lint") or [] if isinstance(x, dict))}
        got = {i: {"name": names[i], **where.get(i, {})} for i in mine if isinstance(i, str) and i in names}
        if got:
            n["recs"] = got


_HOMES_MEMO: dict = {}


def _record_homes(archive) -> dict:
    """{record id: {col: the collection it is kept in, schema}}, read again only when a collection file changes."""
    root = Path(str(archive or ""))
    files = sorted((root / "collections").glob("*.json")) if root.is_dir() else []
    key = (str(root), tuple((f.name, f.stat().st_mtime_ns) for f in files))
    if _HOMES_MEMO.get("key") != key:
        homes = {}
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for r in data if isinstance(data, list) else data.get("records") or []:
                if isinstance(r, dict) and r.get("id"):
                    homes[r["id"]] = {"col": f.stem, "schema": r.get("schema") or ""}
        _HOMES_MEMO.update(key=key, homes=homes)
    return _HOMES_MEMO["homes"]


def raw_view(files, kind: str) -> dict:
    """{driver: [line], hydra: [{at, tool, ms, ok, args, text}], models: [{n, at, model, label, ms, ok, tokens, prompt_chars,
    answer_chars, why}], transcript: [{n, type, text}]}: the tails of the run's own files."""
    hy = [{"at": r.get("at"), "tool": r.get("tool"), "ms": r.get("ms"), "ok": r.get("ok", True),
           "args": json.dumps(r.get("args") or {}, ensure_ascii=False)[:600], "text": str(r.get("text") or "")[:600]}
          for r in files.hydra_rows()[-600:]]
    if kind != "pageflow":                                   # an agent session: hydra's own call log of the archive, with its notes
        hy = [call_row(c) for c in files.mcp_rows()[-600:]]
        _name_note_ids(hy, files.archive())
    ms =[{"n": m.get("n") or j + 1, "at": m.get("at"), "model": m.get("model"), "label": m.get("label"), "ms": m.get("ms"),
           "ok": m.get("ok", True), "tokens": m.get("tokens"), "prompt_chars": m.get("prompt_chars"),
           "answer_chars": None if m.get("answer") is None else len(str(m["answer"])), "why": m.get("why")}
          for j, m in enumerate(files.models())][-600:]
    return {"archive": files.archive() if kind != "pageflow" else None,
            "driver": files.driver_log(300) if kind == "pageflow" else [], "hydra": hy, "models": ms,
            "transcript": transcript_rows(files.transcript_events()) if kind != "pageflow" else []}

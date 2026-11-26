"""choices.py: the agent's choices in a page-flow run, read from the run's own files. Reads only; nothing is written.

The page flow ticks each hydra task step P0-P9 after the step's own check passed (pageflow.py `Run.tick`: `state.json` `ticks`).
The choices behind those ticks are in the run folder (`<run>/pageflow/`): `triage.json`, `pages/<n>.mentions.json`
(mentions kept, names dropped with reasons), `state.json` (`roster_items`, `questions`, `close`, `places`), `pages/<n>.facts.json`,
`.ties.json`, `.ties-wide.json` (records saved, records refused by the checks), `pages/<n>.island-*.json`, and `trace.jsonl` (the
checker's send-backs, `Cited` and `Placed` lines).

`build(files)` gives {ticks, choices}: a tick is {step, key, ok, result, error?}; a choice is
{id, key, tick, group, kind, verb, object, page, quote, why, outcome}. `key` names the route step it belongs to (runview.flow_steps
keys: preflight, setup, triage, names, roster, records, identity, close); `kind` is Kept | Dropped | Merged | Asked | Refused;
`group` is the word the step view heads its lines with; `who` is the model that decided it (haiku | sonnet) or "" when the driver's
own rule or check did. Only exceptions are listed for rule steps (skipped pages, refused records, unplaced names, close findings);
the routine passes are counts in the tick's `line`. Ids are stable between reads of a growing run, so the page can patch.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from runfiles import read_json

# the tick codes (pageflow.py STEPS) and the route step each one is shown under; Save (P7) is shown under Records and ties
TICK_KEY = {"P0": "preflight", "P1": "setup", "P2": "triage", "P3": "names", "P4": "roster", "P5": "records", "P6": "records",
            "P7": "records", "P8": "identity", "P9": "close"}
KINDS = ("Kept", "Dropped", "Merged", "Asked", "Refused")
RETRY = {"ok": "Passed", "repaired": "Passed on a retry", "escalated": "Passed on Sonnet", "sonnet": "Read by Sonnet",
         "gave-up": "Gave up"}
PLAIN_PROPS = {"notes", "role", "date", "startDate", "endDate", "name", "summary", "description", "status", "location"}


def _row(tick: str, group: str, kind: str, verb: str, obj: str, page=None, quote: str = "", why: str = "", outcome: str = "", who: str = "") -> dict:
    return {"key": TICK_KEY[tick], "tick": tick, "group": group, "kind": kind, "verb": verb, "object": str(obj or "")[:200],
            "page": page if isinstance(page, int) else None, "quote": quote or "", "why": why or "", "outcome": outcome or "", "who": who}


def _who(how) -> str:
    """The model that wrote a page's answer: a page `how` of sonnet or escalated was Sonnet's, any other Haiku's."""
    return "sonnet" if how in ("sonnet", "escalated") else "haiku"


def _plain(text) -> str:
    """The driver's own tick text in plain words: a path as its last name, a dict or list as `a 1, b 2`."""
    s = re.sub(r"(?<![\w.])(?:/[A-Za-z0-9_.\-]+){2,}", lambda m: m.group(0).rsplit("/", 1)[-1], str(text or ""))
    s = re.sub(r"\{([^{}]*)(?:\}|$)", lambda m: re.sub(r"['\"]", "", m.group(1)).replace(": ", " "), s)      # `$`: cut short by the driver
    s = re.sub(r"\[([^\[\]]*)(?:\]|$)", lambda m: re.sub(r"['\"]", "", m.group(1)), s)
    return s.replace("['", "").replace("', '", ", ")          # a list cut short by the driver's own limit


def _pretty(ident) -> str:
    """`person-jonathan-jay-pollard` as `Jonathan Jay Pollard`."""
    s = str(ident or "")
    return (s.split("-", 1)[1] if "-" in s else s).replace("-", " ").strip().title() or s


def _first(v) -> str:
    return str(v[0]) if isinstance(v, list) and v else str(v or "")


def _is_id(v) -> bool:
    return isinstance(v, str) and "-" in v and " " not in v and v.islower()


def _page_files(pf: Path) -> list[tuple[int, str, Path]]:
    """(page, kind, path) for every pages/<n>.<kind>.json, in page order."""
    out = []
    for p in (pf / "pages").glob("*.json") if (pf / "pages").is_dir() else []:
        m = re.fullmatch(r"(\d+)\.(mentions|facts|ties|ties-wide|island-.+)\.json", p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    return sorted(out, key=lambda x: (x[0], x[1]))


def _triage(pf: Path) -> tuple[list[dict], Counter]:
    """The pages the driver's floors skipped (a row each, with the reason) and the count of every status in the run's range."""
    t = read_json(pf / "triage.json", [])
    t = t.get("pages", []) if isinstance(t, dict) else t
    out, count = [], Counter()
    for r in t if isinstance(t, list) else []:
        st = r.get("status")
        if st == "out-of-scope":                             # the run's page range, not a choice
            continue
        count[st] += 1
        if st != "todo":
            out.append(_row("P2", "Skipped", "Dropped", "Skipped", st or "skipped", r.get("page"), why=r.get("why") or ""))
    return out, count


def _mentions(page: int, d: dict) -> list[dict]:
    out = []
    for m in d.get("mentions") or []:
        said = m.get("said") or ""
        out.append(_row("P3", "Kept", "Kept", "Kept", m.get("name"), page, m.get("quote") or "",
                        " · ".join(x for x in (m.get("kind"), said) if x), who=_who(d.get("how"))))
    for m in d.get("dropped") or []:
        out.append(_row("P3", "Dropped", "Dropped", "Dropped", m.get("name"), page, why=m.get("why") or "", who=_who(d.get("how"))))
    return out


def _record_object(r: dict) -> str:
    props = r.get("properties") or {}
    if props.get("name"):
        return _first(props["name"])
    ends = [_pretty(v) for k, vs in props.items() if k not in PLAIN_PROPS for v in (vs if isinstance(vs, list) else [vs]) if _is_id(v)]
    return f"{r.get('schema', '')}: {' – '.join(ends[:3])}" if ends else str(r.get("schema") or r.get("id") or "")


def _records(tick: str, group: str, page: int, d: dict) -> list[dict]:
    out = []
    for r in d.get("records") or []:
        props = r.get("properties") or {}
        out.append(_row(tick, group, "Kept", "Saved", _record_object(r), page, _first(props.get("notes")),
                        " · ".join(x for x in (r.get("schema"), _first(props.get("role"))) if x), who=_who(d.get("how"))))
    pairs = {p.get("pair"): p for p in d.get("pairs") or [] if isinstance(p, dict)}
    for r in d.get("rejected") or []:
        props = r.get("properties") or {}
        why = r.get("why")
        why = "\n".join(why) if isinstance(why, list) else str(why or "")
        pr = pairs.get(r.get("pair")) or {}
        obj = _record_object(r) if r.get("schema") or props else (f"{_pretty(pr.get('a'))} – {_pretty(pr.get('b'))}" if pr else re.sub(r"^L\d+\s+", "", why.split("\n")[0]))
        out.append(_row(tick, group, "Refused", "Refused", obj, page, _first(props.get("notes")) or r.get("notes") or pr.get("sentence") or "", why))
    return out


def _island(page: int, d: dict) -> dict:
    ent = _pretty(d.get("entity"))
    recs = d.get("records") or []
    if recs:
        notes = [_first((r.get("properties") or {}).get("notes")) for r in recs]
        return _row("P8", "Joined", "Merged", "Joined", ent, page, notes[0] if notes else "",
                    " · ".join(sorted({str(r.get("schema")) for r in recs if r.get("schema")})), who=_who(d.get("how")))
    return _row("P8", "Left", "Dropped", "Left", ent, page, why=" ".join(str(p) for p in d.get("problems") or []), who=_who(d.get("how")))


def _roster(state: dict) -> list[dict]:
    out, seen = [], set()
    for it in state.get("roster_items") or []:
        v = it.get("verdict") or {}
        verdict, reason = v.get("verdict"), v.get("reason") or ""
        seen.add((it.get("form"), it.get("page")))
        if verdict == "same":
            out.append(_row("P4", "Same", "Merged", "Same", f"{it.get('form')} → {_pretty(v.get('same_as') or it.get('claimed'))}",
                            it.get("page"), it.get("quote") or "", reason, who="sonnet"))
        elif verdict == "question":
            out.append(_row("P4", "Asked", "Asked", "Asked", it.get("form"), it.get("page"), it.get("quote") or "", reason, who="sonnet"))
        else:
            out.append(_row("P4", "New", "Kept", "New", it.get("form"), it.get("page"), it.get("quote") or "", " · ".join(x for x in (it.get("kind"), reason) if x), who="sonnet"))
    for q in state.get("questions") or []:
        if (q.get("form"), q.get("page")) not in seen:
            out.append(_row("P4", "Asked", "Asked", "Asked", q.get("form"), q.get("page"), q.get("quote") or "", q.get("why") or "", who="sonnet"))
    for r in state.get("roster_refused") or []:
        out.append(_row("P4", "Refused", "Refused", "Refused", r.get("form") if isinstance(r, dict) else r, r.get("page") if isinstance(r, dict) else None,
                        why=str(r.get("why") or "") if isinstance(r, dict) else ""))
    return out


def _sent_back(trace: list[dict], pages: dict) -> list[dict]:
    """The checker's send-backs: a `Asked` line of kind fail. Pass A, C and T name the page; the retry outcome is the page's own result."""
    out = []
    for i, r in enumerate(trace):
        if r.get("verb") != "Asked" or r.get("kind") != "fail" or r.get("descriptor") != "Page" or not r.get("detail"):
            continue
        stage = r.get("stage")
        tick = {"A": "P3", "C": "P5", "T": "P6"}.get(stage)
        if not tick:
            continue
        probs = [p.strip() for p in str(r["detail"]).split("; ") if p.strip()]
        n = r.get("page")
        res = ((pages.get(str(n)) or {}).get(stage)) if n is not None else None
        row = _row(tick, "Returned" if stage == "A" else ("Facts" if stage == "C" else "Ties"), "Refused", "Returned",
                   re.sub(r"^L\d+\s+", "", probs[0]) if probs else "", n, why="\n".join(probs),
                   outcome=RETRY.get(res, "") if res else "")
        row["_i"] = i
        out.append(row)
    return out


def _unplaced(trace: list[dict]) -> list[dict]:
    """Names the map could not place: the exception. Placing and citing are routine passes, counted in the tick's line."""
    out = []
    for i, r in enumerate(trace):
        if r.get("verb") == "Asked" and r.get("descriptor") == "OSM" and r.get("kind") == "fail":
            out.append(_row("P7", "Unplaced", "Refused", "Unplaced", r.get("object"), why="No match on the map"))
            out[-1]["_i"] = i
        elif r.get("verb") == "Flagged" and r.get("descriptor") == "Place":      # geo-context: hydra flagged a place against its source
            out.append(_row("P7", "Flagged", "Asked", "Flagged", r.get("object"), why=r.get("detail") or ""))
            out[-1]["_i"] = i
    return out


def _close(state: dict) -> list[dict]:
    fs = (((state.get("close") or {}).get("wiki_readiness") or {}).get("findings")) or []
    out = []
    for f in fs:
        ex = "\n".join(str(x) for x in (f.get("examples") or [])[:5])
        why = "\n".join(x for x in (f.get("do") or "", ex) if x)
        out.append(_row("P9", "Flagged", "Asked", "Flagged", f"{f.get('count', '')} {f.get('what') or f.get('code')}".strip(), why=why))
        out[-1]["outcome"] = (f.get("level") or "").capitalize()
    return out


def _archive_name(state: dict) -> str:
    name = Path(str(state.get("archive") or "")).name
    return "Draft" if name == "draft" else name


def _lines(state: dict, rows: list[dict], count: Counter, trace: list[dict]) -> dict:
    """One short line per tick, in plain words, from the counts the run's files give."""
    def n(tick, **want):
        return sum(1 for r in rows if r["tick"] == tick and all(r[k] == v for k, v in want.items()))

    def join(*parts):
        return " · ".join(p for p in parts if p)
    pages = Counter((p or {}).get("A") for p in (state.get("pages") or {}).values())
    findings = len((((state.get("close") or {}).get("wiki_readiness") or {}).get("findings")) or [])
    facts, ties = n("P5", verb="Saved"), n("P6", verb="Saved")
    placed = sum(1 for r in trace if r.get("verb") == "Placed")
    flagged = sum(1 for r in trace if r.get("verb") == "Flagged")           # geo-context: a place set against its source
    return {"P0": f"{len(state.get('preflight') or [])} checks" if state.get("preflight") else "",
            "P1": _archive_name(state),
            "P2": join(f"{count['todo']} read", *(f"{v} {k}" for k, v in count.items() if k != "todo")),
            "P3": join(f"{n('P3', verb='Kept')} names", *(f"{pages[k]} {w}" for k, w in (("repaired", "repaired"), ("escalated", "escalated"),
                                                                                    ("sonnet", "on Sonnet")) if pages[k])),
            "P4": join(*(f"{n('P4', group=g)} {g.lower()}" for g in ("New", "Same", "Asked") if n("P4", group=g))),
            "P5": f"{facts} facts", "P6": join(f"{facts} facts", f"{ties} ties"),
            "P7": join(f"{facts} facts" if facts else "", f"{ties} ties" if ties else "", f"{placed} placed" if placed else "",
                         f"{flagged} flagged" if flagged else ""),
            "P8": join(*(f"{n('P8', group=g)} {g.lower()}" for g in ("Joined", "Left") if n("P8", group=g))),
            "P9": f"{findings} finding{'' if findings == 1 else 's'}"}


NO_DETAIL = {"P2", "P4"}                                     # their line already says all the tick does


def _ticks(state: dict, lines: dict) -> list[dict]:
    out = []
    for t in state.get("ticks") or []:
        if t.get("step") in TICK_KEY:
            ok, res = bool(t.get("ok")), _plain(t.get("result"))
            out.append({"step": t["step"], "key": TICK_KEY[t["step"]], "ok": ok, "line": (lines.get(t["step"]) or res[:60]) if ok else "Failed",
                        "result": res, "detail": "" if t["step"] in NO_DETAIL or not ok else res,
                        **({"error": _plain(t["error"])} if t.get("error") else {})})
    return out


def build(files) -> dict:
    """{ticks, choices} for one page-flow run (a `runfiles.RunFiles`). A run with no page-flow files gives empty lists."""
    pf = files.pf
    state = files.state()
    trace = files.trace()
    pages = state.get("pages") or {}
    rows, count = _triage(pf)
    for n, kind, path in _page_files(pf):
        d = read_json(path, {}) or {}
        if kind == "mentions":
            rows += _mentions(n, d)
        elif kind == "facts":
            rows += _records("P5", "Facts", n, d)
        elif kind.startswith("ties"):
            rows += _records("P6", "Ties", n, d)
        else:
            rows.append(_island(n, d))
    rows += _roster(state) + _sent_back(trace, pages) + _unplaced(trace) + _close(state)
    lines = _lines(state, rows, count, trace)
    seen: dict = {}
    for r in rows:                                           # stable ids: tick, group, page and the order within them
        k = (r["tick"], r["group"], r["page"], r["kind"])
        seen[k] = seen.get(k, 0) + 1
        i = r.pop("_i", None)
        r["id"] = f"{r['tick']}.{r['group']}.{'' if r['page'] is None else r['page']}.{r['kind'][0]}{i if i is not None else seen[k]}"
    return {"ticks": _ticks(state, lines), "choices": rows}

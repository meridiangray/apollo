"""engineentities.py: what an engine run did, as the entities it touched. Read only.

An engine run (assemble, refine, timeline, places, finance, wikidata ...) stages its decisions in `<run>/<flow>/decisions.jsonl`:
a merge, a date, a bind, a tie, a question. Its `pageflow/found.jsonl` says each in a phrase ("Cleveland is Cleveland (geonames:5150529)"),
so the Found tab could not tell the entities from the decisions. `rows(files)` reads the structured decisions instead and answers
the found rows the Basic run writes, one per entity touched: `{page, schema, thing, quote, state, id, did}` where `schema` is the
entity's own (Person, Company, Place ...), `thing` its name from the archive, and `did` what this run did to it:
`{kind, verb, other?, text?, why?}` (kind is the decision's, which has its icon). The entity list, the cards and the rich view
then read them exactly as they read a Basic run's rows. Names and schemas come from the archive's records (runview.archive_names,
archive_records) and, for a record a merge removed, from the run's own gather.json and the decision's words.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from runfiles import read_json
from runview import archive_names, archive_records

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_MEMO: dict = {}                                            # {flow folder: (key, rows)}
TIE = "@tie"                                               # in `touches`, the tie itself


def flow_dir(folder) -> Path | None:
    """The folder an engine run keeps its staged output in (<run>/<flow>/): the one holding decisions.jsonl."""
    for f in sorted(Path(folder).glob("*/decisions.jsonl")):
        if f.parent.name != "pageflow":
            return f.parent
    return None


def long_date(v) -> str:
    """A date as a reader says it: 1872-01-02 as "January 2, 1872", 1872-01 as "January 1872", a bare year as it is."""
    s = str(v or "").strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", s)
    if not m:
        return s
    y, mo, d = m.group(1), m.group(2), m.group(3)
    try:
        if mo and not 1 <= int(mo) <= 12:
            return s
        if mo:
            return f"{MONTHS[int(mo) - 1]} {int(d)}, {y}" if d and date(int(y), int(mo), int(d)) else f"{MONTHS[int(mo) - 1]} {y}"
    except ValueError:
        return s
    return y


def _read_lines(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except OSError:
        pass
    return out


def _stamp(p: Path):
    try:
        s = p.stat()
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None


class _Who:
    """Names and schemas of the records a run's decisions point at."""

    def __init__(self, archive, flow: Path, decisions: list[dict]):
        self.names = archive_names(archive)
        self.by_id = {r["id"]: r for r in archive_records(archive)}
        gather = read_json(flow / "gather.json", {}) or {}
        self.gathered = {r["id"]: r.get("schema") for r in (gather.get("records") or []) if isinstance(r, dict) and r.get("id")}
        self.said: dict[str, str] = {}                       # a record a merge removed: the name the decision gives it
        for d in decisions:
            if d.get("kind") == "merge" and d.get("status") == "ok" and d.get("absorb") and " into " in str(d.get("thing") or ""):
                a, b = str(d["thing"]).split(" into ", 1)
                self.said.setdefault(d["absorb"], a.strip())
                self.said.setdefault(d.get("keep"), b.strip())

    def name(self, rid) -> str:
        return self.names.get(rid) or self.said.get(rid) or ""

    def schema(self, rid) -> str:
        return (self.by_id.get(rid) or {}).get("schema") or self.gathered.get(rid) or ""

    def plain(self, text) -> str:
        """A question's words with the record ids it quotes said by name: "Mr. Clark (per-0csp28vhac)" is "Mr. Clark"."""
        text = re.sub(r"\s*\(\s*[a-z]{2,4}-[a-z0-9]{10}\s*\)", "", str(text or ""))
        return re.sub(r"\b[a-z]{2,4}-[a-z0-9]{10}\b", lambda m: self.name(m.group(0)) or m.group(0), text)

    def ref(self, rid) -> dict | None:
        n = self.name(rid)
        return {"name": n, "schema": self.schema(rid)} if n else None


def _shares(d: dict) -> str:
    bits = []
    if d.get("shares"):
        bits.append(f"{d['shares']} shares")
    if d.get("percentage"):
        bits.append(f"{d['percentage']}%")
    if d.get("value"):
        bits.append(f"{d['value']} {d.get('currency') or ''}".strip())
    return ", ".join(bits)


def touches(d: dict, who: _Who, asked: dict) -> list[tuple]:
    """What one decision did, as [(record id, verb, other record id | None, text, tie schema | None)] a row per entity it touched."""
    k = d.get("kind")
    if k == "merge":
        return [(d.get("keep"), "Merged with", d.get("absorb"), "", None), (d.get("absorb"), "Merged into", d.get("keep"), "", None)]
    if k == "apart":
        return [(d.get("a"), "Kept apart from", d.get("b"), "", None), (d.get("b"), "Kept apart from", d.get("a"), "", None)]
    if k == "alias":
        return [(d.get("record"), "Alias", None, str(d.get("alias") or ""), None)]
    if k == "rename":
        return [(d.get("record"), "Renamed to", None, str(d.get("name") or ""), None)]
    if k == "retype":
        return [(d.get("record"), "Retyped to", None, str(d.get("schema") or d.get("to") or ""), None)]
    if k == "prop":
        verb = "Removed" if d.get("action") == "remove" else "Added"
        return [(d.get("record"), verb, None, f"{d.get('field')}: {d.get('value')}", None)]
    if k == "date":
        field = str(d.get("field") or "date")
        verb = {"date": "Dated", "startDate": "Started", "endDate": "Ended"}.get(field, f"{field[:1].upper()}{field[1:]}")
        return [(d.get("record"), verb, None, long_date(d.get("value")), None)]
    if k == "order":
        return [(d.get("before"), "Before", d.get("after"), "", None), (d.get("after"), "After", d.get("before"), "", None)]
    if k == "bind":
        place = str(d.get("thing") or "").split(" is ", 1)[-1] or str(d.get("choice") or "")
        return [(d.get("place"), "Bound to", None, place, None)]
    if k == "none":
        return [(d.get("place"), "No match", None, "in the register", None)]
    if k == "link":
        role = str(d.get("role") or "link")
        role = role[:1].upper() + role[1:]
        return [(d.get("entity"), f"{role} in", d.get("place"), "", None), (d.get("place"), f"{role} of", d.get("entity"), "", None)]
    if k in ("tie", "stake"):
        a, b = (d.get("src"), d.get("dst")) if k == "tie" else (d.get("owner"), d.get("asset"))
        role = str(d.get("role") or ("holder" if k == "stake" else "")).strip()
        word = role[:1].upper() + role[1:] if role else str(d.get("schema") or "Link")
        text = _shares(d)
        return [(a, f"{word} of", b, text, None), (b, f"{word}:", a, text, None), (TIE, word, None, text, d.get("schema") or "Ownership")]
    if k == "payment":
        text = " ".join(x for x in (str(d.get("amount") or ""), str(d.get("purpose") or "")) if x)
        return [(d.get("payer"), "Paid", d.get("beneficiary"), text, None), (d.get("beneficiary"), "Paid by", d.get("payer"), text, None)]
    if k == "accept":
        label = str(d.get("label") or "")
        return [(d.get("record"), "Accepted", None, f"Wikidata {d.get('qid')}" + (f" ({label})" if label and label != who.name(d.get("record")) else ""), None)]
    if k == "reject":
        return [(d.get("record"), "Rejected", None, "Wikidata", None)]
    if k == "leave":
        return [(d.get("record"), "Left", None, "without a Wikidata item", None)]
    if k == "ask":
        return [(r, "Asked", None, who.plain(d.get("question")), None) for r in d.get("records") or []]
    if k == "answer":
        q = asked.get(d.get("id")) or {}
        return [(r, "Answered", None, f"{who.plain(q.get('question'))} {d.get('choice') or ''}".strip(), None) for r in q.get("records") or []]
    return []


def rows(files) -> list[dict]:
    """The found rows of an engine run: one per (decision, entity it touched). Kept until the run's decisions or the archive change."""
    flow = flow_dir(files.folder)
    if flow is None:
        return []
    archive = files.archive()
    recs = archive_records(archive)
    key = (str(flow), _stamp(flow / "decisions.jsonl"), _stamp(flow / "applied.json"), id(recs))
    kept = _MEMO.get(str(flow))
    if kept and kept[0] == key:
        return kept[1]
    decisions = [d for d in _read_lines(flow / "decisions.jsonl") if d.get("status") == "ok"]
    who = _Who(archive, flow, decisions)
    asked = {q.get("id"): q for q in files.questions()}
    applied = bool((read_json(flow / "applied.json", {}) or {}).get("wrote"))
    out = []
    for d in decisions:
        for rid, verb, other, text, tie_schema in touches(d, who, asked):
            if rid == TIE:                                  # the tie itself, as an entity: "A → B"
                a, b = who.name(d.get("src") or d.get("owner")), who.name(d.get("dst") or d.get("asset"))
                if not (a and b):
                    continue
                rid, name, schema = None, f"{a} → {b}", tie_schema
            else:
                name, schema = who.name(rid), who.schema(rid)
            if not (name and schema):
                continue
            did = {"kind": d.get("kind"), "verb": verb}
            if other and who.ref(other):
                did["other"] = who.ref(other)
            if text:
                did["text"] = text
            why = d.get("why") or d.get("reason")
            if why:
                did["why"] = str(why)
            out.append({"page": d.get("page") or 0, "schema": schema, "thing": name, "id": rid, "quote": str(d.get("quote") or ""),
                        "state": "cited" if applied else "proposed", "did": did})
    _MEMO[str(flow)] = (key, out)
    return out

"""entitytrace.py: one entity's compact Trace, and the entities of a source, for the right rails of Sources and Collections.

    trace(archive, record_id=None, name=None, schema=None)    {ok, entity, cats, lines}: the Trace, newest first
    entities(archive, source, title="")                       {ok, entities}: the entities a source gave (found rows, and
                                                              the archive's records that cite it)

Read only. A step comes from three places: Apollo's own log (`found`, `questions`, `events`), the run folder's files when the
log lacks them (`pageflow/found.jsonl`), and the archive's own history (hydra's `history.log` and `describe`: which snapshots
wrote the entity's record, or a link record that names it). Never a name from an author: a person is "you", an agent its role.
Steps of one category no more than 3 s apart are one line (`n` steps); the line shows the most telling verb.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import archives
import runlog

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
FOLD_SECONDS = 3
HISTORY_STEPS = 400
CATS = ("org", "kin", "post", "geo", "event", "hold", "id", "src", "rev")
PRIORITY = ("Merged", "Separated", "Matched", "Enriched", "Placed", "Linked", "Classed", "Answered", "Published", "Cited", "Saved",
            "Imported", "Found", "Added", "Removed", "Amended", "Edited", "Keyed", "Asked")
NAME_KEYS = ("name", "title", "summary", "role", "label")
ALIAS_KEYS = ("alias", "previousName", "weakAlias")
SKIP_KEYS = {"sources", "notes", "sourceUrl", "name", "title", "summary", "label", "alias", "previousName", "weakAlias", "description"}
END_KEYS = ("owner", "asset", "holder", "role", "post", "organization", "organisation", "person", "parent", "child", "relative",
            "subject", "object", "member", "director", "employer", "employee", "family", "involved", "party", "agent")
KIN = {"family", "familyrelation", "kin"}
POST = {"occupancy", "succession", "position"}
HOLD = {"ownership", "payment", "debt", "contract", "asset"}
EVENT = {"event"}
TOOLS = {"enrich", "portrait", "labelling", "wiki", "geo"}
# a snapshot's message says what it wrote when its diff cannot: first match wins (regex, verb, category, descriptor)
MESSAGE_VERBS = (
    (r"\bas wikidata\b|^key\b|\bkey [^ ]+ as\b", "Keyed", "id", "Wikidata"),
    (r"\bpin\b|\bto H-", "Pinned", "id", "H"),
    (r"removed geo link|unplaced", "Unplaced", "geo", "Place"),
    (r"^geo\b|\bgeo:|\bgeo link", "Placed", "geo", "Place"),
    (r"\bcite\b|\bcited\b", "Cited", "src", "Quote"),
    (r"\banswer\b|wiki review", "Answered", "rev", "Question"),
    (r"archetype", "Classed", "id", "Archetype"),
    (r"\bmerge\b", "Merged", "id", "Pair"),
    (r"link_image|\bportrait\b|\bphoto\b|\blogo\b", "Linked", "id", "Image"),
)
EVENT_CATS = {"Placed": "geo", "Asked": "rev", "Found": "src"}
EVENT_WHO = {"osm": "places", "haiku": "reader", "sonnet": "reader", "opus": "reader", "hydra": "hydra", "driver": "driver"}


# ---- small helpers -----------------------------------------------------------------------------------------------------

def _vals(props: dict, *keys) -> list[str]:
    out = []
    for k in keys:
        v = props.get(k)
        for x in v if isinstance(v, list) else [v]:
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
    return out


def _first(props: dict, *keys):
    got = _vals(props, *keys)
    return got[0] if got else None


def _utc(v) -> str | None:
    """An ISO time as UTC with a Z: a naive time is the machine's local time, as the run's log keeps it."""
    if v in (None, ""):
        return None
    try:
        d = datetime.fromisoformat(str(v).replace(" ", "T").replace("Z", "+00:00")[:32])
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _who(author: str | None) -> str:
    """Never an author's name: "you" for a person, "agent" for an agent or a session, a tool's own word for a tool."""
    a = str(author or "").strip().lower()
    if a.startswith("agent:") or a.startswith("mcp") or a in ("pageflow", "agent"):
        return "agent"
    return a if a in TOOLS else "you"


def _plain(text: str) -> str:
    try:
        from hydra.archive.history import read
        return read.plain_command(text or "")
    except Exception:                                    # noqa: BLE001 - the words stay, the paths are the only loss
        return text or ""


def _query(sql: str, params: list | tuple = ()) -> list[dict]:
    """A SELECT on Apollo's log, read only; [] when the log is missing or busy."""
    try:
        if not runlog.db_path().exists():
            return []
        con = runlog._open(True, 1.0)
    except Exception:                                    # noqa: BLE001 - the files are the other way in
        return []
    try:
        cur = con.execute(sql, list(params))
        cols = [d[0] for d in cur.description or []]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:                                    # noqa: BLE001
        return []
    finally:
        con.close()


# ---- the archive's records ---------------------------------------------------------------------------------------------

def _records(arch: Path) -> list[tuple[str, dict]]:
    from hydra.archive import collections_io
    out = []
    for name in collections_io.names(arch):
        try:
            rows = collections_io.read(collections_io.path(arch, name))[0] or []
        except (OSError, ValueError):
            continue
        out += [(name, r) for r in rows if isinstance(r, dict) and r.get("id")]
    return out


def _display(rec: dict) -> str:
    return _first(rec.get("properties") or {}, *NAME_KEYS) or rec.get("id") or ""


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _link_info(link: dict, mine: set[str]) -> tuple[str, str, str] | None:
    """(category, object, descriptor) when `link` names the entity (by id, name or alias) in one of its properties."""
    props, schema = link.get("properties") or {}, str(link.get("schema") or "")
    hit = None
    for k, v in props.items():
        if k in SKIP_KEYS:
            continue
        if any(_norm(x) in mine for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)):
            hit = k
            break
    if not hit:
        return None
    low = schema.lower()
    if low in HOLD:
        cat = "hold" if hit in ("owner", "holder", "payer") else "org"
    else:
        cat = "kin" if low in KIN else "post" if low in POST else "event" if low in EVENT else "org"
    others = [v for k in END_KEYS if k != hit for v in _vals(props, k) if _norm(v) not in mine]
    obj = _first(props, *NAME_KEYS) or (others[0] if others else "") or archives_word(schema)
    if cat == "kin" and others and _first(props, "relationship", "relation", "kind"):
        obj = f"{others[0]} ({_first(props, 'relationship', 'relation', 'kind')})"
    return cat, obj, "Event" if cat == "event" else archives_word(schema)


def archives_word(schema: str) -> str:
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", schema or "")


def _entity(arch: Path, record_id: str | None, name: str | None, schema: str | None) -> tuple[dict, dict | None, list]:
    """(entity card, the record or None, all (collection, record) pairs): by id, else by name for a found-only entity."""
    pairs = _records(arch)
    rec = coll = None
    if record_id:
        for c, r in pairs:
            if r.get("id") == record_id:
                rec, coll = r, c
                break
        if rec is None:
            raise ValueError(f"no record {record_id!r}")
    elif name:
        for c, r in pairs:
            if _norm(_display(r)) == _norm(name) and (not schema or r.get("schema") == schema):
                rec, coll = r, c
                break
    else:
        raise ValueError("a record id or a name")
    if rec is None:
        return {"id": None, "name": name, "schema": schema, "collection": None}, None, pairs
    titles = archives.source_titles(arch)
    props = rec.get("properties") or {}
    card = {"id": rec["id"], "name": _display(rec), "schema": rec.get("schema"), "collection": coll,
            "sources": [titles.get(s, s) for s in _vals(props, "sources")]}
    try:
        from hydra.archive import manifest
        t = ((manifest.read(arch).get("collections") or {}).get(coll) or {}).get("title")
        card["collection_title"] = t or coll
    except Exception:                                    # noqa: BLE001
        card["collection_title"] = coll
    return card, rec, pairs


# ---- the archive's history ---------------------------------------------------------------------------------------------

def _history_steps(arch: Path, rec: dict, tracked: dict, titles: dict) -> list[dict]:
    """Steps from the snapshots that wrote the entity's record, a link that names it, or a message that names it."""
    try:
        from hydra.archive.history import read, store
        if not store.enabled(arch):
            return []
        snaps = _log(arch, store)
    except Exception:                                    # noqa: BLE001 - no history is an empty Trace, not an error
        return []
    rid = rec["id"]
    word = re.compile(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])")
    steps, cited = [], False
    for s in sorted(snaps, key=lambda x: x.get("time") or ""):
        try:
            d = _describe(arch, read, s["id"])
        except Exception:                                # noqa: BLE001
            continue
        at, msg, who = _utc(s.get("time")), _plain(s.get("message") or ""), _who(s.get("author"))
        hit = False
        for c in d.get("collections") or []:
            for change, verb in (("added", "Added"), ("changed", "Amended"), ("removed", "Removed")):
                for x in c.get(change) or []:
                    if x not in tracked:
                        continue
                    hit = True
                    cat, obj, desc, q = tracked[x]
                    now = {} if change == "removed" else _at(arch, read, s["id"], c.get("path"), x)
                    q = _first(now.get("properties") or {}, "notes") if now else q
                    own = x == rid
                    got = _message_step(msg) if own else None
                    if got and verb != "Removed":
                        v, cat, desc = got
                    else:
                        v = verb
                    steps.append({"at": at, "cat": cat, "verb": v, "obj": obj, "desc": desc, "who": who, "quote": q, "msg": msg})
                    if own and verb == "Added" and not cited:
                        cited = True
                        for t in [titles.get(x, x) for x in _vals(now.get("properties") or rec.get("properties") or {}, "sources")]:
                            steps.append({"at": at, "cat": "src", "verb": "Cited", "obj": t, "desc": "Quote", "who": who, "quote": q, "msg": msg})
        if not hit and word.search(msg):
            got = _message_step(msg)
            if got:
                v, cat, desc = got
                steps.append({"at": at, "cat": cat, "verb": v, "obj": tracked[rid][1], "desc": desc, "who": who, "quote": None, "msg": msg})
    return steps


def _at(arch: Path, read, sid: str, path: str | None, rid: str) -> dict:
    """The record as snapshot `sid` held it ({} when it cannot be read)."""
    try:
        return _records_at(arch, read, sid, path).get(rid) or {}
    except Exception:                                    # noqa: BLE001
        return {}


# ---- a snapshot never changes once written: what it describes, and the records it held, are read once and kept ------
# (in this worker's memory, and on disk under state/entitytrace/, so the first Trace after a restart is fast too)

_MEMO: dict[tuple, object] = {}


def _cache_dir(arch: Path) -> Path:
    import hashlib
    import tools
    return tools.state_dir() / "entitytrace" / hashlib.sha1(str(arch.resolve()).encode()).hexdigest()[:16]


def _cached(arch: Path, kind: str, key: str, make):
    mk = (str(arch), kind, key)
    if mk in _MEMO:
        return _MEMO[mk]
    f = _cache_dir(arch) / kind / (re.sub(r"[^\w.-]", "_", key) + ".json")
    try:
        val = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        val = make()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(val, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass
    if len(_MEMO) > 20000:
        _MEMO.clear()
    _MEMO[mk] = val
    return val


def _log(arch: Path, store) -> list[dict]:
    """The history log, read again only when the archive's head moves (reading it walks every snapshot file)."""
    head = store.head(arch)[1] or ""
    return _cached(arch, "log", head, lambda: store.log(arch, limit=HISTORY_STEPS))


def _describe(arch: Path, read, sid: str) -> dict:
    """What snapshot `sid` changed. All of an archive's descriptions live in one file (one read, not one per snapshot)."""
    key = (str(arch), "describe-all")
    bundle = _MEMO.get(key)
    f = _cache_dir(arch) / "describe.json"
    if bundle is None:
        try:
            bundle = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            bundle = {}
        _MEMO[key] = bundle
    if sid not in bundle:
        bundle[sid] = read.describe(arch, sid)
        _MEMO[(str(arch), "describe-dirty")] = True
    return bundle[sid]


def _flush_describe(arch: Path) -> None:
    """Write the description bundle once per Trace, when it gained snapshots."""
    if not _MEMO.pop((str(arch), "describe-dirty"), None):
        return
    f = _cache_dir(arch) / "describe.json"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(_MEMO[(str(arch), "describe-all")], ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
    except OSError:
        pass


def _records_at(arch: Path, read, sid: str, path: str | None) -> dict:
    return _cached(arch, "records", f"{sid}--{path or ''}", lambda: read.records_at(arch, sid, path))


def _message_step(msg: str):
    for pat, verb, cat, desc in MESSAGE_VERBS:
        if re.search(pat, msg, re.I):
            return verb, cat, desc
    return None


# ---- Apollo's own log, and the run folders -----------------------------------------------------------------------------

def _runs(arch: Path) -> list[dict]:
    rows = _query("SELECT id, flow, source, archive, started FROM runs ORDER BY started")
    out = []
    for r in rows:
        try:
            if r.get("archive") and Path(r["archive"]).resolve() == arch:
                out.append(r)
        except OSError:
            continue
    return out


def _run_folder(rid: str) -> Path | None:
    try:
        rec = json.loads((runlog.state_dir() / f"{rid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    f = rec.get("final_folder") or rec.get("run_folder")
    return Path(f) if f else None


def _clock(run: dict, t) -> str | None:
    try:
        d = datetime.fromisoformat(str(run["started"]).replace(" ", "T")[:19])
        return _utc(datetime.fromtimestamp(d.timestamp() + float(t or 0)).isoformat())
    except (TypeError, ValueError, KeyError):
        return None


def _found_rows(run: dict) -> list[dict]:
    got = _query("SELECT page, schema, thing, quote, record_id, state FROM found WHERE run_id=? ORDER BY seq", [run["id"]])
    if got:
        return got
    f = _run_folder(run["id"])
    out = []
    try:
        lines = (f / "pageflow" / "found.jsonl").read_text(encoding="utf-8").splitlines() if f else []
    except OSError:
        return []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        out.append({"page": r.get("page"), "schema": r.get("schema"), "thing": r.get("thing"), "quote": r.get("quote"),
                    "record_id": r.get("record_id"), "state": r.get("state")})
    return out


def _page_times(run: dict) -> dict:
    """{(verb, page): (t, stage)}: when the run's pass worked on a page, from its events."""
    out = {}
    for r in _query("SELECT t, stage, verb, page FROM events WHERE run_id=? AND page IS NOT NULL ORDER BY seq", [run["id"]]):
        out.setdefault((r["verb"], r["page"]), (r["t"], r["stage"]))
        out.setdefault(("", r["page"]), (r["t"], r["stage"]))
    return out


def _log_steps(arch: Path, rec: dict | None, card: dict) -> list[dict]:
    names = {_norm(card.get("name"))}
    if rec:
        names |= {_norm(x) for x in _vals(rec.get("properties") or {}, *ALIAS_KEYS)}
    names.discard("")
    rid = card.get("id")
    steps = []
    for run in _runs(arch):
        word = str(run.get("flow") or "run").title()
        pt = _page_times(run)
        for r in _found_rows(run):
            if not (_norm(r["thing"]) in names or (rid and r.get("record_id") == rid)):
                continue
            t, stage = pt.get(("Found", r["page"])) or pt.get(("", r["page"])) or (0, "")
            obj = " · ".join(x for x in (word, stage, f"p{r['page']}" if r.get("page") is not None else "") if x)
            steps.append({"at": _clock(run, t), "cat": "src", "verb": "Found", "obj": obj, "desc": str(r.get("state") or "").title(),
                          "who": "reader", "quote": r.get("quote"), "msg": f"Found in {obj}"})
        for q in _query("SELECT page, question, quote, records, answer, status FROM questions WHERE run_id=?", [run["id"]]):
            hay = _norm(q.get("question"))
            if (rid and rid in str(q.get("records") or "")) or any(n in hay for n in names):
                t, _ = pt.get(("Asked", q["page"])) or pt.get(("", q["page"])) or (0, "")
                steps.append({"at": _clock(run, t), "cat": "rev", "verb": "Asked", "obj": q.get("question") or "", "desc": "Question",
                              "who": "reader", "quote": q.get("quote") or q.get("answer"), "msg": q.get("question") or ""})
        for e in _query("SELECT t, verb, object, descriptor, who FROM events WHERE run_id=? AND kind<>'log' AND verb<>'Found'", [run["id"]]):
            if _norm(e["object"]) in names:
                steps.append({"at": _clock(run, e["t"]), "cat": EVENT_CATS.get(e["verb"], "src"), "verb": e["verb"], "obj": e["object"],
                              "desc": e.get("descriptor") or "", "who": EVENT_WHO.get(e.get("who"), "agent"), "quote": None,
                              "msg": f"{e['verb']} {e['object']}"})
    return steps


# ---- folding ----------------------------------------------------------------------------------------------------------

def _prio(v: str) -> int:
    return PRIORITY.index(v) if v in PRIORITY else 99


def _fold(steps: list[dict]) -> list[dict]:
    """Steps of one category no more than FOLD_SECONDS apart are one line. Newest first."""
    steps = sorted((s for s in steps if s.get("at")), key=lambda s: s["at"])
    groups: list[list[dict]] = []
    for s in steps:
        for g in groups:
            if g[0]["cat"] == s["cat"] and abs((_dt(s["at"]) - _dt(g[-1]["at"]))) <= FOLD_SECONDS:
                g.append(s)
                break
        else:
            groups.append([s])
    lines = []
    for g in groups:
        head = min(g, key=lambda s: (_prio(s["verb"]), g.index(s)))
        lines.append({"at": g[-1]["at"], "cat": head["cat"], "verb": head["verb"], "obj": head["obj"], "desc": head["desc"],
                      "n": len(g), "who": head["who"], "tip": head["msg"],
                      "steps": [{"verb": s["verb"], "obj": s["obj"], "quote": s.get("quote")} for s in g]})
    lines.sort(key=lambda ln: ln["at"], reverse=True)
    return lines


def _dt(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


# ---- the two answers ---------------------------------------------------------------------------------------------------

def trace(archive: str, record_id: str | None = None, name: str | None = None, schema: str | None = None) -> dict:
    """The compact Trace of one entity: {ok, entity, cats, lines}. A name with no record is a found-only entity."""
    arch = Path(archive).resolve()
    try:
        card, rec, pairs = _entity(arch, record_id, name, schema)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    steps = _log_steps(arch, rec, card)
    if rec:
        mine = {_norm(card["id"]), _norm(card["name"])} | {_norm(x) for x in _vals(rec.get("properties") or {}, *ALIAS_KEYS)}
        mine.discard("")
        tracked = {rec["id"]: ("id", card["name"], "Record", _first(rec.get("properties") or {}, "notes"))}
        for c, r in pairs:
            if r["id"] != rec["id"]:
                got = _link_info(r, mine)
                if got:
                    tracked[r["id"]] = (got[0], got[1], got[2], _first(r.get("properties") or {}, "notes"))
        steps += _history_steps(arch, rec, tracked, archives.source_titles(arch))
        _flush_describe(arch)
    lines = _fold(steps)
    return {"ok": True, "entity": card, "cats": [c for c in CATS if any(ln["cat"] == c for ln in lines)], "lines": lines}


def entities(archive: str, source: str, title: str = "") -> dict:
    """The entities one source gave: found rows of the runs that read it, and the archive's records that cite it."""
    arch = Path(archive).resolve()
    pairs = _records(arch)
    by_name = {}
    for c, r in pairs:
        by_name.setdefault(_norm(_display(r)), (c, r))
    ids = _source_ids(arch, source, title)
    out: dict[str, dict] = {}
    for c, r in pairs:
        if ids & set(_vals(r.get("properties") or {}, "sources")):
            out[_norm(_display(r)) or r["id"]] = {"id": r["id"], "name": _display(r), "schema": r.get("schema"), "collection": c}
    for run in _runs(arch):
        if run.get("source") != source:
            continue
        for f in _found_rows(run):
            k = _norm(f.get("thing"))
            if not k or k in out:
                continue
            c, r = by_name.get(k, (None, None))
            out[k] = {"id": r["id"] if r else None, "name": _display(r) if r else f["thing"],
                      "schema": (r or {}).get("schema") or f.get("schema"), "collection": c}
    return {"ok": True, "entities": sorted(out.values(), key=lambda e: (_norm(e["name"]), e["schema"] or ""))}


def _source_ids(arch: Path, slug: str, title: str) -> set[str]:
    """The ids of the archive's source entries that are this source: by title, or by a file name that is the slug."""
    from hydra.archive import collections_io
    from hydra.sources import sourcetitle
    d, out = arch / ".hydra" / "sources" / "entries", set()
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            rows = collections_io.read(f)[0] or []
        except (OSError, ValueError):
            continue
        for r in rows:
            p = (r or {}).get("properties") or {}
            t = _first(p, "title", "name", "fileName") or ""
            stem = Path(_first(p, "fileName") or "").stem
            if r.get("id") and ((title and _norm(sourcetitle.scrub(t)) == _norm(title)) or _norm(stem) == _norm(slug)):
                out.add(r["id"])
    return out

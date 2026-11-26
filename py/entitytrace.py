"""entitytrace.py: one entity's compact Trace, and the entities of a source, for the right rails of Sources and Collections.

    trace(archive, record_id=None, name=None, schema=None)    {ok, entity, cats, lines}: the Trace, newest first
    entities(archive, source, title="")                       {ok, entities, lanes}: the entities a source gave (found rows,
                                                              and the archive's records that cite it), and per run of a
                                                              flow after Read, what it touched on each page of the source
    run_pages(run_id, archive, source, title, started, ended)  {cells, heat}: a finished workflow run's page cells (scope per
                                                              page of its source) and heatmap rows (scope, changed, asked)
    warm(archive) / warm_later(archive)                     bring the archive's history index up to date (now / in a thread)

Read only. A step comes from three places: Apollo's own log (`found`, `questions`, `events`), the run folder's files when the
log lacks them (`pageflow/found.jsonl`), and the archive's own history (which snapshots wrote the entity's record, or a link
record that names it, or name it in their message). Never a name from an author: a person is "you", an agent its role.
Steps of one category no more than 3 s apart are one line (`n` steps); the line shows the most telling verb.

The history is read through an index kept per archive under state/entitytrace/: {record id: [the snapshots that added,
changed or removed it]}, built once from every snapshot and then extended by the snapshots written since (a snapshot never
changes once written, so one indexed is never read again). A trace after new history reads only the new snapshot files and
the collection files they changed; the history store sits on a slow drive, so those reads go out in parallel.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import archives
import runfiles
import runlog

HERE = Path(__file__).resolve().parent.parent   # Apollo's folder (the code is in py/)
FOLD_SECONDS = 3
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


_LOCAL = threading.local()                               # the log connection one answer shares (one open, not one per query)


def _query(sql: str, params: list | tuple = ()) -> list[dict]:
    """A SELECT on Apollo's log, read only; [] when the log is missing or busy."""
    con = getattr(_LOCAL, "con", None)
    own = con is None
    if own:
        try:
            if not runlog.db_path().exists():
                return []
            con = runlog._open(True, 1.0)
        except Exception:                                # noqa: BLE001 - the files are the other way in
            return []
    try:
        cur = con.execute(sql, list(params))
        cols = [d[0] for d in cur.description or []]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:                                    # noqa: BLE001
        return []
    finally:
        if own:
            con.close()


class _logdb:
    """`with _logdb():` the queries inside share one read-only connection to the log."""
    def __enter__(self):
        try:
            _LOCAL.con = runlog._open(True, 1.0) if runlog.db_path().exists() else None
        except Exception:                                # noqa: BLE001 - each query then tries on its own
            _LOCAL.con = None
        return self

    def __exit__(self, *exc):
        con, _LOCAL.con = getattr(_LOCAL, "con", None), None
        if con is not None:
            con.close()
        return False


# ---- the archive's records ---------------------------------------------------------------------------------------------

_RECORDS: dict[str, tuple] = {}


def _records(arch: Path) -> list[tuple[str, dict]]:
    """(collection, record) for every record of the archive's collections, read again only when a collection file changes."""
    from hydra.archive import collections_io
    names = collections_io.names(arch)
    try:
        key = tuple((n, collections_io.path(arch, n).stat().st_mtime_ns) for n in names)
    except OSError:
        key = None
    got = _RECORDS.get(str(arch))
    if key is not None and got and got[0] == key:
        return got[1]
    out = []
    for name in names:
        try:
            rows = collections_io.read(collections_io.path(arch, name))[0] or []
        except (OSError, ValueError):
            continue
        out += [(name, r) for r in rows if isinstance(r, dict) and r.get("id")]
    _RECORDS[str(arch)] = (key, out)
    return out


def _names(arch: Path) -> dict:
    """{record id: what to call it}: an entity's name, a tie by its two ends ("Mr. Potts → Empire Transportation Company")."""
    try:
        import runview
        return runview.archive_names(arch)
    except Exception:                                    # noqa: BLE001 - the record's own words stand in
        return {}


def _display(rec: dict, names: dict | None = None) -> str:
    """What a record is called, as Collections calls it: its name, else (a tie) its two ends' names, else its role, else its id."""
    props = rec.get("properties") or {}
    return (_first(props, "name", "title", "summary", "label") or (names or {}).get(rec.get("id")) or _first(props, "role")
            or rec.get("id") or "")


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
    names = _names(arch)
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
            if _norm(_display(r, names)) == _norm(name) and (not schema or r.get("schema") == schema):
                rec, coll = r, c
                break
    else:
        raise ValueError("a record id or a name")
    if rec is None:
        return {"id": None, "name": name, "schema": schema, "collection": None}, None, pairs
    titles = _titles(arch)
    props = rec.get("properties") or {}
    card = {"id": rec["id"], "name": _display(rec, names), "schema": rec.get("schema"), "collection": coll,
            "sources": [titles.get(s, s) for s in _vals(props, "sources")]}
    try:
        from hydra.archive import manifest
        t = ((manifest.read(arch).get("collections") or {}).get(coll) or {}).get("title")
        card["collection_title"] = t or coll
    except Exception:                                    # noqa: BLE001
        card["collection_title"] = coll
    return card, rec, pairs


_TITLES: dict[str, tuple] = {}


def _titles(arch: Path) -> dict:
    """{source id: title}, read again only when a source entries file changes."""
    d = arch / ".hydra" / "sources" / "entries"
    try:
        key = tuple((e.name, e.stat().st_mtime_ns) for e in sorted(os.scandir(d), key=lambda e: e.name)) if d.is_dir() else ()
    except OSError:
        key = None
    got = _TITLES.get(str(arch))
    if key is not None and got and got[0] == key:
        return got[1]
    out = archives.source_titles(arch)
    _TITLES[str(arch)] = (key, out)
    return out


# ---- the history index -------------------------------------------------------------------------------------------------
# {"v", "head", "snaps": {sid: {p: parents, t: UTC time, a: who (never a name), m: message, f: {collection file: sha}}},
#  "recs": {record id: [[sid, "a" | "c" | "r", collection file, notes then, sources (added only)]]}}

INDEX_VERSION = 1
READERS = 16                                             # parallel reads: each read on the slow drive is a round trip
_IDX: dict[str, dict] = {}
_IDX_LOCK = threading.Lock()


def _cache_dir(arch: Path) -> Path:
    import hashlib
    import tools
    return tools.state_dir() / "entitytrace" / hashlib.sha1(str(arch.resolve()).encode()).hexdigest()[:16]


def _hist(arch: Path) -> Path:
    from hydra.archive import layout
    return arch / layout.HISTORY_DIR


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_obj(arch: Path, sha: str) -> bytes | None:
    try:
        return zlib.decompress((_hist(arch) / "objects" / sha[:2] / sha).read_bytes())
    except (OSError, zlib.error):
        return None


def _parallel(fn, items: list) -> list:
    if len(items) < 3:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(min(READERS, len(items))) as ex:
        return list(ex.map(fn, items))


def _coll_files(files: dict) -> dict:
    from hydra.archive import layout
    pre = layout.COLLECTIONS_DIR + "/"
    return {p: s for p, s in files.items() if p.startswith(pre) and p.endswith(".json")}


def _parse_records(data: bytes | None) -> dict | None:
    """A collection file's records by id (a JSON array, or one record per line); None when it is not one."""
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
        doc = json.loads(text) if text.strip() else []
    except UnicodeDecodeError:
        return None
    except ValueError:
        try:
            doc = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        except ValueError:
            return None
    if isinstance(doc, list) and all(isinstance(r, dict) and r.get("id") for r in doc):
        return {str(r["id"]): r for r in doc}
    return None


def _empty() -> dict:
    return {"v": INDEX_VERSION, "head": None, "snaps": {}, "recs": {}}


def _stamp(arch: Path):
    try:
        st = (_cache_dir(arch) / "index.json").stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _load(arch: Path) -> dict:
    stamp = _stamp(arch)
    got = _read_json(_cache_dir(arch) / "index.json") if stamp else None
    idx = got if isinstance(got, dict) and got.get("v") == INDEX_VERSION and isinstance(got.get("snaps"), dict) else _empty()
    idx["_stamp"] = stamp
    return idx


def _save(arch: Path, idx: dict) -> None:
    f = _cache_dir(arch) / "index.json"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"index.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({k: v for k, v in idx.items() if not k.startswith("_")}, ensure_ascii=False,
                                  separators=(",", ":")), encoding="utf-8")
        tmp.replace(f)
        idx["_stamp"] = _stamp(arch)
    except OSError:
        pass


def _extend(arch: Path, idx: dict, head: str) -> bool:
    """Index the snapshots not indexed yet (those written since, or on another branch); set the head once every snapshot
    it descends from is in. True when anything was added."""
    d = _hist(arch) / "snapshots"
    try:
        with os.scandir(d) as it:
            listed = {e.name[:-5] for e in it if e.name.endswith(".json")}
    except OSError:
        return False
    snaps = idx["snaps"]
    todo = sorted(listed - snaps.keys())
    bodies = _parallel(lambda s: _read_json(d / f"{s}.json"), todo)
    new = {}
    for sid, b in zip(todo, bodies):
        if isinstance(b, dict):                        # one half-written is read again next time
            new[sid] = {"p": [p for p in b.get("parents") or [] if isinstance(p, str)], "t": _utc(b.get("time")) or "",
                        "a": _who(b.get("author")), "m": _plain(b.get("message") or ""), "f": _coll_files(b.get("files") or {})}
    known = {**snaps, **new}
    pairs = []                                         # (sid, parent's collection files) of the snapshots that can be diffed
    for sid, s in sorted(new.items(), key=lambda kv: kv[1]["t"]):
        p = s["p"][0] if s["p"] else None
        if p and p not in known:
            if p in listed:                            # its parent could not be read: both wait for the next call
                continue
            pairs.append((sid, {}))                    # the history was cut there
        else:
            pairs.append((sid, known[p]["f"] if p else {}))
    uses: dict[str, int] = {}
    work = []
    for sid, fa in pairs:
        fb = new[sid]["f"]
        for path in sorted(set(fa) | set(fb)):
            if fa.get(path) != fb.get(path):
                work.append((sid, path, fa.get(path), fb.get(path)))
                for sha in (fa.get(path), fb.get(path)):
                    if sha:
                        uses[sha] = uses.get(sha, 0) + 1
    shas = sorted(uses)
    raw = dict(zip(shas, _parallel(lambda h: _read_obj(arch, h), shas)))
    parsed: dict = {}

    def take(sha):
        if not sha:
            return {}
        if sha not in parsed:
            parsed[sha] = _parse_records(raw.pop(sha, None))
        got = parsed[sha]
        uses[sha] -= 1
        if not uses[sha]:
            parsed.pop(sha, None)
        return got

    recs = idx["recs"]
    for sid, path, a, b in work:
        ra, rb = take(a), take(b)
        if ra is None or rb is None:                   # not a list of records: no record changed there
            continue
        for rid in sorted(set(rb) - set(ra)):
            props = rb[rid].get("properties") or {}
            recs.setdefault(rid, []).append([sid, "a", path, _first(props, "notes"), _vals(props, "sources")])
        for rid in sorted(k for k in rb if k in ra and rb[k] != ra[k]):
            recs.setdefault(rid, []).append([sid, "c", path, _first(rb[rid].get("properties") or {}, "notes"), None])
        for rid in sorted(set(ra) - set(rb)):
            recs.setdefault(rid, []).append([sid, "r", path, None, None])
    for sid, _ in pairs:
        snaps[sid] = new[sid]
    seen, walk, whole = set(), [head], True
    while walk:
        s = walk.pop()
        if s in seen:
            continue
        seen.add(s)
        if s not in snaps:
            whole = whole and s not in listed
            continue
        walk += snaps[s]["p"]
    if whole:
        idx["head"] = head
    return bool(pairs)


def _index(arch: Path) -> dict | None:
    """The archive's history index, up to its head; None when it keeps no history."""
    try:
        from hydra.archive.history import store
        if not store.enabled(arch):
            return None
        head = store.head(arch)[1]
    except Exception:                                    # noqa: BLE001 - no history is an empty Trace, not an error
        return None
    with _IDX_LOCK:
        idx = _IDX.get(str(arch))
        if idx is None or (head and idx["head"] != head and _stamp(arch) != idx.get("_stamp")):
            idx = _IDX[str(arch)] = _load(arch)          # first use, or another process (a run's end) wrote it since
        if head and idx["head"] != head:
            _extend(arch, idx, head)
            if idx["head"] == head:
                _save(arch, idx)
        return idx


def warm(archive) -> None:
    """Bring the archive's history index up to date (after a run, at start): the next Trace then reads no history."""
    try:
        _index(Path(archive).resolve())
    except Exception:                                    # noqa: BLE001 - a warm-up is a help, never a failure
        pass


def warm_later(archive) -> None:
    threading.Thread(target=warm, args=(archive,), daemon=True).start()


def _order(idx: dict) -> dict[str, int]:
    """{snapshot id: its place in time} for the snapshots the head descends from."""
    head = idx.get("head")
    if idx.get("_order_head") == head and "_order" in idx:
        return idx["_order"]
    snaps, seen, walk = idx["snaps"], set(), [head] if head else []
    while walk:
        s = walk.pop()
        if s in seen or s not in snaps:
            continue
        seen.add(s)
        walk += snaps[s]["p"]
    order = {s: i for i, s in enumerate(sorted(seen, key=lambda s: (snaps[s]["t"], s)))}
    idx["_order"], idx["_order_head"] = order, head
    return order


def _history_steps(idx: dict, rec: dict, tracked: dict, titles: dict) -> list[dict]:
    """Steps from the snapshots that wrote the entity's record, a link that names it, or a message that names it."""
    pos, snaps = _order(idx), idx["snaps"]
    rid = rec["id"]
    hits = sorted((pos[e[0]], e[2], "acr".index(e[1]), x, e) for x in tracked for e in idx["recs"].get(x, ()) if e[0] in pos)
    steps, cited, touched = [], False, set()
    for _, _, ci, x, e in hits:
        s = snaps[e[0]]
        touched.add(e[0])
        at, msg, who = s["t"] or None, s["m"], s["a"]
        cat, obj, desc, q = tracked[x]
        verb = ("Added", "Amended", "Removed")[ci]
        if e[1] != "r":
            q = e[3]                                     # the record as that step held it
        own = x == rid
        got = _message_step(msg) if own else None
        if got and verb != "Removed":
            v, cat, desc = got
        else:
            v = verb
        steps.append({"at": at, "cat": cat, "verb": v, "obj": obj, "desc": desc, "who": who, "quote": q, "msg": msg})
        if own and verb == "Added" and not cited:
            cited = True
            for t in [titles.get(y, y) for y in e[4] or []]:
                steps.append({"at": at, "cat": "src", "verb": "Cited", "obj": t, "desc": "Quote", "who": who, "quote": q, "msg": msg})
    word = re.compile(r"(?<![\w-])" + re.escape(rid) + r"(?![\w-])")
    for sid in sorted(pos, key=pos.get):
        s = snaps[sid]
        if sid in touched or not word.search(s["m"]):
            continue
        got = _message_step(s["m"])
        if got:
            v, cat, desc = got
            steps.append({"at": s["t"] or None, "cat": cat, "verb": v, "obj": tracked[rid][1], "desc": desc, "who": s["a"],
                          "quote": None, "msg": s["m"]})
    return steps


def _message_step(msg: str):
    for pat, verb, cat, desc in MESSAGE_VERBS:
        if re.search(pat, msg, re.I):
            return verb, cat, desc
    return None


# ---- Apollo's own log, and the run folders -----------------------------------------------------------------------------

def _runs(arch: Path) -> list[dict]:
    rows = _query("SELECT id, flow, source, archive, started, ended, status FROM runs ORDER BY started")
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


def _minted(arch: Path, runs: list[dict]) -> dict:
    """{working id: saved record id}: hydra's re-mint map in the archive, and each page-flow run's own (the run's wins)."""
    out = {}
    for f in [arch / ".hydra" / "reminted.json"] + [d / "pageflow" / "minted.json" for d in map(_run_folder, (r["id"] for r in runs)) if d]:
        got = _read_json(f)
        ids = got.get("ids") if isinstance(got, dict) else None
        out.update({str(k): str(v) for k, v in (ids or {}).items()})
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
    runs = _runs(arch)
    minted = _minted(arch, runs) if rid else {}
    steps = []
    for run in runs:
        word = str(run.get("flow") or "run").title()
        pt = _page_times(run)
        for r in _found_rows(run):
            if not (_norm(r["thing"]) in names or (rid and (r.get("record_id") == rid or minted.get(r.get("thing") or "") == rid))):
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
    with _logdb():
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
        idx = _index(arch)
        if idx:
            steps += _history_steps(idx, rec, tracked, _titles(arch))
    lines = _fold(steps)
    return {"ok": True, "entity": card, "cats": [c for c in CATS if any(ln["cat"] == c for ln in lines)], "lines": lines}


def entities(archive: str, source: str, title: str = "") -> dict:
    """The entities one source gave: found rows of the runs that read it, and the archive's records that cite it; and `lanes`,
    {run id: {page: n}} for every run on the archive after Read: what it touched on each page of this source."""
    arch = Path(archive).resolve()
    pairs = _records(arch)
    names = _names(arch)
    by_id = {r["id"]: (c, r) for c, r in pairs}
    ids = _source_ids(arch, source, title)
    out: dict[str, dict] = {}

    def own(rid):
        c, r = by_id[rid]
        return {"id": rid, "name": _display(r, names), "schema": r.get("schema"), "collection": c}
    for c, r in pairs:
        if ids & set(_vals(r.get("properties") or {}, "sources")):
            out[r["id"]] = own(r["id"])
    with _logdb():
        runs = _runs(arch)
        pages, minted, found = _source_pages(arch, source, runs, pairs, names)
        for f, rid in found:
            thing = str(f.get("thing") or "")
            if rid:
                out.setdefault(rid, own(rid))
                continue
            k = "~" + _norm(thing)
            if thing and k not in out:
                out[k] = {"id": None, "name": _said(thing, minted, names), "schema": f.get("schema"), "collection": None}
        lanes = {}
        coll_of = {r["id"]: c for c, r in pairs}
        for run in (r for r in runs if (r.get("flow") or "read") != "read"):
            m = _run_measures(arch, run, pages, minted, ids, coll_of)
            lanes[run["id"]] = {str(p): len(v["changed"] | v["asked"] | v["events"]) for p, v in sorted(m.items())
                                if v["changed"] | v["asked"] | v["events"]}
    if not lanes:
        _index(arch)                                     # the list opens before a Trace: its history is then read already
    shown, seen = [], set()                             # one name of one type is listed once (a record before a found row)
    for e in sorted(out.values(), key=lambda e: (_norm(e["name"]), e["schema"] or "", e["id"] is None)):
        if (_norm(e["name"]), e["schema"] or "") not in seen:
            seen.add((_norm(e["name"]), e["schema"] or ""))
            shown.append(e)
    return {"ok": True, "entities": shown, "lanes": lanes}


def _said(thing: str, minted: dict, names: dict) -> str:
    """A found row's thing as a reader says it: a working id ("p47-t1", "person-x → company-y") by its record's name."""
    def one(t):
        t = t.strip()
        return names.get(minted.get(t, t)) or names.get(t) or t
    return " → ".join(one(t) for t in thing.split(" → ")) if " → " in thing else one(thing)


def _found_ids(runs: list[dict], minted: dict, by_id: dict, by_name: dict):
    """(found row, the record it is) for every found row of the runs; None when it is no record of the archive (yet)."""
    for run in runs:
        for f in _found_rows(run):
            thing = str(f.get("thing") or "")
            yield f, next((x for x in (f.get("record_id"), minted.get(thing), thing if thing in by_id else None,
                                       by_name.get(_norm(thing))) if x and x in by_id), None)


_SOURCE_PAGES: dict[str, tuple] = {}


def _source_pages(arch: Path, source: str, runs: list[dict], pairs: list, names: dict) -> tuple[dict, dict, list]:
    """({record id: {pages of the source it was found on}}, {working id: record id}, [(found row, record id | None)]): what the
    source's own runs found, and the ties and links between found records (on their ends' pages). The last answer per source is
    kept while no collection file and no run on the source changes."""
    mine = [r for r in runs if r.get("source") == source]
    key = (_RECORDS.get(str(arch), (None,))[0], tuple((r["id"], str(r.get("ended"))) for r in mine))
    kept = _SOURCE_PAGES.get(f"{arch}|{source}")
    if kept and key[0] is not None and kept[0] == key:
        return kept[1]
    by_id = {r["id"]: (c, r) for c, r in pairs}
    by_name: dict[str, str] = {}
    for c, r in pairs:
        by_name.setdefault(_norm(_display(r, names)), r["id"])
    minted = _minted(arch, mine)
    found = list(_found_ids(mine, minted, by_id, by_name))
    pages: dict[str, set] = {}
    for f, rid in found:
        if rid and f.get("page") is not None:
            pages.setdefault(rid, set()).add(f["page"])
    pre = arch.name + ":"
    for _, r in pairs:                                  # a tie, a link, a place: the pages of the records it names
        if r["id"] in pages:
            continue
        got = set()
        for k, v in (r.get("properties") or {}).items():
            if k in SKIP_KEYS:
                continue
            for x in v if isinstance(v, list) else [v]:
                if isinstance(x, str):
                    got |= pages.get(x.removeprefix(pre), set())
        if got:
            pages[r["id"]] = got
    answer = (pages, minted, found)
    _SOURCE_PAGES[f"{arch}|{source}"] = (key, answer)
    return answer


def _window(run: dict) -> tuple[float, float] | None:
    """(start, end) epoch seconds of a run in the log (end: now while it runs)."""
    try:
        t0 = datetime.fromisoformat(str(run["started"]).replace(" ", "T")[:19]).timestamp()
        t1 = datetime.fromisoformat(str(run["ended"]).replace(" ", "T")[:19]).timestamp() if run.get("ended") else time.time()
    except (KeyError, TypeError, ValueError):
        return None
    return (t0, t1)


_ASKS: dict[str, tuple] = {}


def _review_questions(arch: Path) -> list[dict]:
    """The archive's review store (`.hydra/review/questions.yaml`), read again only when the file changes."""
    try:
        stamp = (arch / ".hydra" / "review" / "questions.yaml").stat().st_mtime_ns
    except OSError:
        return []
    got = _ASKS.get(str(arch))
    if got and got[0] == stamp:
        return got[1]
    qs = runfiles.load_questions(arch)
    _ASKS[str(arch)] = (stamp, qs)
    return qs


def _run_asks(arch: Path, window) -> list[dict]:
    """The review-store questions a run put: its tasks' (`asked_by.task`, a task created inside the run's window) and any
    asked inside the window. The Assemble's nine live only here, not in Apollo's own `questions` table."""
    if not window:
        return []
    mine = runfiles.window_tasks(arch, window)
    return [q for q in _review_questions(arch)
            if runfiles.in_window(q.get("asked"), window) or (q.get("asked_by") or {}).get("task") in mine]


def _run_collections(arch: Path, window) -> set[str]:
    """The collections a run's tasks named in their calls (`collections` / `collection`)."""
    out: set[str] = set()
    for c in runfiles.archive_calls(arch) if window else []:
        if runfiles.in_window(c.get("_stamp"), window):
            for k in ("collections", "collection"):
                v = (c.get("_args") or {}).get(k)
                out |= {x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)}
    return out


def _run_measures(arch: Path, run: dict, pages: dict, minted: dict, ids: set, coll_of: dict) -> dict:
    """{page: {scope, changed, asked, events, touch: sets}}: per page of the source, the records the run's work covers there
    (those of its collections found on the page, and any it changed or asked about), the records it added, changed or removed
    (its history steps), the questions it put (the review store: Apollo's own `questions` rows are copies of it, and an
    ingest before the run's window was known copied every run's) and its events there."""
    pre = arch.name + ":"
    lo, hi = _utc(run.get("started")), _utc(run.get("ended")) or "9999"
    window = _window(run)
    idx = _index(arch)
    pos = _order(idx) if idx else {}
    out: dict[int, dict[str, set]] = {}

    def put(page, key, v):
        try:
            out.setdefault(int(page), {k: set() for k in ("scope", "changed", "asked", "events", "touch")})[key].add(v)
        except (TypeError, ValueError):
            pass

    if idx and lo:
        for rid, entries in idx["recs"].items():
            if rid in pages and any(e[0] in pos and lo <= idx["snaps"][e[0]]["t"] <= hi for e in entries):
                for p in pages[rid]:
                    put(p, "changed", rid)
                    put(p, "touch", rid)

    def ask(qid, page, said):
        ps = {page} if page not in (None, "") else set()
        for x in said if isinstance(said, list) else []:
            rid = str(x).removeprefix(pre)
            rid = minted.get(rid, rid)
            for p in pages.get(rid, ()):
                ps.add(p)
                put(p, "touch", rid)
        for p in ps:
            put(p, "asked", "q:" + str(qid))

    for q in _run_asks(arch, window):
        src = str(q.get("source") or "").removeprefix(pre)
        if not ids or not src or src in ids:
            ask(q["id"], q.get("page"), q.get("records"))
    for e in _query("SELECT seq, page FROM events WHERE run_id=? AND page IS NOT NULL AND kind<>'log'", [run["id"]]):
        put(e["page"], "events", f"e:{e['seq']}")
    cols = _run_collections(arch, window)
    for rid, ps in pages.items():
        if coll_of.get(rid) in cols:
            for p in ps:
                put(p, "scope", rid)
    for v in out.values():
        v["scope"] |= v["touch"]
    return out


# ---- a finished workflow run's page cells and heatmap rows --------------------------------------------------------------
# One shape with a page-flow run's: `cells` [[page, status, records]] (records: the run's scope on the page) and `heat` rows
# like runfiles.heat's, with `changed` and `asked` beside `records`. A finished run never changes, so it is worked out once
# (kept in memory and under state/entitytrace/runpages/); a run that gave nothing is tried again after RETRY seconds, in case
# a Read on its source has been logged since.

RETRY = 30
_RUNPAGES: dict[str, tuple] = {}
_RUNPAGES_LOCK = threading.Lock()


def run_pages(run_id: str, archive: str | None, source: str | None, title: str | None, started: str | None, ended: str | None) -> dict:
    """{cells, heat} of a finished workflow run on a source's pages; both [] for a run still going or with no archive or source."""
    none = {"cells": [], "heat": []}
    if not (archive and source and started and ended):
        return none
    with _RUNPAGES_LOCK:
        got = _RUNPAGES.get(run_id)
        if got and (got[1]["cells"] or time.time() - got[0] < RETRY):
            return got[1]
        f = runlog.state_dir() / "entitytrace" / "runpages" / f"{run_id}.json"
        disk = _read_json(f)
        if isinstance(disk, dict) and disk.get("cells"):
            _RUNPAGES[run_id] = (time.time(), disk)
            return disk
        try:
            out = _compute_run_pages(run_id, archive, source, title or "", started, ended)
        except Exception:                                # noqa: BLE001 - a missing map is no cells, never a failed list
            out = none
        _RUNPAGES[run_id] = (time.time(), out)
        if out["cells"]:
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps(out), encoding="utf-8")
            except OSError:
                pass
        return out


def _compute_run_pages(run_id: str, archive: str, source: str, title: str, started: str, ended: str) -> dict:
    arch = Path(archive).resolve()
    pairs, names = _records(arch), _names(arch)
    run = {"id": run_id, "started": started, "ended": ended}
    with _logdb():
        runs = _runs(arch)
        pages, minted, _ = _source_pages(arch, source, runs, pairs, names)
        m = _run_measures(arch, run, pages, minted, _source_ids(arch, source, title), {r["id"]: c for c, r in pairs})
        read = [r for r in runs if r.get("source") == source and (r.get("flow") or "read") == "read"]
        status = {int(x["page"]): x["status"] for x in _query("SELECT page, status FROM pages WHERE run_id=?", [read[-1]["id"]])
                  if x.get("page") is not None} if read else {}
    cells, heat = [], []
    for p in sorted(set(status) | set(m)):
        if status.get(p) == "out-of-scope":
            continue
        v = m.get(p) or {}
        n, changed, asked = len(v.get("scope", ())), len(v.get("changed", ())), len(v.get("asked", ()))
        st = status[p] if status.get(p) in runfiles.SKIPPED else "done"
        cells.append([p, st, n])
        heat.append({"page": p, "status": st, "records": n, "quotes": 0, "places": 0, "groups": {}, "calls": 0, "seconds": 0.0,
                     "problems": {}, **({"changed": changed} if changed else {}), **({"asked": asked} if asked else {})})
    return {"cells": cells, "heat": heat}


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

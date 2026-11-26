"""pageview.py: one page of a run as plain data for the window's page box, and the run's page strip. Reads only.

`strip(files)` is the run's pages (status, records, words, redaction) and, when the run's archive keeps the source's
doc-structure sidecar, its sections. `page(files, n)` is the page box: the section, the counts, then the people, bodies,
places (countries as outlines), events and quotes found on that page. A part with no data is left out of the answer, so
the window shows nothing where there is nothing: no placeholders. Everything comes from the run folder (found.jsonl,
triage.json, pages/N.mentions.json, pages/N.facts.json, roster.json) and, read only, the run's archive.

Country outlines: hydra's own Natural Earth adm0 layer (`hydra.geo.register`, on this machine, never the network),
simplified, drawn as one SVG path per country and kept in `state/pageview/countries.json`.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from runfiles import PLACE_SCHEMAS, cells, read_json
from runview import TIE_ENDS, archive_names, archive_records, named, type_group

SIZE = 48                                              # a country's box, px (the draft draws at most 48)
GROUPS = ("people", "bodies", "places", "events", "ties", "posts")   # the Found tab's categories (runview.TYPE_GROUPS), one filter for both
RED_WORD = ("partial", "mostly", "entire")
NEAR_FRACTION, NEAR_DEGREES = 0.1, 2.0                 # a piece joins the main one within this share of its size, and this many degrees
CACHE_VERSION = 2                                      # the outlines kept in state/pageview/countries.json; raise it when the drawing changes


def _state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or Path(__file__).resolve().parent / "state")


MIN_SHARE = 0.15                                       # a text source's share of redacted lines below this is noise, not redaction


def is_pdf(files) -> bool:
    """The run's source is a PDF (its pages are numbered): else it is read in parts. The page flow's own test (state.profile.pages)."""
    return bool((files.state().get("profile") or {}).get("pages"))


def redaction(share, status: str = "", pdf: bool = True) -> str | None:
    """The level of a page's redaction from triage's share of redacted lines: partial, mostly or entire (None: none). Real only for a
    PDF, or when the share is at least MIN_SHARE: the triage counts lines that look redacted, and a published book has a few that do not."""
    try:
        s = float(share or 0)
    except (TypeError, ValueError):
        return None
    if not (pdf or s >= MIN_SHARE):
        return None
    if status == "redacted":
        return "entire"
    return None if s <= 0 else "partial" if s < 0.2 else "mostly" if s < 0.75 else "entire"


def _page_rows(found: list[dict], page: int | None = None) -> list[dict]:
    """The records found, one per (page, schema, thing): found.jsonl holds a thing once as `proposed` (Pass A) and again as `cited` (saved), and
    again for each quote; this is the one list every count of the window comes from. `cited` wins, the first quote stays."""
    best = {}
    for r in found:
        p, t = r.get("page"), (r.get("thing") or "").strip()
        if not p or not t or (page is not None and p != page):
            continue
        k = (p, r.get("schema") or "", t.lower())
        old = best.get(k)
        if old is None:
            best[k] = {**r, "thing": t}
        else:
            if r.get("state") == "cited":
                old["state"] = "cited"
            if not old.get("quote") and r.get("quote"):
                old["quote"] = r["quote"]
    return list(best.values())


def _triage(files) -> dict:
    rows = read_json(files.pf / "triage.json", []) or []
    return {r["page"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("page"), int)}


# ---- sections: the source's doc-structure sidecar, read through hydra's own api ---------------------------------------------

def _heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip("~|_.,;:-").strip()[:90]


def _plausible(title: str) -> bool:
    """A heading worth a band: a few words that read as words. The rules read headings off scanned text too, where a stray line
    of noise or a whole sentence can look like one; a title repeated across the document is furniture and is dropped by the caller."""
    letters = sum(c.isalpha() or c == " " for c in title)
    return 4 <= len(title) <= 80 and 1 <= len(title.split()) <= 8 and letters >= 0.9 * len(title)


def structure_pages(archive) -> dict:
    """{page: {title, label}}: the heading each PDF page sits under, from the kept source's `structure.json` sidecar, and the
    printed page label where hydra knows it. {} when the archive has no source with a sidecar (then there is no band)."""
    if not archive:
        return {}
    root = Path(archive)
    entries = root / ".hydra" / "sources" / "entries"
    if not entries.is_dir():
        return {}
    try:
        from hydra.api import sources as hs
    except ImportError:
        return {}
    for f in sorted(entries.glob("*.json")):
        for rec in read_json(f, []) or []:
            sid = rec.get("id") if isinstance(rec, dict) else None
            if not sid:
                continue
            try:
                doc = hs.structure_of(root, sid)
                ranges = hs.page_ranges_of(root, sid)
                text = hs.text_of(root, sid)
            except Exception:                              # a source that cannot be read has no sections
                continue
            if not doc or not ranges or text is None:
                continue
            try:
                labels = hs.page_labels_of(root, sid) or {}
            except Exception:
                labels = {}
            heads = sorted((s for s in doc["spans"] if s["kind"] == "heading"), key=lambda s: s["start"])
            seen = {}
            for s in heads:
                t = _heading(text[s["start"]:s["end"]])
                seen[t] = seen.get(t, 0) + 1
            heads = [s for s in heads if _plausible(_heading(text[s["start"]:s["end"]])) and seen[_heading(text[s["start"]:s["end"]])] < 3]
            levels = sorted({(s.get("attrs") or {}).get("level") or 1 for s in heads})
            top = levels[min(1, len(levels) - 1)] if levels else 1
            heads = [s for s in heads if ((s.get("attrs") or {}).get("level") or 1) <= top]
            out, i, cur = {}, 0, None
            for r in sorted(ranges, key=lambda r: r["page"]):
                while i < len(heads) and heads[i]["start"] < r["end"]:
                    t = _heading(text[heads[i]["start"]:heads[i]["end"]])
                    cur = t or cur
                    i += 1
                out[int(r["page"])] = {"title": cur, "label": labels.get(r["page"]) or labels.get(str(r["page"]))}
            return out
    return {}


def _sections(pages: list[int], meta: dict) -> list[dict]:
    """[{title, a, b, i}]: runs of consecutive pages under one heading (a page with none is no band)."""
    out = []
    for p in pages:
        t = (meta.get(p) or {}).get("title")
        if not t:
            continue
        if out and out[-1]["title"] == t and out[-1]["b"] == p - 1:
            out[-1]["b"] = p
        else:
            out.append({"title": t, "a": p, "b": p})
    for i, s in enumerate(out):
        s["i"] = i
    return out


# ---- the strip ----------------------------------------------------------------------------------------------------------

def _evidence(rows: list[dict], cmap: dict) -> dict:
    """Quoted n / N and Located n / N over deduped records (`_page_rows`); `cmap` is `_mention_countries`."""
    out = {}
    if rows:
        out["quoted"] = [sum(1 for r in rows if r.get("quote")), len(rows)]
    pl = {(r["page"], r["thing"].lower()) for r in rows if (r.get("schema") or "") in PLACE_SCHEMAS}
    if pl:
        out["located"] = [sum(1 for p, t in pl if t in cmap.get(p, {})), len(pl)]
    return out


def _mention_countries(files, pages) -> dict:
    """{page: {place name lower: (ISO, place_type)}} from pages/N.mentions.json: the Read pass's own country for a place, and what kind
    of place it called it (country, city, ...)."""
    out = {}
    for p in pages:
        doc = read_json(files.pf / "pages" / f"{p}.mentions.json", {}) or {}
        m = {}
        for x in doc.get("mentions") or []:
            if x.get("kind") == "Place" and x.get("country"):
                for n in [x.get("name")] + list(x.get("as_written") or []):
                    if n:
                        m[str(n).lower()] = (str(x["country"]).upper(), str(x.get("place_type") or ""))
        if m:
            out[p] = m
    return out


def strip(files, archive=None) -> dict:
    """The run's pages, one cell each: {ok, pages: [{page, status, records, words, red, label, section}], sections?, redaction?, evidence}."""
    found = files.found()
    prog = files.progress() or {}
    tri = _triage(files)
    cs = cells(prog, found)
    pages = [c[0] for c in cs]
    meta = structure_pages(archive or files.archive())
    secs = _sections(pages, meta)
    sec_of = {p: s["i"] for s in secs for p in range(s["a"], s["b"] + 1)}
    pdf = is_pdf(files)
    rows = _page_rows(found)
    count = {}
    for r in rows:
        count[r["page"]] = count.get(r["page"], 0) + 1
    out_pages = []
    for page, status, _ in cs:
        t = tri.get(page) or {}
        row = {"page": page, "status": status, "records": count.get(page, 0)}
        if t.get("words"):
            row["words"] = t["words"]
        red = redaction(t.get("redacted_share"), t.get("status") if t.get("status") == "redacted" else "", pdf)
        if red:
            row["red"] = red
        lab = (meta.get(page) or {}).get("label")
        if lab:
            row["label"] = str(lab)
        if page in sec_of:
            row["section"] = sec_of[page]
        out_pages.append(row)
    out = {"ok": True, "unit": "page" if pdf else "part", "pages": out_pages}
    if secs:
        out["sections"] = secs
    if any(r.get("red") for r in out_pages):
        out["redaction"] = True
    ev = _evidence(rows, _mention_countries(files, pages))
    if ev:
        out["evidence"] = ev
    return out


# ---- country outlines ---------------------------------------------------------------------------------------------------

def _rings(geom: dict) -> list[list]:
    """The outer rings of a Polygon or MultiPolygon as [[lon, lat], ...]: the largest piece, and the pieces that lie close to it (an island
    chain: Corsica, Hokkaido). Far-off territories (Alaska, Hawaii, an overseas department) are not part of the shape."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]] if geom["type"] == "Polygon" else []
    rings = [p[0] for p in polys if p]
    if not rings:
        return []

    def box(r):
        xs, ys = [c[0] for c in r], [c[1] for c in r]
        return min(xs), min(ys), max(xs), max(ys)

    def area(b):
        return (b[2] - b[0]) * (b[3] - b[1])
    boxes = [box(r) for r in rings]
    main = max(range(len(rings)), key=lambda i: area(boxes[i]))
    mb = boxes[main]
    limit = min(NEAR_FRACTION * max(mb[2] - mb[0], mb[3] - mb[1]), NEAR_DEGREES)

    def gap(b):
        return max(0.0, b[0] - mb[2], mb[0] - b[2], b[1] - mb[3], mb[1] - b[3])
    return [r for i, r in enumerate(rings) if i == main or (gap(boxes[i]) <= limit and area(boxes[i]) >= area(mb) * 0.005)]


def outline(iso: str, store=None) -> list | None:
    """[name, svg path d, width, height] for a country by its ISO code, from hydra's register (read only); None if unknown."""
    key = "iso3166:" + iso.upper()
    if store is None:
        from hydra.geo import register
        store = register.store()
    ent = store.entry(key)
    geom = store.shape(key, 15000)
    if not ent or not geom:
        return None
    rings = _rings(geom)
    if not rings:
        return None
    pts = [c for r in rings for c in r]
    mid = (min(c[1] for c in pts) + max(c[1] for c in pts)) / 2
    k = math.cos(math.radians(mid))
    x0, x1 = min(c[0] for c in pts) * k, max(c[0] for c in pts) * k
    y0, y1 = min(c[1] for c in pts), max(c[1] for c in pts)
    sc = (SIZE - 2) / max(x1 - x0, y1 - y0, 1e-9)
    w, h = round((x1 - x0) * sc + 2), round((y1 - y0) * sc + 2)
    d = []
    for r in rings:
        d.append("M" + "L".join(f"{(c[0] * k - x0) * sc + 1:.1f} {(y1 - c[1]) * sc + 1:.1f}" for c in r) + "Z")
    return [ent.get("name") or iso.upper(), "".join(d), w, h]


def countries(isos) -> dict:
    """{ISO: [name, d, w, h]} for these countries, outlines kept in state/pageview/countries.json; a country hydra does not know is left out."""
    path = _state_dir() / "pageview" / "countries.json"
    doc = read_json(path, {}) or {}
    have = doc.get("c", {}) if doc.get("v") == CACHE_VERSION else {}
    todo = [i for i in dict.fromkeys(x.upper() for x in isos) if i not in have]
    if todo:
        try:
            from hydra.geo import register
            store = register.store()
        except Exception:                                  # no register on this machine: no outlines
            store = None
        if store is not None:
            for i in todo:
                try:
                    have[i] = outline(i, store)
                except Exception:
                    have[i] = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"v": CACHE_VERSION, "c": have}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    return {i: have[i] for i in (x.upper() for x in isos) if have.get(i)}


# ---- the page box -------------------------------------------------------------------------------------------------------

def _group(rows: list[dict], key: str) -> list[dict]:
    """[{name, schema, count, quote}] for the entities of one group on the page (`rows` are `_page_rows`, one per thing): `count` is how
    many of the page's records name it in their quote, at least the one it was found in."""
    quotes = {(r.get("quote") or "").strip() for r in rows if r.get("quote")}
    out = []
    for r in rows:
        if type_group(r.get("schema") or "") != key:
            continue
        name = r["thing"]
        n = sum(1 for q in quotes if name.lower() in q.lower())
        out.append({"name": name, "schema": r.get("schema"), "count": max(1, n), "quote": r.get("quote") or ""})
    return sorted(out, key=lambda e: (-e["count"], e["name"].lower()))


def _minted(files) -> dict:
    """{working id: minted id}: the page flow saves minted ids (`<run>/pageflow/minted.json`, hydra's api/mint.py) and keeps its own
    working ids (p37-t1) inside the run; a run from before minting has none."""
    got = read_json(files.pf / "minted.json", {}) or {}
    ids = got.get("ids") if isinstance(got, dict) else None
    return {str(k): str(v) for k, v in (ids or {}).items()}


def _roster(files) -> dict:
    out = {x["id"]: x.get("name") for x in (read_json(files.folder / "roster.json", []) or []) if isinstance(x, dict) and x.get("id")}
    for w, m in _minted(files).items():                      # the saved records answer to their minted ids too
        if w in out:
            out[m] = out[w]
    return out


def _events(files, page: int, rows: list[dict], names: dict | None = None) -> list[dict]:
    facts = read_json(files.pf / "pages" / f"{page}.facts.json", {}) or {}
    names = names if names is not None else _roster(files)
    by = {}
    for rec in facts.get("records") or []:
        if rec.get("schema") not in ("Event", "Payment"):
            continue
        pr = rec.get("properties") or {}
        nm = (pr.get("name") or [""])[0]
        if nm:
            by[nm.lower()] = pr
    out, seen = [], set()
    for r in rows:
        if r.get("schema") not in ("Event", "Payment"):
            continue
        t = (r.get("thing") or "").strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        pr = by.get(t.lower()) or {}
        who = []
        for pid in pr.get("involved") or []:
            nm = names.get(pid)
            if nm:
                who.append({"name": nm, "schema": "Organization" if re.match(r"(organization|publicbody|company)", pid) else "Person"})
        e = {"title": t, "quote": r.get("quote") or ""}
        if pr.get("summary"):
            e["summary"] = pr["summary"][0]
        if pr.get("date"):
            e["date"] = pr["date"][0]
        if who:
            e["who"] = who[:5]
        out.append(e)
    return out


def _places(files, page, rows: list[dict], pages_of: dict | None = None) -> dict:
    """{countries: [{iso, name, count, places, d, w, h}], loose: [{name, count}]}: places found on the page (`page` may be a list of pages,
    for a whole run), a place with a country (from the Read pass's mentions) filed under it, the rest as chips. `pages_of` ({name lower:
    [pages]}) adds `pages` to each card and chip."""
    by = {}                                                # one place once, whatever schema it was found as
    for r in rows:
        if (r.get("schema") or "") in PLACE_SCHEMAS:
            by.setdefault(r["thing"].lower(), r["thing"])
    if not by:
        return {}
    iso_of = {}
    for m in _mention_countries(files, page if isinstance(page, (list, tuple, set)) else [page]).values():
        for k, v in m.items():
            iso_of.setdefault(k, v)
    cty, loose = {}, []
    for k, nm in by.items():
        iso, kind = iso_of.get(k, (None, ""))
        if iso:
            c = cty.setdefault(iso, {"iso": iso, "count": 0, "places": [], "self": 0, "names": []})
            c["names"].append(nm)
            c["count"] += 1                               # records: this country's places on the page, one each
            if kind == "country":                          # the country itself (even as OCR spells it) is the card, not a city under it
                c["self"] += 1
            else:
                c["places"].append(nm)
        else:
            loose.append({"name": nm, "count": 1})
    shapes = countries(list(cty))
    cards = []
    for iso, c in sorted(cty.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        shp = shapes.get(iso)
        if not shp:                                        # no outline on this machine: the places are chips
            loose += [{"name": n, "count": 1} for n in c["places"]]
            continue
        name, d, w, h = shp
        c.pop("self")
        names = c.pop("names")
        if pages_of is not None:
            c["pages"] = sorted({p for n in names for p in pages_of.get(n.lower(), [])})
        c["name"], c["d"], c["w"], c["h"] = name, d, w, h
        c["places"] = [n for n in c["places"] if n.lower() != name.lower()]   # a place named as the country, whatever its kind
        cards.append(c)
    # geo-context (read-only): a place hydra flagged as set against its source marks its country card and its chip
    odd = {str(f.get("title") or "").lower() for f in (((files.state().get("close") or {}).get("geo_flags") or {}).get("flags") or [])
           if f.get("code") == "geo.outlier"}
    for c in cards:
        if odd & {p.lower() for p in c["places"]}:
            c["odd"] = 1
    for e in loose:
        if e["name"].lower() in odd:
            e["odd"] = 1
        if pages_of is not None:
            e["pages"] = pages_of.get(e["name"].lower(), [])
    out = {}
    if cards:
        out["countries"] = cards
    if loose:
        out["loose"] = sorted(loose, key=lambda e: (-e["count"], e["name"].lower()))
    return out


def page(files, n: int, archive=None) -> dict:
    """The page box for page `n` of the run: {ok, page, label?, pdf?, section?, pos?, of?, prev?, next?, counts, redaction?, people?,
    bodies?, places?, events?, quotes?, evidence?}. Only what the run has for this page."""
    st = strip(files, archive)
    pages = [r["page"] for r in st["pages"]]
    if n not in pages:
        return {"ok": False, "why": f"page {n} is not one of this run's pages"}
    i = pages.index(n)
    cell = st["pages"][i]
    rows = _page_rows(named(files.found(), archive), n)                 # ids said by name (runview.named)
    out = {"ok": True, "unit": st["unit"], "page": n, "prev": pages[i - 1] if i else None, "next": pages[i + 1] if i + 1 < len(pages) else None}
    if cell.get("label") and cell["label"] != str(n):
        out["label"] = cell["label"]
    out["pos"], out["of"] = i + 1, len(pages)
    if "section" in cell:
        s = st["sections"][cell["section"]]
        out["section"] = {"title": s["title"], "i": s["i"]}
        out["pos"], out["of"] = n - s["a"] + 1, s["b"] - s["a"] + 1
    counts = {"records": len(rows)}
    if cell.get("words"):
        counts["words"] = cell["words"]
    if cell.get("red"):
        out["redaction"] = cell["red"]
    groups = {}
    for key in ("people", "bodies", "ties", "posts"):
        g = _group(rows, key)
        if g:
            groups[key] = g
            counts[key] = len(g)
    pl = _places(files, n, rows)
    if pl:
        groups["places"] = pl
        counts["places"] = len({r["thing"].lower() for r in rows if (r.get("schema") or "") in PLACE_SCHEMAS})
    ev = _events(files, n, rows)
    if ev:
        groups["events"] = ev
        counts["events"] = len(ev)
    out.update(groups)
    out["counts"] = counts
    quotes, seen = [], set()
    for r in rows:
        q = (r.get("quote") or "").strip()
        if q and q not in seen:
            seen.add(q)
            quotes.append({"q": q[:400], "what": r.get("thing") or ""})
    if quotes:
        out["quotes"] = quotes[:12]
    evi = _evidence(rows, _mention_countries(files, [n]))
    if evi:
        out["evidence"] = evi
    return out


# ---- the whole run ------------------------------------------------------------------------------------------------------

_RUN_MEMO: dict = {}                                   # {key: answer}: the run's rich data, kept while its files are as they were
_HID_MEMO: dict = {}
RUN_MEMO_MAX = 16
RUN_QUOTES = 60


def _stamp(path) -> tuple | None:
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _hids(archive) -> dict:
    """{record id: H-ID} from the archive's published universe/canon.jsonl (a member "self/draft:person-x" is the record "person-x");
    empty when the archive's build has none."""
    f = Path(str(archive or "")) / "universe" / "canon.jsonl"
    stamp = _stamp(f)
    if stamp is None:
        return {}
    if _HID_MEMO.get("key") == (str(f), stamp):
        return _HID_MEMO["h"]
    out = {}
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("h"):
                for m in row.get("members") or [row.get("anchor")]:
                    if isinstance(m, str):
                        out[m.split(":", 1)[-1]] = row["h"]
    except OSError:
        return {}
    _HID_MEMO.update(key=(str(f), stamp), h=out)
    return out


def _saved(archive, minted: dict | None = None) -> dict:
    """{(schema, name lower): [archive records]}: what the run saved, found again by the name its found rows use (a tie is "A → B")."""
    names = archive_names(archive)
    back = {str(m).split(":", 1)[-1]: w for w, m in (minted or {}).items()}    # a minted record's working id (p37-t1) gives its page
    out = {}
    for r in archive_records(archive):
        nm = names.get(r["id"])
        if nm:
            w = back.get(str(r["id"]).split(":", 1)[-1])
            out.setdefault((r.get("schema") or "", nm.lower()), []).append({**r, "_working": w} if w else r)
    return out


def _saved_for(saved: dict, schema: str, thing: str, page: int) -> dict | None:
    cands = saved.get((schema, thing.lower())) or []
    for r in cands:                                          # a tie saved on this page, before one of the same name saved on another
        if str(r["id"]).startswith(f"p{page}-") or str(r.get("_working") or "").startswith(f"p{page}-"):
            return r
    return cands[0] if cands else None


def _tie(rec: dict | None, thing: str, names: dict, by_id: dict) -> dict:
    """{from, to, role?, date?, start?, end?} of a tie: both ends by name (and id and schema when the archive has them) from the saved
    record's two end properties, or, for a tie not saved, from its "A → B" name."""
    p = (rec or {}).get("properties") or {}
    ids = [v[0] for k in TIE_ENDS for v in [p.get(k)] if isinstance(v, list) and v and isinstance(v[0], str)][:2]
    ends = []
    if len(ids) == 2:
        for i in ids:
            e = {"name": names.get(i) or names.get(i.split(":", 1)[-1]) or i, "id": i}
            sch = (by_id.get(i) or by_id.get(i.split(":", 1)[-1]) or {}).get("schema")
            if sch:
                e["schema"] = sch
            ends.append(e)
    elif " → " in thing:
        ends = [{"name": x.strip()} for x in thing.split(" → ", 1)]
    out = {}
    if len(ends) == 2:
        out["from"], out["to"] = ends
    for key, prop in (("role", "role"), ("date", "date"), ("start", "startDate"), ("end", "endDate")):
        v = p.get(prop)
        if isinstance(v, list) and v and v[0]:
            out[key] = v[0]
    return out


def _run_key(files, archive, group, page) -> tuple:
    root = Path(str(archive or ""))
    cols = sorted((root / "collections").glob("*.json")) if root.is_dir() else []
    return (str(files.folder), group, page, _stamp(files.pf / "found.jsonl"), _stamp(files.pf / "pages"), _stamp(files.pf / "state.json"),
            _stamp(files.folder / "roster.json"), _stamp(root / "universe" / "canon.jsonl"), tuple((f.name, _stamp(f)) for f in cols))


def run(files, archive=None, group: str | None = None, page: int | None = None) -> dict:
    """The page box for the whole run (or one page): {ok, scope, unit, page?, group?, pages, counts, people?, bodies?, places?, events?,
    ties?, posts?, quotes?, evidence?}, each group shaped as `page()` shapes it, every item also with `pages` (where it appears) and a
    summed `count`, `cited`, and `h` (the archive's H-ID) when there is one. `group` keeps one of GROUPS. A tie carries `kind` (its schema),
    `from` and `to` (both ends by name), and its role and dates. Kept until a file of the run or its archive changes."""
    if group is not None and group not in GROUPS:
        return {"ok": False, "why": f"group: one of {', '.join(GROUPS)}"}
    archive = archive or files.archive()
    key = _run_key(files, archive, group, page)
    if key in _RUN_MEMO:
        return _RUN_MEMO[key]
    wanted = (group,) if group else GROUPS
    rows = [r for r in _page_rows(named(files.found(), archive), page) if type_group(r.get("schema") or "") in wanted]
    pages = sorted({r["page"] for r in rows})
    by_page = {}
    for r in rows:
        by_page.setdefault(r["page"], []).append(r)
    cited = {(r["page"], r.get("schema") or "", r["thing"].lower()) for r in rows if r.get("state") == "cited"}
    saved, hids, names = _saved(archive, _minted(files)), _hids(archive), archive_names(archive)
    by_id = {r["id"]: r for r in archive_records(archive)}
    out = {"ok": True, "scope": "page" if page else "run", "unit": "page" if is_pdf(files) else "part"}
    if page:
        out["page"] = page
    if group:
        out["group"] = group
    out["pages"] = pages
    counts = {"records": len(rows), "pages": len(pages)}
    for gkey in ("people", "bodies", "ties", "posts"):
        if gkey not in wanted:
            continue
        items = {}
        for p in pages:
            for e in _group(by_page.get(p, []), gkey):
                k = (e["schema"], e["name"].lower())
                it = items.get(k)
                if it is None:
                    it = items[k] = {**e, "count": 0, "pages": [], "cited": False}
                    rec = _saved_for(saved, e["schema"], e["name"], p)
                    if rec:
                        it["id"] = rec["id"]
                        if rec["id"] in hids:
                            it["h"] = hids[rec["id"]]
                    if gkey == "ties":
                        it["kind"] = e["schema"]
                        it.update(_tie(rec, e["name"], names, by_id))
                it["count"] += e["count"]
                it["pages"].append(p)
                it["quote"] = it["quote"] or e["quote"]
                it["cited"] = it["cited"] or (p, e["schema"], e["name"].lower()) in cited
        if items:
            out[gkey] = sorted(items.values(), key=lambda e: (-e["count"], e["name"].lower()))
            counts[gkey] = len(items)
    if "places" in wanted:
        pages_of = {}
        for r in rows:
            if (r.get("schema") or "") in PLACE_SCHEMAS:
                pages_of.setdefault(r["thing"].lower(), set()).add(r["page"])
        pl = _places(files, pages, rows, {k: sorted(v) for k, v in pages_of.items()})
        if pl:
            out["places"] = pl
            counts["places"] = len(pages_of)
    if "events" in wanted:
        roster, evs = _roster(files), {}
        for p in pages:
            for e in _events(files, p, by_page.get(p, []), roster):
                it = evs.get(e["title"].lower())
                if it is None:
                    it = evs[e["title"].lower()] = {**e, "count": 0, "pages": [], "cited": False}
                    for sch in ("Event", "Payment"):
                        rec = _saved_for(saved, sch, e["title"], p)
                        if rec:
                            it["id"] = rec["id"]
                            if rec["id"] in hids:
                                it["h"] = hids[rec["id"]]
                            break
                else:
                    for fld in ("summary", "date", "quote"):
                        if e.get(fld) and not it.get(fld):
                            it[fld] = e[fld]
                    more = [w for w in e.get("who") or [] if w["name"] not in {x["name"] for x in it.get("who") or []}]
                    if more:
                        it["who"] = ((it.get("who") or []) + more)[:5]
                it["count"] += 1
                it["pages"].append(p)
                it["cited"] = it["cited"] or any((p, sch, e["title"].lower()) in cited for sch in ("Event", "Payment"))
        if evs:
            out["events"] = sorted(evs.values(), key=lambda e: (e["pages"][0], e["title"].lower()))
            counts["events"] = len(evs)
    out["counts"] = counts
    quotes, seen = [], set()
    for r in sorted(rows, key=lambda r: r["page"]):
        q = (r.get("quote") or "").strip()
        if q and q not in seen:
            seen.add(q)
            quotes.append({"q": q[:400], "what": r.get("thing") or "", "page": r["page"]})
    if quotes:
        out["quotes"] = quotes[:RUN_QUOTES]
    evi = _evidence(rows, _mention_countries(files, pages))
    if evi:
        out["evidence"] = evi
    while len(_RUN_MEMO) >= RUN_MEMO_MAX:
        _RUN_MEMO.pop(next(iter(_RUN_MEMO)))
    _RUN_MEMO[key] = out
    return out


def export_records(files, archive=None, group: str | None = None, page: int | None = None) -> list[dict]:
    """The run's results as saved records, one per (page, schema, thing) found, in page then group order: {id, schema, group, name,
    properties, page, quote, source, state (cited or proposed), cited, saved, h?, from?, to?}. `id`, `properties` and `source` come from the
    archive's saved record of that name (null, {} and [] when it was found but never saved); `h` is the archive's H-ID when its build has one."""
    archive = archive or files.archive()
    saved, hids, names = _saved(archive, _minted(files)), _hids(archive), archive_names(archive)
    by_id = {r["id"]: r for r in archive_records(archive)}
    order = {k: i for i, k in enumerate(GROUPS)}
    out = []
    rows = sorted(_page_rows(named(files.found(), archive), page), key=lambda r: (r["page"], order.get(type_group(r.get("schema") or ""), 99), r["thing"].lower()))
    for r in rows:
        schema = r.get("schema") or ""
        g = type_group(schema)
        if group and g != group:
            continue
        rec = _saved_for(saved, schema, r["thing"], r["page"])
        props = (rec or {}).get("properties") or {}
        item = {"id": rec["id"] if rec else None, "schema": schema, "group": g, "name": r["thing"], "properties": props, "page": r["page"],
                "quote": r.get("quote") or "", "source": list(props.get("sources") or []), "state": r.get("state") or "proposed",
                "cited": r.get("state") == "cited", "saved": rec is not None}
        if rec and rec["id"] in hids:
            item["h"] = hids[rec["id"]]
        if g == "ties":
            item.update({k: v for k, v in _tie(rec, r["thing"], names, by_id).items() if k in ("from", "to")})
        out.append(item)
    return out

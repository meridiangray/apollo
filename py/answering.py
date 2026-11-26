"""answering.py: the Questions tab's one-click answers, through hydra's own answer path.

    run_collections(rec)                 the collections a run works on (its prompt names them; else the ones it added)
    build_rows(archive, collections, names)
                                         the build's open merges, references and placements of those collections, and the ones
                                         answered here, as Questions rows (`group` "build"), with the buttons hydra offers
    answer(archive, qid, choice)         one click: hydra's Review-page answer (hydra.wiki.pages.reviewqueue.answer), which
                                         holds the archive's write lock and takes one history step; `clear` takes it back

An agent's question (`ask-`) is answered by hydra's `answer_question` operation: choice `<n>` is its option n, `dismiss`
dismisses it, `clear` opens it again. A build question is `hydra-ctl answer`'s write: merge and placement `confirm` or
`reject`; a reference its candidate's number or `none`. Nothing here writes a file of its own."""
from __future__ import annotations

import re
from pathlib import Path

CHOICES = ("confirm", "reject", "none", "dismiss", "clear")
_ASK = "ask-"
_BUILD = ("merge-", "place-", "ref-")
_MEMO: dict = {}                      # {archive: (stamp, rows)}: the build's rows are read again only when the build changed
_WORDS = {"merge-": {"confirm": "Same", "reject": "Different"}, "place-": {"confirm": "Placed there", "reject": "Not there"}}


def run_collections(rec: dict) -> set[str]:
    """The collections a run works on: those its prompt names ("Collections (the run's own...): a, b"), else the ones the
    archive's tracking says it added."""
    try:
        text = (Path(rec["run_folder"]) / "prompt.md").read_text(encoding="utf-8")
    except (OSError, KeyError, TypeError):
        text = ""
    m = re.search(r"^Collections \(the run's own[^)]*\):\s*(.+)$", text, re.M)
    names = {n.strip() for n in m.group(1).split(",") if n.strip()} if m else set()
    if not names:
        try:
            import archives
            for t in archives._read_tracking():
                if t.get("run") == rec.get("id"):
                    names |= set(t.get("collections") or [])
        except Exception:                                          # noqa: BLE001 - tracking is a help
            pass
    return names


def _stamp(root: Path) -> tuple:
    files = [root / ".hydra" / "parsed" / "build.yaml", root / ".hydra" / "review" / "verdicts.yaml"]
    files += sorted((root / "collections").glob("*.json"))
    out = []
    for f in files:
        try:
            s = f.stat()
            out.append((f.name, s.st_mtime_ns, s.st_size))
        except OSError:
            out.append((f.name, 0, 0))
    return tuple(out)


def _local(rid) -> str:
    return str(rid or "").split(":", 1)[-1]


def _holders(root: Path) -> dict[str, str]:
    """{record id: the collection that holds it}."""
    from hydra.archive import collections_io as cio
    own = {}
    for n in cio.names(root):
        for r in cio.read(cio.path(root, n))[0] or []:
            if isinstance(r, dict) and r.get("id"):
                own[str(r["id"])] = n
    return own


def _said(key: str, v, cands: dict) -> str:
    if key.startswith("ref-"):
        return "None of these" if v == "none" else cands.get(str(v)) or str(v)
    from hydra.review import verdicts as vd
    st = {"confirmed": "confirm", "rejected": "reject"}.get(vd.status(v), str(vd.status(v)))
    return _WORDS[key.split("-")[0] + "-"].get(st, st)


def _card(kind: str, row: dict, names: dict) -> dict:
    """One build row as a Questions row: its text, the records it is about, and the buttons hydra offers."""
    nm = lambda rid: names.get(str(rid)) or names.get(_local(rid)) or str(rid)
    if kind == "merge":
        a, b = row.get("acap") or nm(row["a"]), row.get("bcap") or nm(row["b"])
        return {"title": f"{a} ↔ {b}", "question": f"Are “{a}” and “{b}” the same entity?", "records": [row["a"], row["b"]],
                "labels": {row["a"]: a, row["b"]: b}, "suggestion": row.get("criteria") or None,
                "choices": [{"choice": "confirm", "label": "Same"}, {"choice": "reject", "label": "Different"}]}
    if kind == "placement":
        a, b = row.get("subcap") or nm(row["sub"]), row.get("supcap") or nm(row["sup"])
        return {"title": f"{a} → {b}", "question": f"Is “{a}” under “{b}”?", "records": [row["sub"], row["sup"]],
                "labels": {row["sub"]: a, row["sup"]: b}, "suggestion": row.get("criteria") or None,
                "choices": [{"choice": "confirm", "label": "Placed there"}, {"choice": "reject", "label": "Not there"}]}
    value = str(row.get("value") or "")
    shown = nm(value) if value else value
    where = nm(row.get("record"))
    cands = [{"choice": str(i), "label": c.get("caption") or nm(c["id"]), "id": c["id"]}
             for i, c in enumerate(row.get("candidates") or [], 1)]
    return {"title": shown, "question": f"Which record does “{shown}” name, in {where}?",
            "records": [row["record"]] if row.get("record") else [], "labels": {row["record"]: where} if row.get("record") else {},
            "suggestion": None, "choices": [*cands, {"choice": "none", "label": "None of these", "none": True}]}


def _build_passage(root: Path, key: str, kind: str, raw: dict, c: dict) -> dict | None:
    """What hydra kept for a build question: the words its answer rests on (review-evidence), else the kept words of the
    first record it is about that has some."""
    kept = _kept(root)
    given = kept["evidence"].get(key) or {}
    labels = [v for v in c["labels"].values() if v]
    if kind == "reference" and raw.get("value"):
        labels.insert(0, str(raw["value"]))
    if given.get("quote"):
        return _passage(given["quote"], labels, None)
    for r in c["records"]:
        p = _of_record(kept, r, labels[:1] if kind == "reference" else ())
        if p:
            return p
    return None


def _row(key: str, kind: str, raw: dict, names: dict, status: str = "open", answer: dict | None = None, root: Path | None = None) -> dict:
    c = _card(kind, raw, names)
    return {"id": key, "title": c["title"], "status": status, "kind": kind, "page": None, "records": c["records"],
            "labels": c["labels"], "question": c["question"], "quote": "", "options": [x["label"] for x in c["choices"]],
            "choices": c["choices"], "suggestion": None, "answer": answer,
            "asked": None, "group": "build", "passage": _build_passage(root, key, kind, raw, c) if root else None}


def _answered_by(root: Path) -> dict[str, dict]:
    """{question id: {by, at}} for what was answered on the Review page: the history steps "wiki review: answer <key> <choice>"
    (newest first, so the latest answer of a key wins)."""
    from hydra.archive.history import store
    out = {}
    try:
        steps = store.log(root, limit=400)
    except Exception:                                              # noqa: BLE001 - a damaged history only loses the "by"
        return out
    for s in steps:
        m = re.match(r"wiki review: answer (\S+) (.+)$", str(s.get("message") or ""))
        if m and m.group(1) not in out:
            out[m.group(1)] = {"choice": m.group(2), "by": None if s.get("author") in (None, "unknown") else you(s["author"]), "at": s.get("time")}
    return out


def build_rows(archive, collections, names: dict | None = None) -> list[dict]:
    """The build's open merges, references and placements of `collections`, and those answered on the Review page, as
    Questions rows (open ones first, each kind in the build's order). Not built, or no collections: []."""
    if not archive or not collections:
        return []
    root = Path(str(archive))
    key = (str(root), tuple(sorted(collections)))
    stamp = _stamp(root)
    hit = _MEMO.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        rows = _read_rows(root, set(collections), names or {})
    except (SystemExit, Exception):                                # noqa: BLE001 - mid-rebuild: keep what was last seen
        return hit[1] if hit else []
    _MEMO[key] = (stamp, rows)
    return rows


def _read_rows(root: Path, collections: set[str], names: dict) -> list[dict]:
    from hydra.archive import resources
    from hydra.review import todo, verdicts as vd
    got = todo.collect(root)
    own = _holders(root)
    mine = lambda *ids: any(own.get(_local(i)) in collections for i in ids if i)
    open_rows = []
    for r in got["merges"]:
        if mine(r["a"], r["b"]):
            open_rows.append(_row(r["id"], "merge", r, names, root=root))
    for r in got["references"]:
        if mine(r.get("record")):
            open_rows.append(_row(r["id"], "reference", r, names, root=root))
    for r in got["placements"]:
        if mine(r["sub"], r["sup"]):
            open_rows.append(_row(r["id"], "placement", r, names, root=root))
    given, _ = vd.load(root)
    review = resources.review_rows(root)
    who = None
    done = []
    for key, v in sorted(given.items()):
        if not key.startswith(_BUILD) or key not in review or key in {x["id"] for x in open_rows}:
            continue
        raw = review[key]
        kind = {"merge-": "merge", "place-": "placement", "ref-": "reference"}[key[:key.index("-") + 1]]
        if not mine(*(raw.get(k) for k in ("a", "b", "sub", "sup", "record"))):
            continue
        who = who if who is not None else _answered_by(root)
        if key not in who:                                         # not answered on the Review page: an agent's or an old pin
            continue
        cands = {str(c["id"]): c.get("caption") or names.get(_local(c["id"])) or str(c["id"]) for c in raw.get("candidates") or []}
        done.append(_row(key, kind, raw, names, "answered", {"choice": _said(key, v, cands), "text": None,
                                                              "by": who[key]["by"], "at": who[key]["at"]}, root))
    return open_rows + done


def answer(archive, qid: str, choice: str) -> dict:
    """One click, through hydra: {ok, id, said} or {ok: False, why}. `choice`: an option's number, confirm, reject, none,
    dismiss, or clear (take the answer back)."""
    qid, choice = str(qid or ""), str(choice or "")
    if not archive or not (Path(str(archive)) / "hydra.yaml").is_file():
        return {"ok": False, "why": "this run has no archive to answer in"}
    if not qid.startswith((_ASK, *_BUILD)):
        return {"ok": False, "why": f"{qid or 'a question'} is not one this tab answers"}
    if choice not in CHOICES and not choice.isdigit():
        return {"ok": False, "why": "a choice is an option's number, confirm, reject, none, dismiss or clear"}
    try:
        from hydra.wiki.pages import reviewqueue
        said = reviewqueue.answer(Path(str(archive)), qid, choice)
    except SystemExit as e:                                        # hydra's refusals in the CLI's words
        return {"ok": False, "why": re.sub(r"^hydra:\s*", "", str(e.code))}
    except Exception as e:                                         # noqa: BLE001 - a busy lock, a refused answer: said as hydra says it
        return {"ok": False, "why": re.sub(r"^hydra:\s*", "", str(e)) or e.__class__.__name__}
    _MEMO.clear()
    return {"ok": True, "id": qid, "said": said}


# ---- the passage under a question: the kept words that name the entity, with their page ---------------------------------
_PASSAGE = 320
_NONE = re.compile(r"^\s*none of these\.?\s*$", re.I)


def is_none(label) -> bool:
    return bool(_NONE.match(str(label or "")))


def you(by):
    """Who answered, as the tab says it: the person is "you"; an agent keeps its role."""
    return "you" if str(by or "").strip().lower() in ("person", "you") else by


def _kept(root: Path) -> dict:
    """{records: {id: properties}, cites: {id: [{pages, pdfPages, quote}]}, evidence: {question key: {quote, source}}}, read
    again only when the archive's files changed."""
    key = ("kept", str(root))
    extra = [root / ".hydra" / "sources" / "citations.jsonl", root / ".hydra" / "review-evidence.yaml"]
    stamp = _stamp(root) + tuple((f.name, f.stat().st_mtime_ns) if f.exists() else (f.name, 0) for f in extra)
    hit = _MEMO.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    from hydra.archive import collections_io as cio
    from hydra.sources import citations
    import yaml
    recs = {}
    for n in cio.names(root):
        for r in cio.read(cio.path(root, n))[0] or []:
            if isinstance(r, dict) and r.get("id"):
                recs[_local(r["id"])] = r.get("properties") or {}
    cites: dict = {}
    for (rec, _src), d in citations.load(root).items():
        cites.setdefault(_local(rec), []).append(d)
    try:
        evidence = yaml.safe_load(extra[1].read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        evidence = {}
    got = {"records": recs, "cites": cites, "evidence": evidence if isinstance(evidence, dict) else {}}
    _MEMO[key] = (stamp, got)
    return got


def _names(props: dict) -> list[str]:
    return [str(x) for k in ("name", "alias") for x in props.get(k) or [] if str(x).strip()]


def _found(text: str, names: list[str]) -> tuple[int, str] | None:
    """Where the longest of `names` first stands in `text` (case aside): (index, as written there)."""
    low = text.lower()
    for n in sorted({n for n in names if n}, key=len, reverse=True):
        i = low.find(n.lower())
        if i >= 0:
            return i, text[i:i + len(n)]
    return None


_ABBR = {"mr", "mrs", "ms", "dr", "st", "co", "jr", "sr", "messrs", "gen", "col", "capt", "gov", "hon", "rev", "no", "vs", "inc", "ltd", "mt"}


def _sentences(text: str) -> list[str]:
    """Split at a full stop and a capital, but not after a title (Mr.), a company's Co., or an initial (J. D. Rockefeller)."""
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z“\"'])", text):
        word = re.search(r"(\w+)[.!?]$", text[start:m.start()])
        if word and (word.group(1).lower() in _ABBR or (len(word.group(1)) == 1 and word.group(1).isupper())):
            continue
        out.append(text[start:m.start()])
        start = m.end()
    return out + [text[start:]]


def _short(text: str, names: list[str]) -> tuple[str, str | None]:
    """The part of a kept quote that names the entity: its sentence (and the next, if that one is short), at most ~320
    characters; with the name as written there, or None."""
    text = " ".join(str(text or "").split())
    parts = [p.strip() for p in text.split(" / ") if p.strip()]
    if len(parts) > 1:
        text = next((p for p in parts if _found(p, names)), parts[0])
    sents = _sentences(text)
    if len(sents) > 1:
        i = next((j for j, s in enumerate(sents) if _found(s, names)), 0)
        text = sents[i] + (" " + sents[i + 1] if len(sents[i]) < 80 and i + 1 < len(sents) else "")
    if len(text) > _PASSAGE:
        hit = _found(text, names)
        lo = max(0, min((hit[0] if hit else 0) - 100, len(text) - _PASSAGE))
        end = lo + _PASSAGE
        cut = text[lo:end]
        if lo:
            cut = cut[cut.find(" ") + 1:]
        if end < len(text):
            cut = cut[:cut.rfind(" ")]
        text = ("…" if lo else "") + cut + ("…" if end < len(text) else "")
    hit = _found(text, names)
    return text, hit[1] if hit else None


def _of_record(kept: dict, rid, extra_names=()) -> dict | None:
    """A record's kept words: its citation's quote (with the page), else its notes."""
    props = kept["records"].get(_local(rid))
    if props is None:
        return None
    names = [*extra_names, *_names(props)]
    for c in kept["cites"].get(_local(rid), []):
        if str(c.get("quote") or "").strip():
            return _passage(c["quote"], names, c.get("pages") or c.get("pdfPages"))
    for n in props.get("notes") or []:
        if str(n).strip():
            return _passage(n, names, None)
    return None


def _passage(text, names, page) -> dict | None:
    from hydra.sources import citations
    text = citations.shown_quote(text)
    if not text:
        return None
    short, name = _short(text, [n for n in names if n])
    return {"text": short, "mark": name, "page": str(page).strip() if page else None}


def attach(archive, qs: list[dict]) -> list[dict]:
    """The agent's questions, each with its `passage` ({text, mark, page}) when hydra kept words for it: the quote the
    question was raised on, else the kept words (citation, then notes) of the records it is about."""
    if not archive or not qs:
        return qs
    root = Path(str(archive))
    kept = _kept(root)
    out = []
    for q in qs:
        recs = [str(r) for r in q.get("records") or []]
        names = [n for r in recs for n in _names(kept["records"].get(_local(r)) or {})]
        p = _passage(q["quote"], names, q.get("page")) if q.get("quote") else None
        for r in recs:
            if p:
                break
            p = _of_record(kept, r)
        out.append({**q, "passage": p})
    return out

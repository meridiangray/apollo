"""archives.py: Apollo's own archives: the Draft every session starts with, and the collections of any archive it knows.

    draft_path()                      apollo/archives/draft (APOLLO_ARCHIVES overrides the folder, for tests)
    ensure_draft()                    make the empty Draft archive when it is missing, with hydra's own `hydra-ctl init`
    reset_draft()                     delete the Draft and make it again, empty
    collections(archive)              the archive's collections: name, records, counts by type, the sources cited, the run
                                      that added it and when (read through hydra's own readers, read only)
    records(archive, name, ...)       one collection's records, a page at a time
    track(run, archive, names)        remember which run added which collections (state/collections.json)
    untrack(run)                      forget a run (a cleared run)

hydra is only read here, never edited. Anything that runs hydra gets HYDRA_CONFIG_DIR in Apollo's own state folder, so the
owner's hydra settings are never touched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRAFT_NAME = "Draft"
DRAFT_PREFIX = "draft"
PAGE = 200                                                   # records a collection shows before "Show more"
_LOCK = threading.Lock()


def folder() -> Path:
    return Path(os.environ.get("APOLLO_ARCHIVES") or HERE / "archives")


def draft_path() -> Path:
    return folder() / "draft"


def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def is_archive(p) -> bool:
    return (Path(p) / "hydra.yaml").is_file()


def is_draft(p) -> bool:
    try:
        return Path(p).resolve() == draft_path().resolve()
    except OSError:
        return False


# ---- the Draft ------------------------------------------------------------------------------------------------------

def ensure_draft() -> dict:
    """The Draft archive, made empty if it is missing: {path, created}. Raises RuntimeError with hydra's own words when
    hydra cannot make it."""
    import tools
    d = draft_path()
    with _LOCK:
        if is_archive(d):
            return {"path": str(d), "created": False}
        d.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(tools._hydra_ctl() + ["init", str(d), "--name", DRAFT_NAME, "--prefix", DRAFT_PREFIX],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                           env=tools._hydra_env(), stdin=subprocess.DEVNULL)
        if p.returncode != 0 and not is_archive(d):
            raise RuntimeError(((p.stderr or p.stdout).strip().splitlines() or [f"hydra-ctl init exited {p.returncode}"])[-1][:300])
        return {"path": str(d), "created": True}


def ensure_draft_later() -> None:
    """ensure_draft off the caller's path: the window never waits for it, and a failure is only retried next time."""
    if is_archive(draft_path()) or os.environ.get("APOLLO_NO_DRAFT"):
        return

    def go():
        try:
            ensure_draft()
        except Exception:                                    # noqa: BLE001 - the Draft is made lazily; the next call tries again
            pass
    threading.Thread(target=go, daemon=True).start()


def reset_draft() -> dict:
    """Delete the Draft archive and make it again, empty; forget which runs added to it."""
    d = draft_path()
    if d.exists():
        if d.name != "draft" or d.parent.resolve() != folder().resolve():
            raise ValueError("not the Draft")
        shutil.rmtree(d)
    forget_archive(str(d))
    return ensure_draft()


def remove_collections(archive: str, names: list[str]) -> list[str]:
    """Take collections a cleared run added out of a shared archive: their record files, their source-entry files, their
    entries in hydra.yaml and their place in the collection groups. Through hydra's own manifest writers."""
    from hydra.archive import collections_io, manifest
    arch = Path(archive)
    gone = []
    for n in names:
        f = collections_io.path(arch, n)
        if f.is_file():
            f.unlink()
            gone.append(n)
        (arch / ".hydra" / "sources" / "entries" / f"{n}.json").unlink(missing_ok=True)
        entry = (manifest.read(arch).get("collections") or {}).get(n)
        if isinstance(entry, dict):
            manifest.set_collection(arch, n, **{k: None for k in entry})
    groups = manifest.read(arch).get("collection_groups")
    if isinstance(groups, dict) and groups:
        kept = {}
        for g, spec in groups.items():
            members = [c for c in (spec.get("collections") or []) if c not in names] if isinstance(spec, dict) else []
            if members:
                kept[g] = {**spec, "collections": members}
        if kept != groups:
            if kept:
                manifest.set_top(manifest.path(arch), "collection_groups", kept)
            else:
                manifest.drop_top(manifest.path(arch), "collection_groups")
    return gone


# ---- which run added which collection -------------------------------------------------------------------------------

def _tracking_file() -> Path:
    return state_dir() / "collections.json"


def _read_tracking() -> list[dict]:
    try:
        got = json.loads(_tracking_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = got.get("entries") if isinstance(got, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _write_tracking(rows: list[dict]) -> None:
    f = _tracking_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"entries": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def track(run: str, archive: str, names: list[str]) -> None:
    """Write down that `run` added `names` to `archive` (one row per run and archive, replaced if it is written again)."""
    rows = [r for r in _read_tracking() if not (r.get("run") == run and r.get("archive") == str(archive))]
    rows.append({"run": run, "archive": str(archive), "collections": sorted(names),
                 "at": datetime.now().isoformat(timespec="seconds")})
    _write_tracking(rows)


def track_import(origin: str, archive: str, names: list[str]) -> None:
    """Write down that `names` came into `archive` from another archive (`origin`), not from a run: the Collections view says
    "imported from <origin>"."""
    rows = [r for r in _read_tracking() if not (r.get("imported") == origin and r.get("archive") == str(archive))]
    rows.append({"run": None, "imported": origin, "archive": str(archive), "collections": sorted(names),
                 "at": datetime.now().isoformat(timespec="seconds")})
    _write_tracking(rows)


def untrack(run: str) -> None:
    rows = _read_tracking()
    keep = [r for r in rows if r.get("run") != run]
    if len(keep) != len(rows):
        _write_tracking(keep)


def forget_archive(archive: str) -> None:
    rows = _read_tracking()
    keep = [r for r in rows if r.get("archive") != str(archive)]
    if len(keep) != len(rows):
        _write_tracking(keep)


def tracked(archive: str) -> dict:
    """{collection: {run, imported, at}} for an archive; the newest row wins."""
    out = {}
    for r in sorted(_read_tracking(), key=lambda r: r.get("at") or ""):
        if r.get("archive") == str(archive):
            for n in r.get("collections") or []:
                out[n] = {"run": r.get("run"), "imported": r.get("imported"), "at": r.get("at")}
    return out


# ---- reading an archive's collections (hydra's own readers) ---------------------------------------------------------

def _first(props: dict, *keys):
    for k in keys:
        v = props.get(k)
        if isinstance(v, list) and v:
            return str(v[0])
        if isinstance(v, str) and v:
            return v
    return None


def source_titles(arch: Path) -> dict:
    """{source id: title} from the archive's source entries (.hydra/sources/entries/*.json)."""
    from hydra.archive import collections_io
    from hydra.sources import sourcetitle
    out = {}
    d = arch / ".hydra" / "sources" / "entries"
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            rows = collections_io.read(f)[0] or []
        except (OSError, ValueError):
            continue
        for r in rows:
            if isinstance(r, dict) and r.get("id"):
                t = _first(r.get("properties") or {}, "title", "name", "fileName")
                out[r["id"]] = sourcetitle.scrub(t) if t else r["id"]
    return out


_SCHEMA_LABELS = {"PublicBody": "Public body", "UnknownLink": "Link"}


def schema_label(schema: str) -> str:
    """A type's display name: "GeoLink" is "Geo Link" (the same words the UI's type chips use)."""
    return _SCHEMA_LABELS.get(schema) or re.sub(r"([a-z])([A-Z])", r" ", str(schema or ""))


_SUMMARY: dict = {}


def _summary(arch: Path, name: str, titles: dict) -> dict:
    from hydra.archive import collections_io
    f = collections_io.path(arch, name)
    st = f.stat()
    key = (str(f), st.st_mtime_ns, st.st_size, len(titles))
    if key in _SUMMARY:
        return _SUMMARY[key]
    recs = [r for r in (collections_io.read(f)[0] or []) if isinstance(r, dict)]
    types: dict = {}
    sources: dict = {}
    for r in recs:
        types[r.get("schema") or "Other"] = types.get(r.get("schema") or "Other", 0) + 1
        for sid in (r.get("properties") or {}).get("sources") or []:
            sources[titles.get(sid, sid)] = 1
    out = {"count": len(recs), "types": [{"schema": k, "label": schema_label(k), "n": n} for k, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))],
           "sources": sorted(sources), "mtime": st.st_mtime}
    for k in [k for k in _SUMMARY if k[0] == key[0]]:
        _SUMMARY.pop(k)
    _SUMMARY[key] = out
    return out


def collections(archive: str) -> dict:
    """{name, archive, collections: [{name, title, count, types, sources, run, added}]} of one archive. `run` is the run
    id that added it (from state/collections.json, else the run folder name an archive of a run sits in)."""
    from hydra.archive import collections_io, manifest
    arch = Path(archive)
    meta = manifest.read(arch)
    cm = meta.get("collections") if isinstance(meta.get("collections"), dict) else {}
    titles = source_titles(arch)
    tr = tracked(str(arch))
    own = arch.parent.name if arch.parent.parent.name == "archives" and not is_draft(arch) else None   # archives/<run>/<prefix>
    rows = []
    for name in collections_io.names(arch):
        s = _summary(arch, name, titles)
        entry = cm.get(name) if isinstance(cm.get(name), dict) else {}
        t = tr.get(name) or {}
        added = t.get("at") or datetime.fromtimestamp(s["mtime"]).isoformat(timespec="seconds")
        rows.append({"name": name, "title": entry.get("title") or name, "count": s["count"], "types": s["types"],
                     "sources": s["sources"], "run": None if t.get("imported") else t.get("run") or own,
                     "imported": t.get("imported"), "added": added})
    rows.sort(key=lambda r: (r["added"], r["name"]))
    return {"ok": True, "archive": str(arch), "name": meta.get("name") or arch.name, "draft": is_draft(arch), "collections": rows}


def records(archive: str, name: str, offset: int = 0, limit: int = PAGE) -> dict:
    """One collection's records from `offset`: {total, records: [{id, schema, name, source, quote}]}."""
    from hydra.archive import collections_io
    arch = Path(archive)
    if name not in collections_io.names(arch):
        return {"ok": False, "why": f"no collection {name!r}"}
    titles = source_titles(arch)
    recs = [r for r in (collections_io.read(collections_io.path(arch, name))[0] or []) if isinstance(r, dict)]
    import runview
    names = runview.archive_names(arch)                      # a link or tie is named by its two ends: "event → place"
    out = []
    for r in recs[max(0, offset):max(0, offset) + max(1, min(limit, 1000))]:
        props = r.get("properties") or {}
        sid = _first(props, "sources")
        quote = _first(props, "notes")
        out.append({"id": r.get("id"), "schema": r.get("schema"), "type": schema_label(r.get("schema")),
                    "name": _first(props, "name", "title", "summary", "label") or names.get(r.get("id")) or _first(props, "role") or r.get("id"),
                    "source": titles.get(sid, sid) if sid else None, "quote": quote})
    return {"ok": True, "name": name, "total": len(recs), "offset": max(0, offset), "records": out}

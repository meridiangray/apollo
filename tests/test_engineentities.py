"""engineentities: an engine run's decisions as the entities they touched; Sources' Found merged over its runs."""
import json

import engineentities
import runfiles
import runview


def make_run(tmp_path, decisions, name="run1"):
    archive = tmp_path / "archive"
    (archive / "collections").mkdir(parents=True, exist_ok=True)
    recs = [{"id": "org-a", "schema": "Company", "properties": {"name": ["Lake Shore Railroad"]}},
            {"id": "per-bbbbbbbbbb", "schema": "Person", "properties": {"name": ["Amasa Stone"]}},
            {"id": "pla-c", "schema": "Place", "properties": {"name": ["Cleveland"], "country": ["us"]}},
            {"id": "evt-d", "schema": "Event", "properties": {"name": ["Meeting"]}}]
    (archive / "collections" / "c.json").write_text(json.dumps(recs), encoding="utf-8")
    run = tmp_path / name
    (run / "refine").mkdir(parents=True)
    (run / "pageflow").mkdir()
    (run / "refine" / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions), encoding="utf-8")
    (run / "refine" / "gather.json").write_text(json.dumps({"records": [{"id": "org-gone", "schema": "Company"}]}), encoding="utf-8")
    (run / "refine" / "applied.json").write_text(json.dumps({"wrote": {"c": {"saved": True}}}), encoding="utf-8")
    return runfiles.RunFiles(run, "engine", str(archive))


DECISIONS = [
    {"status": "ok", "kind": "merge", "keep": "org-a", "absorb": "org-gone", "page": 3, "quote": "q1", "thing": "Lake Shore road into Lake Shore Railroad"},
    {"status": "ok", "kind": "date", "record": "evt-d", "field": "date", "value": "1872-01-02", "page": 4, "quote": "q2"},
    {"status": "ok", "kind": "bind", "place": "pla-c", "choice": "geonames:1", "page": 5, "quote": "q3", "thing": "Cleveland is Cleveland (geonames:1)"},
    {"status": "refused", "kind": "prop", "record": "per-bbbbbbbbbb", "field": "title", "value": "Mr.", "page": 6},
    {"status": "ok", "kind": "ask", "records": ["per-bbbbbbbbbb"], "question": "Is per-bbbbbbbbbb (per-bbbbbbbbbb) Stone?", "page": None, "quote": ""},
]


def test_rows_are_entities_with_what_the_run_did(tmp_path):
    rows = engineentities.rows(make_run(tmp_path, DECISIONS))
    by = {(r["thing"], r["did"]["verb"]): r for r in rows}
    assert by[("Lake Shore Railroad", "Merged with")]["did"]["other"]["name"] == "Lake Shore road"
    assert by[("Lake Shore road", "Merged into")]["schema"] == "Company"          # a record the merge removed: schema from the run's gather.json, name from the decision
    assert by[("Meeting", "Dated")]["did"]["text"] == "January 2, 1872"
    assert by[("Cleveland", "Bound to")]["did"]["text"] == "Cleveland (geonames:1)"
    assert by[("Amasa Stone", "Asked")]["did"]["text"] == "Is Amasa Stone Stone?"   # ids said by name, a refused decision is no row
    assert not any(r["did"]["kind"] == "prop" for r in rows)


def test_found_entities_keep_each_decision_and_count_kinds(tmp_path):
    out = runview.found_entities(engineentities.rows(make_run(tmp_path, DECISIONS)))
    assert {t["key"] for t in out["types"]} == {"bodies", "events", "places", "people"}
    assert {d["key"] for d in out["dids"]} == {"merge", "date", "bind", "ask"}
    lake = next(e for e in out["entities"] if e["thing"] == "Lake Shore Railroad")
    assert lake["did"][0]["kind"] == "merge" and lake["recs"][0]["did"]["verb"] == "Merged with" and lake["cited"]


def test_a_source_merges_runs_each_entity_once(tmp_path):
    a = engineentities.rows(make_run(tmp_path, DECISIONS[:1], "r1"))
    b = engineentities.rows(make_run(tmp_path, DECISIONS[:2], "r2"))
    rows = [{**r, "run": "r1", "did": {**r["did"], "flow": "Refine"}} for r in a] + [{**r, "run": "r2", "did": {**r["did"], "flow": "Timeline"}} for r in b]
    out = runview.found_entities(rows)
    lake = [e for e in out["entities"] if e["thing"] == "Lake Shore Railroad"]
    assert len(lake) == 1 and [d["flow"] for d in lake[0]["did"]] == ["Refine", "Timeline"]
    assert out["entity_count"] == 3


def test_long_dates():
    assert engineentities.long_date("1872") == "1872" and engineentities.long_date("1872-01") == "January 1872"
    assert engineentities.long_date("1872-13-40") == "1872-13-40"

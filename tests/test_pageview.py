"""The page strip and the page box: what a run's files say about its pages, on a small fixture run in a scratch folder."""
import json
from pathlib import Path

import pytest

import pageview
import runfiles


def put(path: Path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")


@pytest.fixture
def run(tmp_path):
    """Pages 20-23: 20 has people, a body, two places (one with a country) and an event; 21 is redacted a third; 22 is empty;
    23 has one record, no quote."""
    pf = tmp_path / "run" / "pageflow"
    put(pf / "progress.json", {"pages": {"19": "out-of-scope", "20": "done", "21": "escalated", "22": "empty", "23": "done"}})
    rows = [
        {"page": 20, "schema": "Person", "thing": "Ann Lee", "quote": "Ann Lee met the minister in Paris.", "state": "proposed"},
        {"page": 20, "schema": "Person", "thing": "Bo Kim", "quote": "", "state": "proposed"},
        {"page": 20, "schema": "PublicBody", "thing": "Ministry of Works", "quote": "The Ministry of Works agreed.", "state": "proposed"},
        {"page": 20, "schema": "Place", "thing": "Paris", "quote": "Ann Lee met the minister in Paris.", "state": "proposed"},
        {"page": 20, "schema": "Place", "thing": "Middle East", "quote": "", "state": "proposed"},
        {"page": 20, "schema": "Event", "thing": "The talks", "quote": "The talks began.", "state": "proposed"},
        {"page": 20, "schema": "Person", "thing": "Ann Lee", "quote": "Ann Lee met the minister in Paris.", "state": "cited"},     # saved: the same thing again
        {"page": 20, "schema": "Place", "thing": "Paris", "quote": "Ann Lee met the minister in Paris.", "state": "cited"},
        {"page": 20, "schema": "Event", "thing": "The talks", "quote": "The talks began.", "state": "cited"},
        {"page": 21, "schema": "Person", "thing": "Ann Lee", "quote": "Lee again.", "state": "proposed"},
        {"page": 23, "schema": "Organization", "thing": "Acme", "quote": "", "state": "proposed"},
    ]
    put(pf / "found.jsonl", "\n".join(json.dumps(r) for r in rows) + "\n")
    put(pf / "triage.json", [
        {"page": 19, "status": "out-of-scope", "words": 5, "redacted_share": 0.0},
        {"page": 20, "status": "todo", "words": 166, "redacted_share": 0.0},
        {"page": 21, "status": "todo", "words": 90, "redacted_share": 0.3},
        {"page": 22, "status": "empty", "words": 0, "redacted_share": 0.0},
        {"page": 23, "status": "todo", "words": 40, "redacted_share": 0.9}])
    put(pf / "pages" / "20.mentions.json", {"page": 20, "mentions": [
        {"name": "Paris", "kind": "Place", "as_written": ["Paris"], "country": "fr", "place_type": "city"},
        {"name": "Middle East", "kind": "Place", "as_written": ["Middle East"], "country": "", "place_type": "region"}]})
    put(pf / "pages" / "20.facts.json", {"page": 20, "records": [
        {"id": "p20-e1", "schema": "Event", "properties": {"name": ["The talks"], "date": ["1985-03"], "summary": ["The talks started."],
                                                           "involved": ["person-ann-lee", "publicbody-ministry"]}}]})
    put(pf / "state.json", {"profile": {"pages": 165}})                  # a PDF: pages, not parts
    put(tmp_path / "run" / "roster.json", [{"id": "person-ann-lee", "name": "Ann Lee"}, {"id": "publicbody-ministry", "name": "Ministry of Works"}])
    return runfiles.RunFiles(tmp_path / "run", "pageflow", None)


def test_strip_has_a_cell_per_run_page(run):
    s = pageview.strip(run)
    assert [c["page"] for c in s["pages"]] == [20, 21, 22, 23]          # the out-of-scope page is not a cell
    by = {c["page"]: c for c in s["pages"]}
    assert by[20]["records"] == 6 and by[20]["words"] == 166 and "red" not in by[20]
    assert by[21]["red"] == "mostly" and by[23]["red"] == "entire"
    assert s["redaction"] is True
    assert "sections" not in s                                           # no structure sidecar: no band, no placeholder
    assert s["evidence"]["quoted"] == [5, 8] and s["evidence"]["located"] == [1, 2]


def test_page_box_groups_and_gates(run):
    d = pageview.page(run, 20)
    assert d["ok"] and d["prev"] is None and d["next"] == 21 and (d["pos"], d["of"]) == (1, 4)
    assert d["counts"] == {"records": 6, "words": 166, "people": 2, "bodies": 1, "places": 2, "events": 1}
    assert [p["name"] for p in d["people"]] == ["Ann Lee", "Bo Kim"]
    assert d["bodies"][0]["name"] == "Ministry of Works"
    assert d["events"][0]["title"] == "The talks" and d["events"][0]["date"] == "1985-03"
    assert [w["name"] for w in d["events"][0]["who"]] == ["Ann Lee", "Ministry of Works"]
    assert [q["what"] for q in d["quotes"]][0] == "Ann Lee"
    assert "section" not in d and "redaction" not in d
    # a page with nothing of a category has no key for it
    d23 = pageview.page(run, 23)
    assert set(d23) >= {"bodies", "counts"} and "people" not in d23 and "places" not in d23 and "events" not in d23 and "quotes" not in d23
    assert d23["redaction"] == "entire" and d23["next"] is None
    assert pageview.page(run, 22)["counts"] == {"records": 0}
    assert pageview.page(run, 99)["ok"] is False


def test_places_with_a_country_become_cards(run, monkeypatch):
    monkeypatch.setattr(pageview, "countries", lambda isos: {i: ["France", "M0 0L4 0L4 4Z", 40, 44] for i in isos})
    p = pageview.page(run, 20)["places"]
    assert p["countries"][0]["name"] == "France" and p["countries"][0]["places"] == ["Paris"] and p["countries"][0]["count"] == 1
    assert p["loose"] == [{"name": "Middle East", "count": 1}]
    # no outline on the machine: the place is a chip, not a card
    monkeypatch.setattr(pageview, "countries", lambda isos: {})
    p = pageview.page(run, 20)["places"]
    assert "countries" not in p and {x["name"] for x in p["loose"]} == {"Paris", "Middle East"}


def test_a_lone_country_card_is_marked(run, monkeypatch):
    """hydra flagged Paris as set against its source: its card and (without an outline) its chip carry `odd`."""
    state = run.state()
    state["close"] = {"geo_flags": {"flags": [{"code": "geo.outlier", "title": "Paris"}, {"code": "geo.lone", "title": "Middle East"}]}}
    monkeypatch.setattr(run, "state", lambda: state)
    monkeypatch.setattr(pageview, "countries", lambda isos: {i: ["France", "M0 0L4 0L4 4Z", 40, 44] for i in isos})
    p = pageview.page(run, 20)["places"]
    assert p["countries"][0]["odd"] == 1 and p["loose"] == [{"name": "Middle East", "count": 1}]       # a soft flag marks nothing here
    monkeypatch.setattr(pageview, "countries", lambda isos: {})
    chips = {x["name"]: x for x in pageview.page(run, 20)["places"]["loose"]}
    assert chips["Paris"]["odd"] == 1 and "odd" not in chips["Middle East"]


def test_sections_come_from_the_sidecar(run, monkeypatch):
    meta = {20: {"title": "Findings", "label": "1"}, 21: {"title": "Findings", "label": "2"}, 22: {"title": "Findings"}, 23: {"title": "Notes"}}
    monkeypatch.setattr(pageview, "structure_pages", lambda archive: meta)
    s = pageview.strip(run)
    assert s["sections"] == [{"title": "Findings", "a": 20, "b": 22, "i": 0}, {"title": "Notes", "a": 23, "b": 23, "i": 1}]
    assert [c["section"] for c in s["pages"]] == [0, 0, 0, 1]
    d = pageview.page(run, 21)
    assert d["section"] == {"title": "Findings", "i": 0} and (d["pos"], d["of"]) == (2, 3) and d["label"] == "2"


def test_structure_pages_read_the_sidecar_headings(tmp_path, monkeypatch):
    from hydra.api import sources as hs
    assert pageview.structure_pages(None) == {} and pageview.structure_pages(tmp_path) == {}     # no archive, or none with sources
    put(tmp_path / ".hydra" / "sources" / "entries" / "docs.json", [{"id": "doc-1"}])
    text = "# Intro\nbody one\n\f## Details\nbody two\n\f# Findings\nbody three\n"
    at = lambda s: text.index(s)                                                                  # noqa: E731
    spans = [{"kind": "heading", "start": at("Intro"), "end": at("Intro") + 5, "attrs": {"level": 1}},
             {"kind": "heading", "start": at("Details"), "end": at("Details") + 7, "attrs": {"level": 2}},
             {"kind": "heading", "start": at("Findings"), "end": at("Findings") + 8, "attrs": {"level": 1}}]
    ranges = [{"page": 1, "start": 0, "end": at("## Details")}, {"page": 2, "start": at("## Details"), "end": at("# Findings")}, {"page": 3, "start": at("# Findings"), "end": len(text)}]
    monkeypatch.setattr(hs, "structure_of", lambda root, sid: {"spans": spans})
    monkeypatch.setattr(hs, "page_ranges_of", lambda root, sid: ranges)
    monkeypatch.setattr(hs, "text_of", lambda root, sid: text)
    monkeypatch.setattr(hs, "page_labels_of", lambda root, sid: {3: "iii"})
    got = pageview.structure_pages(tmp_path)
    assert [got[p]["title"] for p in (1, 2, 3)] == ["Intro", "Details", "Findings"] and got[3]["label"] == "iii"
    monkeypatch.setattr(hs, "structure_of", lambda root, sid: None)                              # no sidecar: no sections
    assert pageview.structure_pages(tmp_path) == {}


def test_redaction_levels():
    assert pageview.redaction(0) is None and pageview.redaction(0.1) == "partial" and pageview.redaction(0.5) == "mostly"
    assert pageview.redaction(0.9) == "entire" and pageview.redaction(0, "redacted") == "entire"


def test_country_outline_is_drawn_from_hydra_register(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_STATE", str(tmp_path / "state"))
    got = pageview.countries(["fr", "zz"])
    if not got:
        pytest.skip("no adm0 layer on this machine")
    name, d, w, h = got["FR"]
    assert name == "France" and d.startswith("M") and 0 < w <= pageview.SIZE and 0 < h <= pageview.SIZE and "ZZ" not in got
    assert (tmp_path / "state" / "pageview" / "countries.json").is_file()      # kept for next time
    assert pageview.countries(["fr"])["FR"] == got["FR"]


# ---- one record once, the right unit, redaction only where it is real, a country as one card ----------------------------------------

def test_a_thing_found_twice_counts_once_everywhere(run, monkeypatch):
    """found.jsonl holds a thing as proposed and again as cited: every number of the box, the strip and the cards is of one record each."""
    monkeypatch.setattr(pageview, "countries", lambda isos: {i: ["France", "M0 0L4 0L4 4Z", 40, 44] for i in isos})
    s = pageview.strip(run)
    d = pageview.page(run, 20)
    assert {c["page"]: c["records"] for c in s["pages"]}[20] == d["counts"]["records"] == 6          # not 9: three of them were there twice
    assert d["counts"]["people"] == len(d["people"]) == 2 and d["counts"]["events"] == len(d["events"]) == 1
    p = d["places"]
    assert d["counts"]["places"] == sum(c["count"] for c in p["countries"]) + sum(x["count"] for x in p["loose"]) == 2
    assert s["evidence"]["quoted"][1] == len(pageview._page_rows(run.found()))                       # the document's N is the same records
    rows = pageview._page_rows(run.found(), 20)
    assert next(r for r in rows if r["thing"] == "Ann Lee")["state"] == "cited"                       # cited wins


def test_a_country_is_not_listed_as_its_own_city(run, monkeypatch):
    monkeypatch.setattr(pageview, "countries", lambda isos: {i: ["Yemen", "M0 0L4 0L4 4Z", 40, 30] for i in isos})
    put(run.pf / "found.jsonl", "\n".join(json.dumps(r) for r in [
        {"page": 20, "schema": "Place", "thing": "Yernens", "quote": "to the Yernens", "state": "proposed"},
        {"page": 20, "schema": "Place", "thing": "Yemen", "quote": "in Yemen", "state": "proposed"},
        {"page": 20, "schema": "Place", "thing": "Sanaa", "quote": "at Sanaa", "state": "proposed"},
        {"page": 20, "schema": "Place", "thing": "Sanaa", "quote": "at Sanaa", "state": "cited"}]) + "\n")
    put(run.pf / "pages" / "20.mentions.json", {"page": 20, "mentions": [
        {"name": "Yernens", "kind": "Place", "as_written": ["Yernens"], "country": "ye", "place_type": "country"},
        {"name": "Yemen", "kind": "Place", "as_written": ["Yemen"], "country": "ye", "place_type": "country"},
        {"name": "Sanaa", "kind": "Place", "as_written": ["Sanaa"], "country": "ye", "place_type": "city"}]})
    card = pageview.page(run, 20)["places"]["countries"][0]
    assert card["name"] == "Yemen" and card["places"] == ["Sanaa"] and card["count"] == 3         # the cities once; the country is the card


def test_a_text_source_is_read_in_parts(run):
    assert pageview.strip(run)["unit"] == "page" and pageview.page(run, 20)["unit"] == "page"
    put(run.pf / "state.json", {"profile": {"pages": None, "words": 299218}})
    assert pageview.strip(run)["unit"] == "part" and pageview.page(run, 20)["unit"] == "part"


def test_redaction_is_real_for_a_pdf_or_a_large_share(run):
    assert pageview.redaction(0.07, "", False) is None and pageview.redaction(0.027, "", False) is None      # a book: three odd lines
    assert pageview.redaction(0.15, "", False) == "partial" and pageview.redaction(0.9, "", False) == "entire"
    assert pageview.redaction(0.07, "", True) == "partial" and pageview.redaction(0, "", True) is None
    assert pageview.redaction(0.05, "redacted", False) is None
    put(run.pf / "state.json", {"profile": {"pages": None}})                                         # the fixture as a text source
    put(run.pf / "triage.json", [{"page": 20, "status": "todo", "words": 9, "redacted_share": 0.07},
                                 {"page": 21, "status": "todo", "words": 9, "redacted_share": 0.3}])
    s = pageview.strip(run)
    by = {c["page"]: c for c in s["pages"]}
    assert "red" not in by[20] and by[21]["red"] == "mostly" and s["redaction"] is True
    assert "redaction" not in pageview.page(run, 20)


def test_a_country_is_its_mainland_and_near_islands():
    from hydra.geo import register
    store = register.store()
    if not store.entry("iso3166:US"):
        pytest.skip("no adm0 layer on this machine")

    def span(iso):
        rings = pageview._rings(store.shape("iso3166:" + iso, 15000))
        pts = [c for r in rings for c in r]
        return len(rings), min(c[0] for c in pts), max(c[0] for c in pts), min(c[1] for c in pts), max(c[1] for c in pts)
    n, lo, hi, south, north = span("US")
    assert lo > -130 and south > 24 and north < 50                                                   # no Alaska, no Hawaii
    n, lo, hi, south, north = span("FR")
    assert south > 40 and lo > -6 and hi < 11                                                        # Corsica stays; Guiana and Reunion do not
    n, lo, hi, south, north = span("RU")
    assert lo > 25                                                                                    # no Kaliningrad, no far-east strip across the date line


# ---- the whole run, and its export --------------------------------------------------------------------------------------

@pytest.fixture
def tied(run, tmp_path):
    """The run above plus an archive that saved Ann Lee, the Ministry and an Employment tie between them (with a role and a date), a
    tie found on page 21 only, and a published H-ID for Ann Lee."""
    arch = tmp_path / "arch"
    put(arch / "collections" / "c.json", [
        {"id": "person-ann-lee", "schema": "Person", "properties": {"name": ["Ann Lee"], "sources": ["doc-1"]}},
        {"id": "publicbody-ministry", "schema": "PublicBody", "properties": {"name": ["Ministry of Works"]}},
        {"id": "p20-t1", "schema": "Employment", "properties": {"employee": ["person-ann-lee"], "employer": ["publicbody-ministry"],
                                                                  "role": ["Adviser"], "startDate": ["1985"], "sources": ["doc-1"]}}])
    put(arch / "universe" / "canon.jsonl", json.dumps({"h": "H-p0-abc", "anchor": "self/draft:person-ann-lee", "members": ["self/draft:person-ann-lee"]}) + "\n")
    pf = tmp_path / "run" / "pageflow"
    with open(pf / "found.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"page": 20, "schema": "Employment", "thing": "person-ann-lee → publicbody-ministry", "quote": "Lee advised the Ministry.", "state": "cited"}) + "\n")
        f.write(json.dumps({"page": 21, "schema": "Employment", "thing": "person-bo-kim → publicbody-ministry", "quote": "", "state": "proposed"}) + "\n")
    return runfiles.RunFiles(tmp_path / "run", "pageflow", str(arch)), str(arch)


def test_run_aggregates_every_page(run):
    d = pageview.run(run)
    assert d["ok"] and d["scope"] == "run" and d["pages"] == [20, 21, 23]
    ann = next(p for p in d["people"] if p["name"] == "Ann Lee")
    assert ann["pages"] == [20, 21] and ann["count"] >= 2 and ann["cited"] is True and ann["quote"]
    assert d["counts"]["people"] == 2 and d["counts"]["pages"] == 3
    assert {b["name"] for b in d["bodies"]} == {"Ministry of Works", "Acme"}
    assert d["events"][0]["title"] == "The talks" and d["events"][0]["pages"] == [20] and d["events"][0]["date"] == "1985-03"
    assert d["places"]["loose"][0]["pages"] == [20] and d["counts"]["places"] == 2
    assert [q["page"] for q in d["quotes"]] == sorted(q["page"] for q in d["quotes"])


def test_run_filters_by_group_and_page(run):
    d = pageview.run(run, group="people")
    assert "people" in d and "bodies" not in d and "events" not in d and d["group"] == "people"
    one = pageview.run(run, page=21)
    assert one["scope"] == "page" and one["page"] == 21 and one["pages"] == [21]
    assert [p["name"] for p in one["people"]] == ["Ann Lee"] and one["people"][0]["pages"] == [21]
    assert pageview.run(run, group="nonsense")["ok"] is False
    assert pageview.run(run, page=22)["counts"] == {"records": 0, "pages": 0}


def test_run_is_kept_until_the_found_file_changes(run):
    assert pageview.run(run) is pageview.run(run)
    with open(run.pf / "found.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"page": 23, "schema": "Person", "thing": "Cy Dee", "quote": "", "state": "proposed"}) + "\n")
    run._found = runfiles.Tail(run.pf / "found.jsonl")
    assert "Cy Dee" in {p["name"] for p in pageview.run(run)["people"]}


def test_run_ties_carry_both_ends_kind_and_dates(tied):
    files, arch = tied
    d = pageview.run(files, arch, group="ties")
    t = next(x for x in d["ties"] if x["from"]["name"] == "Ann Lee")
    assert t["name"] == "Ann Lee → Ministry of Works" and t["kind"] == "Employment" and t["pages"] == [20] and t["cited"] is True
    assert (t["to"]["name"], t["to"]["schema"], t["from"]["id"]) == ("Ministry of Works", "PublicBody", "person-ann-lee")
    assert t["role"] == "Adviser" and t["start"] == "1985" and t["quote"] == "Lee advised the Ministry."
    raw = next(x for x in d["ties"] if x is not t)                      # found, never saved: ends from its name
    assert raw["pages"] == [21] and raw["cited"] is False and "to" in raw


def test_run_people_carry_the_hid(tied):
    files, arch = tied
    ann = next(p for p in pageview.run(files, arch, group="people")["people"] if p["name"] == "Ann Lee")
    assert ann["id"] == "person-ann-lee" and ann["h"] == "H-p0-abc"


def exported(tmp_path, arch, status=None, fmt="jsonl", **kw):
    import apollo
    rec = {"id": "r1", "kind": "pageflow", "run_folder": str(tmp_path / "run"), "archive": arch, "name": "read-x", "source": "x"}
    if status:
        apollo.state_dir().mkdir(parents=True, exist_ok=True)
        (apollo.state_dir() / "r1.exit.json").write_text(json.dumps({"status": status, "code": 0}), encoding="utf-8")
    return apollo.export_run(rec, fmt, **kw)


def test_export_one_jsonl_file_with_a_meta_line(tied, tmp_path):
    files, arch = tied
    out = exported(tmp_path, arch, "stopped")
    assert out["ok"] and out["status"] == "stopped" and out["files"] == [str(tmp_path / "run" / "exports" / "r1.jsonl")]
    lines = [json.loads(x) for x in Path(out["files"][0]).read_text(encoding="utf-8").splitlines()]
    assert lines[0]["meta"]["status"] == "stopped" and lines[0]["meta"]["count"] == out["count"] == len(lines) - 1
    ann = next(r for r in lines[1:] if r["name"] == "Ann Lee" and r["page"] == 20)
    assert ann["id"] == "person-ann-lee" and ann["h"] == "H-p0-abc" and ann["source"] == ["doc-1"] and ann["cited"] is True
    assert ann["group"] == "people" and ann["saved"] is True and ann["quote"].startswith("Ann Lee met")
    assert next(r for r in lines[1:] if r["name"] == "Bo Kim")["saved"] is False


def test_export_json_by_group_and_by_page(tied, tmp_path):
    files, arch = tied
    out = exported(tmp_path, arch, "done", fmt="json", by="group")
    names = {Path(f).name for f in out["files"]}
    assert {"r1-people.json", "r1-bodies.json", "r1-places.json", "r1-events.json", "r1-ties.json"} <= names
    doc = json.loads((tmp_path / "run" / "exports" / "r1-ties.json").read_text(encoding="utf-8"))
    assert doc["meta"]["group"] == "ties" and doc["meta"]["status"] == "done" and len(doc["records"]) == doc["meta"]["count"] == 2
    assert doc["records"][0]["from"]["name"] == "Ann Lee"
    pages = exported(tmp_path, arch, "done", fmt="json", by="page")
    assert {Path(f).name for f in pages["files"]} == {"r1-p20.json", "r1-p21.json", "r1-p23.json"}
    one = exported(tmp_path, arch, "done", group="people", page=21)
    assert Path(one["files"][0]).name == "r1-people-p21.jsonl" and one["count"] == 1


def test_export_refuses_a_running_run_and_bad_arguments(tied, tmp_path, monkeypatch):
    import apollo
    files, arch = tied
    out = exported(tmp_path, arch)                                      # no exit record, no process: died, still exported
    assert out["ok"] and out["status"] == "died"
    rec = {"id": "r1", "kind": "pageflow", "run_folder": str(tmp_path / "run"), "archive": arch, "pid": 1}
    (tmp_path / "run" / "exports" / "r1.jsonl").unlink()
    monkeypatch.setattr(apollo, "alive", lambda r: True)
    out = apollo.export_run(rec, "json")
    assert out["ok"] is False and "still running" in out["why"] and not (tmp_path / "run" / "exports" / "r1.json").exists()
    assert apollo.export_run(rec, "xml")["ok"] is False and apollo.export_run(rec, "json", group="x")["ok"] is False
    assert apollo.export_run(rec, "json", page=0)["ok"] is False

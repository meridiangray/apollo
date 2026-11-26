"""The Flow select, the Draft archive, the collections an archive holds, the sources a window drops in, and a page of text.
Scratch folders only (conftest); the real `hydra-ctl` makes the Draft, nothing else runs hydra."""
import json
from pathlib import Path

import pytest
import yaml

import apollo
import archives
import pagetext
import sourcedrop
import tools

PROSE = "The Harbour Board met on 4 May 1971 and appointed Ana Reyes as secretary. " * 80


def cfg_of(rec):
    return yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith("fleet-config.yaml")))


def flat(d, pre=""):
    out = {}
    for k, v in d.items():
        out.update(flat(v, pre + k + ".") if isinstance(v, dict) else {pre + k: v})
    return out


def changed(a, b):
    fa, fb = flat(a), flat(b)
    return {k for k in set(fa) | set(fb) if fa.get(k, "<none>") != fb.get(k, "<none>")}


@pytest.fixture
def text_source(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    f = tmp_path / "in" / "Harbour minutes.txt"
    f.parent.mkdir()
    f.write_text(PROSE, encoding="utf-8")
    return sourcedrop.add(f)


@pytest.fixture
def draft():
    return Path(archives.ensure_draft()["path"])


# ---- the Flow select composes from `full` ----------------------------------------------------------------------------

def test_pollard_pp_1_to_11_one_dollar_lag_off_is_the_smoke_composition_except_for_what_differs():
    smoke = cfg_of(apollo.plan(recipe="smoke", source="pollard", pages="1-11", name="same"))
    flow = cfg_of(apollo.plan(flow="read", source="pollard", pages="1-11", budget="1", name="same"))
    assert flow["budget_usd"] == smoke["budget_usd"] == 1 and flow["pages"] == smoke["pages"] == {"from": 1, "to": 11}
    # the differences, all intended: the label the archive's name carries, the recipe's name, the `full` recipe's own
    # per-page rates (no call or token caps: every budget is null, in both), and the one tool (ask_person) that `full` authorizes and `smoke` does not
    assert changed(smoke, flow) == {"archive.name", "authorized", "recipe", "sonnet_parallel"}
    assert flow["recipe"] == "full" and flow["authorized"] == smoke["authorized"] + ["ask_person"]
    assert "lag" not in flow and flow["archive"]["name"].endswith("(page flow, full, pp. 1-11)")


def test_lag_on_takes_the_lag_recipes_settings_and_tools():
    lag = cfg_of(apollo.plan(recipe="lag", source="pollard", pages="1-11", name="same"))
    flow = cfg_of(apollo.plan(flow="read", source="pollard", pages="1-11", budget="1", lag=True, name="same"))
    assert flow["lag"] == lag["lag"] and flow["lag"]["enabled"] is True and flow["authorized"] == lag["authorized"]
    off = cfg_of(apollo.plan(flow="read", source="pollard", pages="1-11", budget="1", name="same"))
    assert flow["budgets"] == off["budgets"] and all(v is None for v in flow["budgets"].values())      # no call caps, so the lag adds none


def test_a_run_has_no_cost_cap_unless_the_person_sets_one(monkeypatch):
    assert [apollo.default_budget(n) for n in (1, 11, 107, 500)] == [None] * 4
    assert apollo.plan(flow="read", source="pollard", pages="1-11", name="a")["budget_usd"] is None
    assert apollo.plan(flow="read", source="pollard", pages="1-50", name="b")["budget_usd"] is None
    assert apollo.plan(flow="read", source="pollard", pages="1-50", budget="3", name="c")["budget_usd"] == 3
    wf = apollo.plan(flow="stubs", archive="/a/arch", dry=True)
    assert wf["budget_usd"] is None and "--max-budget-usd" not in wf["command"]
    assert "--max-budget-usd" in apollo.plan(flow="stubs", archive="/a/arch", dry=True, budget="2")["command"]


def test_hard_pages_off_stops_the_escalations_and_rate_off_drops_the_rubric():
    cfg = cfg_of(apollo.plan(flow="read", source="pollard", pages="1-5", without="escalate,rate", name="x"))
    assert cfg["budgets"]["escalations"] == 0 and "rubric" not in cfg
    with pytest.raises(ValueError):
        apollo.plan(flow="read", source="pollard", without="nobody")


def test_a_flow_is_a_start_of_its_own_and_lag_goes_with_read():
    for kw in ({"flow": "nope", "source": "pollard"}, {"flow": "read"}, {"flow": "read", "source": "pollard", "recipe": "smoke"},
               {"flow": "stubs", "archive": "draft", "lag": True}):
        with pytest.raises(ValueError):
            apollo.plan(**kw)


# ---- the Draft archive -----------------------------------------------------------------------------------------------

def test_the_draft_is_made_empty_once_by_hydras_own_init(draft):
    assert draft == archives.draft_path() and archives.is_archive(draft)
    assert yaml.safe_load((draft / "hydra.yaml").read_text(encoding="utf-8"))["name"] == "Draft"
    assert archives.ensure_draft() == {"path": str(draft), "created": False}
    assert archives.collections(str(draft))["collections"] == []


def test_a_flow_into_the_draft_adds_its_collections_to_it_with_the_sources_prefix(draft):
    rec = apollo.plan(flow="read", source="pollard", pages="1-3", archive="draft", name="d", dry=True)
    cfg = cfg_of(rec)
    assert rec["archive"] == str(draft) and cfg["archive"]["path"] == str(draft) and cfg["archive"]["existing"] is True
    assert cfg["archive"]["prefix"] == "draft" and all(k.startswith("pollard-da-") for k in cfg["collections"])
    assert "view_copy" not in cfg["archive"]
    fresh = cfg_of(apollo.plan(flow="read", source="pollard", pages="1-3", archive="new", name="e", dry=True))
    assert not fresh["archive"].get("existing") and fresh["archive"]["path"].endswith("/archives/" + fresh["run"] + "/pollard-da")
    wf = apollo.plan(flow="stubs", archive="draft", dry=True)
    assert wf["kind"] == "mcp-session" and wf["archive"] == str(draft) and wf["flow"] == "stubs"


def test_a_page_flow_adds_only_to_an_archive_of_apollos_own(tmp_path, draft):
    with pytest.raises(ValueError, match="only archives of Apollo"):
        apollo.plan(flow="read", source="pollard", pages="1-3", archive=str(tmp_path / "elsewhere"), dry=True)
    assert apollo.plan(flow="read", source="pollard", pages="1-3", archive=str(draft), dry=True)["archive"] == str(draft)


def test_the_draft_reset_makes_it_empty_again(draft):
    from hydra.archive import collections_io
    collections_io.write(collections_io.path(draft, "notes"), [{"id": "n1", "schema": "Person", "properties": {"name": ["A"]}}], False)
    archives.track("r1", str(draft), ["notes"])
    assert archives.collections(str(draft))["collections"][0]["count"] == 1
    archives.reset_draft()
    assert archives.collections(str(draft))["collections"] == [] and archives.tracked(str(draft)) == {}


# ---- collections -----------------------------------------------------------------------------------------------------

def test_collections_count_types_sources_and_the_run_that_added_them(draft):
    from hydra.archive import collections_io
    src = {"id": "doc-1", "schema": "Document", "properties": {"title": ["The Harbour Minutes"]}}
    collections_io.write(draft / ".hydra" / "sources" / "entries" / "pollard-da-actors.json", [src], False)
    recs = [{"id": f"p{i}", "schema": "Person", "properties": {"name": [f"Person {i}"], "sources": ["doc-1"],
                                                                "notes": [f"Quote {i}."]}} for i in range(250)]
    recs += [{"id": "o1", "schema": "Organization", "properties": {"name": ["The Board"], "sources": ["doc-1"]}}]
    collections_io.write(collections_io.path(draft, "pollard-da-actors"), recs, False)
    archives.track("2026-11-25-read-pollard", str(draft), ["pollard-da-actors"])
    got = archives.collections(str(draft))
    row = got["collections"][0]
    assert got["name"] == "Draft" and got["draft"] is True and row["name"] == "pollard-da-actors" and row["count"] == 251
    assert row["types"] == [{"schema": "Person", "label": "Person", "n": 250}, {"schema": "Organization", "label": "Organization", "n": 1}]
    assert row["sources"] == ["The Harbour Minutes"] and row["run"] == "2026-11-25-read-pollard" and row["added"]
    page = archives.records(str(draft), "pollard-da-actors", 0, 200)
    assert page["total"] == 251 and len(page["records"]) == 200
    assert page["records"][0] == {"id": "p0", "schema": "Person", "type": "Person", "name": "Person 0", "source": "The Harbour Minutes", "quote": "Quote 0."}
    assert len(archives.records(str(draft), "pollard-da-actors", 200, 200)["records"]) == 51
    assert archives.records(str(draft), "nope")["ok"] is False


def test_the_collections_command_names_the_run_and_refuses_an_archive_outside_apollos_own(draft, tmp_path):
    rec = apollo.plan(flow="read", source="pollard", pages="1-3", archive="draft", dry=True)
    apollo.save({**rec, "started": "2026-11-25T10:00:00"})
    from hydra.archive import collections_io
    collections_io.write(collections_io.path(draft, "pollard-da-actors"), [{"id": "a", "schema": "Person", "properties": {"name": ["A"]}}], False)
    archives.track(rec["id"], str(draft), ["pollard-da-actors"])
    out = apollo.other_command(type("A", (), {"cmd": "collections", "archive": str(draft), "collection": None, "offset": 0, "limit": 200})())
    assert out["collections"][0]["run"] == {"id": rec["id"], "flow": "Basic", "source": "Pollard damage assessment"} or \
        out["collections"][0]["run"]["flow"] == "Basic"
    bad = apollo.other_command(type("A", (), {"cmd": "collections", "archive": str(tmp_path), "collection": None, "offset": 0, "limit": 1})())
    assert bad["ok"] is False


def test_a_run_that_ends_notes_its_collections_and_builds_its_archive_once(draft, monkeypatch):
    from hydra.archive import collections_io
    rec = apollo.plan(flow="read", source="pollard", pages="1-3", archive="draft", name="fin", dry=True)
    name = next(iter(cfg_of(rec)["collections"]))
    collections_io.write(collections_io.path(draft, name), [{"id": "a", "schema": "Person", "properties": {"name": ["A"]}}], False)
    Path(rec["run_folder"]).mkdir(parents=True)
    Path(rec["run_folder"], "fleet-config.yaml").write_text(next(iter(rec["files"].values())), encoding="utf-8")
    built = []
    monkeypatch.setattr(tools, "build_now", lambda a: built.append(a))
    apollo.finish_archive({**rec, "started": "x"})
    assert built == [str(draft)] and archives.tracked(str(draft))[name]["run"] == rec["id"]


def test_clearing_a_run_that_added_to_the_draft_takes_its_collections_out(draft):
    from hydra.archive import collections_io, manifest
    rec = apollo.plan(flow="read", source="pollard", pages="1-3", archive="draft", name="clr", dry=True)
    names = list(cfg_of(rec)["collections"])
    for n in names:
        collections_io.write(collections_io.path(draft, n), [{"id": "a", "schema": "Person", "properties": {"name": ["A"]}}], False)
    keep = "other-notes"
    collections_io.write(collections_io.path(draft, keep), [{"id": "k", "schema": "Person", "properties": {"name": ["K"]}}], False)
    archives.track(rec["id"], str(draft), names)
    apollo.save({**rec, "started": "2026-11-25T10:00:00", "status": "done"})
    apollo._write(apollo.state_dir() / f"{rec['id']}.exit.json", {"status": "done", "detail": "done", "ended": "2026-11-25T10:01:00"})
    Path(rec["run_folder"]).mkdir(parents=True, exist_ok=True)
    out = apollo.clear(rec["id"])
    assert out["ok"] and archives.collections(str(draft))["collections"][0]["name"] == keep
    assert len(archives.collections(str(draft))["collections"]) == 1
    assert not set(names) & set((manifest.read(draft).get("collections") or {}))
    assert archives.tracked(str(draft)) == {}


def test_a_build_is_started_only_when_the_archive_is_stale(draft, monkeypatch):
    (draft / "collections" / "a.json").write_text("[]", encoding="utf-8")                   # an archive with nothing in it is never built
    started = []
    monkeypatch.setattr(tools, "begin", lambda tool, source, archive, *a: started.append((tool, archive)) or {"ok": True, "started": True})
    assert tools.build_if_stale(str(draft)) == {"building": True, "started": True}           # never built here
    key = tools.key_of("build", None, draft)
    tools._save(tools.job_file(key, "build"), {"tool": "build", "key": key, "status": "done", "ended": 9e12})
    assert tools.build_if_stale(str(draft)) == {"building": False, "started": False}          # built after the last change
    tools._save(tools.job_file(key, "build"), {"tool": "build", "key": key, "status": "done", "ended": 1.0})
    assert tools.build_if_stale(str(draft))["started"] is True                                # a file changed since
    assert len(started) == 2 and started[0] == ("build", str(draft))


def test_check_and_build_take_the_draft_but_not_a_folder_elsewhere(draft, tmp_path):
    assert tools.allowed_archive(str(draft)) == draft.resolve()
    with pytest.raises(ValueError):
        tools.allowed_archive(str(tmp_path))
    with pytest.raises(ValueError):
        tools.allowed_archive(str(archives.folder()))


# ---- dropped sources in the manager ----------------------------------------------------------------------------------

def test_the_sources_list_has_built_in_and_dropped_and_a_dropped_one_runs_with_no_context(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    f = tmp_path / "in" / "Notes of a meeting.txt"
    f.parent.mkdir()
    f.write_text(PROSE, encoding="utf-8")
    got = sourcedrop.add(f)
    assert got["needs"] == []
    rows = {r["name"]: r for r in apollo.recipes_and_sources()["sources"]}
    assert rows["pollard"]["dropped"] is False and rows[got["slug"]]["dropped"] is True and rows[got["slug"]]["needs"] == []
    assert [r["name"] for r in apollo.recipes_and_sources()["sources"]][:3] == ["alwaleed", "jeddah-tower", "pollard"]
    rec = apollo.plan(flow="read", source=got["slug"], dry=True)
    assert cfg_of(rec)["hydra_context"] == "general" and rec["source_title"] == "Notes of a meeting"
    ready = tools.ready(got["slug"], None, "full", None, lambda *a: {"ok": True, "pages": []})
    assert "context" not in " ".join(i["text"] for i in ready["items"])


def test_ready_does_not_ask_a_dropped_source_for_a_context(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    f = tmp_path / "in" / "Untitled meeting notes.txt"
    f.parent.mkdir()
    f.write_text(PROSE, encoding="utf-8")
    slug = sourcedrop.add(f)["slug"]
    out = tools.ready(slug, None, "full", None, lambda *a: {"ok": True, "pages": []})
    assert not any("context" in i["text"] for i in out["items"])


def args(**kw):
    return type("A", (), kw)()


def test_sourceadd_takes_the_upload_in_and_deletes_the_incoming_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    inc = tmp_path / "sources" / "incoming" / "abc123" / "Harbour minutes.txt"
    inc.parent.mkdir(parents=True)
    inc.write_text(PROSE, encoding="utf-8")
    out = apollo.other_command(args(cmd="sourceadd", path=str(inc), title=None))
    assert out["ok"] and out["needs"] == [] and out["title"] == "Harbour minutes"
    assert not inc.exists() and not inc.parent.exists()                                    # the copy and its folder are gone
    assert (tmp_path / "sources" / "files" / f"{out['slug']}.txt").is_file()
    bad = tmp_path / "sources" / "incoming" / "zzz" / "x.exe"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"MZ")
    refused = apollo.other_command(args(cmd="sourceadd", path=str(bad), title=None))
    assert refused["ok"] is False and "not a format" in refused["why"] and not bad.exists()
    outside = tmp_path / "keep.txt"
    outside.write_text(PROSE, encoding="utf-8")
    assert apollo.other_command(args(cmd="sourceadd", path=str(outside), title=None))["ok"] is True
    assert outside.exists()                                                                # only the incoming folder is cleaned


def test_sourceupdate_sourceremove_and_sourcelist(text_source):
    slug = text_source["slug"]
    out = apollo.other_command(args(cmd="sourceupdate", slug=slug, fields=json.dumps({"title": "Minutes of the Board"})))
    assert out["ok"] and out["needs"] == []
    listed = apollo.other_command(args(cmd="sourcelist"))
    row = next(r for r in listed["dropped"] if r["slug"] == slug)
    assert row["title"] == "Minutes of the Board" and "context" not in row and row["kind"] == "parts" and row["brief_default"] is True
    assert [r["slug"] for r in listed["builtin"]] == ["alwaleed", "jeddah-tower", "pollard"] and "contexts" not in listed
    assert apollo.other_command(args(cmd="sourceupdate", slug=slug, fields=json.dumps({"colour": "red"})))["ok"] is False
    assert apollo.other_command(args(cmd="sourceremove", slug=slug))["ok"] is True
    assert apollo.other_command(args(cmd="sourcelist"))["dropped"] == []


def test_every_driver_run_gets_the_dropped_sources_folder(text_source):
    assert str(sourcedrop.folder()) in apollo.sources_extra().split(":")
    assert apollo.composer().source_dirs()[-1] == sourcedrop.folder()


# ---- a page of text --------------------------------------------------------------------------------------------------

def test_a_pdf_page_comes_from_its_text_layer_and_is_cached():
    got = pagetext.page("pollard", 20)
    assert got["ok"] and got["kind"] == "pages" and got["layer"] == "text" and got["page"] == 20 and got["total"] > 100
    assert len(got["text"]) > 200
    cached = list((pagetext.state_dir() / "pagetext" / "pollard").glob("20.txt"))
    assert cached and pagetext.page("pollard", 20) == got
    assert pagetext.page("pollard", 99999)["ok"] is False and pagetext.page("nope", 1)["ok"] is False
    assert pagetext.page("pollard", 0)["ok"] is False


def test_a_text_source_is_read_part_by_part(text_source):
    got = pagetext.page(text_source["slug"], 1)
    assert got["ok"] and got["kind"] == "parts" and got["total"] == 2 and got["text"].startswith("The Harbour Board")
    assert pagetext.page(text_source["slug"], 3)["ok"] is False


def test_a_page_without_text_shows_the_ocr_text_when_there_is_some(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    w = PdfWriter()
    for _ in range(2):
        w.add_blank_page(200, 200)
    pdf = tmp_path / "in" / "Scan.pdf"
    pdf.parent.mkdir()
    with pdf.open("wb") as fh:
        w.write(fh)
    slug = sourcedrop.add(pdf)["slug"]
    bare = pagetext.page(slug, 1)
    assert bare["ok"] and bare["layer"] == "none" and bare["text"] == "" and bare["total"] == 2
    ocr = pagetext.state_dir() / "tools" / slug / "ocr.json"
    ocr.parent.mkdir(parents=True)
    ocr.write_text(json.dumps({"pages": [{"page": 2, "text": "Read by OCR", "lines": 1}]}), encoding="utf-8")
    assert pagetext.page(slug, 2)["layer"] == "ocr" and pagetext.page(slug, 2)["text"] == "Read by OCR"
    assert pagetext.page(slug, 1)["layer"] == "none"


# ---- the run's words -------------------------------------------------------------------------------------------------

def test_a_run_is_named_by_its_flow_and_its_source_and_an_old_recipe_run_still_shows():
    rec = apollo.plan(flow="read", source="pollard", pages="1-3", dry=True)
    s = apollo.summary({**rec, "started": "2026-11-25T10:00:00"})
    assert s["flow"] == "Basic" and s["source_title"] == "The Jonathan Jay Pollard Espionage Case: A Damage Assessment"
    old = apollo.plan(recipe="smoke", source="pollard", dry=True)
    assert apollo.summary({**old, "started": "2026-11-25T10:00:00"})["flow"] == "Basic"
    wf = apollo.plan(flow="enrich", archive="/a/arch", dry=True)
    assert apollo.summary({**wf, "started": "2026-11-25T10:00:00"})["flow"] == "Enrich"


def test_flow_names_domains_and_agents():
    import apollo, runview
    assert [f["name"] for f in runview.flow_catalogue()] == ["Basic", "Assemble", "Refine", "Timeline", "Places", "Stubs", "Enrich", "Wikidata",
                                                            "Finance", "Curate"]
    assert [f["domain"] for f in runview.flow_catalogue()] == ["Records", "Graph", "Graph", "Time", "Geo", "Profiles", "Profiles", "Profiles",
                                                              "Finance", "Curate"]
    assert runview.flow_key({"flow": "Fill stubs"}) == "stubs" and runview.flow_label({"kind": "pageflow"}) == "Basic"
    assert runview.flow_key({"name": "workflow-links", "kind": "mcp-session"}) == "wikidata"         # Links became Wikidata
    assert runview.flow_key({"flow": "links"}) == runview.flow_key({"flow": "Suggest links"}) == "wikidata"
    assert apollo.agents_for("read", None) == {"haiku": 4, "sonnet": 2}
    assert apollo.agents_for("stubs", {"haiku": 3}) == {"haiku": 3, "sonnet": 1, "opus": 0}
    for flow, bad in (("read", {"opus": 1}), ("read", {"haiku": 0}), ("assemble", {"sonnet": 2}), ("stubs", {"sonnet": 9})):
        with pytest.raises(ValueError):
            apollo.agents_for(flow, bad)
    assert apollo.sub_agents({"haiku": 3, "sonnet": 1, "opus": 0}, "claude-sonnet-5-5") == {"haiku": 3}


def test_scoped_flows_brief_their_tools_and_wikidata_answers_only_when_switched_on():
    import apollo
    assert apollo.AFTER == ("assemble",) and "links" not in {w[0] for w in apollo.WORKFLOWS}
    assert "wikidata_decide" in apollo.flow_brief("wikidata", True) and "wikidata_decide" not in apollo.flow_brief("wikidata", False)
    assert "views=[]" in apollo.flow_brief("timeline") and "authorized=[\"assemble\", \"save_source_note\"]" in apollo.flow_brief("assemble")
    assert apollo.workflow_steps("links") == apollo.workflow_steps("wikidata") != []
    rec = apollo.plan(flow="wikidata", archive="/a/arch", source="tarbell", dry=True, answer=True)    # the engine now
    assert rec["kind"] == "engine" and rec["command"][2:5] == ["run", "--flow", "wikidata"]
    with pytest.raises(ValueError):
        apollo.plan(flow="timeline", archive="/a/arch", dry=True, answer=True)
    with pytest.raises(ValueError):
        apollo.plan(flow="read", source="pollard", dry=True, after="links")


def test_prepare_source_runs_the_steps_and_skips_what_is_cached(monkeypatch, tmp_path):
    import apollo, tools, catalogue
    calls = []
    monkeypatch.setattr(tools, "source_file", lambda s: tmp_path / "x.pdf")
    monkeypatch.setattr(apollo, "analyse", lambda s, *a: {"ok": True, "pages": [{"page": 1, "needs_ocr": False}]})
    monkeypatch.setattr(apollo, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(tools, "get", lambda t, s=None, a=None: {"status": "done" if t == "text" else "none"})
    monkeypatch.setattr(tools, "begin", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(catalogue, "basic", lambda s: calls.append(("basic", s)), raising=False)
    got = {r["step"]: r["status"] for r in apollo.prepare_source("x")}
    assert got == {"analyse": "done", "ocr": "skipped", "text": "cached", "catalogue": "done"} and calls == [("basic", "x")]


def test_curate_and_finance_start_the_engine():
    import apollo, runview
    spec = next(f for f in runview.flow_catalogue() if f["key"] == "wiki")
    assert spec["name"] == "Curate" and spec["needs"] == "archive" and spec["grades"]["sonnet"] == {"default": 1, "max": 1}
    for flow in ("wiki", "finance"):
        rec = apollo.plan(flow=flow, archive="/a/arch", dry=True, for_source="pollard")
        cmd = rec["command"]
        assert rec["kind"] == "engine" and cmd[cmd.index("--flow") + 1] == flow and cmd[cmd.index("--grade") + 1] == "sonnet"
    rec = apollo.plan(flow="wiki", archive="/a/arch", dry=True, for_source="pollard")
    assert apollo.summary({**rec, "started": "2026-11-25T10:00:00"})["flow"] == "Curate"
    assert apollo.agents_for("wiki", None) == {"haiku": 0, "sonnet": 1, "opus": 0}


def test_assemble_covers_the_pages_read_so_far_or_the_range_set(monkeypatch, tmp_path):
    import apollo
    arch = tmp_path / "arch"
    arch.mkdir()
    for rid, pages in (("r1", {"1": "done", "2": "done", "3": "out-of-scope"}), ("r2", {"2": "done", "5": "done", "6": "empty"})):
        pf = tmp_path / rid / "pageflow"
        pf.mkdir(parents=True)
        (pf / "progress.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
        apollo.save({"id": rid, "kind": "pageflow", "source": "tarbell", "archive": str(arch), "run_folder": str(tmp_path / rid),
                     "started": "2026-11-26T10:00:00"})
    apollo.save({"id": "r3", "kind": "pageflow", "source": "tarbell", "archive": "/elsewhere", "run_folder": str(tmp_path / "r1"),
                 "started": "2026-11-26T11:00:00"})
    assert apollo.source_pages("tarbell", arch) == [1, 2, 5]                 # the union; skipped and out-of-scope are not read
    assert apollo.source_pages("other", arch) == []
    monkeypatch.setattr(apollo, "known_sources", lambda: {"tarbell": "Tarbell, Standard Oil"})
    text = apollo.assemble_range(str(arch), "tarbell")
    assert "Source: tarbell (Tarbell, Standard Oil)" in text and "Pages to cover: 1-2,5" in text and "archive so far" in text
    assert "Pages to cover: 5-40 (the range the person set)" in apollo.assemble_range(str(arch), "tarbell", "5-40")
    assert "No page of it was read" in apollo.assemble_range(str(arch), "other")
    with pytest.raises(ValueError):
        apollo.assemble_range(str(arch), "tarbell", "9-x")


def test_a_run_lifts_the_mcp_reply_cap_and_sets_one_read_size(monkeypatch):
    import apollo
    monkeypatch.delenv("MAX_MCP_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("HYDRA_MCP_READ_CHARS", raising=False)
    env = apollo.run_env()
    assert env["MAX_MCP_OUTPUT_TOKENS"] == "100000" and env["HYDRA_MCP_READ_CHARS"] == "40000"
    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "150000")                     # the person's own setting stands
    assert apollo.run_env()["MAX_MCP_OUTPUT_TOKENS"] == "150000"
    rec = apollo.plan(flow="stubs", archive="/a/arch", dry=True, pages="5-9")
    client = json.loads(next(t for f, t in rec["files"].items() if f.endswith("mcp.json")))
    assert client["mcpServers"]["hydra"]["env"]["HYDRA_MCP_READ_CHARS"] == "40000"
    prompt = next(t for f, t in rec["files"].items() if f.endswith("prompt.md"))
    assert "workflow stubs" in prompt


# ---- the engine flows (pageflow/engine.py): Assemble and Timeline start the driver, not a session ----------------------

def test_assemble_and_timeline_start_the_engine_with_the_grade_the_person_picks():
    import apollo
    on = apollo.plan(flow="assemble", archive="/a/arch", source="tarbell", dry=True, pages="36-50")
    cmd = on["command"]
    assert on["kind"] == "engine" and cmd[1].endswith("pageflow/engine.py") and cmd[2:5] == ["run", "--flow", "assemble"]
    assert cmd[cmd.index("--archive") + 1] == "/a/arch" and cmd[cmd.index("--source") + 1] == "tarbell"
    assert cmd[cmd.index("--name") + 1] == on["id"] and on["run_folder"].endswith("/runs/" + on["id"])
    assert cmd[cmd.index("--root") + 1] == str(apollo.root_for(True)) and "--dry" in cmd and "--pages" in cmd
    assert cmd[cmd.index("--grade") + 1] == "sonnet" and "--no-answers" not in cmd and on["opts"]["answer"] is True
    assert on["files"] == {} and "--max-budget-usd" not in cmd and on["io"]["stdout"].endswith("driver.log")
    off = apollo.plan(flow="assemble", archive="/a/arch", source="tarbell", dry=True, answer=False,
                      agents={"haiku": 0, "sonnet": 0, "opus": 1})
    assert "--no-answers" in off["command"] and off["command"][off["command"].index("--grade") + 1] == "opus"
    tl = apollo.plan(flow="timeline", archive="/a/arch", for_source="tarbell", agents={"haiku": 1, "sonnet": 0})
    assert tl["kind"] == "engine" and tl["command"][tl["command"].index("--grade") + 1] == "haiku" and "--dry" not in tl["command"]
    for kw in ({"flow": "assemble", "archive": "/a/arch"}, {"flow": "timeline", "source": "tarbell"}):
        with pytest.raises(ValueError):
            apollo.plan(dry=True, **kw)


def test_an_engine_run_shows_its_page_heat_cost_and_ending(tmp_path):
    import apollo
    rec = {**apollo.plan(flow="assemble", archive="/a/arch", source="tarbell", dry=True), "run_folder": str(tmp_path),
           "started": "2026-11-26T10:00:00"}
    (tmp_path / "pageflow").mkdir()
    (tmp_path / "pageflow" / "progress.json").write_text(json.dumps({"fraction": 0.5, "stage": "judge", "pages": {"36": "done", "37": "escalated"}}), encoding="utf-8")
    (tmp_path / "pageflow" / "found.jsonl").write_text(json.dumps({"page": 36, "schema": "merge", "thing": "A into B", "quote": "q"}) + "\n", encoding="utf-8")
    (tmp_path / "assemble").mkdir()
    (tmp_path / "assemble" / "state.json").write_text(json.dumps({"status": "done", "models": {"used": {"sonnet": 2}, "tokens": {}}}), encoding="utf-8")
    s = apollo.summary(rec)
    assert s["flow"] == "Assemble" and s["kind"] == "engine" and [c[:2] for c in s["cells"]] == [[36, "done"], [37, "escalated"]]
    assert s["progress"]["pages_read"] == 2
    assert apollo.final_status(rec, 0, False) == ("done", "done")
    (tmp_path / "assemble" / "state.json").write_text(json.dumps({"status": "stopped-error: boom"}), encoding="utf-8")
    assert apollo.final_status(rec, 2, False)[0] == "stopped"


def test_refine_starts_the_engine_with_opus_and_finds_its_source_from_the_basic_run(tmp_path):
    import apollo, runview
    spec = next(f for f in runview.flow_catalogue() if f["key"] == "refine")
    assert spec["name"] == "Refine" and spec["after"] == "assemble" and spec["grades"]["opus"]["default"] == 1
    rec = apollo.plan(flow="refine", archive="/a/arch", source="tarbell", dry=True)
    cmd = rec["command"]
    assert rec["kind"] == "engine" and cmd[2:5] == ["run", "--flow", "refine"] and cmd[cmd.index("--grade") + 1] == "opus"
    son = apollo.plan(flow="refine", archive="/a/arch", source="tarbell", agents={"haiku": 0, "sonnet": 1, "opus": 0})["command"]
    assert son[son.index("--grade") + 1] == "sonnet"                              # the person picks another grade
    apollo.save({"id": "basic-1", "kind": "pageflow", "source": "tarbell", "archive": "/a/arch", "run_folder": str(tmp_path),
                 "started": "2026-11-26T10:00:00"})
    got = apollo.plan(flow="refine", archive="/a/arch", dry=True)                   # no source given: the Basic run's
    assert got["command"][got["command"].index("--source") + 1] == "tarbell"

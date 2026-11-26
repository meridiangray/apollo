"""`apollo.py view` (one tab as rows with fields) and `apollo.py configs` (what can be started): the JSON Apollo's web
app reads. Scratch folders only; the page-flow driver is tests/fake_driver.py."""
import json
import os
import subprocess
import sys
from pathlib import Path

import apollo
import runfiles


def cli(*args) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), *args], capture_output=True, text=True, timeout=60,
                       env=os.environ.copy())
    return p.returncode, p.stdout


def test_view_json_gives_every_tab_of_a_dry_page_flow_run_as_rows(scratch, smoke_config, waiter):
    code, out = cli("start", str(smoke_config), "--dry", "--json")
    rid = json.loads(out)["id"]
    waiter(lambda: apollo.exit_info(rid))
    views = {}
    for tab in apollo.TABS:
        code, out = cli("view", rid[-12:], "--tab", tab, "--json")
        assert code == 0
        views[tab] = json.loads(out)
        assert views[tab]["id"] == rid and views[tab]["tab"] == tab and views[tab]["kind"] == "pageflow"
    run = views["run"]
    assert run["pages"] == [[20, "done"], [21, "done"]] and run["total"] >= len(run["rows"]) > 0
    first = run["rows"][0]                                                  # newest first
    assert set(first) == {"at", "verb", "count", "n", "page", "object", "more", "descriptor", "who", "kind", "tag",
                          "i", "open", "nrecs", "schema", "call"}
    assert (first["verb"], first["object"]) == ("Ran", "run done")
    held = [r for r in run["rows"] if r["verb"] == "Asked"]
    assert held and held[0]["who"] == "haiku" and held[0]["page"] in (20, 21)
    found = views["found"]
    assert found["total"] == 2 and found["on_pages"] == 2 and found["pages"] == [[20, "done"], [21, "done"]]
    assert [g["page"] for g in found["groups"]] == [20, 21]
    rec = found["groups"][0]["records"][0]
    assert rec == {"schema": "Person", "thing": "Person 20", "category": "Entities", "state": "proposed",
                   "cited": False, "quote": "A sentence."}
    calls = views["calls"]
    assert calls["total"] == 3 and calls["errors"] == 1
    assert [(c["tool"], c["slow"], c["ok"]) for c in calls["rows"]] == [("guide", True, True), ("save", True, True),
                                                                         ("cite", True, False)]
    assert calls["slowest"][0] == {"tool": "save", "ms": 9000, "at": "10:00:01"}
    q = views["questions"]
    assert q["rows"] == [] and (q["open"], q["answered"], q["dismissed"]) == (0, 0, 0)
    rep = views["report"]
    assert rep["path"].endswith("report.md")
    assert rep["blocks"][0] == {"type": "h1", "text": "Report"}
    assert {"type": "table", "rows": [["a", "b"], ["1", "2"]]} in rep["blocks"]
    code, out = cli("view", rid, "--tab", "calls")                          # without --json: the window's text
    assert code == 0 and out.startswith("3 calls · 1 errors")


def test_view_refuses_a_bad_tab_and_an_unknown_run(scratch):
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "view", "x", "--tab", "nope", "--json"],
                       capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert p.returncode == 2 and "invalid choice" in p.stderr
    code, out = cli("view", "nothing-like-this", "--json")
    assert code == 2 and json.loads(out) == {"ok": False, "why": "no run nothing-like-this"}


def test_view_of_a_session_reads_its_archive_questions(scratch, tmp_path, brief):
    a = tmp_path / "arch"
    (a / ".hydra" / "review").mkdir(parents=True)
    (a / ".hydra" / "review" / "questions.yaml").write_text(
        "questions:\n- id: ask-2\n  question: Which year?\n  answer: {choice: '1985'}\n"
        "- id: ask-1\n  question: Same person?\n  kind: same-name\n  page: 23\n  records: [rec-1, rec-2]\n"
        "  quote: Ann met Ann.\n  options: [One, Two]\n  suggestion: One\n", encoding="utf-8")
    rec = apollo.plan(brief, dry=True, archive=str(a))
    v = apollo.view(rec, "questions")
    assert (v["open"], v["answered"], v["dismissed"]) == (1, 1, 0) and v["pages"] == []
    assert v["rows"][0] == {"id": "ask-1", "title": "Same person?", "labels": {}, "asked": None, "status": "open", "kind": "same-name", "page": 23,
                            "records": ["rec-1", "rec-2"], "question": "Same person?", "quote": "Ann met Ann.",
                            "options": ["One", "Two"], "suggestion": "One", "answer": None}
    assert v["rows"][1]["answer"] == {"choice": "1985", "text": None}
    assert apollo.view(rec, "report") ["blocks"] == [] and apollo.view(rec, "report")["path"] is None


def test_report_blocks_and_run_rows():
    md = "# Report\n\nSome **bold** and `code`.\n\n\n## Counts\n\n| a | b |\n|---|---|\n| 1 | 2 |\n- one\n  - two\n\n"
    assert runfiles.report_blocks(md) == [
        {"type": "h1", "text": "Report"}, {"type": "gap"}, {"type": "p", "text": "Some bold and code."}, {"type": "gap"},
        {"type": "h2", "text": "Counts"}, {"type": "gap"}, {"type": "table", "rows": [["a", "b"], ["1", "2"]]},
        {"type": "li", "text": "one", "indent": 0}, {"type": "li", "text": "two", "indent": 2}]
    rows = [{"t": i, "at": f"10:00:0{i}", "verb": "Called", "object": f"id={i}", "descriptor": "save", "who": "agent",
             "kind": "info"} for i in range(3)]
    rows.append({"t": 4, "at": "10:00:04", "verb": "Ran", "object": "Pass A", "descriptor": "", "who": "driver",
                 "kind": "phase"})
    got = runfiles.run_rows(rows)
    assert [(r["verb"], r["count"], r["who"], r["tag"]) for r in got] == [("Ran", 1, None, "phase"),
                                                                          ("Called", 3, "agent", "")]
    assert got[1]["object"] == "id=2" and runfiles.run_rows(rows, limit=1) == got[:1]


def test_configs_lists_recipes_sources_and_briefs():
    got = apollo.recipes_and_sources()
    assert [r["name"] for r in got["recipes"]] == ["full", "lag", "smoke"]
    smoke = {r["name"]: r for r in got["recipes"]}["smoke"]
    assert smoke["path"].endswith("pageflow/recipes/smoke.yaml")
    assert smoke["description"] == "The first 11 pages of the source, small budgets."
    assert smoke["defaults"] == {"model": "claude-haiku-5-5", "span": 11, "budget_usd": 1, "lag": False}
    src = {s["name"]: s for s in got["sources"]}
    assert {"pollard", "alwaleed"} <= set(src)
    assert src["pollard"]["pages"] == "20-126" and src["pollard"]["reference"] is True
    assert src["alwaleed"]["reference"] is False and src["alwaleed"]["publisher"] == "Wikipedia"
    brief = {(r["kind"], r["name"]): r for r in got["briefs"]}[("mcp-session", "pollard-incident")]
    assert brief["description"] == "The Pollard Damage Assessment, incident context"
    assert brief["defaults"]["model"] == apollo.SESSION_MODEL
    code, out = cli("configs", "--json")
    assert code == 0 and json.loads(out) == got and set(got) == {"recipes", "sources", "briefs", "flows", "draft"}
    code, out = cli("configs", "--legacy", "--json")                      # the old flat list, for a reader of the old shape
    assert code == 0 and isinstance(json.loads(out), list) and all(set(r) == {"name", "kind", "path", "description", "defaults"} for r in json.loads(out))


def test_configs_skip_what_cannot_be_started(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "a.yaml").write_text("run: a\n", encoding="utf-8")
    (tmp_path / "runs" / "notes.json").write_text("{}", encoding="utf-8")
    (tmp_path / "runs" / "bad.yaml").write_text("# Broken: on purpose. More.\nrun: [\n", encoding="utf-8")
    rows = apollo.configs([(tmp_path / "runs", "*")])
    assert [(r["name"], r["description"]) for r in rows] == [("a", ""), ("bad", "Broken: on purpose.")]
    assert rows[0]["defaults"] == {"model": None, "pages": None, "budget": None}


def test_route_heat_and_measures_from_a_run_folder():
    import runfiles
    trace = [{"kind": "phase", "stage": s} for s in ("preflight", "setup", "triage", "A", "B", "C", "C", "T", "save", "C")]
    progress = {"pages": {str(p): "done" for p in range(20, 45)} | {"50": "empty"}}
    r = runfiles.route(trace, progress, 10)
    assert [n["key"] for n in r["nodes"]][:6] == ["preflight", "setup", "triage", "A", "B", "chunk1"]
    assert r["chunk"] == {"at": 2, "of": 3} and r["nodes"][r["now"]]["key"] == "chunk2"
    assert r["nodes"][5]["state"] == "done" and r["nodes"][-1]["state"] == "todo"
    done = runfiles.route(trace, progress, 10, finished=True)
    assert done["now"] is None and all(n["state"] == "done" for n in done["nodes"])
    found = [{"page": 20, "schema": "Person", "thing": "A", "quote": "q"}, {"page": 20, "schema": "Person", "thing": "B"}]
    assert runfiles.cells(progress, found)[0] == [20, "done", 2]
    h = runfiles.heat(progress, {"pages": {"20": {"calls": 3, "seconds": 4.5, "fails": {"C": ["x"], "A": []}}}}, found)[0]
    assert (h["records"], h["quotes"], h["calls"], h["seconds"], h["problems"]) == (2, 1, 3, 4.5, {"C": 1})
    m = runfiles.measures({"passes": {"CT": {"status": "done", "seconds": 9}}, "richness": {"run": {"duplicates": {"value": 0, "bar": "0", "ok": True}}}},
                          [{"tool": "save", "ms": 2000}, {"tool": "save", "ms": 1000}], progress)
    assert m["targets"][0]["label"] == "Dupes" and m["passes"][0]["label"] == "Facts and ties" and m["tools"][0] == {"tool": "save", "calls": 2, "seconds": 3.0}


def test_heat_groups_by_category_and_every_key_is_in_the_go_fixture():
    import json
    from pathlib import Path
    found = [{"page": 9, "schema": "Person", "thing": "A", "quote": "q"}, {"page": 9, "schema": "Person", "thing": "B"},
             {"page": 9, "schema": "Company", "thing": "C"}, {"page": 9, "schema": "Place", "thing": "D"},
             {"page": 9, "schema": "Employment", "thing": "E"}, {"page": 10, "schema": "Position", "thing": "F"}]
    rows = runfiles.heat({"pages": {"9": "done", "10": "done", "11": "done"}},
                         {"pages": {"9": {"calls": 2, "seconds": 3, "fails": {"C": ["x"]}, "gave_up": "x"}}}, found)
    assert rows[0]["groups"] == {"people": 2, "bodies": 1, "places": 1, "ties": 1} and rows[0]["places"] == 1
    assert rows[1]["groups"] == {"posts": 1} and rows[2]["groups"] == {}
    fixture = json.loads((Path(__file__).parent.parent / "go" / "testdata" / "heat-row.json").read_text())
    assert set(rows[0]) == set(fixture)

"""The entity Trace: steps from Apollo's log, the run folder's files and the archive's own history, on a scratch archive."""
import json
from pathlib import Path

import pytest
import yaml

import apollo
import archives
import entitytrace
import runlog

RUN = "2026-11-25-read-alwaleed"
ME = "p-alwaleed"


def rec(i, schema, name=None, **props):
    p = {"name": [name]} if name else {}
    p.update({k: v if isinstance(v, list) else [v] for k, v in props.items()})
    return {"id": i, "schema": schema, "properties": p}


def snap(arch, message, who, at):
    from hydra.archive.history import store
    return store.snapshot(arch, message, who=who, auto=True, now=at)


def write(arch, name, rows):
    from hydra.archive import collections_io
    collections_io.write(collections_io.path(arch, name), rows, False)


def seed(arch: Path, started="2026-11-25T13:39:07"):
    """A scratch archive with two collections, a source, history steps by a person, an agent and a tool, and a run in the log."""
    doc = yaml.safe_load((arch / "hydra.yaml").read_text(encoding="utf-8")) or {}
    doc["collections"] = {"alwaleed-actors": {"title": "People"}, "alwaleed-material": {"title": "Money"}}
    (arch / "hydra.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    ent = arch / ".hydra" / "sources" / "entries"
    ent.mkdir(parents=True, exist_ok=True)
    (ent / "docs.json").write_text(json.dumps([rec("doc-1", "Article", fileName="al-waleed-bin-talal-al-saud.txt",
                                                    title="Al Waleed bin Talal Al Saud")]), encoding="utf-8")
    quote = "Al Waleed is the founder of the Kingdom Holding Company."
    me = rec(ME, "Person", "Al Waleed bin Talal Al Saud", sources="doc-1", notes=quote)
    father = rec("p-talal", "Person", "Talal bin Abdulaziz Al Saud", sources="doc-1")
    write(arch, "alwaleed-actors", [me, father])
    snap(arch, "hydra-mcp save: alwaleed-actors (2 records)", "agent:hydra-mcp", "2026-11-25T14:00:00+00:00")
    own = rec("own-1", "Ownership", owner="Al Waleed bin Talal Al Saud", asset="Kingdom Holding Company", notes="He owns 95 percent.")
    fam = rec("fam-1", "Family", person="Al Waleed bin Talal Al Saud", relative="Talal bin Abdulaziz Al Saud", relationship="father")
    write(arch, "alwaleed-material", [own, fam])
    snap(arch, "hydra-mcp save: alwaleed-material (2 records)", "agent:hydra-mcp", "2026-11-25T14:00:01+00:00")
    me["properties"]["notes"] = [quote + " (corrected)"]
    write(arch, "alwaleed-actors", [me, father])
    snap(arch, "amend alwaleed-actors: 1 changed", "Jane Q. Person", "2026-11-25T15:30:00+00:00")
    with runlog.writer() as con:
        for stmt in runlog.SCHEMA:
            con.execute(stmt)
        con.execute("INSERT INTO runs(id, flow, kind, source, archive, started) VALUES (?,?,?,?,?,?)",
                    [RUN, "read", "pageflow", "alwaleed", str(arch), started])
        con.execute("INSERT INTO events(run_id, seq, t, stage, verb, object, descriptor, who, kind, page) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [RUN, 1, 120.0, "A", "Found", "3 mentions", "Names", "haiku", "info", 3])
        con.execute("INSERT INTO events(run_id, seq, t, stage, verb, object, descriptor, who, kind) VALUES (?,?,?,?,?,?,?,?,?)",
                    [RUN, 2, 200.0, "A", "Placed", "Al Waleed bin Talal Al Saud", "OSM", "osm", "info"])
        con.execute("INSERT INTO found(run_id, seq, page, schema, thing, quote, record_id, state) VALUES (?,?,?,?,?,?,?,?)",
                    [RUN, 0, 3, "Person", "Prince Alwaleed", quote, None, "proposed"])
        con.execute("INSERT INTO found(run_id, seq, page, schema, thing, quote, record_id, state) VALUES (?,?,?,?,?,?,?,?)",
                    [RUN, 1, 3, "Person", "Al Waleed bin Talal Al Saud", quote, None, "proposed"])
        con.execute("INSERT INTO found(run_id, seq, page, schema, thing, quote, record_id, state) VALUES (?,?,?,?,?,?,?,?)",
                    [RUN, 2, 3, "Person", "Issy Sharp", "Issy Sharp states it.", None, "proposed"])
        con.execute("INSERT INTO questions(run_id, id, status, kind, page, question, quote, records, answer) VALUES (?,?,?,?,?,?,?,?,?)",
                    [RUN, "q1", "open", "identity", 3, "Is Al Waleed bin Talal Al Saud the same as Prince Alwaleed?", "same man", "", None])


@pytest.fixture
def arch(scratch):
    path = Path(archives.ensure_draft()["path"])
    seed(path)
    return path


def lines(got, cat=None):
    return [ln for ln in got["lines"] if cat in (None, ln["cat"])]


def test_the_steps_come_from_the_log_and_the_archives_history(arch):
    got = entitytrace.trace(str(arch), ME)
    assert got["ok"] and got["entity"]["name"] == "Al Waleed bin Talal Al Saud" and got["entity"]["collection_title"] == "People"
    by = {(ln["cat"], ln["verb"]) for ln in got["lines"]}
    assert ("src", "Found") in by and ("rev", "Asked") in by and ("geo", "Placed") in by
    assert ("id", "Added") in by and ("id", "Amended") in by and ("src", "Cited") in by      # the record's own history
    assert ("hold", "Added") in by and ("kin", "Added") in by                                  # a link that names it
    assert got["cats"] == ["kin", "geo", "hold", "id", "src", "rev"]
    added = next(ln for ln in got["lines"] if ln["cat"] == "id" and ln["verb"] == "Added")
    amended = next(ln for ln in got["lines"] if ln["verb"] == "Amended")
    assert added["steps"][0]["quote"] == "Al Waleed is the founder of the Kingdom Holding Company."       # the record as that step held it
    assert amended["steps"][0]["quote"].endswith("(corrected)")
    assert [ln["at"] for ln in got["lines"]] == sorted((ln["at"] for ln in got["lines"]), reverse=True)


def test_a_found_line_names_the_run_the_pass_and_the_page_and_carries_the_quote(arch):
    got = entitytrace.trace(str(arch), ME)
    found = lines(got, "src")
    f = next(ln for ln in found if ln["verb"] == "Found")
    assert f["obj"] == "Read · A · p3" and f["n"] == 1 and f["desc"] == "Proposed"
    assert f["steps"][0]["quote"].startswith("Al Waleed is the founder")


def test_steps_of_one_category_within_three_seconds_fold_into_one_line(arch):
    got = entitytrace.trace(str(arch), ME)
    material = [ln for ln in got["lines"] if ln["cat"] in ("hold", "kin")]
    assert len(material) == 2 and all(ln["n"] == 1 for ln in material)          # two categories: they never fold together
    ent = entitytrace.trace(str(arch), "p-talal")
    cited = [ln for ln in ent["lines"] if ln["cat"] == "src"]
    assert len(cited) == 1 and cited[0]["verb"] == "Cited"
    fold = entitytrace._fold([{"at": "2026-11-25T10:00:00Z", "cat": "id", "verb": "Keyed", "obj": "a", "desc": "", "who": "you", "msg": ""},
                              {"at": "2026-11-25T10:00:02Z", "cat": "id", "verb": "Matched", "obj": "b", "desc": "", "who": "you", "msg": ""},
                              {"at": "2026-11-25T10:00:09Z", "cat": "id", "verb": "Keyed", "obj": "c", "desc": "", "who": "you", "msg": ""}])
    assert [(ln["verb"], ln["n"]) for ln in fold] == [("Keyed", 1), ("Matched", 2)]


def test_no_author_name_is_ever_shown(arch):
    got = entitytrace.trace(str(arch), ME)
    assert "Jane" not in json.dumps(got) and "Person" not in {ln["who"] for ln in got["lines"]}
    amended = next(ln for ln in got["lines"] if ln["verb"] == "Amended")
    assert amended["who"] == "you"
    assert next(ln for ln in got["lines"] if ln["verb"] == "Added" and ln["cat"] == "id")["who"] == "agent"
    assert entitytrace._who("agent:hydra-mcp") == "agent" and entitytrace._who("enrich") == "enrich" and entitytrace._who("Some One") == "you"


def test_the_run_folders_files_stand_in_when_the_log_lacks_the_found_rows(arch, tmp_path):
    with runlog.writer() as con:
        con.execute("DELETE FROM found")
    folder = tmp_path / "run"
    (folder / "pageflow").mkdir(parents=True)
    (folder / "pageflow" / "found.jsonl").write_text(json.dumps({"page": 4, "schema": "Person", "thing": "Prince Alwaleed", "quote": "From the file.",
                                                                  "state": "proposed", "order": 0}) + "\n", encoding="utf-8")
    state = runlog.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{RUN}.json").write_text(json.dumps({"id": RUN, "run_folder": str(folder)}), encoding="utf-8")
    with runlog.writer() as con:
        con.execute("UPDATE runs SET archive=?", [str(arch)])
    got = entitytrace.trace(str(arch), None, name="Prince Alwaleed", schema="Person")
    assert got["ok"] and got["entity"]["id"] is None
    assert [ln["steps"][0]["quote"] for ln in got["lines"] if ln["verb"] == "Found"] == ["From the file."]


def test_a_sources_entities_are_its_found_rows_and_the_records_citing_it(arch):
    got = entitytrace.entities(str(arch), "alwaleed", "Al Waleed bin Talal Al Saud")
    names = {e["name"]: e for e in got["entities"]}
    assert set(names) == {"Al Waleed bin Talal Al Saud", "Talal bin Abdulaziz Al Saud", "Prince Alwaleed", "Issy Sharp"}
    assert names["Al Waleed bin Talal Al Saud"]["id"] == ME and names["Prince Alwaleed"]["id"] is None


def test_the_command_reads_through_the_manager_and_refuses_a_foreign_archive(arch, tmp_path):
    ns = lambda **k: type("A", (), {"cmd": "entitytrace", "record": None, "name": None, "schema": None, "source": None, "title": "", "json": True, **k})()
    ok = apollo.entitytrace_command(ns(archive=str(arch), record=ME))
    assert ok["ok"] and ok["lines"]
    assert apollo.entitytrace_command(ns(archive=str(arch), source="alwaleed", title="Al Waleed bin Talal Al Saud"))["entities"]
    assert apollo.entitytrace_command(ns(archive=str(arch), record="nope"))["ok"] is False
    assert apollo.entitytrace_command(ns(archive=str(tmp_path), record=ME))["ok"] is False


def test_the_history_index_is_built_once_then_extended_by_the_new_snapshots_only(arch, monkeypatch):
    first = entitytrace.trace(str(arch), ME)
    f = entitytrace._cache_dir(arch.resolve()) / "index.json"
    assert f.is_file() and json.loads(f.read_text(encoding="utf-8"))["recs"][ME]
    entitytrace._IDX.clear()                                     # a restarted worker reads the index from disk, not the history
    with monkeypatch.context() as m:                             # never monkeypatch.undo(): it would drop the scratch state too
        m.setattr(entitytrace, "_extend", lambda *a: pytest.fail("the indexed history was read again"))
        assert entitytrace.trace(str(arch), ME)["lines"] == first["lines"]
    from hydra.archive import collections_io
    from hydra.archive.history import store
    rows = collections_io.read(collections_io.path(arch, "alwaleed-actors"))[0]
    rows[0]["properties"]["notes"] = ["A third reading."]
    write(arch, "alwaleed-actors", rows)
    snap(arch, "amend alwaleed-actors: 1 changed", "agent:hydra-mcp", "2026-11-25T16:00:00+00:00")
    read = []
    real = entitytrace._read_json
    monkeypatch.setattr(entitytrace, "_read_json", lambda p: read.append(p) or real(p))
    got = entitytrace.trace(str(arch), ME)
    assert [p.name for p in read if p.parent.name == "snapshots"] == [f"{store.head(arch)[1]}.json"]    # only the new snapshot
    assert len([ln for ln in got["lines"] if ln["verb"] == "Amended"]) == 2
    assert next(ln for ln in got["lines"] if ln["verb"] == "Amended")["steps"][0]["quote"] == "A third reading."


def test_a_tie_found_under_its_working_id_is_named_by_its_ends_and_a_later_flow_has_a_lane(arch):
    from hydra.archive import collections_io
    rows = collections_io.read(collections_io.path(arch, "alwaleed-material"))[0]
    rows.append(rec("emp-1", "Employment", employee=ME, employer="p-talal", sources="doc-1"))
    write(arch, "alwaleed-material", rows)
    snap(arch, "links: save employment", "agent:hydra-mcp", "2026-11-26T10:00:05+00:00")
    (arch / ".hydra" / "reminted.json").write_text(json.dumps({"prefix": "draft", "ids": {"p3-t1": "emp-1"}}), encoding="utf-8")
    from datetime import datetime
    local = lambda iso: datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)      # the log keeps the machine's local time
    with runlog.writer() as con:
        con.execute("INSERT INTO found(run_id, seq, page, schema, thing, quote, record_id, state) VALUES (?,?,?,?,?,?,?,?)",
                    [RUN, 3, 3, "Employment", "p3-t1", "He worked for his father.", None, "cited"])
        con.execute("INSERT INTO runs(id, flow, kind, source, archive, started, ended, status) VALUES (?,?,?,?,?,?,?,?)",
                    ["links-1", "links", "mcp-session", None, str(arch), local("2026-11-26T10:00:00+00:00"),
                     local("2026-11-26T10:01:00+00:00"), "done"])
        con.execute("INSERT INTO questions(run_id, id, status, kind, page, question, quote, records, answer) VALUES (?,?,?,?,?,?,?,?,?)",
                    ["links-1", "q9", "open", "merge", None, "One entity?", "", json.dumps(["draft:p-talal"]), None])
    entitytrace._RECORDS.clear()
    got = entitytrace.entities(str(arch), "alwaleed", "Al Waleed bin Talal Al Saud")
    names = [e["name"] for e in got["entities"]]
    assert "Al Waleed bin Talal Al Saud → Talal bin Abdulaziz Al Saud" in names and "p3-t1" not in names
    assert len(names) == len(set(names))                           # nothing listed twice
    lane = got["lanes"]["links-1"]
    assert lane == {"3": 1}                                         # the tie it saved, found on page 3
    assert "2026-11-25-read-alwaleed" not in got["lanes"]           # Read keeps its own Records row
    t = entitytrace.trace(str(arch), "emp-1")
    assert t["entity"]["name"] == "Al Waleed bin Talal Al Saud → Talal bin Abdulaziz Al Saud"
    assert any(ln["verb"] == "Found" for ln in t["lines"])         # the found row under its working id is this record's


# ---- a finished workflow run's page cells (entitytrace.run_pages) -----------------------------------------------------

WF = "2026-11-26-workflow-assemble"


def _local(iso):                                                    # the log and the run record keep the machine's local time
    from datetime import datetime
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _workflow(arch, collections=("alwaleed-actors",)):
    """A finished workflow run on the source 'alwaleed' (10:00:00-10:01:00 UTC) with a task created inside its window, that task's
    call log naming its collections, and a review store: two questions its task asked on page 3, one another run asked a day
    before. Returns (started, ended) as the run record keeps them."""
    for cache in (entitytrace._RUNPAGES, entitytrace._SOURCE_PAGES, entitytrace._ASKS, entitytrace._RECORDS):
        cache.clear()
    tasks = arch / ".hydra" / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "tsk-wf.md").write_text("---\nid: tsk-wf\n---\n## Log\n- 2026-11-26T10:00:05Z created (assemble)\n", encoding="utf-8")
    (tasks / "tsk-wf.calls.jsonl").write_text(
        json.dumps({"n": 1, "at": "2026-11-26T10:00:10Z", "tool": "assemble", "args": {"collections": list(collections)}}) + "\n",
        encoding="utf-8")
    review = arch / ".hydra" / "review"
    review.mkdir(parents=True, exist_ok=True)
    ask = lambda i, task, at, **kw: {"id": i, "question": "One entity?", "kind": "merge", "asked": at, "source": "draft:doc-1",
                                     "asked_by": {"actor": "agent:claude-code", "task": task, "run": "assemble"}, **kw}
    (review / "questions.yaml").write_text(yaml.safe_dump({"questions": [
        ask("ask-1", "tsk-wf", "2026-11-26T10:00:20Z", page=3, records=["draft:" + ME]),
        ask("ask-2", "tsk-wf", "2026-11-26T10:00:30Z", page=3),
        ask("ask-old", "tsk-old", "2026-11-25T09:00:00Z", page=3)]}), encoding="utf-8")
    started, ended = _local("2026-11-26T10:00:00+00:00"), _local("2026-11-26T10:01:00+00:00")
    with runlog.writer() as con:
        con.execute("INSERT INTO runs(id, flow, kind, source, archive, started, ended, status) VALUES (?,?,?,?,?,?,?,?)",
                    [WF, "assemble", "mcp-session", "alwaleed", str(arch), started, ended, "done"])
    return started, ended


def test_a_run_that_only_asked_still_draws_where_it_looked_and_where_it_asked(arch):
    started, ended = _workflow(arch)
    got = entitytrace.run_pages(WF, str(arch), "alwaleed", "Al Waleed bin Talal Al Saud", started, ended)
    assert got["cells"] == [[3, "done", 1]]                          # page 3: the one record of its collection found there
    assert got["heat"][0]["page"] == 3 and got["heat"][0]["records"] == 1
    assert got["heat"][0]["asked"] == 2 and "changed" not in got["heat"][0]      # its two questions, not the one asked the day before
    assert entitytrace.entities(str(arch), "alwaleed", "Al Waleed bin Talal Al Saud")["lanes"][WF] == {"3": 2}


def test_a_run_that_changed_records_counts_them_on_their_pages(arch):
    started, ended = _workflow(arch)
    me = {**rec(ME, "Person", "Al Waleed bin Talal Al Saud", sources="doc-1"), "properties": {
        "name": ["Al Waleed bin Talal Al Saud"], "sources": ["doc-1"], "notes": ["Founder of Kingdom Holding."]}}
    write(arch, "alwaleed-actors", [me, rec("p-talal", "Person", "Talal bin Abdulaziz Al Saud", sources="doc-1")])
    snap(arch, "assemble: notes", "agent:hydra-mcp", "2026-11-26T10:00:40+00:00")
    got = entitytrace.run_pages(WF, str(arch), "alwaleed", "Al Waleed bin Talal Al Saud", started, ended)
    row = got["heat"][0]
    assert row["changed"] == 1 and row["asked"] == 2 and row["records"] == 1
    assert got["cells"] == [[3, "done", 1]]


def test_a_run_that_named_no_collection_is_scoped_by_what_it_touched(arch):
    started, ended = _workflow(arch, collections=())
    got = entitytrace.run_pages(WF, str(arch), "alwaleed", "Al Waleed bin Talal Al Saud", started, ended)
    assert got["cells"] == [[3, "done", 1]]                          # the record its first question names, found on page 3


def test_a_finished_runs_cells_are_worked_out_once_and_a_live_run_has_none(arch, monkeypatch):
    started, ended = _workflow(arch)
    first = entitytrace.run_pages(WF, str(arch), "alwaleed", "Al Waleed bin Talal Al Saud", started, ended)
    monkeypatch.setattr(entitytrace, "_compute_run_pages", lambda *a: pytest.fail("worked out again"))
    assert entitytrace.run_pages(WF, str(arch), "alwaleed", "x", started, ended) == first            # memory
    entitytrace._RUNPAGES.clear()
    assert entitytrace.run_pages(WF, str(arch), "alwaleed", "x", started, ended) == first            # the file under state/
    assert entitytrace.run_pages(WF, str(arch), "alwaleed", "x", started, None) == {"cells": [], "heat": []}
    assert entitytrace.run_pages(WF, str(arch), None, "x", started, ended) == {"cells": [], "heat": []}


def test_a_workflow_runs_ingest_keeps_only_its_own_questions(arch):
    started, ended = _workflow(arch)
    rec_ = {"id": WF, "name": "workflow-assemble", "kind": "mcp-session", "flow": "assemble", "archive": str(arch),
            "run_folder": str(arch.parent / "run"), "started": started}
    (arch.parent / "run").mkdir(exist_ok=True)
    runlog.ingest_run(rec_, {"status": "done", "ended": ended}, final=True)
    ids = {r[0] for r in runlog.sql(f"SELECT id FROM questions WHERE run_id='{WF}'")["rows"]}
    assert ids == {"ask-1", "ask-2"}

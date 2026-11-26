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

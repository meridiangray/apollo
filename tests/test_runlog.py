"""The DuckDB run log: ingest of a fake run folder, idempotent re-ingest, clear, rebuild, and the SELECT-only guard."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import apollo
import runlog


def finished_run(waiter, smoke_config):
    rec = apollo.start(smoke_config, dry=True)
    waiter(lambda: apollo.exit_info(rec["id"]))
    waiter(lambda: not apollo.alive(rec))
    return apollo.load(rec["id"])


def one(query):
    got = runlog.sql(query)
    assert got["ok"], got
    return got["rows"]


def counts(rid):
    return {t: one(f"select count(*) from {t} where run_id='{rid}'")[0][0]
            for t in ("events", "found", "model_calls", "hydra_calls", "pages")}


def test_the_wrapper_ingests_the_run_when_it_ends(scratch, waiter, smoke_config):
    rec = finished_run(waiter, smoke_config)
    rid = rec["id"]
    assert one(f"select status, dry, kind, source is null from runs where id='{rid}'")[0][:3] == ["done", True, "pageflow"]
    assert one(f"select config_yaml from runs where id='{rid}'")[0][0]
    c = counts(rid)
    assert c["found"] == 2 and c["model_calls"] == 1 and c["hydra_calls"] == 3 and c["events"] >= 3 and c["pages"] == 2
    assert one(f"select count(*) from hydra_calls where run_id='{rid}' and not ok")[0][0] == 1
    assert one(f"select tool from hydra_calls where run_id='{rid}' order by ms desc limit 1")[0][0] == "save"
    assert one(f"select page, schema, thing from found where run_id='{rid}' order by page")[0] == [20, "Person", "Person 20"]
    assert one(f"select label, page from model_calls where run_id='{rid}'")[0] == ["A p20", 20]


def test_reingest_changes_nothing_and_a_grown_file_adds_only_its_new_rows(scratch, waiter, smoke_config):
    rec = finished_run(waiter, smoke_config)
    before = counts(rec["id"])
    status = {"status": "done", "detail": "done", "ended": None, "exit_code": 0}
    runlog.ingest_run(rec, status)
    runlog.ingest_run(rec, status)
    assert counts(rec["id"]) == before
    assert one(f"select count(*) from runs where id='{rec['id']}'")[0][0] == 1
    found = Path(rec["run_folder"]) / "pageflow" / "found.jsonl"
    with found.open("a") as f:
        f.write(json.dumps({"page": 22, "schema": "Place", "thing": "Haifa", "quote": "q", "state": "proposed"}) + "\n")
        f.write('{"page": 23, "sch')                                   # a half line waits for its newline
    added = runlog.ingest_run(rec, status)
    assert added["found"] == 1 and counts(rec["id"])["found"] == before["found"] + 1
    runlog.ingest_run(rec, status)
    assert counts(rec["id"])["found"] == before["found"] + 1


def test_a_replaced_file_is_read_again_from_its_start(scratch, waiter, smoke_config):
    rec = finished_run(waiter, smoke_config)
    found = Path(rec["run_folder"]) / "pageflow" / "found.jsonl"
    found.write_text(json.dumps({"page": 30, "schema": "Person", "thing": "Only", "quote": "q", "state": "cited"}) + "\n")
    runlog.ingest_run(rec, {"status": "done"})
    assert one(f"select thing from found where run_id='{rec['id']}'") == [["Only"]]


def test_clear_removes_the_runs_rows(scratch, waiter, smoke_config):
    rec = finished_run(waiter, smoke_config)
    assert apollo.clear(rec["id"])["ok"]
    assert one("select count(*) from runs")[0][0] == 0
    assert all(v == 0 for v in counts(rec["id"]).values())


def test_rebuild_ingests_every_run_folder_still_on_disk(scratch, waiter, smoke_config):
    rec = finished_run(waiter, smoke_config)
    runlog.db_path().unlink()
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "logdb", "--rebuild"], capture_output=True, text=True,
                       timeout=60, env=os.environ.copy())
    out = json.loads(p.stdout)
    assert out["ok"] and out["runs"] == 1
    assert counts(rec["id"])["found"] == 2
    assert one(f"select status from runs where id='{rec['id']}'")[0][0] == "done"


def test_tool_runs_and_a_collection_are_logged(scratch):
    t = apollo.state_dir() / "tools" / "pollard"
    t.mkdir(parents=True)
    (t / "text.json").write_text(json.dumps({"tool": "text", "key": "pollard", "status": "done", "started": time.time() - 3,
                                             "ended": time.time(), "done": 2, "total": 2, "source": "pollard",
                                             "pages": [{"page": 1, "text": "x"}, {"page": 2, "text": "y"}]}))
    assert runlog.ingest_tools() == 1 and runlog.ingest_tools() == 1
    assert one("select tool, key, status, done, total from tool_runs") == [["text", "pollard", "done", 2, 2]]
    assert json.loads(one("select detail from tool_runs")[0][0])["pages_count"] == 2


def test_only_select_and_with_get_through(scratch):
    runlog.ingest_tools()
    for bad in ("insert into runs(id) values ('x')", "drop table runs", "delete from runs", "select 1; drop table runs",
                "update runs set id='x'", "create table t as select 1", "attach 'x.db'", "copy runs to '/tmp/x.csv'",
                "pragma database_list", "", "  ", "with x as (select 1) delete from runs", "-- a comment\ndrop table runs"):
        got = runlog.sql(bad)
        assert got["ok"] is False and got["why"], bad
    assert runlog.sql("  -- fine\n select 1 as one ; ")["rows"] == [[1]]
    assert runlog.sql("with a as (select 2 as two) select two from a")["rows"] == [[2]]
    assert runlog.sql("select 'drop table x;' as s")["rows"] == [["drop table x;"]]
    assert one("select count(*) from runs")[0][0] == 0                          # nothing above changed it


def test_a_query_cannot_read_files_and_the_rows_are_capped(scratch):
    got = runlog.sql("select * from read_text('/etc/hostname')")
    assert got["ok"] is False
    big = runlog.sql("select * from range(5000)", limit=10)
    assert len(big["rows"]) == 10 and big["truncated"] is True and big["columns"] == ["range"]


def test_logdb_command_and_base64_query(scratch):
    q = "select 40 + 2 as answer"
    import base64
    b = base64.urlsafe_b64encode(q.encode()).decode().rstrip("=")
    for args in (["--sql", q], ["--sql64", b]):
        p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "logdb", *args], capture_output=True, text=True,
                           timeout=60, env=os.environ.copy())
        assert json.loads(p.stdout)["rows"] == [[42]]
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "logdb", "--sql", "drop table runs"], capture_output=True,
                       text=True, timeout=60, env=os.environ.copy())
    assert p.returncode == 2 and json.loads(p.stdout)["ok"] is False


def test_the_worker_reads_while_a_writer_waits_its_turn(scratch):
    runlog.ingest_tools()
    with runlog.writer() as con:                                # a writer holds the file: a reader waits, then says it is busy
        con.execute("select 1")
        monkey = runlog.READ_WAIT
        runlog.READ_WAIT = 0.3
        try:
            got = runlog.sql("select 1")
        finally:
            runlog.READ_WAIT = monkey
    assert got["ok"] is False and "busy" in got["why"]
    assert runlog.sql("select 1")["ok"]


def test_the_log_is_whole_by_the_time_the_run_reads_as_ended(scratch, waiter, smoke_config):
    rec = apollo.start(smoke_config, dry=True)
    waiter(lambda: apollo.exit_info(rec["id"]))
    assert one(f"select status, exit_code from runs where id='{rec['id']}'")[0] == ["done", 0]
    assert counts(rec["id"])["found"] == 2

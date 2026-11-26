"""The app's pure parts: reading a run's files a piece at a time, the transcript, the archive's tasks and questions, and
the lines each tab shows. No window is opened."""
import json
import time

import app
import drysession
import apollo
import runfiles


def write_lines(path, rows, end="\n"):
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows[:-1]) + json.dumps(rows[-1]) + end)


def test_tail_reads_only_whole_new_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    t = runfiles.Tail(p)
    assert t.read() == []                                                  # no file yet
    write_lines(p, [{"a": 1}, {"a": 2}], end="")                           # the last line is still being written
    assert t.read() == [{"a": 1}]
    with p.open("a") as f:
        f.write("\nnot json\n")
    assert t.read() == [{"a": 2}] and t.all() == [{"a": 1}, {"a": 2}]
    p.write_text(json.dumps({"b": 1}) + "\n")                              # replaced by a shorter file
    assert t.read() == [{"b": 1}]


def test_a_session_transcript_gives_rows_calls_cost_and_its_archive():
    s = runfiles.Session()
    evs = drysession.events("# Brief\n", "claude-sonnet-5-5")
    evs[2]["message"]["content"][0]["input"]["archive"] = "/a/b"
    s.feed(evs[:5], now=100.0)
    assert s.calls[0]["tool"] == "guide" and s.calls[0]["ok"] is True and s.calls[0]["ms"] is None   # one read
    s.feed(evs[5:], now=101.5)
    assert s.archive == "/a/b" and s.cost == 0.0 and s.turns == 8 and s.model == "claude-sonnet-5-5"
    assert [c["ok"] for c in s.calls] == [True, True, True, False, True, True]
    assert s.calls[3]["error"] == "dry run: no source here"
    assert s.rows[-1]["kind"] == "end" and any(r["verb"] == "Failed" for r in s.rows)


def test_run_lines_newest_first_with_repeats_folded():
    rows = [{"t": i, "at": f"10:00:0{i}", "verb": "Called", "object": f"id={i}", "descriptor": "save", "who": "agent",
             "kind": "info"} for i in range(3)]
    rows.append({"t": 4, "at": "10:00:04", "verb": "Failed", "object": "boom", "descriptor": "cite", "who": "agent",
                 "kind": "fail"})
    got = runfiles.run_lines(rows)
    assert got[0] == ("10:00:04", "Failed boom  cite  [agent]", "fail")
    assert got[1][1].startswith("Called ×3 id=2  save")
    assert runfiles.tail_lines(rows, 1) == ["10:00:04  Failed boom  cite  [agent]"]


def test_found_lines_one_per_record_later_state_wins():
    rows = [{"page": 20, "schema": "Person", "thing": "Ann", "quote": "Ann came.", "state": "proposed"},
            {"page": 21, "schema": "Person", "thing": "Ann", "state": "cited"},
            {"page": 20, "schema": "Event", "thing": "Meeting", "state": "proposed"}]
    lines, count = runfiles.found_lines(rows, quotes=True)
    text = ["".join(t for t, _ in ln) for ln in lines]
    assert text[0] == "p. 20  ·  2 found, 1 cited"
    assert any(t.strip().startswith("● Person") and t.endswith("Ann") for t in text)
    assert any("“Ann came.”" in t for t in text) and count.startswith("2 records on 1 pages")


def test_calls_mark_the_slowest_and_errors():
    calls = [{"at": "1", "tool": "guide", "ms": 10, "ok": True}, {"at": "2", "tool": "save", "ms": 9000, "ok": True},
             {"at": "3", "tool": "cite", "ms": 50, "ok": False, "error": "no quote"},
             {"at": "4", "tool": "tasks", "ms": None, "ok": None}]
    rows = runfiles.call_rows(calls, slowest=1)
    assert [r["slow"] for r in rows] == [False, True, False, False]
    assert runfiles.call_line(rows[2]).endswith("ERROR no quote") and "… ms" in runfiles.call_line(rows[3])
    assert runfiles.calls_head(calls) == "4 calls · 1 errors · slowest save 9000 ms"


def test_questions_tasks_and_calls_from_an_archive(tmp_path):
    a = tmp_path / "arch"
    (a / ".hydra" / "review").mkdir(parents=True)
    (a / ".hydra" / "tasks").mkdir(parents=True)
    (a / ".hydra" / "review" / "questions.yaml").write_text(
        "questions:\n- id: ask-000000000001\n  question: Same person?\n  kind: same-name\n  page: 23\n"
        "  quote: Ann met Ann.\n  options: [One, Two]\n- id: ask-000000000002\n  question: Which year?\n"
        "  answer: {choice: '1985'}\n", encoding="utf-8")
    (a / ".hydra" / "tasks" / "tsk-1.md").write_text(
        "---\nid: tsk-1\ntitle: Page flow\nstatus: checked-out\n---\n## A\n- [x] A1 Read => ok\n- [-] A2 Skip => no\n"
        "- [ ] A3 Save =>\n## Log\n- 2026 created\n- 2026 step A1 -> done\n", encoding="utf-8")
    (a / ".hydra" / "tasks" / "tsk-1.calls.jsonl").write_text(
        json.dumps({"n": 1, "at": "2026-11-23T18:15:59Z", "tool": "save", "ms": 70, "ok": True}) + "\n")
    f = runfiles.RunFiles(tmp_path / "run", "mcp-session", str(a))
    assert f.tasks() == [{"id": "tsk-1", "title": "Page flow", "status": "checked-out", "holder": None, "model": None,
                          "steps_done": 2, "steps_total": 3, "last": "2026 step A1 -> done"}]
    assert f.calls() == [{"at": "18:15:59", "tool": "save", "ms": 70, "ok": True, "error": None}]
    lines = runfiles.question_lines(f.questions())
    assert lines[0] == ("[open] ask-000000000001 · same-name · p. 23", "open")
    assert ("  options: One / Two", "dim") in lines and ("  answer: 1985", "answered") in lines


def test_report_as_text():
    md = "# Report\n\nSome **bold** and `code`.\n\n## Counts\n\n| a | b |\n|---|---|\n| 1 | 2 |\n- one\n"
    got = runfiles.report_lines(md)
    assert got[0] == ("Report", "h1") and ("Some bold and code.", "") in got and ("Counts", "h2") in got
    assert ("  a  │  b", "table") in got and ("  1  │  2", "table") in got and ("• one", "") in got


def test_small_formats():
    assert runfiles.bar_text(0.62) == "██████░░░░ 62%" and runfiles.bar_text(None) == ""
    assert runfiles.elapsed_text(3725) == "1:02:05" and runfiles.elapsed_text(65) == "1:05"
    assert runfiles.cost_text({"usd": 0.4, "haiku": [37, 80], "sonnet": [1, None]}) == "$0.40 · H 37/80 · S 1/∞"
    assert runfiles.cost_text({"usd": 1.234, "turns": 3}, 5) == "$1.23 / $5"


def test_the_app_reader_sees_every_run_and_rows_read_well(scratch, smoke_config):
    rec = apollo.plan(smoke_config, name="smoke", dry=True)
    rec.update({"files": [], "started": "2026-11-24T10:00:00"})
    apollo.save(rec)
    pf = apollo.Path(rec["run_folder"]) / "pageflow"
    pf.mkdir(parents=True)
    (pf / "progress.json").write_text(json.dumps({"fraction": 0.5, "stage": "A", "label": "Pass A", "done": 1,
                                                  "total": 2, "status": "running", "elapsed": 3, "pages": {"20": "done"}}))
    (pf / "hydra.jsonl").write_text(json.dumps({"tool": "guide", "ms": 3, "ok": True}) + "\n")
    (apollo.state_dir() / f"{rec['id']}.exit.json").write_text(json.dumps(
        {"code": 0, "ended": "2026-11-24T10:01:05", "status": "done", "detail": "done"}))
    r = app.Reader()
    got = r.read()
    assert [s["id"] for s in got] == [rec["id"]]
    assert app.row_values(got[0]) == ("smoke (dry)", "pageflow", "claude-haiku-5-5", "done", "█████░░░░░ 50%", 1,
                                      "$0.00", "1:05")
    with (pf / "hydra.jsonl").open("a") as f:                              # the next read takes only what is new
        f.write(json.dumps({"tool": "save", "ms": 9, "ok": False}) + "\n")
    t = time.time()
    again = r.read()[0]
    assert again["calls"]["hydra"] == 2 and again["calls"]["errors"] == 1 and time.time() - t < 5
    assert r.files_of(rec) is r.files[rec["id"]]


def _archive_with_two_runs(tmp_path):
    import yaml
    a = tmp_path / "arc"
    (a / ".hydra" / "tasks").mkdir(parents=True)
    (a / ".hydra" / "review").mkdir(parents=True)
    rows = [("2026-11-25T20:00:10Z", "t1"), ("2026-11-25T20:30:10Z", "t2"), ("2026-11-25T20:30:20Z", "t2")]
    for n, (at, task) in enumerate(rows):
        with open(a / ".hydra" / "tasks" / f"{task}.calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"n": n, "at": at, "tool": "guide", "ms": 5, "ok": True}) + "\n")
    (a / ".hydra" / "tasks" / "t2.md").write_text("---\nid: t2\n---\n## Log\n- 2026-11-25T20:30:05Z created (links)\n", encoding="utf-8")
    (a / ".hydra" / "review" / "links.json").write_text(json.dumps({"t2": {"applied": ["merge.x"]}}), encoding="utf-8")
    qs = [{"id": "ask-1", "question": "A or B?", "options": "link / keep apart", "records": ["draft:a"], "asked": "2026-11-25T20:30:12Z"},
          {"id": "ask-2", "question": "Older", "asked": "2026-11-25T20:00:12Z"}]
    (a / ".hydra" / "review" / "questions.yaml").write_text(yaml.safe_dump({"questions": qs}), encoding="utf-8")
    return a


def test_a_run_on_a_shared_archive_counts_only_its_own_calls_questions_and_changes(tmp_path):
    a = _archive_with_two_runs(tmp_path)
    f = runfiles.RunFiles(tmp_path / "run", "mcp-session", str(a))
    assert len(f.calls()) == 3 and len(f.questions()) == 2
    f.window = (runfiles.epoch("2026-11-25T20:30:00Z"), runfiles.epoch("2026-11-25T20:31:00Z"))
    assert len(f.calls()) == 2 and [q["id"] for q in f.questions()] == ["ask-1"]
    rows = runfiles.question_rows(f.questions(), {"a": "Alpha"})["rows"]
    assert rows[0]["options"] == ["link", "keep apart"] and rows[0]["title"] == "A or B?"
    got = runfiles.workflow_changes(str(a), f.window)
    assert [c["kind"] for c in got] == ["applied", "asked"] and got[0]["ids"] == ["merge.x"]


def test_a_workflow_run_takes_its_source_from_its_name():
    rec = {"id": "x", "name": "links-tarbell-standard-oil", "kind": "mcp-session", "archive": None}
    apollo._SOURCES_MEMO.update(at=time.time(), titles={"tarbell-standard-oil": "Standard Oil", "oil": "Oil"})
    assert apollo.run_source(rec) == "tarbell-standard-oil" and apollo.source_title(rec) == "Standard Oil"
    assert apollo.run_source({**rec, "name": "links-other"}) is None


def test_a_workflow_run_takes_its_source_from_the_collections_its_prompt_names(tmp_path):
    apollo._SOURCES_MEMO.update(at=time.time(), titles={"tarbell-standard-oil": "Standard Oil", "mapsaud-alwaleed": "Al Waleed"})
    (tmp_path / "prompt.md").write_text("x\nCollections (the run's own; work on their entities only): tarbell-standard-oil-actors, tarbell-standard-oil-places\n", encoding="utf-8")
    rec = {"id": "x", "name": "links-draft", "kind": "mcp-session", "archive": None, "run_folder": str(tmp_path)}
    assert apollo.run_source(rec) == "tarbell-standard-oil" and apollo.source_title(rec) == "Standard Oil"
    (tmp_path / "prompt.md").write_text("Collections (the run's own; work on their entities only): tarbell-standard-oil-actors, mapsaud-alwaleed\n", encoding="utf-8")
    assert apollo.run_source(rec) is None
    assert apollo.scope_source(["tarbell-standard-oil-actors"]) == "tarbell-standard-oil" and apollo.scope_source([]) is None


def test_a_question_another_run_asked_first_still_shows_in_the_run_whose_sweep_reports_it(tmp_path):
    a = _archive_with_two_runs(tmp_path)                                    # ask-2 was asked at 20:00, before this run's window
    run = tmp_path / "run"
    run.mkdir()
    sweep = json.dumps({"asked": {"r1|wikidata-find": "ask-1", "r2|resolve.x": "ask-2", "r3|resolve.x": "ask-2"}})
    ev = {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": sweep}]}]}}
    (run / "transcript.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8")
    f = runfiles.RunFiles(run, "mcp-session", str(a))
    f.window = (runfiles.epoch("2026-11-25T20:30:00Z"), runfiles.epoch("2026-11-25T20:31:00Z"))
    assert sorted(q["id"] for q in f.questions()) == ["ask-1", "ask-2"]
    got = runfiles.workflow_changes(str(a), f.window, f.swept())
    assert got[-1]["kind"] == "asked" and sorted(got[-1]["ids"]) == ["ask-1", "ask-2"]

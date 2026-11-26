"""runview: the Now sentence, the live Flow card, Found by entity, what a log row unfolds to, the Raw tab."""
import json

import runview
import runfiles


def trace_rows():
    return [
        {"t": 0, "at": "10:00:00", "stage": "preflight", "verb": "Ran", "object": "Preflight", "descriptor": "Pass", "who": "driver", "kind": "phase"},
        {"t": 5, "at": "10:00:05", "stage": "A", "verb": "Ran", "object": "Pass A", "descriptor": "Pass", "who": "driver", "kind": "phase"},
        {"t": 6, "at": "10:00:06", "stage": "A", "verb": "Asked", "object": "A", "descriptor": "Page", "who": "haiku", "kind": "info", "page": 3},
        {"t": 7, "at": "10:00:07", "stage": "A", "verb": "Found", "object": "2 mentions", "descriptor": "Names", "who": "haiku", "kind": "info", "page": 3},
        {"t": 8, "at": "10:00:08", "stage": "A", "verb": "Asked", "object": "A", "descriptor": "Page", "who": "haiku", "kind": "fail", "page": 4,
         "detail": "quote not found; name empty"},
        {"t": 9, "at": "10:00:09", "stage": "A", "verb": "Escalated", "object": "A  (2 problems)", "descriptor": "Page", "who": "driver", "kind": "escalated", "page": 4},
        {"t": 20, "at": "10:00:20", "stage": "B", "verb": "Added", "object": "Pollard", "descriptor": "Records", "who": "hydra", "kind": "info", "n": 2, "more": 1},
        {"t": 21, "at": "10:00:21", "stage": "B", "verb": "Cited", "object": "actors", "descriptor": "Quote", "who": "hydra", "kind": "info", "n": 2},
    ]


FOUND = [
    {"page": 3, "schema": "Person", "thing": "Pollard", "quote": "Jonathan Pollard was an analyst.", "state": "proposed"},
    {"page": 3, "schema": "PublicBody", "thing": "Navy", "quote": "He worked for the Navy.", "state": "proposed"},
    {"page": 5, "schema": "Person", "thing": "Pollard", "quote": "Pollard was arrested.", "state": "proposed"},
    {"page": 3, "schema": "Person", "thing": "Pollard", "quote": "Jonathan Pollard was an analyst.", "state": "cited"},
    {"page": 3, "schema": "PublicBody", "thing": "Navy", "quote": "He worked for the Navy.", "state": "cited"},
]
MODELS = [{"n": 1, "model": "haiku", "label": "A p3", "ms": 900, "ok": True, "answer": "{\"mentions\": []}", "prompt_chars": 40},
          {"n": 2, "model": "haiku", "label": "A p4", "ms": 700, "ok": True, "answer": "{}", "prompt_chars": 41}]


def test_found_by_entity_groups_one_row_per_thing_and_marks_cited():
    out = runview.found_entities(FOUND)
    assert out["entity_count"] == 2
    pollard = next(e for e in out["entities"] if e["thing"] == "Pollard")
    assert pollard["records"] == 2 and pollard["pages"] == [3, 5] and pollard["cited"] and pollard["group"] == "people"
    assert [t["label"] for t in out["types"]] == ["People", "Bodies"]


def test_trace_links_found_added_cited_and_asked():
    tr = trace_rows()
    links = runview.trace_links(tr, FOUND, MODELS)
    assert links[3]["recs"] == [0, 1]                   # the two proposed records of page 3
    assert links[6]["recs"] == [3, 4] and links[7]["recs"] == [3, 4]
    assert links[2]["call"] == 1 and links[4]["call"] == 2


def test_run_rows_carry_the_unfolding_fields_and_detail_has_the_answer():
    tr = trace_rows()
    links = runview.trace_links(tr, FOUND, MODELS)
    rows = runview.run_rows_linked(tr, links, FOUND)
    by_i = {r["i"]: r for r in rows}
    assert by_i[3]["open"] and by_i[3]["nrecs"] == 2 and by_i[3]["schema"] in ("Person", "PublicBody")
    assert by_i[2]["call"] == 1
    d = runview.row_detail(2, tr, FOUND, MODELS, links, {})
    assert d["call"]["answer"] == "{\"mentions\": []}" and d["call"]["ms"] == 900
    esc = runview.row_detail(5, tr, FOUND, MODELS, links, {})
    assert esc["problems"] == ["quote not found", "name empty"]
    phase = runview.row_detail(1, tr, FOUND, MODELS, links, {})
    assert "asked a model 2 times" in phase["did"].lower()


def test_now_line_running_finished_and_stopped():
    prog = {"stage": "C", "pages": {"40": "doing", "41": "doing", "42": "todo", "43": "done"}, "label": "Pass C"}
    s = runview.now_line("pageflow", "running", "", prog, {"profile": {"pages": 107}}, 312, 4, 100, None)
    assert s.startswith("Writing the records and ties for pages 40–41 of 4, four at a time.") and "312 records so far" in s
    done = runview.now_line("pageflow", "done", "done", prog, {}, 627, 4, 4320, None, pages_done=66)
    assert done == "Finished: 627 records from 66 parts in 72 min." or done.startswith("Finished: 627 records from 66")
    st = {"models": {"used": {"haiku": 76}, "budgets": {"haiku": 76, "sonnet": 3}}, "profile": {"pages": None}}
    stopped = runview.now_line("pageflow", "stopped", "stopped-budget", prog, st, 10, 4, 10, 9)
    assert stopped == "Stopped: the Haiku safety limit on model calls was reached on page 9."


def test_flow_steps_mark_done_now_next_and_skipped():
    steps = [{"label": "Preflight", "who": "code"}, {"label": "Names", "who": "haiku"}, {"label": "Records and ties", "who": "haiku"},
             {"label": "Lag check", "who": "haiku", "on": False}, {"label": "Close", "who": "code"}]
    rt = {"nodes": [{"key": "preflight", "kind": "pass", "state": "done"}, {"key": "A", "kind": "pass", "state": "now"},
                    {"key": "chunk1", "kind": "chunk", "state": "todo"}, {"key": "close", "kind": "pass", "state": "todo"}]}
    out = {s["label"]: s["state"] for s in runview.flow_steps(steps, rt, [], {}, False, {})}
    assert out == {"Preflight": "done", "Names": "now", "Records and ties": "next", "Lag check": "skipped", "Close": "next"}


def test_config_rows_are_plain_labels():
    cfg = {"recipe": "full", "source": {"title": "Al Waleed"}, "pages": {"from": 1, "to": 9}, "models": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"},
           "budgets": {"haiku": 76, "sonnet": 3, "escalations": 3}, "parallel": 4, "flush_pages": 10, "stub_models": True}
    rows = {r["label"]: r["value"] for r in runview.config_rows(cfg, {}, {})}
    assert rows["Reader"] == "Haiku 5.5" and rows["Haiku agents"] == "4" and rows["Dry"] == "Yes" and rows["Source"] == "Al Waleed" and rows["Runner"] == "Claude Code"
    arch = runview.session_config_rows({"workflow": "stubs", "model": "claude-sonnet-5-5", "archive": "/x/archives/2026-11-25-dry-full-alwaleed/alwaleed", "dry": True})
    assert {"label": "Archive", "value": "alwaleed · 2026-11-25-dry-full-alwaleed", "title": "/x/archives/2026-11-25-dry-full-alwaleed/alwaleed"} in arch
    assert arch[-1] == {"label": "Dry", "value": "Yes"} and arch[0]["value"] == "Stubs"


def test_raw_view_reads_the_files(tmp_path):
    pf = tmp_path / "pageflow"
    pf.mkdir()
    (pf / "models.jsonl").write_text(json.dumps(MODELS[0]) + "\n", encoding="utf-8")
    (pf / "hydra.jsonl").write_text(json.dumps({"at": "10:00:00", "tool": "save", "ms": 5, "ok": True, "args": {"collection": "actors"}}) + "\n", encoding="utf-8")
    (tmp_path / "driver.log").write_text("line one\nline two\n", encoding="utf-8")
    files = runfiles.RunFiles(tmp_path, "pageflow")
    raw = runview.raw_view(files, "pageflow")
    assert raw["driver"] == ["line one", "line two"]
    assert raw["hydra"][0]["tool"] == "save" and "actors" in raw["hydra"][0]["args"]
    assert raw["models"][0]["answer_chars"] == len(MODELS[0]["answer"]) and "answer" not in raw["models"][0]


def test_session_transcript_rows():
    events = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."},
                                                           {"type": "tool_use", "name": "mcp__hydra__guide", "input": {"topic": "start"}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok text"}]}}]
    rows = runview.transcript_rows(events)
    assert rows[0]["type"] == "call" and "guide" in rows[0]["text"] and rows[1]["type"] == "back"


def test_a_given_up_page_is_said_in_plain_words_and_a_dollar_stop_says_the_cost_cap_was_reached():
    state = {"pages": {"41": {"status": "done"}, "42": {"status": "gave-up", "gave_up": "it reached 14 model calls"}},
             "stop": "spent $1.02 of $1"}
    assert runview.gave_up_text(state) == "gave up on p. 42"
    live = runview.now_line("pageflow", "running", "", {"stage": "A", "pages": {"41": "done", "42": "gave-up"}}, state, 3, 4, 60, 42)
    assert "Gave up on p. 42." in live
    assert "the cost cap was reached, so it wrote the report" in runview.now_line("pageflow", "stopped", "stopped-budget", {}, state, 3, 4, 60, 42)
    rows = runfiles.heat({"pages": {"42": "gave-up"}}, state, [])
    assert rows[0]["status"] == "gave-up" and rows[0]["reason"] == "gave up: it reached 14 model calls"
    flow = runview.flow_steps([{"label": "Hard pages"}], {"nodes": []}, [], state, False, {})
    assert flow[0]["detail"] == "gave up on p. 42"


def test_rows_with_no_linked_records_take_their_subjects_type():
    found = [{"page": 7, "schema": "Person", "thing": "A"}, {"page": 7, "schema": "Person", "thing": "B"}, {"page": 7, "schema": "Place", "thing": "C"}]
    tr = [{"at": "10:00:00", "verb": v, "object": o, "descriptor": d, "who": "haiku", "kind": k, "page": p}
          for v, o, d, k, p in [("Cited", "bodies", "Quote", "info", None), ("Placed", "Cairo", "OSM", "info", None),
                                ("Asked", "roster: 3 short forms", "Identity", "info", None), ("Escalated", "C  (1 problems)", "Page", "escalated", 7),
                                ("Asked", "A", "Page", "info", 7), ("Added", "places", "Collection", "info", None),
                                ("Ran", "triage", "Triage", "info", None)]]
    rows = runview.run_rows_linked(tr, runview.trace_links(tr, found, []), found)
    got = {(r["verb"], r["descriptor"]): r["schema"] for r in rows}
    assert got == {("Cited", "Quote"): "PublicBody", ("Placed", "OSM"): "Place", ("Asked", "Identity"): "Person",
                   ("Escalated", "Page"): "Person", ("Asked", "Page"): None, ("Added", "Collection"): "Place", ("Ran", "Triage"): None}


def test_raw_view_of_a_session_carries_the_call_notes(tmp_path):
    a = tmp_path / "arch"
    (a / "collections").mkdir(parents=True)
    (a / ".hydra" / "tasks").mkdir(parents=True)
    (a / "collections" / "c1.json").write_text(json.dumps({"records": [
        {"id": "emp-1", "schema": "Employment", "properties": {"name": ["Ann at Acme"]}}]}), encoding="utf-8")
    note = {"did": "Saved 1 record in c1", "counts": {"records": 1}, "refs": ["emp-1", "emp-2"], "refs_more": 3, "step": "S2 Save",
            "lint": [{"rule": "date-order", "id": "emp-1", "say": "starts after it ends."}]}
    rows = [{"n": 1, "at": "2026-11-26T10:00:00Z", "tool": "save", "args": {"c": 1}, "ok": True, "ms": 5, "step": "S2 Save", "note": note},
            {"n": 2, "at": "2026-11-26T10:00:01Z", "tool": "tasks", "args": {}, "ok": True, "ms": 1, "step": None}]
    (a / ".hydra" / "tasks" / "t.calls.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    files = runfiles.RunFiles(tmp_path / "run", "mcp-session", str(a))
    raw = runview.raw_view(files, "mcp-session")
    first, second = raw["hydra"]
    assert raw["archive"] == str(a) and first["step"] == "S2 Save" and first["note"]["did"] == "Saved 1 record in c1"
    assert first["note"]["recs"] == {"emp-1": {"name": "Ann at Acme", "col": "c1", "schema": "Employment"}}
    assert first["note"]["lint"][0]["say"] == "starts after it ends." and first["note"]["refs_more"] == 3
    assert second["tool"] == "tasks" and "note" not in second and second["step"] is None

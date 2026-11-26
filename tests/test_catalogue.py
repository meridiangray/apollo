"""Catalogue (the library record of a source), Action History, and hiding a built-in source: guards, the job with a fake `claude`
and `pdftoppm`, the check, the exports, the log rows, and that the page flow's profiles are never written."""
import json
import stat
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import apollo
import catalogue
import history
import proofread
import runlog
import sourcedrop
import toolargs

ITEMS = [{"key": "title", "kind": "title", "value": "A Damage Assessment", "page": 1},
         {"key": "title", "kind": "responsibility", "value": "Central Intelligence Agency", "page": 1},
         {"key": "author", "kind": "corporate", "value": "Central Intelligence Agency", "page": 1},
         {"key": "imprint", "kind": "date", "value": "October 1987", "page": 2},
         {"key": "extent", "kind": "", "value": "165 pages", "page": 2},
         {"key": "identifier", "kind": "MORI DocID", "value": "1346933", "page": 1},
         {"key": "language", "kind": "", "value": "English", "page": 2},
         {"key": "summary", "kind": "", "value": "A report.", "page": 2},
         {"key": "subject", "kind": "610", "value": "United States. Central Intelligence Agency", "page": 1},
         {"key": "bogus", "kind": "", "value": "dropped: unknown key", "page": 1},
         {"key": "note", "kind": "", "value": "dropped: a page that was not sent", "page": 9},
         {"key": "note", "kind": "", "value": "", "page": 1}]
RESULT = {"type": "result", "is_error": False, "usage": {"output_tokens": 7, "input_tokens": 100}, "total_cost_usd": 0.002,
          "structured_output": {"fields": ITEMS}}
PROSE = "The Harbour Board met on 4 May 1971 and appointed Ana Reyes as secretary. " * 80


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture(autouse=True)
def sources(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    seen = tmp_path / "stdin.txt"
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(RESULT))
    monkeypatch.setenv("APOLLO_CLAUDE", str(script(tmp_path / "claude", f"cat >> {seen}\ncat {answer}\n")))
    return seen


@pytest.fixture
def text_source(tmp_path):
    f = tmp_path / "in" / "Harbour minutes.txt"
    f.parent.mkdir()
    f.write_text(PROSE, encoding="utf-8")
    return sourcedrop.add(f)["slug"]


def test_clean_keeps_only_cited_known_fields():
    got = catalogue.clean({"fields": ITEMS}, [1, 2])
    assert [i["key"] for i in got] == ["title", "title", "author", "imprint", "extent", "identifier", "language", "summary", "subject"]
    assert {"key": "identifier", "kind": "MORI DocID", "value": "1346933", "page": 1} in got
    assert all(i["page"] in (1, 2) and i["value"] for i in got)


def test_job_for_a_text_source_writes_pending_then_accept_and_exports(text_source, fake_claude):
    assert catalogue.get(text_source)["available"] is True
    assert catalogue.run(text_source, "haiku") == 0
    got = catalogue.get(text_source)
    assert got["job"]["status"] == "done" and got["accepted"] is None
    assert got["pending"]["model"] == "claude-haiku-5-5" and got["pending"]["pages"][0] == 1
    assert "Opening text" in fake_claude.read_text() and "Harbour Board" in fake_claude.read_text()
    assert catalogue.export(text_source, "csl")["ok"] is False                      # nothing accepted yet
    assert catalogue.act(text_source, "accept")["ok"]
    got = catalogue.get(text_source)
    assert got["pending"] is None and got["accepted"]["record"] and got["accepted"]["accepted"]
    assert (catalogue.folder() / f"{text_source}.json").is_file()
    xml = catalogue.export(text_source, "marcxml")
    root = ET.fromstring(xml["text"])
    ns = {"m": "http://www.loc.gov/MARC21/slim"}
    tags = [d.get("tag") for d in root.findall("m:datafield", ns)]
    assert "245" in tags and "110" in tags and "024" in tags and "610" in tags
    ident = next(d for d in root.findall("m:datafield", ns) if d.get("tag") == "024")
    assert [s.text for s in ident] == ["1346933", "MORI DocID"]
    csl = json.loads(catalogue.export(text_source, "csl")["text"])[0]
    assert csl["title"] == "A Damage Assessment" and csl["number"] == "MORI DocID 1346933" and csl["number-of-pages"] == "165"
    assert catalogue.act(text_source, "revert")["ok"] and catalogue.get(text_source)["accepted"] is None


def test_the_call_and_the_actions_are_logged(text_source, fake_claude):
    catalogue.run(text_source, "sonnet")
    catalogue.act(text_source, "accept")
    calls = runlog.sql("SELECT origin, model, ok, source FROM model_calls")["rows"]
    assert calls == [["catalogue", "claude-sonnet-5-5", True, text_source]]
    tools = runlog.sql("SELECT tool, status FROM tool_runs ORDER BY tool")["rows"]
    assert ["catalogue", "done"] in tools and ["catalogue-act", "accept"] in tools
    rows = history.rows()["rows"]
    assert {r["what"] for r in rows} >= {"Catalogue", "Catalogue accepted"}
    assert history.rows("toast")["rows"] == []


def test_check_lists_the_differences_with_what_apollo_holds(text_source):
    items = [{"key": "title", "kind": "title", "value": "Harbour minutes", "page": 1},
             {"key": "extent", "kind": "", "value": "5 pages", "page": 1},
             {"key": "language", "kind": "", "value": "English", "page": 1}]
    rows = {r["field"]: r for r in catalogue.check(text_source, items)}
    assert rows["Title"]["differs"] is False
    assert rows["Pages"]["held"] == 2 and rows["Pages"]["record"] == 5 and rows["Pages"]["differs"] is True
    assert rows["Language"]["held"] is None and rows["Language"]["differs"] is False


def test_failures_say_why_and_write_no_record(text_source, tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_CLAUDE", str(script(tmp_path / "bad", "cat > /dev/null\necho nothing\n")))
    assert catalogue.run(text_source, "haiku") == 1
    got = catalogue.get(text_source)
    assert got["job"]["status"] == "failed" and got["pending"] is None and got["accepted"] is None
    with pytest.raises(ValueError):
        catalogue.model_key("gpt")
    with pytest.raises(ValueError):
        catalogue.begin("../x")


def test_proofread_takes_the_chosen_model(monkeypatch):
    assert proofread.tier_model(None) == proofread.model_id()
    assert proofread.tier_model("opus") == "claude-opus-5-5"


def test_hide_and_unhide_a_builtin_source_never_touches_its_profile(tmp_path):
    names = [b["slug"] for b in apollo.builtin_sources()]
    assert "pollard" in names
    p = apollo.composer().SOURCES / "pollard.yaml"
    before = p.read_bytes()
    assert apollo.set_hidden("pollard", True)["hidden"] == ["pollard"]
    assert "pollard" not in [b["slug"] for b in apollo.builtin_sources()]
    assert apollo.hidden_sources() == {"pollard"}
    apollo.set_hidden("pollard", False)
    assert "pollard" in [b["slug"] for b in apollo.builtin_sources()]
    assert p.read_bytes() == before
    with pytest.raises(ValueError):
        apollo.set_hidden("nosuch", True)
    tools = [r["what"] for r in history.rows("source")["rows"]]
    assert sorted(tools) == ["Source hidden", "Source shown"]


def test_builtin_rows_carry_what_the_card_shows():
    row = next(b for b in apollo.builtin_sources() if b["slug"] == "pollard")
    assert row["builtin"] is True and row["format"] == "pdf" and "context" not in row and row["publisher"]


def test_history_orders_newest_first_and_filters_by_kind():
    runlog.log_tool_run("notice", "error-1", {"status": "error", "started": 100.0, "ended": 100.0, "error": "a toast"})
    runlog.log_tool_run("source", "add-x", {"status": "done", "started": 200.0, "ended": 200.0, "source": "x", "detail": {"action": "add"}})
    got = history.rows()
    assert [r["kind"] for r in got["rows"]] == ["source", "toast"] and got["counts"]["toast"] == 1
    assert [r["what"] for r in history.rows("source")["rows"]] == ["Source added"]
    assert history.rows("toast")["rows"][0]["detail"] == "a toast"


def test_catalogue_takes_its_arguments_into_the_prompt_and_the_record(text_source, fake_claude):
    args = {"pages": "1-2", "fields": "dates", "note": "Dates are on the second part.", "agent": "sonnet"}
    assert catalogue.run(text_source, "sonnet", args) == 0
    sent = fake_claude.read_text()
    assert "Note from the cataloguer: Dates are on the second part." in sent and "Give only these keys: imprint." in sent
    rec = catalogue.get(text_source)["pending"]
    assert [(i["key"], i["kind"]) for i in rec["record"]] == [("imprint", "date")]
    assert rec["args"]["fields"] == "dates" and rec["pages"] == [1, 2]


def test_catalogue_without_arguments_asks_as_before(text_source, fake_claude):
    assert catalogue.run(text_source, "haiku") == 0
    assert "Note from the cataloguer" not in fake_claude.read_text() and "Give only" not in fake_claude.read_text()
    assert catalogue.get(text_source)["pending"]["args"] == toolargs.clean("catalogue", None)


def test_a_bad_catalogue_argument_starts_nothing(text_source, fake_claude):
    with pytest.raises(ValueError, match="dpi"):
        catalogue.begin(text_source, "haiku", {"dpi": 5})
    assert catalogue.get(text_source)["job"] is None


def test_the_listing_marks_each_sources_catalogue_state(text_source, fake_claude):
    assert catalogue.marks() == {}
    catalogue.run(text_source, "haiku")
    assert catalogue.marks() == {text_source: "pending"}
    catalogue.act(text_source, "accept")
    assert catalogue.marks() == {text_source: "accepted"}


def test_an_accepted_field_is_edited_in_place_or_removed(text_source, fake_claude):
    catalogue.run(text_source, "haiku")
    with pytest.raises(ValueError, match="no accepted"):
        catalogue.edit(text_source, 0, "x")
    catalogue.act(text_source, "accept")
    rec = catalogue.get(text_source)["accepted"]["record"]
    i = next(n for n, it in enumerate(rec) if it["key"] == "identifier")
    assert catalogue.edit(text_source, i, "  1346933-A ")["ok"]
    got = catalogue.get(text_source)["accepted"]
    assert got["record"][i]["value"] == "1346933-A" and got["record"][i]["kind"] == "MORI DocID" and got["edited"]
    assert catalogue.edit(text_source, i, "")["ok"] and len(catalogue.get(text_source)["accepted"]["record"]) == len(rec) - 1
    for bad in (99, -1):
        with pytest.raises(ValueError, match="no such field"):
            catalogue.edit(text_source, bad, "x")
    with pytest.raises(ValueError, match="1500"):
        catalogue.edit(text_source, 0, "x" * 1600)


def test_a_waiting_field_is_edited_before_accept_and_the_edit_is_kept(text_source, fake_claude):
    with pytest.raises(ValueError, match="nothing is waiting"):
        catalogue.edit(text_source, 0, "x", pending=True)
    catalogue.run(text_source, "haiku")
    rec = catalogue.get(text_source)["pending"]["record"]
    i = next(n for n, it in enumerate(rec) if it["key"] == "identifier")
    catalogue.mark(text_source, i, True)                       # a rejected field that is then edited is taken back in
    assert catalogue.edit(text_source, i, " 1346933-B ", pending=True)["ok"]
    got = catalogue.get(text_source)["pending"]["record"][i]
    assert got["value"] == "1346933-B" and not got.get("rejected") and got["edited"]
    assert catalogue.get(text_source).get("accepted") is None   # the waiting record changed, nothing was accepted
    catalogue.act(text_source, "accept")
    assert any(it["value"] == "1346933-B" for it in catalogue.get(text_source)["accepted"]["record"])


def test_accept_keeps_only_the_fields_not_rejected(text_source, fake_claude):
    catalogue.run(text_source, "haiku")
    rec = catalogue.get(text_source)["pending"]["record"]
    gone = [i for i, it in enumerate(rec) if it["key"] in ("identifier", "extent") or (it["key"] == "subject" and it["kind"] == "610")]
    for i in gone:
        assert catalogue.mark(text_source, i, True)["ok"]
    assert catalogue.get(text_source)["pending"]["record"][gone[0]]["rejected"] is True      # kept in the waiting record: it survives a refresh
    assert catalogue.mark(text_source, gone[0], True)["ok"] and catalogue.mark(text_source, gone[-1], False)["ok"]
    gone = gone[:-1]
    assert catalogue.act(text_source, "accept")["ok"]
    kept = catalogue.get(text_source)["accepted"]["record"]
    assert len(kept) == len(rec) - len(gone) and not any(i.get("rejected") for i in kept)
    assert not any(it["key"] in ("identifier", "extent") for it in kept) and any(it["key"] == "subject" for it in kept)


def test_accept_with_every_field_rejected_stores_nothing(text_source, fake_claude):
    catalogue.run(text_source, "haiku")
    for i in range(len(catalogue.get(text_source)["pending"]["record"])):
        catalogue.mark(text_source, i, True)
    out = catalogue.act(text_source, "accept")
    assert out["ok"] is False and "rejected" in out["why"] and catalogue.get(text_source)["accepted"] is None
    with pytest.raises(ValueError, match="no such field"):
        catalogue.mark(text_source, 99, True)


STAMP = [{"key": "access", "kind": "", "value": "TOP SECRET UMBRA", "page": 1},
         {"key": "declass", "kind": "authority", "value": "Interagency Security Classification Appeals Panel, E.O. 13526, section 5.3(b)(3)", "page": 1},
         {"key": "declass", "kind": "date", "value": "2012", "page": 1},
         {"key": "declass", "kind": "iscap", "value": "2007-010", "page": 1},
         {"key": "declass", "kind": "document", "value": "1", "page": 1},
         {"key": "declass", "kind": "bogus", "value": "kind dropped to blank", "page": 1}]


def test_the_classification_stamp_is_kept_as_release_facts(text_source, tmp_path, monkeypatch):
    answer = tmp_path / "stamp.json"
    answer.write_text(json.dumps({**RESULT, "structured_output": {"fields": STAMP}}))
    seen = tmp_path / "stdin2.txt"
    monkeypatch.setenv("APOLLO_CLAUDE", str(script(tmp_path / "claude2", f"cat >> {seen}\ncat {answer}\n")))
    assert catalogue.run(text_source, "haiku") == 0
    assert 'declass (540)' in seen.read_text() and "ISCAP case number" in seen.read_text()
    rec = catalogue.get(text_source)
    assert [f["name"] for f in rec["fields"] if f["key"] in ("access", "declass")] == ["Classification", "Declassified"]
    got = [(i["key"], i["kind"], i["value"]) for i in rec["pending"]["record"]]
    assert ("access", "", "TOP SECRET UMBRA") in got and ("declass", "iscap", "2007-010") in got and ("declass", "", "kind dropped to blank") in got
    catalogue.act(text_source, "accept")
    assert "Declassified authority: Interagency" in catalogue.export(text_source, "marcxml")["text"]
    assert "Declassified iscap: 2007-010" in catalogue.export(text_source, "csl")["text"]


def test_the_notes_field_choice_keeps_the_classification_and_its_release():
    keep = catalogue.clean({"fields": STAMP + ITEMS}, [1, 2], "notes")
    assert {i["key"] for i in keep} == {"access", "declass"}


def test_release_facts_come_out_in_reading_order():
    got = catalogue.clean({"fields": [STAMP[3], STAMP[2], STAMP[1], STAMP[4], STAMP[0]]}, [1])
    assert [(i["key"], i["kind"]) for i in got] == [("access", ""), ("declass", "authority"), ("declass", "date"), ("declass", "iscap"), ("declass", "document")]


# ---- the basic record (no model) ---------------------------------------------------------------------------------------------

STAMPED = ["TOP SECRET UMBRA\nApproved for Release 2012/03/15 : CIA-RDP80-00809A000600370058-3\n",
           "text\nSanitized Copy Approved for Release 2012/03/15 : CIA-RDP80\n", "text\nApproved For Release 2002/05/17 : CIA-RDP80\n",
           "Release Date: 15 March 2012\n", "no stamp here\n"]


def test_release_date_is_the_date_most_pages_carry_as_iso():
    assert catalogue.release_date(STAMPED) == ("2012-03-15", 1)
    assert catalogue.release_date(["Declassified on March 5, 2010", "nothing"]) == ("2010-03-05", 1)
    assert catalogue.release_date(["DECLASSIFIED UNDER AUTHORITY OF THE PANEL. E.O. 13526, SECTION 5.3(b)(3)", "30 October 1987"]) is None


def test_decap_reads_capitals_in_normal_case_and_leaves_the_rest():
    assert catalogue.decap("THE HISTORY OF THE STANDARD OIL COMPANY") == "The History of the Standard Oil Company"
    assert catalogue.decap("Ida M. Tarbell") == "Ida M. Tarbell"


@pytest.fixture
def title_page_source(tmp_path):
    f = tmp_path / "in" / "Standard Oil.txt"
    f.parent.mkdir()
    page = ("THE HISTORY OF THE STANDARD OIL COMPANY\n\nBY IDA M. TARBELL, AUTHOR OF THE LIFE OF ABRAHAM LINCOLN\n\nNEW YORK\nPublished, November 1904\n"
            "TOP SECRET\nISBN: 978-3-16-148410-0\n")
    f.write_text(page + ("Approved for Release 2012/03/15 : CIA-RDP80\n" + "The trust grew and the oil moved by rail. " * 40 + "\n\n") * 30, encoding="utf-8")
    return sourcedrop.add(f)["slug"]


def test_basic_reads_the_title_page_without_a_model(title_page_source, monkeypatch):
    monkeypatch.setenv("APOLLO_CLAUDE", "/nonexistent")
    out = catalogue.basic(title_page_source)
    assert out["written"] is True
    got = {(i["key"], i["kind"]): i["value"] for i in out["record"]}
    assert got[("title", "title")] == "The History of the Standard Oil Company"
    assert got[("author", "personal")] == "Ida M. Tarbell"
    assert got[("imprint", "date")] == "November 1904"
    assert got[("access", "")] == "TOP SECRET"
    assert got[("declass", "date")] == "2012-03-15"
    assert got[("identifier", "ISBN")] == "978-3-16-148410-0"
    pend = catalogue.pending(title_page_source)
    assert pend["tier"] == "basic" and pend["record"] == out["record"]


def test_basic_twice_keeps_the_owners_edits_and_never_touches_an_accepted_record(title_page_source):
    catalogue.basic(title_page_source)
    rec = catalogue.pending(title_page_source)["record"]
    i = next(n for n, x in enumerate(rec) if x["key"] == "author")
    catalogue.edit(title_page_source, i, "I. M. Tarbell", pending=True)
    catalogue.mark(title_page_source, next(n for n, x in enumerate(rec) if x["key"] == "access"), True)
    again = catalogue.basic(title_page_source)
    got = {(x["key"], x["kind"]): x for x in again["record"]}
    assert got[("author", "personal")]["value"] == "I. M. Tarbell" and got[("access", "")]["rejected"]
    assert len([x for x in again["record"] if x["key"] == "title"]) == 1
    assert catalogue.act(title_page_source, "accept")["ok"]
    acc = catalogue.accepted(title_page_source)
    assert catalogue.basic(title_page_source)["written"] is False
    assert catalogue.accepted(title_page_source) == acc and catalogue.pending(title_page_source) is None


def test_basic_does_not_replace_a_record_the_model_proposed(title_page_source):
    catalogue._write(catalogue.folder() / f"{title_page_source}.pending.json", {"source": title_page_source, "tier": "haiku", "record": ITEMS[:1]})
    assert catalogue.basic(title_page_source)["written"] is False
    assert catalogue.pending(title_page_source)["tier"] == "haiku"


def test_brief_draft_asks_with_the_pages_and_record_and_returns_plain_lines(text_source, monkeypatch):
    seen = {}

    def fake(prompt, key, images, label, schema=None):
        seen.update(prompt=prompt, key=key, schema=schema)
        return {"brief": "A set of minutes.\n\nThe Harbour Board, 1971.\n"}, {"model": "m", "ok": True}
    monkeypatch.setattr(catalogue, "ask_model", fake)
    catalogue._write(catalogue.folder() / f"{text_source}.pending.json", {"source": text_source, "record": [ITEMS[0]]})
    assert catalogue.brief_draft(text_source, "claude-haiku-5-5") == "A set of minutes.\nThe Harbour Board, 1971."
    assert seen["key"] == "haiku" and seen["schema"] is catalogue.BRIEF_SCHEMA
    assert "Harbour Board" in seen["prompt"] and "A Damage Assessment" in seen["prompt"]
    monkeypatch.setattr(catalogue, "ask_model", lambda *a, **k: ({"brief": " "}, {}))
    with pytest.raises(ValueError):
        catalogue.brief_draft(text_source)

"""The Questions tab's one-click answers: through hydra's own answer path (the Review page's), on a scratch archive."""
import pytest

import answering
import runfiles

hq = pytest.importorskip("hydra.review.questions")


def _archive(tmp_path, rows):
    a = tmp_path / "arc"
    (a / ".hydra" / "review").mkdir(parents=True)
    (a / "hydra.yaml").write_text("name: arc\n", encoding="utf-8")
    hq.save(a, rows)
    return a


def _row(a, qid):
    return next(r for r in runfiles.question_rows(runfiles.load_questions(a))["rows"] if r["id"] == qid)


def _asks():
    return [hq.make("Which Wikidata item is Fisk?", options=["Robert Fisk (Q310900)", "Jim Fisk (Q323705)", "None of these"],
                    kind="wikidata", asked="2026-11-26T03:00:00Z"),
            hq.make("Are these two one entity?", options=["link", "keep apart"], kind="same-name", page=12, quote="Standard Oil of Cleveland",
                    asked="2026-11-26T03:00:05Z")]


def test_a_click_answers_through_hydra_and_clear_takes_it_back(tmp_path):
    a = _archive(tmp_path, _asks())
    qid = _asks()[0]["id"]
    got = answering.answer(a, qid, "2")
    assert got["ok"] and got["said"] == "Jim Fisk (Q323705)"
    row = _row(a, qid)
    assert row["status"] == "answered" and row["answer"]["choice"] == "Jim Fisk (Q323705)"
    assert row["answer"]["by"] == "you" and row["answer"]["at"]
    assert [c["label"] for c in row["choices"]] == row["options"]
    assert answering.answer(a, qid, "clear")["ok"]
    assert _row(a, qid)["status"] == "open"


def test_dismiss_is_an_answer_with_who_and_when(tmp_path):
    a = _archive(tmp_path, _asks())
    qid = _asks()[1]["id"]
    assert answering.answer(a, qid, "dismiss")["ok"]
    row = _row(a, qid)
    assert row["status"] == "dismissed" and row["answer"]["choice"] == "Dismissed" and row["answer"]["by"] == "you"


def test_what_hydra_or_the_tab_refuses_comes_back_as_a_reason(tmp_path):
    a = _archive(tmp_path, _asks())
    qid = _asks()[0]["id"]
    assert not answering.answer(a, qid, "7")["ok"]                      # no option 7
    assert not answering.answer(a, "end-abc12345", "confirm")["ok"]     # not a question this tab answers
    assert not answering.answer(a, qid, "text:hello")["ok"]             # one click, never free text
    assert not answering.answer(tmp_path / "none", qid, "1")["ok"]      # no archive
    assert _row(a, qid)["status"] == "open"


def test_the_runs_collections_come_from_its_prompt(tmp_path):
    (tmp_path / "prompt.md").write_text("x\n\nCollections (the run's own; work on their entities only): a-actors, a-places\n", encoding="utf-8")
    assert answering.run_collections({"id": "r", "run_folder": str(tmp_path)}) == {"a-actors", "a-places"}
    assert answering.build_rows(None, {"a-actors"}) == [] and answering.build_rows(tmp_path, set()) == []


def test_the_heatmap_counts_the_questions_asked_on_each_page():
    qs = [{"id": "a", "page": 12}, {"id": "b", "page": 12}, {"id": "c", "page": 13}, {"id": "d"}]
    rows = runfiles.heat({"pages": {"12": "done", "13": "done", "14": "done"}}, {}, [], qs)
    assert [r.get("asked") for r in rows] == [2, 1, None]


def test_the_person_is_you_and_an_agent_keeps_its_role():
    assert answering.you("person") == "you" and answering.you("agent:claude-code") == "agent:claude-code" and answering.you(None) is None


def test_none_of_these_is_marked_so_the_tab_can_page_the_candidates(tmp_path):
    a = _archive(tmp_path, _asks())
    row = _row(a, _asks()[0]["id"])
    assert [c.get("none") for c in row["choices"]] == [None, None, True]


def _kept_archive(tmp_path):
    import json
    a = _archive(tmp_path, [hq.make("Which Wikidata item is Fisk?", records=["per-fisk"], options=["x", "None of these"], kind="wikidata",
                                    asked="2026-11-26T03:00:00Z"),
                            hq.make("Same?", records=["per-gould"], options=["link", "keep apart"], page=12,
                                    quote="Mr. Gould met Mr. Fisk. They were friends.", kind="same-name", asked="2026-11-26T03:00:05Z"),
                            hq.make("Which entry?", options=["a"], kind="reference", asked="2026-11-26T03:00:09Z")])
    (a / "collections").mkdir()
    recs = [{"id": "per-fisk", "schema": "Person", "properties": {"name": ["Fisk"], "notes": ["Fallback note about Fisk."]}},
            {"id": "per-gould", "schema": "Person", "properties": {"name": ["Gould"]}}]
    (a / "collections" / "actors.json").write_text(json.dumps(recs), encoding="utf-8")
    (a / ".hydra" / "sources").mkdir()
    (a / ".hydra" / "sources" / "citations.jsonl").write_text(json.dumps(
        {"record": "arc:per-fisk", "source": "arc:doc-1", "pages": "47", "quote": "Aaa bbb. Gould and Fisk had gone into the fight. Ccc ddd."}) + "\n", encoding="utf-8")
    return a


def test_a_question_carries_the_kept_words_that_name_its_entity_with_the_page(tmp_path):
    a = _kept_archive(tmp_path)
    got = {q["kind"]: q["passage"] for q in answering.attach(a, runfiles.load_questions(a))}
    assert got["wikidata"] == {"text": "Gould and Fisk had gone into the fight. Ccc ddd.", "mark": "Fisk", "page": "47"}
    assert got["same-name"]["text"] == "Mr. Gould met Mr. Fisk. They were friends."
    assert got["same-name"]["mark"] == "Gould" and got["same-name"]["page"] == "12"
    assert got["reference"] is None and answering.attach(a, [{"id": "ask-x", "question": "?"}])[0]["passage"] is None   # nothing kept: nothing shown


def test_the_notes_stand_in_when_no_citation_is_kept(tmp_path):
    a = _kept_archive(tmp_path)
    (a / ".hydra" / "sources" / "citations.jsonl").write_text("", encoding="utf-8")
    first = answering.attach(a, runfiles.load_questions(a))[0]["passage"]
    assert first == {"text": "Fallback note about Fisk.", "mark": "Fisk", "page": None}


def test_a_long_quote_is_cut_around_the_name():
    text, mark = answering._short("Lorem ipsum " * 40 + "then Fisk arrived " + "dolor sit " * 40, ["Fisk"])
    assert len(text) <= 330 and mark == "Fisk" and "Fisk" in text and text.startswith("…") and text.endswith("…")

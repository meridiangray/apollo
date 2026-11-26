"""Tool arguments: each tool's declaration, the checks, the page lists, and the sentence the panel shows."""
import pytest

import catalogue
import proofread
import toolargs


def test_every_tool_declares_its_arguments_once():
    assert set(toolargs.ARGS) == {"ocr", "text", "catalogue", "proofread", "check", "build"}
    for tool in toolargs.ARGS:
        for a in toolargs.declarations(tool):
            assert {"name", "label", "type", "default"} <= set(a) and a["type"] in ("pages", "number", "select", "switch", "text")
            assert a["label"] and " " not in a["name"]
    assert [a["name"] for a in toolargs.ARGS["catalogue"]] == ["pages", "max_pages", "dpi", "fields", "agent", "note"]
    assert [a["name"] for a in toolargs.ARGS["text"]] == ["pages"]
    assert toolargs.ARGS["check"] == [] and toolargs.ARGS["build"] == []


def test_catalogue_fields_and_agents_match_what_the_tools_know():
    decl = {a["name"]: a for a in toolargs.ARGS["catalogue"]}
    assert [c[0] for c in decl["fields"]["choices"]] == list(catalogue.GROUPS)
    assert [c[0] for c in decl["agent"]["choices"]] == list(catalogue.MODELS)
    assert [a["name"] for a in toolargs.ARGS["proofread"]] == ["pages", "agent"]
    assert "pages" in proofread.begin.__code__.co_varnames and "tier" in proofread.begin.__code__.co_varnames


def test_clean_fills_defaults_and_refuses_the_wrong():
    got = toolargs.clean("catalogue", None)
    assert got == {"pages": "1-4", "max_pages": 4, "dpi": 110, "fields": "all", "agent": "haiku", "note": ""}
    assert toolargs.clean("catalogue", {"max_pages": "6", "note": "  look  at the\nstamp "})["note"] == "look at the stamp"
    for bad in ({"max_pages": 0}, {"dpi": 900}, {"fields": "colour"}, {"agent": "gpt"}, {"pages": "4-2"}, {"pages": "x"}, {"zoom": 2}, {"note": "n" * 600}):
        with pytest.raises(ValueError):
            toolargs.clean("catalogue", bad)


def test_pages_take_ranges_lists_and_end():
    assert toolargs.pages("1-3,40,44-45") == [1, 2, 3, 40, 44, 45]
    assert toolargs.pages("") is None
    assert toolargs.pages("1,135-end", 137) == [1, 135, 136, 137]
    assert toolargs.pages("end", 9) == [9]
    assert toolargs.pages("3-20", 5) == [3, 4, 5]
    with pytest.raises(ValueError, match="page count"):
        toolargs.pages("5-end")
    assert toolargs.compact([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10"


def test_the_sentence_follows_the_arguments():
    d = toolargs.describe
    assert d("text", {"pages": "40-45"}) == "Shows the text layer of pages 40 to 45."
    assert d("text", {}) == "Shows the text layer of all pages."
    assert d("ocr", {"pages": "7", "lang": "auto", "dpi": 300}) == "Reads the words on page 7 from their picture at 300 dpi, detecting the language."
    assert d("catalogue", {}) == "Haiku reads pages 1 to 4 at 110 dpi and writes its library catalogue record, each citing its page."
    one = d("catalogue", {"pages": "1,135-end", "max_pages": 3, "agent": "opus", "fields": "identifiers", "note": "Check the stamp."})
    assert one == ("Opus reads up to 3 of pages 1 and 135 to the end at 110 dpi and writes the identifier fields of its library catalogue record, "
                   "each citing its page, and follows your note.")
    assert "first pages" not in d("catalogue", {"pages": "40-45"}) and "pages 40 to 45" in d("catalogue", {"pages": "40-45", "max_pages": 6})
    assert d("catalogue", {"pages": "2-3"}, pdf=False).startswith("Haiku reads parts 2 to 3 and")
    assert d("proofread", {"pages": "3-4", "agent": "sonnet"}).startswith("Sonnet reads pages 3 to 4 against their text")

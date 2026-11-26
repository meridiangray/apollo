"""choices.py and `apollo.py choices`: the ticks and the agent's choices, read from a run's page-flow files. The fixture is a trimmed
copy of a finished run (tests/fixtures/choices/pageflow); nothing is written to it."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import apollo
import choices
import runfiles

FIX = Path(__file__).resolve().parent / "fixtures" / "choices"


def built(tmp_path, edit=None):
    folder = tmp_path / "run"
    shutil.copytree(FIX, folder)
    if edit:
        edit(folder / "pageflow")
    return choices.build(runfiles.RunFiles(folder, "pageflow")), folder


def lines_of(out):
    return {t["step"]: t["line"] for t in out["ticks"]}


def pick(out, **want):
    return [c for c in out["choices"] if all(c[k] == v for k, v in want.items())]


def test_ticks_come_from_state_each_under_its_route_step(tmp_path):
    out, _ = built(tmp_path)
    assert [t["step"] for t in out["ticks"]] == [f"P{i}" for i in range(10)]
    keys = {t["step"]: t["key"] for t in out["ticks"]}
    assert keys["P0"] == "preflight" and keys["P3"] == "names" and keys["P4"] == "roster"
    assert {keys["P5"], keys["P6"], keys["P7"]} == {"records"} and keys["P8"] == "identity" and keys["P9"] == "close"
    assert all(t["ok"] and t["result"] and t["line"] for t in out["ticks"])


def test_a_failed_tick_keeps_its_error(tmp_path):
    def edit(pf):
        st = json.loads((pf / "state.json").read_text())
        st["ticks"][3] = {"step": "P3", "ok": False, "result": "485 mentions", "error": "hydra refused the step"}
        (pf / "state.json").write_text(json.dumps(st))
    out, _ = built(tmp_path, edit)
    bad = [t for t in out["ticks"] if not t["ok"]]
    assert [(t["step"], t["line"], t["error"]) for t in bad] == [("P3", "Failed", "hydra refused the step")]


def test_triage_lists_only_the_skipped_pages_and_counts_the_rest(tmp_path):
    out, _ = built(tmp_path)
    assert [(c["page"], c["object"], c["why"], c["who"]) for c in pick(out, tick="P2")] == [(51, "empty", "22 words < 40", "")]
    assert not [c for c in out["choices"] if c["page"] == 1]                   # out of the run's range: not a choice
    line = next(t["line"] for t in out["ticks"] if t["step"] == "P2")
    assert line.startswith("3 read") and "1 empty" in line                     # the kept pages are a count


def test_mentions_kept_and_dropped_with_quote_and_reason(tmp_path):
    out, _ = built(tmp_path)
    kept = pick(out, tick="P3", group="Kept", page=110)
    assert {"Wolf Blitzer", "Pollard"} <= {c["object"] for c in kept}
    blitzer = next(c for c in kept if c["object"] == "Wolf Blitzer")
    assert "Wolf Blitzer" in blitzer["quote"] and blitzer["why"].startswith("Person")
    dropped = pick(out, tick="P3", group="Dropped", page=110)
    assert dropped and all(c["kind"] == "Dropped" and c["why"] for c in dropped)


def test_send_backs_name_the_problem_and_the_retry_outcome(tmp_path):
    out, _ = built(tmp_path)
    a = pick(out, tick="P3", group="Returned", page=22)
    assert len(a) == 1 and a[0]["kind"] == "Refused" and a[0]["verb"] == "Returned"
    assert a[0]["why"].startswith("L2 mention") and a[0]["why"].count("\n") == 2          # one line per problem
    assert a[0]["outcome"] == "Read by Sonnet"
    c = pick(out, tick="P5", verb="Returned")
    assert len(c) == 1 and c[0]["page"] == 21 and c[0]["outcome"] == "Passed on a retry"
    t = pick(out, tick="P6", verb="Returned")
    assert len(t) == 1 and t[0]["outcome"] == "Passed on Sonnet"


def test_facts_and_ties_saved_and_refused_by_the_checks(tmp_path):
    out, _ = built(tmp_path)
    saved = pick(out, tick="P5", verb="Saved")
    assert {c["page"] for c in saved} == {38, 111} and all(c["why"] == "Event" and c["quote"] for c in saved)
    refused = pick(out, tick="P5", verb="Refused")
    assert len(refused) == 1 and refused[0]["page"] == 38 and refused[0]["why"].startswith("L2 record e4")
    ties = pick(out, tick="P6", verb="Saved")
    assert "Employment: Wolf Blitzer – The Jerusalem Post" in [c["object"] for c in ties]
    assert next(c for c in ties if c["page"] == 110)["why"] == "Employment · correspondent"
    pairs = pick(out, tick="P6", verb="Refused")
    assert [c["page"] for c in pairs] == [124] * 3 and all("pair" in c["why"] for c in pairs)
    assert all(c["object"] for c in pairs)                                        # a refused pair is named by its ends or its reason


def test_roster_verdicts_are_merged_new_or_asked(tmp_path):
    out, _ = built(tmp_path)
    same = pick(out, tick="P4", kind="Merged")
    assert len(same) == 1 and same[0]["object"] == "Pollard → Jonathan Jay Pollard" and same[0]["page"] == 20 and same[0]["why"]
    new = pick(out, tick="P4", kind="Kept")
    assert len(new) == 1 and new[0]["group"] == "New"
    asked = pick(out, tick="P4", kind="Asked")
    assert [c["object"] for c in asked] == ["NISC"] and asked[0]["why"]


def test_save_lists_only_the_unplaced_names(tmp_path):
    out, _ = built(tmp_path)
    assert not pick(out, verb="Cited") and not pick(out, verb="Placed")        # routine passes: counts in the line
    assert [c["object"] for c in pick(out, tick="P7")] == ["Georgetown"] and pick(out, tick="P7")[0]["kind"] == "Refused"
    assert next(t["line"] for t in out["ticks"] if t["step"] == "P7").endswith("1 placed")


def test_a_geo_outlier_is_a_flagged_row(tmp_path):
    def edit(pf):
        with (pf / "trace.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": 1200.0, "at": "11:52:00", "stage": "close", "verb": "Flagged", "object": "Georgetown",
                                "descriptor": "Place", "who": "hydra", "kind": "warn",
                                "detail": "Georgetown → Guyana is the only Guyana place in this source."}) + "\n")
    out, _ = built(tmp_path, edit)
    row = pick(out, tick="P7", verb="Flagged")
    assert [(c["object"], c["why"]) for c in row] == [("Georgetown", "Georgetown → Guyana is the only Guyana place in this source.")]
    assert next(t["line"] for t in out["ticks"] if t["step"] == "P7").endswith("1 flagged")


def test_who_decided_a_choice_is_the_model_or_nothing(tmp_path):
    out, _ = built(tmp_path)
    assert {c["who"] for c in pick(out, tick="P3", group="Kept")} == {"sonnet"}        # the fixture's page 110 was read by Sonnet
    assert lines_of(out)["P0"] == "7 checks"
    assert {c["who"] for c in pick(out, tick="P4")} == {"sonnet"}
    assert {c["who"] for c in out["choices"] if c["tick"] in ("P2", "P9") or c["verb"] in ("Returned", "Refused", "Unplaced")} == {""}
    assert {c["who"] for c in pick(out, tick="P8")} == {"haiku"}


def test_tick_lines_are_plain_words_with_no_dicts_or_paths(tmp_path):
    out, _ = built(tmp_path)
    lines = {t["step"]: t for t in out["ticks"]}
    assert lines["P1"]["line"] == "pollard-da" and lines["P9"]["line"] == "1 finding"
    assert lines["P3"]["line"].split(" · ")[0].endswith("names")
    for t in out["ticks"]:
        for text in (t["line"], t["detail"], t["result"]):
            assert "{" not in text and "[" not in text and "/mnt" not in text and "'" not in text, text
    assert lines["P2"]["detail"] == "" and lines["P4"]["detail"] == ""         # the line says all there is


def test_islands_joined_or_left(tmp_path):
    out, _ = built(tmp_path)
    joined = pick(out, tick="P8", verb="Joined")
    assert [(c["object"], c["page"], c["kind"]) for c in joined] == [("Middle Eastern Warship Analyst", 114, "Merged")]
    assert [(c["object"], c["page"], c["kind"]) for c in pick(out, tick="P8", verb="Left")] == [("London Sunday Times", 110, "Dropped")]


def test_close_findings_are_flagged(tmp_path):
    out, _ = built(tmp_path)
    f = pick(out, tick="P9")
    assert len(f) == 1 and f[0]["kind"] == "Asked" and f[0]["object"].startswith("34 terms of office") and f[0]["outcome"] == "Warning"


def test_ids_are_unique_and_stable_between_reads(tmp_path):
    one, folder = built(tmp_path)
    ids = [c["id"] for c in one["choices"]]
    assert len(ids) == len(set(ids)) > 20
    again = choices.build(runfiles.RunFiles(folder, "pageflow"))
    assert [c["id"] for c in again["choices"]] == ids
    assert all(c["kind"] in choices.KINDS and c["key"] in set(choices.TICK_KEY.values()) for c in one["choices"])


def test_a_run_with_no_files_gives_empty_lists(tmp_path):
    (tmp_path / "run" / "pageflow").mkdir(parents=True)
    assert choices.build(runfiles.RunFiles(tmp_path / "run", "pageflow")) == {"ticks": [], "choices": []}


def test_choices_command_reads_a_registered_run(scratch, tmp_path):
    folder = tmp_path / "root" / "runs" / "r1"
    shutil.copytree(FIX, folder)
    rec = {"id": "2026-11-25-read-fixture", "kind": "pageflow", "run_folder": str(folder), "archive": None, "pid": 1, "started": "x",
           "name": "x", "dry": False}
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{rec['id']}.json").write_text(json.dumps(rec))
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "choices", rec["id"], "--json"], capture_output=True, text=True,
                       timeout=60, env=os.environ.copy())
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert got["id"] == rec["id"] and len(got["ticks"]) == 10 and got["choices"]

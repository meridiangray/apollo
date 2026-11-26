"""The Tools group (OCR, Text, Check) with fakes: a fake OCR script, pdftotext, pdfinfo and hydra-ctl. No real OCR."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import apollo
import tools


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    ocr = bin_ / "ocr-pdf.py"
    ocr.write_text(
        "import argparse, json\n"
        "ap = argparse.ArgumentParser(); ap.add_argument('pdf', nargs='?'); ap.add_argument('--pages'); ap.add_argument('--lang'); ap.add_argument('--json', action='store_true'); ap.add_argument('--langs', action='store_true'); ap.add_argument('--dpi')\n"
        "a = ap.parse_args()\n"
        "open(__file__ + '.argv', 'w').write(json.dumps([a.pages, a.lang, a.dpi]))\n"
        "if a.langs: print(json.dumps([{'code': 'fr', 'name': 'French', 'script': 'latin'}, {'code': 'ar', 'name': 'Arabic', 'script': 'arabic'}])); raise SystemExit(0)\n"
        "if a.lang == 'auto': print(json.dumps({'detected': {'lang': 'fr', 'name': 'French', 'script': 'latin', 'confidence': 0.97}}), flush=True)\n"
        "ns = [n for part in (a.pages or '1-2').split(',') for n in range(int(part.partition('-')[0]), int(part.partition('-')[2] or part.partition('-')[0]) + 1)]\n"
        "for n in ns:\n"
        "    print(json.dumps({'page': n, 'lines': [{'text': f'{a.lang} line one of page {n}', 'score': 0.9, 'box': []}, {'text': 'two', 'score': 0.9, 'box': []}]}), flush=True)\n")
    ptt = bin_ / "pdftotext"
    ptt.write_text("#!/bin/sh\n# pdftotext -f N -l N -layout FILE -\nprintf 'layer of page %s\\n\\f' \"$2\"\n")
    ptt.chmod(0o755)
    info = bin_ / "pdfinfo"
    info.write_text("#!/bin/sh\necho 'Pages:          5'\n")
    info.chmod(0o755)
    ctl = bin_ / "hydra-ctl"
    ctl.write_text("#!/bin/sh\n"
                   "if [ \"$1\" = check ]; then\n"
                   "  echo '[{\"level\":\"info\",\"code\":\"a.b\",\"where\":\"X\",\"message\":\"m1\",\"file\":\"f\"},"
                   "{\"level\":\"error\",\"code\":\"c.d\",\"where\":\"Y\",\"message\":\"m2\",\"file\":\"g\"}]'\n"
                   "else\n"
                   "  echo 'Built Fake (2 records)'; echo '             check   old build'; echo '  errors         0           0'\n"
                   "  echo '  warnings       0           1'; echo '  info           1           3'\n"
                   "fi\n")
    ctl.chmod(0o755)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setenv("APOLLO_OCR_PYTHON", sys.executable)
    monkeypatch.setenv("APOLLO_OCR_SCRIPT", str(ocr))
    monkeypatch.setenv("APOLLO_PDFTOTEXT", str(ptt))
    monkeypatch.setenv("APOLLO_PDFINFO", str(info))
    monkeypatch.setenv("APOLLO_HYDRA_CTL", str(ctl))

    (tmp_path / "doc.yaml").write_text(f"file: {pdf}\ntitle: Doc\n")
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path))
    return tmp_path


def archive_in(root_name: str, name="run-a") -> Path:
    base = tools.archive_roots()[0 if root_name == "live" else 1]
    p = base / name / "arch"
    p.mkdir(parents=True)
    return p


def finish(waiter, tool, **kw):
    return waiter(lambda: tools.get(tool, **kw)["status"] in ("done", "failed") and tools.get(tool, **kw))


def test_ocr_runs_the_pages_and_writes_under_state_tools(fakes, waiter):
    out = tools.begin("ocr", source="doc", pages="3-4", lang="ar")
    assert out["started"] is True
    got = finish(waiter, "ocr", source="doc")
    assert got["status"] == "done" and got["done"] == 2 and got["total"] == 2
    assert [p["page"] for p in got["pages"]] == [3, 4] and got["pages"][0]["text"] == "ar line one of page 3\ntwo"
    f = tools.job_file("doc", "ocr")
    assert f.is_file() and tools.tools_dir() in f.parents and (f.parent / "ocr-raw.jsonl").is_file()
    assert not list(fakes.glob("doc.*json*"))                                  # nothing beside the source


def test_ocr_without_its_venv_names_the_installer(fakes, monkeypatch):
    monkeypatch.setenv("APOLLO_OCR_PYTHON", str(fakes / "no-venv" / "python"))
    with pytest.raises(ValueError, match="install-ocr-venv.sh"):
        tools.begin("ocr", source="doc", pages="1-2")
    assert tools.get("ocr", source="doc")["ocr_ready"] is False


def test_text_uses_the_text_layer_and_all_pages_asks_pdfinfo(fakes, waiter):
    tools.begin("text", source="doc")
    got = finish(waiter, "text", source="doc")
    assert got["status"] == "done" and got["total"] == 5 and got["pages"][4] == {"page": 5, "text": "layer of page 5", "lines": 1}
    tools.begin("text", source="doc", pages="2-3")
    got = waiter(lambda: (g := tools.get("text", source="doc"))["status"] == "done" and g["total"] == 2 and g)   # a new run replaces the result
    assert [p["page"] for p in got["pages"]] == [2, 3]


def test_a_result_stays_until_the_tool_runs_again(fakes, waiter):
    tools.begin("text", source="doc", pages="1-1")
    finish(waiter, "text", source="doc")
    assert tools.get("text", source="doc")["pages"][0]["page"] == 1
    assert tools.get("ocr", source="doc")["status"] == "none"


def test_a_dead_process_is_not_shown_as_running(fakes, waiter):
    tools.begin("text", source="doc", pages="1-1")
    finish(waiter, "text", source="doc")
    f = tools.job_file("doc", "text")
    f.write_text(json.dumps({"tool": "text", "key": "doc", "status": "running", "pid": 99999999, "started": 1}))
    got = tools.get("text", source="doc")
    assert got["status"] == "failed" and "without a result" in got["error"]


def test_check_lists_findings_worst_first_and_is_limited_to_run_archives(fakes, waiter, tmp_path):
    a = archive_in("dry")
    tools.begin("check", archive=str(a))
    got = finish(waiter, "check", archive=str(a))
    assert got["status"] == "done" and got["counts"] == {"error": 1, "warning": 0, "info": 1}
    assert [i["level"] for i in got["items"]] == ["error", "info"]
    for bad in (str(tmp_path / "elsewhere"), str(tools.archive_roots()[1]), "/etc"):
        with pytest.raises(ValueError):
            tools.begin("check", archive=bad)


def test_the_process_itself_refuses_a_foreign_archive(fakes, tmp_path):
    outside = tmp_path / "x"
    outside.mkdir()
    spec = {"tool": "check", "archive": str(outside), "source": None, "pages": None, "lang": "en"}
    assert tools.run("check", "k", spec) == 1
    assert "only archives" in tools._read(tools.job_file("k", "check"))["error"]


def test_the_commands(fakes):
    def cli(*a):
        p = subprocess.run([sys.executable, str(Path(apollo.__file__)), *a], capture_output=True, text=True, timeout=60, env=os.environ.copy())
        return p.returncode, json.loads(p.stdout)
    code, out = cli("toolstart", "--tool", "ocr", "--source", "nope", "--json")
    assert code == 2 and "no source" in out["why"]
    code, out = cli("toolstart", "--tool", "text", "--source", "doc", "--pages", "9-1", "--json")
    assert code == 2
    code, out = cli("toolget", "--tool", "check", "--archive", "/etc", "--json")
    assert code == 2 and "only archives" in out["why"]
    code, out = cli("toolget", "--tool", "text", "--source", "nope", "--json")
    assert code == 0 and out["status"] == "none"


def test_ocr_auto_reports_the_detected_language_and_the_list_starts_with_auto(fakes, waiter):
    got0 = tools.get("ocr", source="doc")
    assert [x["code"] for x in got0["langs"]] == ["auto", "fr", "ar"]
    tools.begin("ocr", source="doc", pages="1-2")
    got = finish(waiter, "ocr", source="doc")
    assert got["status"] == "done" and got["detected"]["name"] == "French" and [p["page"] for p in got["pages"]] == [1, 2]
    assert got["lang"] == "auto"
    with pytest.raises(ValueError, match="--lang"):
        tools.begin("ocr", source="doc", pages="1-2", lang="French")


def test_check_builds_first_and_then_checks(fakes, waiter, monkeypatch, tmp_path):
    log = tmp_path / "ctl.log"
    ctl = tmp_path / "bin" / "hydra-ctl"
    ctl.write_text("#!/bin/sh\necho \"$1\" >> " + str(log) + "\nif [ \"$1\" = check ]; then echo '[]'; else echo Built; fi\n")
    a = archive_in("dry", "run-b")
    tools.begin("check", archive=str(a))
    got = finish(waiter, "check", archive=str(a))
    assert got["status"] == "done" and got["counts"] == {"error": 0, "warning": 0, "info": 0}
    assert log.read_text().split() == ["build", "check"]


def fake_analyse(rows):
    return lambda source, pages, recipe: {"ok": True, "pages": rows}


def test_ready_levels_and_sentences(fakes):
    rows = [{"page": n, "status": "todo", "needs_ocr": False} for n in range(1, 10)]
    got = tools.ready("doc", "1-9", "full", None, fake_analyse(rows))
    assert got["level"] == "ready" and got["sentence"] == "Ready: 0 errors, 0 warnings" and got["items"] == []
    rows[5]["status"] = "thin"
    got = tools.ready("doc", "1-9", "full", None, fake_analyse(rows))
    assert got["level"] == "warnings" and got["warnings"] == 1 and "page 6" in got["items"][0]["text"]
    for n in (3, 4, 5, 8):
        rows[n]["needs_ocr"] = True
    got = tools.ready("doc", "4-9", "full", None, fake_analyse(rows))               # page 3 is outside the chosen pages
    assert got["level"] == "not-ready" and got["errors"] == 1
    assert got["sentence"] == "Not ready: no text layer on pages 4–6, 9."
    assert tools.ready("nope", None, "full", None, fake_analyse([]))["level"] == "not-ready"


def test_ready_adds_the_archives_findings_and_only_for_run_archives(fakes):
    a = archive_in("dry")
    got = tools.ready("doc", None, "full", str(a), fake_analyse([]))
    assert got["level"] == "not-ready" and any("Y: m2" in i["text"] for i in got["items"])      # the fake check's error
    with pytest.raises(ValueError, match="only archives"):
        tools.ready("doc", None, "full", "/etc", fake_analyse([]))


def test_ready_forbidden_words_in_templates(fakes, monkeypatch, tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "A.md").write_text("please wait here")
    monkeypatch.setattr(tools.runfiles, "PAGEFLOW", tmp_path)
    got = tools.ready("doc", None, "full", None, fake_analyse([]), forbidden=("wait",))
    assert got["level"] == "not-ready" and "A.md" in got["items"][0]["text"]


def test_ocr_takes_its_declared_arguments_and_passes_them_on(fakes, waiter):
    tools.begin("ocr", source="doc", args={"pages": "1,3-4", "lang": "ar", "dpi": 150})
    got = finish(waiter, "ocr", source="doc")
    assert got["status"] == "done"
    assert json.loads((fakes / "bin" / "ocr-pdf.py.argv").read_text()) == ["1,3-4", "ar", "150"]
    assert got["args"] == {"pages": "1,3-4", "lang": "ar", "dpi": 150}


def test_text_resolves_end_against_the_page_count(fakes, waiter):
    tools.begin("text", source="doc", args={"pages": "4-end"})
    got = finish(waiter, "text", source="doc")
    assert [p["page"] for p in got["pages"]] == [4, 5]


def test_a_bad_argument_is_refused_before_anything_starts(fakes):
    for bad in ({"dpi": 5}, {"pages": "9-1"}, {"colour": "red"}, {"lang": "xx9"}):
        with pytest.raises(ValueError):
            tools.begin("ocr", source="doc", args=bad)
    assert tools.get("ocr", source="doc")["status"] == "none"


def test_about_answers_for_the_panel(fakes):
    out = tools.about("ocr", "doc", {"pages": "40-45", "lang": "ar"})
    assert out["problem"] == "" and "pages 40 to 45" in out["about"] and "Arabic" in out["about"]
    assert [a["name"] for a in out["args"]] == ["pages", "lang", "dpi"]
    bad = tools.about("ocr", "doc", {"dpi": 1})
    assert bad["about"] == "" and "dpi" in bad["problem"]
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), "toolabout", "--tool", "text", "--source", "doc", "--args", '{"pages": "2"}'],
                       capture_output=True, text=True, timeout=60, env=os.environ.copy())
    assert json.loads(p.stdout)["about"] == "Shows the text layer of page 2."

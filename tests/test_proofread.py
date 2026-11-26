"""Proofread: the diff, accept and reject, the precedence in pagetext, the job with a fake `claude` and `pdftoppm`, the guards,
and that nothing is written beside the source."""
import json
import stat
import time
from pathlib import Path

import pytest

import pagetext
import proofread
import runlog
import sourcedrop

CANNED = {"text": "The Director [REDACTED] signed it.\nSecond line.", "changes": ["fixed 2 OCR errors", "1 redaction marked"]}
POLLARD_PDF = proofread.runfiles.TESTING / "sources" / "pollard-damage-assessment.pdf"
RESULT = {"type": "result", "is_error": False, "usage": {"output_tokens": 7, "input_tokens": 100}, "total_cost_usd": 0.002,
          "structured_output": CANNED}


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """A `claude` that records its stdin and answers CANNED, and a `pdftoppm` that writes a tiny PNG."""
    seen = tmp_path / "claude-stdin.jsonl"
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(RESULT))
    claude = script(tmp_path / "claude", f"cat >> {seen}\ncat {answer}\n")
    ppm = script(tmp_path / "pdftoppm", 'for a; do last="$a"; done\nprintf "PNG fake" > "$last.png"\n')
    monkeypatch.setenv("APOLLO_CLAUDE", str(claude))
    monkeypatch.setenv("APOLLO_PDFTOPPM", str(ppm))
    return seen


@pytest.fixture
def pollard():
    if not POLLARD_PDF.is_file():
        pytest.skip("pollard pdf not present")
    return "pollard"


def canned_ask(prompt, schema, image, label):
    return CANNED, {"ms": 5, "tokens": 7, "cost_usd": 0.002, "model": "m", "label": label}


def test_the_diff_marks_what_changed_by_word():
    d = proofread.diff("The Dlrector (b)(1) signed\nit.", "The Director [REDACTED] signed\nit.")
    assert [(s["op"], s["text"]) for s in d] == [("eq", "The "), ("del", "Dlrector"), ("ins", "Director"), ("eq", " "),
                                                   ("del", "(b)(1)"), ("ins", "[REDACTED]"), ("eq", " signed\nit.")]
    assert "".join(s["text"] for s in d if s["op"] != "ins") == "The Dlrector (b)(1) signed\nit."
    assert "".join(s["text"] for s in d if s["op"] != "del") == "The Director [REDACTED] signed\nit."
    assert proofread.diff("same", "same") == [{"op": "eq", "text": "same"}]


def test_a_pending_page_is_accepted_and_comes_before_ocr_and_the_text_layer(pollard, monkeypatch):
    monkeypatch.setattr(proofread, "ask_model", canned_ask)
    monkeypatch.setattr(proofread, "render", lambda pdf, source, n: Path("/dev/null"))
    layer = pagetext.page(pollard, 20)
    assert layer["layer"] == "text"
    rec = proofread.proofread_page(pollard, POLLARD_PDF, 20)
    assert rec["old"] == layer["text"] and rec["text"] == CANNED["text"] and rec["changes"] == CANNED["changes"]
    assert pagetext.page(pollard, 20)["layer"] == "text"                        # pending is not yet the page's text
    got = proofread.get(pollard, 20)
    assert got["pending"] == [20] and got["accepted"] == [] and got["page"]["pending"]["diff"]
    assert proofread.act(pollard, 20, "accept")["ok"]
    shown = pagetext.page(pollard, 20)
    assert shown["layer"] == "proofread" and shown["text"] == CANNED["text"] and shown["was"] == "text"
    assert shown["proofread"]["changes"] == CANNED["changes"] and shown["proofread"]["model"] == "m"
    assert pagetext.page(pollard, 20, raw=True)["layer"] == "text"             # what Proofread itself starts from
    assert proofread.get(pollard, 20)["pending"] == [] and proofread.get(pollard, 20)["accepted"] == [20]
    ocr = pagetext.state_dir() / "tools" / pollard / "ocr.json"                # OCR does not outrank it
    ocr.parent.mkdir(parents=True)
    ocr.write_text(json.dumps({"pages": [{"page": 20, "text": "OCR text", "lines": 1}]}))
    assert pagetext.page(pollard, 20)["text"] == CANNED["text"]
    assert proofread.act(pollard, 20, "revert")["ok"] and pagetext.page(pollard, 20)["layer"] == "text"


def test_a_page_with_no_text_layer_takes_the_proofread_over_the_ocr_text(tmp_path, monkeypatch):
    from pypdf import PdfWriter
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    w = PdfWriter()
    w.add_blank_page(200, 200)
    pdf = tmp_path / "in" / "Scan.pdf"
    pdf.parent.mkdir()
    with pdf.open("wb") as fh:
        w.write(fh)
    slug = sourcedrop.add(pdf)["slug"]
    ocr = pagetext.state_dir() / "tools" / slug / "ocr.json"
    ocr.parent.mkdir(parents=True)
    ocr.write_text(json.dumps({"pages": [{"page": 1, "text": "Read by OCR", "lines": 1}]}))
    monkeypatch.setattr(proofread, "ask_model", canned_ask)
    monkeypatch.setattr(proofread, "render", lambda pdf, source, n: Path("/dev/null"))
    rec = proofread.proofread_page(slug, proofread.source_pdf(slug), 1)
    assert rec["old"] == "Read by OCR" and rec["layer"] == "ocr"                # the OCR text is the base, as the page showed it
    proofread.act(slug, 1, "accept")
    assert pagetext.page(slug, 1)["layer"] == "proofread"


def test_reject_drops_the_proposal_and_changes_nothing_else(pollard, monkeypatch):
    monkeypatch.setattr(proofread, "ask_model", canned_ask)
    monkeypatch.setattr(proofread, "render", lambda pdf, source, n: Path("/dev/null"))
    proofread.proofread_page(pollard, POLLARD_PDF, 21)
    assert proofread.act(pollard, 21, "reject")["ok"]
    assert proofread.pending(pollard, 21) is None and proofread.accepted(pollard, 21) is None
    assert pagetext.page(pollard, 21)["layer"] == "text"
    assert proofread.act(pollard, 21, "accept")["ok"] is False                  # nothing waits any more
    assert proofread.act(pollard, 21, "burn")["ok"] is False


def test_the_job_asks_haiku_with_the_page_image_and_writes_only_under_state(pollard, fakes, waiter):
    src_dir = POLLARD_PDF.parent
    before = {p.name: p.stat().st_mtime_ns for p in src_dir.iterdir()}
    out = proofread.begin(pollard, "20-21")
    assert out["started"] is True
    job = waiter(lambda: (lambda j: j if j and j["status"] != "running" else None)(proofread.get(pollard)["job"]), timeout=60)
    assert job["status"] == "done" and job["done"] == 2 and job["calls"] == 2 and job["errors"] == {}
    assert abs(job["cost_usd"] - 0.004) < 1e-9
    assert proofread.get(pollard)["pending"] == [20, 21]
    sent = [json.loads(x) for x in fakes.read_text().splitlines()]
    content = sent[0]["message"]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/png"
    assert "[REDACTED]" in content[1]["text"] and "Machine text of page 20" in content[1]["text"]
    assert {p.name: p.stat().st_mtime_ns for p in src_dir.iterdir()} == before            # the source's folder is untouched
    written = [p for p in proofread.state_dir().rglob("*") if p.is_file()]
    assert all(proofread.state_dir() in p.parents for p in written)
    assert (proofread.folder(pollard) / "p20.png").is_file()
    # the log: both calls with their prompt and raw answer, and one tool run
    calls = runlog.sql("select origin, source, page, ok, tokens, cost_usd, prompt, answer from model_calls order by page")["rows"]
    assert [c[:6] for c in calls] == [["proofread", pollard, 20, True, 7, 0.002], ["proofread", pollard, 21, True, 7, 0.002]]
    assert "Machine text of page 20" in calls[0][6] and json.loads(calls[0][7])["changes"] == CANNED["changes"]
    tr = runlog.sql("select tool, key, status, done, total, calls from tool_runs")["rows"]
    assert tr == [["proofread", pollard, "done", 2, 2, 2]]


def test_a_page_the_model_fails_on_is_noted_and_the_rest_go_on(pollard, monkeypatch):
    n = {"i": 0}

    def ask(prompt, schema, image, label):
        n["i"] += 1
        if n["i"] == 1:
            raise RuntimeError("haiku timed out")
        return CANNED, {"ms": 1, "model": "m", "cost_usd": 0}
    monkeypatch.setattr(proofread, "ask_model", ask)
    monkeypatch.setattr(proofread, "render", lambda pdf, source, n: Path("/dev/null"))
    folder = proofread.folder(pollard)
    proofread._write(folder / "job.json", {"pages": [20, 21], "total": 2, "calls": 0, "cost_usd": 0, "errors": {}, "done": 0,
                                           "started": time.time()})
    assert proofread.run(pollard, "20-21") == 0
    job = json.loads((folder / "job.json").read_text())
    assert job["status"] == "done" and list(job["errors"]) == ["20"] and proofread.pending(pollard, 21)
    assert runlog.sql("select ok from model_calls order by ok")["rows"] == [[False], [True]]


def test_the_guards(pollard, tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_CLAUDE", str(tmp_path / "no-claude"))
    monkeypatch.setenv("PATH", "/nonexistent")
    got = proofread.get(pollard)
    assert got["ok"] and got["available"] is False and "claude" in got["why"]
    with pytest.raises(ValueError, match="claude"):
        proofread.begin(pollard, "20")
    ok = script(tmp_path / "claude", "exit 0\n")
    monkeypatch.setenv("APOLLO_CLAUDE", str(ok))
    monkeypatch.setenv("APOLLO_PDFTOPPM", str(ok))
    assert proofread.get(pollard)["available"] is True
    assert len(proofread.parse_pages("1-20")) == 20
    for bad in ("1-21", "5-3", "0", "x", "", "1-2-3"):
        with pytest.raises(ValueError):
            proofread.parse_pages(bad)
    with pytest.raises(ValueError, match="at most 20"):
        proofread.begin(pollard, "1-21")
    assert proofread.get("../x")["ok"] is False


def test_a_text_source_has_no_proofread(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_SOURCES", str(tmp_path / "sources"))
    txt = tmp_path / "in" / "Notes.txt"
    txt.parent.mkdir()
    txt.write_text("The Harbour Board met.\n\nIt rose.\n", encoding="utf-8")
    slug = sourcedrop.add(txt)["slug"]
    assert proofread.get(slug) == {"ok": False, "why": "this source has no PDF"}
    with pytest.raises(ValueError, match="no PDF"):
        proofread.begin(slug, "1")
    assert pagetext.page(slug, 1)["layer"] == "text"

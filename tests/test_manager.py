"""The manager: the registry, start in --dry (page flow and session), ls/status JSON, stop by PID. Scratch folders only;
the page-flow driver is tests/fake_driver.py, a session is drysession.py: no model, no hydra."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import apollo

LS_KEYS = {"schema", "id", "name", "kind", "model", "status", "detail", "alive", "pid", "dry", "fast", "started",
           "ended", "elapsed_s", "exit_code", "run_folder", "archive", "config", "progress", "calls", "cost", "last",
           "report", "copied", "cells", "recipe", "source", "now", "flow", "source_title", "archive_name", "limits", "resumable", "flow_key", "agents"}
STATUS_KEYS = LS_KEYS | {"questions", "tasks", "budgets", "command", "slowest_calls", "errors"}


def cli(*args) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(Path(apollo.__file__)), *args], capture_output=True, text=True, timeout=60,
                       env=os.environ.copy())
    return p.returncode, p.stdout


def ended(waiter, rid):
    return waiter(lambda: apollo.exit_info(rid))


# ---- the registry ---------------------------------------------------------------------------------------------------

def test_registry_round_trip_and_ids_never_collide(scratch, smoke_config):
    rec = apollo.plan(smoke_config, dry=True)
    assert rec["id"].endswith("-dry-haiku-pageflow-smoke") and rec["status"] == "starting"
    apollo.save({**rec, "started": "2026-11-24T10:00:00"})
    assert apollo.load(rec["id"])["config"] == str(smoke_config.resolve())
    assert apollo.plan(smoke_config, dry=True)["id"] == rec["id"] + "-2"        # taken: the next number
    (apollo.state_dir() / f"{rec['id']}.exit.json").write_text("{}")
    (apollo.state_dir() / "junk.json").write_text("not json")
    assert [r["id"] for r in apollo.records()] == [rec["id"]]                  # exit files and junk are no runs
    assert apollo.find(rec["id"][-12:])["id"] == rec["id"]                       # a unique part finds it


def test_dry_page_flow_config_turns_off_models_network_and_the_window(scratch, smoke_config):
    rec = apollo.plan(smoke_config, name="smoke", dry=True, pages="20-22", model="claude-haiku-x")
    cfg = yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith("fleet-config.yaml")))
    root = scratch / "root" / "dry"
    assert rec["run_folder"] == str(root / "runs" / rec["id"]) == cfg["run_folder"]
    assert cfg["archive"]["path"] == str(root / "archives" / rec["id"] / "pollard-da")
    assert cfg["stub_models"] is True and cfg["window"] is False
    assert cfg["places"]["osm"]["enabled"] is False and cfg["wikidata"]["enabled"] is False
    assert cfg["lag"]["enabled"] is False and cfg["budgets"]["calls_per_page"] is None
    assert cfg["budgets"]["haiku"] == 80
    assert cfg["pages"] == {"from": 20, "to": 22} and cfg["models"]["haiku"] == "claude-haiku-x"
    assert cfg["run"] == rec["id"] and rec["command"][-3:] == ["run", cfg["run_folder"] + "/fleet-config.yaml",
                                                              "--no-window"]


def test_a_live_run_goes_under_the_testing_folder_and_keeps_the_budgets(scratch, smoke_config):
    rec = apollo.plan(smoke_config)
    cfg = yaml.safe_load(next(iter(rec["files"].values())))
    assert rec["run_folder"] == str(scratch / "root" / "runs" / rec["id"])
    assert "stub_models" not in cfg and cfg["budgets"]["haiku"] == 80
    assert yaml.safe_load(next(iter(apollo.plan(smoke_config, budget="none")["files"].values())))["budget_usd"] is None
    assert yaml.safe_load(next(iter(apollo.plan(smoke_config, budget="3")["files"].values())))["budget_usd"] == 3


def test_a_session_runs_claude_by_path_with_the_linux_mcp_config(scratch, brief):
    rec = apollo.plan(brief, budget="5")
    cmd = rec["command"]
    assert cmd[0] == "/bin/false"                                              # APOLLO_CLAUDE; else ~/.local/bin/claude
    assert cmd[cmd.index("--model") + 1] == apollo.SESSION_MODEL
    mcp = cmd[cmd.index("--mcp-config") + 1]
    client = json.loads(apollo.MCP_CLIENT.read_text())
    client["mcpServers"]["hydra"]["env"].update(apollo.mcp_env())                    # every run tells its hydra-mcp one read size
    assert json.loads(rec["files"][mcp]) == client
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--max-budget-usd") + 1] == "5"
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__hydra" and "--verbose" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert rec["io"]["stdin"].endswith("prompt.md") and rec["io"]["stdout"].endswith("transcript.jsonl")
    assert rec["files"][rec["io"]["stdin"]] == brief.read_text()


def test_fast_runs_on_the_wsl_disk_and_names_the_mirror(scratch, smoke_config):
    rec = apollo.plan(smoke_config, fast=True)
    cfg = yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith(".yaml")))
    assert rec["run_folder"] == str(apollo.WS / "fleet-runs" / rec["id"] / "run")
    assert rec["final_folder"] == str(scratch / "root" / "runs" / rec["id"])
    assert cfg["shared_source"] == str(apollo.WS) + "/" and cfg["hydra"]["command"].endswith("hydra-mcp-ws.sh")
    assert cfg["source"]["file"].startswith(str(apollo.WS)) and rec["command"][0] == str(apollo.WS_PYTHON)


# ---- start, ls, status, tail ----------------------------------------------------------------------------------------

def test_start_dry_page_flow_runs_to_done_and_ls_status_tail_say_so(scratch, smoke_config, waiter):
    code, out = cli("start", str(smoke_config), "--dry", "--json")
    got = json.loads(out)
    assert code == 0 and got["ok"] and got["dry"] and got["pid"]
    rid = got["id"]
    ex = ended(waiter, rid)
    assert ex["status"] == "done" and ex["code"] == 0
    seen = json.loads((Path(got["run_folder"]) / "seen-config.json").read_text())
    assert seen["cfg"]["stub_models"] is True                                  # the driver got the dry config
    code, out = cli("ls", "--json")
    rows = json.loads(out)
    assert code == 0 and [r["id"] for r in rows] == [rid] and set(rows[0]) == LS_KEYS
    s = rows[0]
    assert s["status"] == "done" and s["progress"]["fraction"] == 1.0 and s["alive"] is False
    assert s["calls"] == {"hydra": 3, "errors": 1, "model": 1}
    assert s["cost"]["haiku"] == [3, 80] and s["cost"]["usd"] == 0.0
    assert s["report"].endswith("report.md") and "run done" in s["last"]
    code, out = cli("status", rid[-10:], "--json")
    st = json.loads(out)
    assert set(st) == STATUS_KEYS and st["slowest_calls"][0] == {"tool": "save", "ms": 9000, "at": "10:00:01"}
    assert st["errors"][0]["tool"] == "cite" and st["questions"] == {"open": 0, "answered": 0, "dismissed": 0}
    code, out = cli("tail", rid, "-n", "3")
    lines = out.strip().splitlines()
    assert len(lines) == 3 and "Ran run done" in lines[-1]
    code, out = cli("ls")
    assert rid[:40] in out and "done" in out


def test_start_dry_session_replays_a_transcript(scratch, brief, waiter):
    rec = apollo.start(brief, name="look", dry=True)
    assert rec["command"][1].endswith("drysession.py")
    ex = ended(waiter, rec["id"])
    assert ex["status"] == "done", ex
    s = apollo.summary(apollo.load(rec["id"]), full=True)
    assert s["kind"] == "mcp-session" and s["calls"] == {"hydra": 5, "errors": 1, "model": None}
    assert s["cost"]["usd"] == 0.0 and s["cost"]["turns"] == 8 and s["progress"]["fraction"] == 1.0
    lines = apollo.runfiles.tail_lines(apollo._files(rec).trace(), 50)
    assert any("Called" in x and "guide" in x for x in lines) and any("Failed" in x for x in lines)


# ---- stop -----------------------------------------------------------------------------------------------------------

def test_stop_signals_the_runs_own_group_and_it_ends_stopped(scratch, smoke_config, waiter, gone, tmp_path):
    cfg = yaml.safe_load(smoke_config.read_text())
    cfg["fake_hold"] = 60
    held = tmp_path / "held.yaml"
    held.write_text(yaml.safe_dump(cfg))
    rec = apollo.start(held, dry=True)
    live = waiter(lambda: apollo.runfiles.read_json(apollo.state_dir() / f"{rec['id']}.live.json"))
    assert apollo.summary(apollo.load(rec["id"]))["status"] == "running"
    code, out = cli("stop", rec["id"], "--json")
    got = json.loads(out)
    assert code == 0 and got["ok"] and got["how"] == f"SIGTERM to process group {rec['pid']}"
    ex = ended(waiter, rec["id"])
    assert ex["status"] == "stopped"
    waiter(lambda: gone(live["child_pid"]) and gone(rec["pid"]))
    assert cli("stop", rec["id"])[0] == 1                                     # not running: nothing is signalled


def test_stop_never_signals_a_pid_that_is_not_the_runs_wrapper(scratch, smoke_config):
    rec = apollo.plan(smoke_config, dry=True)
    rec.update({"pid": os.getpid(), "pgid": os.getpgid(0), "started": "2026-11-24T10:00:00"})   # pytest itself
    apollo.save(rec)
    assert apollo.alive(rec) is False
    assert apollo.stop(rec["id"]) == {"ok": False, "id": rec["id"], "why": "not running"}
    assert apollo.summary(apollo.load(rec["id"]))["status"] == "died"


def test_start_refuses_what_it_cannot_run(scratch, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{}")
    code, out = cli("start", str(bad), "--json")
    assert code == 2 and "a page-flow config" in json.loads(out)["why"]
    code, out = cli("status", "nothing-like-this", "--json")
    assert code == 2 and json.loads(out)["ok"] is False


# ---- recipe x source --------------------------------------------------------------------------------------------------

def test_a_recipe_on_a_source_is_composed_into_the_run_config(scratch):
    rec = apollo.plan(recipe="smoke", source="pollard", dry=True, pages="20-22")
    cfg = yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith("fleet-config.yaml")))
    root = scratch / "root" / "dry"
    assert rec["id"].endswith("-dry-smoke-pollard") and rec["recipe"] == "smoke" and rec["source"] == "pollard"
    assert cfg["run"] == rec["id"] and cfg["run_folder"] == str(root / "runs" / rec["id"])
    assert cfg["archive"]["path"] == str(root / "archives" / rec["id"] / "pollard-da")
    assert cfg["pages"] == {"from": 20, "to": 22} and cfg["budgets"]["haiku"] is None   # no call or token caps
    assert cfg["stub_models"] is True and cfg["window"] is False and cfg["lag"]["enabled"] is False
    assert rec["budget_usd"] == cfg["budget_usd"] == 1 and apollo.plan(recipe="smoke", source="pollard", dry=True, budget="2.5")["budget_usd"] == 2.5
    assert cfg["source"]["file"].endswith("sources/pollard-damage-assessment.pdf") and cfg["hydra_context"] == "general"


def test_a_recipe_runs_on_another_source_and_keeps_none_of_the_first(scratch):
    rec = apollo.plan(recipe="smoke", source="alwaleed", name="aw", dry=True)
    cfg = yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith("fleet-config.yaml")))
    assert cfg["archive"]["prefix"] == "alwaleed" and cfg["pages"] == {"from": 1, "to": 9}
    assert "reference_archive" not in cfg and "pollard" not in yaml.safe_dump(cfg).lower()


def test_recipe_and_source_go_together_and_must_exist(scratch):
    import pytest
    for kw in ({"recipe": "smoke"}, {"source": "pollard"}, {"recipe": "nope", "source": "pollard"},
               {"recipe": "smoke", "source": "nope"}):
        with pytest.raises(ValueError):
            apollo.plan(dry=True, **kw)
    code, out = cli("start", "--recipe", "smoke", "--source", "nope", "--dry", "--json")
    assert code == 2 and json.loads(out)["ok"] is False


def test_start_a_composed_run_end_to_end(scratch, waiter):
    rec = apollo.start(recipe="smoke", source="pollard", dry=True, pages="20-21")
    done = ended(waiter, rec["id"])
    assert done["status"] == "done"
    seen = json.loads((Path(rec["run_folder"]) / "seen-config.json").read_text())
    assert seen["cfg"]["pages"] == {"from": 20, "to": 21} and seen["cfg"]["recipe"] == "smoke"


def test_a_brief_gets_its_placeholders_filled(scratch):
    brief = scratch / "b.md"
    brief.write_text("run {run_folder}\narchive {archive}\nsource {source}\nkeep {doc, from, to}\n", encoding="utf-8")
    rated = apollo.plan(recipe="smoke", source="pollard", dry=True)
    apollo.save({**rated, "started": "2026-11-24T10:00:00"})
    rec = apollo.plan(brief, dry=True, source="pollard", rate=rated["id"])
    prompt = rec["files"][rec["io"]["stdin"]]
    assert f"run {rated['run_folder']}" in prompt and f"archive {rated['archive']}" in prompt
    assert "source " + str(apollo.TESTING / "sources" / "pollard-damage-assessment.pdf") in prompt
    assert "keep {doc, from, to}" in prompt                                    # not a placeholder: left alone
    bare = apollo.plan(brief, dry=True)
    assert "{run_folder}" in bare["files"][bare["io"]["stdin"]]                # nothing to fill it with
    import pytest
    with pytest.raises(ValueError):
        apollo.plan(brief, dry=True, rate="no-such-run")


def test_a_workflow_session_fills_the_generic_brief(scratch):
    import pytest
    rec = apollo.plan(workflow="stubs", archive="/a/arch", dry=True)
    prompt = rec["files"][rec["io"]["stdin"]]
    assert "from workflow stubs on archive /a/arch" in prompt and "{workflow}" not in prompt and "{archive}" not in prompt
    assert rec["kind"] == "mcp-session" and rec["name"] == "workflow-stubs" and rec["archive"] == "/a/arch"
    with pytest.raises(ValueError):
        apollo.plan(workflow="stubs", dry=True)                               # no archive
    with pytest.raises(ValueError):
        apollo.plan(workflow="nope", archive="/a", dry=True)


def test_models_and_without_shape_the_composed_config(scratch):
    import pytest
    rec = apollo.plan(recipe="full", source="pollard", dry=True, models="haiku=claude-opus-5-5,sonnet=claude-haiku-5-5", without="lag,rate")
    cfg = yaml.safe_load(next(iter(rec["files"].values())))
    assert cfg["models"]["haiku"] == "claude-opus-5-5" and cfg["models"]["sonnet"] == "claude-haiku-5-5"
    assert not cfg.get("lag", {}).get("enabled") and "rubric" not in cfg
    with pytest.raises(ValueError):
        apollo.plan(recipe="full", source="pollard", dry=True, models="reader=x")
    with pytest.raises(ValueError):
        apollo.plan(recipe="full", source="pollard", dry=True, without="reader")


def test_recipe_detail_has_a_button_per_stage_with_its_prompts():
    d = apollo.recipe_detail("full", "pollard")
    keys = [s["key"] for s in d["stages"]]
    assert keys == ["analyse", "read", "escalate", "rate", "stubs", "places", "enrich", "wiki", "assemble", "timeline", "wikidata",
                    "finance"]
    st = {s["key"]: s for s in d["stages"]}
    assert st["analyse"]["model"] is None and st["read"]["role"] == "haiku" and st["read"]["prompts"] == ["A", "C", "T", "I"]
    assert st["stubs"]["prompts"] == ["workflow"] and all(s.get("about") for s in d["stages"]) and apollo.prompt_text("workflow")["text"].startswith("# ")
    assert [l["key"] for l in d["limits"]][:2] == ["calls", "seconds"]


def test_clear_deletes_the_run_and_its_archive_but_nothing_outside_the_run_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("APOLLO_ROOT", str(tmp_path / "root"))
    rid = "2026-11-25-cleared"
    run = tmp_path / "root" / "runs" / rid
    arch = tmp_path / "root" / "archives" / rid / "src"
    outside = tmp_path / "elsewhere" / "kept"
    for d in (run, arch, outside):
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
    apollo.save({"id": rid, "run_folder": str(run), "archive": str(arch), "final_folder": str(outside)})
    out = apollo.clear(rid)
    assert out["ok"] and not run.exists() and not arch.parent.exists()
    assert outside.exists() and str(outside) in out["kept"]
    assert apollo.load(rid) is None
    assert not apollo.clear(rid)["ok"]                        # gone: nothing left to clear


def test_serve_answers_one_json_line_per_request_and_refuses_exec(monkeypatch, capsys):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO('{"args": ["configs", "--json"]}\n{"args": ["exec", "x"]}\n'))
    assert apollo.serve() == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2
    assert lines[0]["code"] == 0 and "recipes" in json.loads(lines[0]["out"])
    assert lines[1]["code"] == 2 and "bad request" in lines[1]["out"]


def test_clear_never_deletes_an_archive_that_is_not_the_runs_own(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("APOLLO_ROOT", str(tmp_path / "root"))
    rid = "2026-11-25-workflow-stubs"
    run = tmp_path / "root" / "runs" / rid
    other = tmp_path / "root" / "archives" / "2026-11-24-full-pollard" / "pollard"      # a page-flow run's archive
    for d in (run, other):
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
    apollo.save({"id": rid, "kind": "mcp-session", "run_folder": str(run), "archive": str(other)})
    out = apollo.clear(rid)
    assert out["ok"] and not run.exists() and other.exists() and str(other) in out["kept"]


# ---- resume: a stopped run carries on, same id, nothing cleared ------------------------------------------------------

def _held(smoke_config, tmp_path):
    cfg = yaml.safe_load(smoke_config.read_text())
    cfg["fake_hold"] = 60
    held = tmp_path / "held.yaml"
    held.write_text(yaml.safe_dump(cfg))
    return held


def test_resume_carries_a_stopped_dry_run_on_to_done_with_the_same_id(scratch, smoke_config, waiter, tmp_path):
    rec = apollo.start(_held(smoke_config, tmp_path), dry=True)
    rid, folder = rec["id"], Path(rec["run_folder"])
    waiter(lambda: (folder / "pageflow" / "state.json").exists() and apollo.runfiles.read_json(apollo.state_dir() / f"{rid}.live.json"))
    assert cli("stop", rid)[0] == 0
    assert ended(waiter, rid)["status"] == "stopped"
    waiter(lambda: not apollo.alive(apollo.load(rid)))
    marker = folder / "kept.txt"
    marker.write_text("x")
    code, out = cli("resume", rid, "--json")
    got = json.loads(out)
    assert code == 0 and got["ok"] and got["id"] == rid and got["pid"] != rec["pid"]
    ex = waiter(lambda: (e := apollo.exit_info(rid)) and e["status"] == "done" and e)
    assert ex["code"] == 0
    assert marker.exists()                                                     # nothing was cleared
    new = apollo.load(rid)
    assert new["id"] == rid and new["run_folder"] == rec["run_folder"] and len(new["resumes"]) == 1
    assert "stop_requested" not in new and new["command"][2:4] == ["resume", str(folder / "run-config.yaml")]
    argvs = [json.loads(line) for line in (folder / "seen-argv.jsonl").read_text().splitlines()]
    assert [a[0] for a in argvs] == ["run", "resume"] and argvs[1][1] == str(folder / "run-config.yaml")
    assert [r["id"] for r in json.loads(cli("ls", "--json")[1])] == [rid]
    assert json.loads(cli("ls", "--json")[1])[0]["status"] == "done"


def test_resume_refuses_a_live_run_a_cleared_folder_missing_files_and_sessions(scratch, smoke_config, brief, waiter, tmp_path):
    assert not apollo.resume("2026-01-01-nope")["ok"]
    live = apollo.start(_held(smoke_config, tmp_path), dry=True)
    waiter(lambda: (Path(live["run_folder"]) / "pageflow" / "state.json").exists() and apollo.alive(apollo.load(live["id"])))
    out = apollo.resume(live["id"])
    assert not out["ok"] and "still running" in out["why"]
    cli("stop", live["id"])
    ended(waiter, live["id"])
    waiter(lambda: not apollo.alive(apollo.load(live["id"])))
    state_json = Path(live["run_folder"]) / "pageflow" / "state.json"
    keep = state_json.read_bytes()
    state_json.unlink()
    out = apollo.resume(live["id"])
    assert not out["ok"] and "no saved state" in out["why"]
    state_json.write_bytes(keep)
    (Path(live["run_folder"]) / "run-config.yaml").unlink()
    assert "run-config.yaml" in apollo.resume(live["id"])["why"]
    shutil.rmtree(live["run_folder"])
    assert "run folder is gone" in apollo.resume(live["id"])["why"]
    sess = apollo.start(brief, dry=True)
    ended(waiter, sess["id"])
    out = apollo.resume(sess["id"])
    assert not out["ok"] and "workflow session" in out["why"]
    code, text = cli("resume", sess["id"])
    assert code == 1 and text.startswith("not resumed:")

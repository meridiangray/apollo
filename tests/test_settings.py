"""The Settings page (settings.py, CZ330): each setting kept once (hydra's chat config and keystore, hydra's basemap switch,
Apollo's apollo.json) and read where it acts (New run's agents and cost cap, the chat's caps, the folders). A key's value
arrives on stdin and is never given back. No provider is called: the Test click's hydra command is faked."""
import base64
import io
import json
import subprocess
import sys

import pytest

import apollo
import archives
import chat
import runview
import settings
import sourcedrop
from hydra.machine import chatconf, secrets

VALUE = "sk-test-VALUE-0123456789"


@pytest.fixture(autouse=True)
def machine(tmp_path, monkeypatch):
    """Apollo's apollo.json, hydra's config and basemap in scratch; a fake keystore, as on Windows."""
    monkeypatch.setenv("APOLLO_CONFIG", str(tmp_path / "apollo.json"))
    monkeypatch.setenv("HYDRA_CONFIG_DIR", str(tmp_path / "hydra-config"))
    monkeypatch.setenv("HYDRA_BASEMAP_DIR", str(tmp_path / "basemap"))
    for n in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    kept = {}
    monkeypatch.setattr(secrets, "_win", lambda: True)
    monkeypatch.setattr(secrets, "_cred_read", lambda t: kept.get(t))
    monkeypatch.setattr(secrets, "_cred_write", lambda t, v: kept.__setitem__(t, v))
    monkeypatch.setattr(secrets, "_cred_delete", lambda t: kept.pop(t, None) is not None)
    return kept


def call(capsys, monkeypatch, req, stdin=None):
    args = ["settingsset", "--req64", base64.b64encode(json.dumps(req).encode()).decode()]
    if stdin is not None:
        args.append("--stdin")
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = apollo.main(args)
    out = capsys.readouterr().out
    return code, json.loads(out), out


def test_the_page_draws_defaults_and_no_value(capsys):
    assert apollo.main(["settings"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in got["models"]][:2] == ["builtin", "claude-opus-5-5"] and not any(m["set"] for m in got["models"])
    assert got["limits"] == {"run_usd": None, "chat_usd": chat.BUDGET_USD, "chat_secs": chat.TIMEOUT}
    assert got["network"] == {"basemap": True} and got["can_store"] is True
    read = next(f for f in got["flows"] if f["key"] == "read")
    assert read["grades"]["haiku"] == {**runview.FLOW_SPEC["read"]["grades"]["haiku"], "low": 1, "code": 4}
    assert not settings.file().exists()                                  # reading writes nothing


def test_a_key_comes_on_stdin_and_never_goes_back(capsys, monkeypatch, machine):
    code, got, out = call(capsys, monkeypatch, {"op": "key", "name": "ANTHROPIC_API_KEY"}, stdin=f" {VALUE}\n")
    assert code == 0 and machine == {"Hydra:ANTHROPIC_API_KEY": VALUE} and VALUE not in out
    assert next(m for m in got["models"] if m["id"] == "claude-opus-5-5")["set"]
    code, got, _ = call(capsys, monkeypatch, {"op": "key", "name": "ANTHROPIC_API_KEY", "remove": True})
    assert code == 0 and not machine and not any(m["set"] for m in got["models"])
    code, got, _ = call(capsys, monkeypatch, {"op": "key", "name": "PATH"}, stdin=VALUE)
    assert code == 2 and got == {"ok": False, "why": "no model uses that key"} and not machine
    monkeypatch.setattr(secrets, "_win", lambda: False)                  # Linux: keys are environment variables
    code, got, out = call(capsys, monkeypatch, {"op": "key", "name": "ANTHROPIC_API_KEY"}, stdin=VALUE)
    assert code == 2 and "environment variable" in got["why"] and VALUE not in out


def test_the_worker_never_takes_a_change(monkeypatch):
    monkeypatch.setattr(apollo, "warm", lambda: None)
    req = json.dumps({"args": ["settingsset", "--req64", "e30=", "--stdin"]})
    sys_in, sys_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO(req + "\n"), io.StringIO()
    try:
        apollo.serve()
        reply = json.loads(sys.stdout.getvalue().splitlines()[-1])
    finally:
        sys.stdin, sys.stdout = sys_in, sys_out
    assert reply["code"] == 2 and "bad request" in reply["out"]


def test_model_rows_are_hydras(capsys, monkeypatch):
    rows = [{"id": "mine", "label": "Mine", "provider": "local", "model": "llama3", "key": "", "endpoint": "http://127.0.0.1:1234/v1"}]
    code, got, _ = call(capsys, monkeypatch, {"op": "models", "rows": rows})
    assert code == 0 and [m["id"] for m in got["models"]] == ["mine"] and chatconf.models()[0]["endpoint"].endswith(":1234/v1")
    code, got, _ = call(capsys, monkeypatch, {"op": "models", "rows": [{**rows[0], "provider": "nope"}]})
    assert code == 2 and "provider" in got["why"] and chatconf.models()[0]["provider"] == "local"


def test_test_runs_hydras_command_only_on_the_click(capsys, monkeypatch):
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true, "reply": "ok", "ms": 412}\n', stderr="")
    monkeypatch.setattr(settings.subprocess, "run", fake)
    apollo.main(["settings"])
    capsys.readouterr()
    assert not seen                                                       # drawing the page calls nothing
    code, got, _ = call(capsys, monkeypatch, {"op": "test", "id": "claude-haiku-5-5"})
    assert code == 0 and got == {"ok": True, "test": {"ok": True, "reply": "ok", "ms": 412}}
    assert seen[0][-3:] == ["env", "test", "claude-haiku-5-5"]
    monkeypatch.setattr(settings.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, stdout='{"ok": false, "error": "Needs a key: ANTHROPIC_API_KEY"}\n', stderr=""))
    assert call(capsys, monkeypatch, {"op": "test", "id": "claude-haiku-5-5"})[1]["test"] == {"ok": False, "error": "Needs a key: ANTHROPIC_API_KEY"}
    assert call(capsys, monkeypatch, {"op": "test", "id": "nope"})[0] == 2


def test_limits_reach_new_run_and_the_chat(capsys, monkeypatch, tmp_path):
    assert apollo.default_budget(50) is None
    for key, v in (("limits.run_usd", 3), ("limits.chat_usd", 0.5), ("limits.chat_secs", 120)):
        assert call(capsys, monkeypatch, {"op": "set", "key": key, "value": v})[0] == 0
    assert apollo.default_budget(50) == 3.0
    assert "--max-budget-usd" in apollo.plan(flow="stubs", archive="/a/arch", dry=True)["command"]      # a set Run cost caps a session
    cmd = chat.command("haiku", tmp_path / "mcp.json", "sys")
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    for bad in ({"key": "limits.chat_usd", "value": "lots"}, {"key": "limits.chat_secs", "value": 5}, {"key": "nope", "value": 1}):
        assert call(capsys, monkeypatch, {"op": "set", **bad})[0] == 2
    assert call(capsys, monkeypatch, {"op": "set", "key": "limits.run_usd", "value": None})[0] == 0
    assert apollo.default_budget(50) is None and "limits.run_usd" not in settings.load()


def test_agent_defaults_reach_new_run(capsys, monkeypatch):
    assert call(capsys, monkeypatch, {"op": "set", "key": "agents.read.haiku", "value": 6})[0] == 0
    assert next(f for f in runview.flow_catalogue() if f["key"] == "read")["grades"]["haiku"]["default"] == 6
    assert apollo.agents_for("read", None)["haiku"] == 6 and apollo.agents_for("read", {"haiku": 2})["haiku"] == 2
    for bad in ({"key": "agents.read.haiku", "value": 99}, {"key": "agents.read.opus", "value": 1}, {"key": "agents.read.haiku", "value": 0}):
        assert call(capsys, monkeypatch, {"op": "set", **bad})[0] == 2


def test_folders_env_first_then_settings(capsys, monkeypatch, tmp_path):
    mine = tmp_path / "elsewhere"
    mine.mkdir()
    assert call(capsys, monkeypatch, {"op": "set", "key": "folders.archives", "value": str(mine)})[0] == 0
    assert archives.folder() == tmp_path / "archives"                     # APOLLO_ARCHIVES (conftest) still wins
    monkeypatch.delenv("APOLLO_ARCHIVES")
    assert archives.folder() == mine
    monkeypatch.delenv("APOLLO_SOURCES", raising=False)
    assert call(capsys, monkeypatch, {"op": "set", "key": "folders.sources", "value": str(mine)})[0] == 0
    assert sourcedrop.folder() == mine
    assert call(capsys, monkeypatch, {"op": "set", "key": "folders.sources", "value": str(tmp_path / "missing")})[0] == 2


def test_the_basemap_switch_is_hydras(capsys, monkeypatch, tmp_path):
    code, got, _ = call(capsys, monkeypatch, {"op": "network", "name": "basemap", "on": False})
    assert code == 0 and got["network"] == {"basemap": False}
    assert json.loads((tmp_path / "basemap" / "basemap.json").read_text())["online"] is False
    assert call(capsys, monkeypatch, {"op": "network", "name": "wikidata", "on": True})[0] == 2

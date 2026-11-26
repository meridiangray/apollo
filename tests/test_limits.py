"""Limits: a run starts without run-level caps (--no-limits), and a live run's caps are switched through
<run_folder>/pageflow/limits.json. Scratch folders only (conftest)."""
import json

import yaml

import apollo


def cfg_of(rec):
    return yaml.safe_load(next(t for f, t in rec["files"].items() if f.endswith("fleet-config.yaml")))


def test_no_limits_starts_the_driver_with_limits_false():
    on = apollo.plan(flow="read", source="pollard", pages="1-11", name="a")
    off = apollo.plan(flow="read", source="pollard", pages="1-11", name="b", limits=False)
    assert "limits" not in cfg_of(on) and on.get("limits", True) is True
    assert cfg_of(off)["limits"] is False and off["limits"] is False
    assert cfg_of(off)["budget_usd"] == cfg_of(on)["budget_usd"]           # the caps stay in the config, to switch back


def test_the_cli_flag_reaches_start(monkeypatch, capsys):
    seen = {}

    def fake(*a, **k):
        seen["limits"] = a[-1]
        return {"id": "x", "pid": 1, "kind": "pageflow", "run_folder": "/x", "dry": True, "fast": False}
    monkeypatch.setattr(apollo, "start", fake)
    assert apollo.main(["start", "--flow", "read", "--source", "pollard", "--no-limits", "--json"]) == 0
    assert seen["limits"] is False
    assert apollo.main(["start", "--flow", "read", "--source", "pollard", "--json"]) == 0
    assert seen["limits"] is True


def test_the_limits_command_writes_the_file_the_summary_reads(tmp_path, monkeypatch):
    rec = {"id": "r1", "kind": "pageflow", "run_folder": str(tmp_path / "run")}
    monkeypatch.setattr(apollo, "find", lambda rid: rec)
    monkeypatch.setattr(apollo, "alive", lambda r: True)
    assert apollo.limits_of(rec) is True
    assert apollo.set_limits("r1", False)["ok"]
    f = tmp_path / "run" / "pageflow" / "limits.json"
    assert json.loads(f.read_text())["limits"] is False and apollo.limits_of(rec) is False
    assert apollo.set_limits("r1", True)["ok"] and apollo.limits_of(rec) is True
    assert apollo.limits_of({**rec, "limits": False, "run_folder": str(tmp_path / "none")}) is False
    monkeypatch.setattr(apollo, "alive", lambda r: False)
    assert not apollo.set_limits("r1", False)["ok"]

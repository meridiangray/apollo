"""The chat bubble's replies (chat.py): the request's checks, the context the model is told, the `claude` command (the model, the
hydra read-only tools and nothing that writes), the job with a fake `claude` (stream-json on stdout), the failures, and the
session log under state/chat. No real model is ever called."""
import base64
import json
import stat
from pathlib import Path

import pytest

import apollo
import chat
from conftest import wait_for


def fake_claude(tmp_path: Path, monkeypatch, body: str) -> Path:
    """A `claude` that records its arguments and stdin, then runs `body` (sh)."""
    p = tmp_path / "claude"
    p.write_text("#!/bin/sh\n"
                 f'printf "%s\\n" "$@" > "{tmp_path}/args.txt"\n'
                 f'cat > "{tmp_path}/stdin.txt"\n' + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("APOLLO_CLAUDE", str(p))
    return p


def stream(*events) -> str:
    return "".join(f"printf '%s\\n' '{json.dumps(e)}'\n" for e in events)


ASSISTANT = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me look."},
                                                          {"type": "tool_use", "name": "mcp__hydra__find_entities", "input": {}}]}}
RESULT = {"type": "result", "subtype": "success", "is_error": False, "result": "Ana Reyes was the secretary.", "total_cost_usd": 0.012}


def test_the_request_is_checked():
    for bad in ({"messages": []}, {"messages": [{"role": "user", "text": "x"}], "model": "gpt"},
                {"messages": [{"role": "user", "text": "x"}, {"role": "assistant", "text": "y"}]},
                {"messages": [{"role": "system", "text": "x"}]}, {"messages": [{"role": "user", "text": "x"}], "session": "../x"}, []):
        with pytest.raises(ValueError):
            chat.clean(bad)
    r = chat.clean({"messages": [{"role": "user", "text": " " + "x" * 9000}], "context": {"view": "nope", "run": "r1", "source": "a\nb"}})
    assert r["model"] == "sonnet" and len(r["messages"][0]["text"]) == chat.MAX_TEXT
    assert r["context"] == {"run": "r1"}


def test_the_prompt_carries_the_thread():
    assert chat.prompt_text([{"role": "user", "text": "Hi"}]) == "Hi"
    t = chat.prompt_text([{"role": "user", "text": "Who?"}, {"role": "assistant", "text": "Ana."}, {"role": "user", "text": "When?"}])
    assert "Person: Who?" in t and "You: Ana." in t and t.endswith("When?")


def test_the_context_names_the_run_and_only_real_archives(tmp_path):
    arc = tmp_path / "arc"
    arc.mkdir()
    (arc / "hydra.yaml").write_text("name: x\n")
    facts = {"flow": "Basic", "status": "done", "source": "pollard", "archive": str(arc)}
    lines = chat.context_lines({"view": "runs", "run": "r1", "archive": str(tmp_path / "not-one")}, lambda rid: facts)
    text = "\n".join(lines)
    assert "the Runs view" in text and "Selected run: r1 (flow Basic, status done, source pollard)." in text
    assert str(arc) in text and "not-one" not in text and "`archive`" in text
    assert "Selected run: r2." in chat.context_lines({"run": "r2"}, lambda rid: (_ for _ in ()).throw(OSError("x")))


def test_the_command_allows_only_hydra_read_tools(tmp_path):
    cmd = chat.command("opus", tmp_path / "mcp.json", "sys")
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--tools") + 1] == "ToolSearch"
    allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--append-system-prompt")]
    assert "mcp__hydra__read_source" in allowed and "mcp__hydra__details" in allowed
    for w in ("save", "amend_records", "create_archive", "task_new", "collection_create", "field_assign"):
        assert f"mcp__hydra__{w}" not in allowed
    assert not {"Write", "Edit", "Bash"} & set(cmd)


def start(messages, **kw) -> dict:
    return chat.begin({"messages": messages, **kw})


def test_a_reply_with_a_fake_claude(tmp_path, monkeypatch):
    fake_claude(tmp_path, monkeypatch, stream({"type": "system", "subtype": "init"}, ASSISTANT, RESULT))
    out = start([{"role": "user", "text": "Who was the secretary?"}, {"role": "assistant", "text": "Of what?"},
                 {"role": "user", "text": "The Harbour Board."}], model="haiku", context={"view": "sources", "source": "pollard"})
    assert out["ok"] and out["id"] == out["session"] + "-1"
    got = wait_for(lambda: (lambda g: g if g["status"] != "running" else None)(chat.get(out["id"])))
    assert got["status"] == "done" and got["text"] == "Ana Reyes was the secretary." and got["tools"] == ["find_entities"]
    args = (tmp_path / "args.txt").read_text().splitlines()
    assert args[args.index("--model") + 1] == "claude-haiku-5-5"
    assert args[args.index("--append-system-prompt") + 1].startswith("You are the chat inside Apollo")
    whole = (tmp_path / "args.txt").read_text()                 # the system prompt spans lines
    assert "read-only" in whole and "Sources view" in whole and "Selected source: pollard." in whole
    sent = (tmp_path / "stdin.txt").read_text()
    assert "Person: Who was the secretary?" in sent and "You: Of what?" in sent and sent.rstrip().endswith("The Harbour Board.")
    mcp = json.loads((chat.folder() / "mcp.json").read_text())
    assert "mcpServers" in mcp
    rows = chat.log(out["session"])
    assert [r["who"] for r in rows] == ["me", "reply"] and rows[1]["text"] == "Ana Reyes was the secretary." and rows[1]["cost_usd"] == 0.012
    # the next message in the same session is its second reply
    again = start([{"role": "user", "text": "Thanks"}], session=out["session"])
    assert again["id"] == out["session"] + "-2"
    wait_for(lambda: chat.get(again["id"])["status"] != "running")
    assert [r["who"] for r in chat.log(out["session"])] == ["me", "reply", "me", "reply"]


def test_a_failed_reply_says_why(tmp_path, monkeypatch):
    fake_claude(tmp_path, monkeypatch, "echo 'Error: not logged in' >&2\nexit 1\n")
    out = start([{"role": "user", "text": "x"}])
    got = wait_for(lambda: (lambda g: g if g["status"] != "running" else None)(chat.get(out["id"])))
    assert got["status"] == "failed" and "not logged in" in got["error"]
    fake_claude(tmp_path, monkeypatch, stream({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "result": ""}))
    out = start([{"role": "user", "text": "y"}])
    got = wait_for(lambda: (lambda g: g if g["status"] != "running" else None)(chat.get(out["id"])))
    assert got["status"] == "failed" and "error_max_budget_usd" in got["error"]
    assert [r["who"] for r in chat.log(out["session"])] == ["me"]


def test_the_manager_commands(tmp_path, monkeypatch, capsys):
    fake_claude(tmp_path, monkeypatch, stream(RESULT))
    req = base64.urlsafe_b64encode(json.dumps({"model": "opus", "messages": [{"role": "user", "text": "Hi"}]}).encode()).decode().rstrip("=")
    assert apollo.main(["chatstart", "--req64", req]) == 0
    out = json.loads(capsys.readouterr().out)
    wait_for(lambda: chat.get(out["id"])["status"] != "running")
    assert apollo.main(["chatget", "--id", out["id"]]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "Ana Reyes was the secretary."
    assert apollo.main(["chatget", "--id", "../x"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert apollo.main(["chatstart", "--req64", "!!"]) == 2
    capsys.readouterr()

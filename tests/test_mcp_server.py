"""Apollo's MCP server: its tools call apollo.py as a subprocess, so these run it against the scratch state and the fake
driver from conftest. Nothing here touches the owner's runs."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

import apollo
import mcp_server as m

SERVER = Path(m.__file__)


def test_tool_list_is_the_small_set():
    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert names == {"runs", "run", "sources", "ready", "start", "stop", "clear", "query"}


def test_stdio_handshake_lists_the_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}
    got = asyncio.run(asyncio.wait_for(go(), 60))
    assert "start" in got and len(got) == 8


def test_start_previews_without_starting_then_confirms(scratch, waiter):
    pre = m.start("read", source="pollard", pages="20-21", archive="new", dry=True)
    assert pre.get("preview"), pre
    assert pre["started"] is False and pre["model_calls"] == "no" and "ready" in pre
    assert m.runs() == {"runs": []}
    got = m.start("read", source="pollard", pages="20-21", archive="new", dry=True, confirm=True)
    assert got["started"] and got["id"]
    waiter(lambda: apollo.exit_info(got["id"]))
    rows = m.runs()["runs"]
    assert [r["id"] for r in rows] == [got["id"]] and rows[0]["dry"] is True
    out = m.run(got["id"][-12:], "found")
    assert out["tab"] == "found" and out["status"]["id"] == got["id"]
    assert m.run(got["id"], "bogus")["error"]
    # clear: preview keeps it, confirm deletes it
    pv = m.clear(got["id"])
    assert pv["preview"] and pv["cleared"] is False and len(m.runs()["runs"]) == 1
    assert m.clear(got["id"], confirm=True)["ok"]
    assert m.runs()["runs"] == []


def test_clear_refused_while_running(scratch, smoke_config):
    got = apollo.start(str(smoke_config), None, None, True, False, None, None, None, None, None, None, None, None, None, None, False, None)
    try:
        out = m.clear(got["id"], confirm=True)
        assert out["error"] and "running" in out["why"] and out["do"]
    finally:
        apollo.stop(got["id"])


def test_query_guard_and_rows(scratch):
    ok = m.query("select count(*) as n from runs")
    assert ok["ok"] and ok["rows"] in ([[0]], [{"n": 0}])
    bad = m.query("delete from runs")
    assert set(bad) == {"error", "why", "do"}


def test_error_shape_for_unknown_run_and_stale_code(scratch, monkeypatch, tmp_path):
    out = m.stop("nope")
    assert set(out) == {"error", "why", "do"} and "no such run" in out["why"]
    broken = tmp_path / "apollo.py"
    broken.write_text("raise SystemExit(Exception)\nimport nonexistent_module_x\n", encoding="utf-8")
    monkeypatch.setattr(m, "APOLLO", broken)
    got = m.sources()
    assert set(got) == {"error", "why", "do"} and "stale" in got["do"]


def test_big_tab_is_cut_and_says_so():
    view = m._cut({"rows": [{"i": i} for i in range(100)], "total": 100})
    assert len(view["rows"]) == m.MAX_ROWS and view["cut"]["lists"]["rows"] == {"shown": m.MAX_ROWS, "of": 100}

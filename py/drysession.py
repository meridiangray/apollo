#!/usr/bin/env python3
"""drysession.py: a stand-in for `claude -p ... --output-format stream-json` in a dry MCP session (apollo --dry).

It takes the same arguments (and ignores them), reads the brief on stdin, and writes a short canned stream-json
transcript to stdout, a line every APOLLO_DRY_STEP seconds (default 1): the init, a few hydra tool calls with their
results (one an error), some text, and a result with no cost. No model, no MCP server, no network: it exists so the
Apollo app's session views (Run, Calls) and the CLI can be exercised end to end for nothing.
"""
from __future__ import annotations

import json
import os
import sys
import time


def events(brief: str, model: str) -> list[dict]:
    first = next((ln.strip("# ").strip() for ln in brief.splitlines() if ln.strip()), "the brief")
    calls = [("mcp__hydra__guide", {"topic": "start"}, False, "guides: start, rules, sources"),
             ("mcp__hydra__tasks", {}, False, "no open tasks"),
             ("mcp__hydra__task_new", {"title": "dry run task"}, False, "tsk-dry0000001"),
             ("mcp__hydra__read_source", {"path": "source.pdf"}, True, "dry run: no source here"),
             ("Read", {"file_path": "brief.md"}, False, "the brief"),
             ("mcp__hydra__task_step", {"id": "tsk-dry0000001", "step": "A1"}, False, "ticked")]
    out = [{"type": "system", "subtype": "init", "model": model, "tools": ["mcp__hydra__guide"], "session_id": "dry"},
           {"type": "assistant", "message": {"content": [{"type": "text", "text": f"Dry run. Brief: {first[:80]}"}],
                                             "usage": {"input_tokens": 0, "output_tokens": 0}}}]
    for i, (name, args, err, result) in enumerate(calls, 1):
        out.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"tu{i}", "name": name, "input": args}], "usage": {"output_tokens": 0}}})
        out.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"tu{i}", "is_error": err, "content": result}]}})
    out.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done: nothing was written."}]}})
    out.append({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0,
                "num_turns": len(calls) + 2, "duration_ms": 0, "result": "dry run"})
    return out


def main(argv: list[str]) -> int:
    model = argv[argv.index("--model") + 1] if "--model" in argv else "dry"
    brief = sys.stdin.read() if not sys.stdin.isatty() else ""
    step = float(os.environ.get("APOLLO_DRY_STEP", "1"))
    for ev in events(brief, model):
        sys.stdout.write(json.dumps(ev) + "\n")
        sys.stdout.flush()
        time.sleep(step)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

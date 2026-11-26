"""A stand-in for pageflow.py in Apollo's process tests: `fake_driver.py run <config> --no-window`.

It writes what the real driver writes for a window (progress.json, trace.jsonl, found.jsonl, hydra.jsonl, models.jsonl,
state.json, report.md) through the real `Progress`, then ends `done`; with `fake_hold: <seconds>` in the config it
waits that long first (for the stop test; `resume` skips it). It also writes run-config.yaml and appends each argv to seen-argv.jsonl. It also records the config it was given (`seen-config.json`).
"""
import json
import os
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str((Path(os.environ.get("APOLLO_TESTING") or Path(__file__).resolve().parents[2] / "hydra-mcp-testing")) / "pageflow"))
import progress  # noqa: E402


def main(argv):
    resuming = argv[0] == "resume"
    cfg = yaml.safe_load(Path(argv[1]).read_text(encoding="utf-8"))
    folder = Path(cfg["run_folder"])
    pf = folder / "pageflow"
    p = progress.Progress(pf)
    (folder / "run-config.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with (folder / "seen-argv.jsonl").open("a") as f:
        f.write(json.dumps(argv) + "\n")
    (folder / "seen-config.json").write_text(json.dumps({"argv": argv, "cfg": cfg}), encoding="utf-8")
    state = {"run": cfg["run"], "archive": cfg["archive"]["path"], "status": "running",
             "models": {"used": {"haiku": 3, "sonnet": 1}, "budgets": cfg.get("budgets") or {},
                        "tokens": {"haiku": {"cost_usd": 0.0}, "sonnet": {"cost_usd": 0.0}}}}
    (pf / "state.json").write_text(json.dumps(state), encoding="utf-8")
    p.phase("preflight", 7)
    p.step(7, 7)
    p.phase("A", 2)
    for n in (20, 21):
        p.page(n, "doing")
        p.trace("Asked", "A", descriptor="Page", who="haiku", page=n)
        p.found([{"page": n, "schema": "Person", "thing": f"Person {n}", "quote": "A sentence.", "state": "proposed"}])
        p.step()
    with (pf / "hydra.jsonl").open("a") as f:
        for i, (tool, ms, ok) in enumerate([("guide", 400, True), ("save", 9000, True), ("cite", 120, False)]):
            f.write(json.dumps({"at": f"10:00:0{i}", "tool": tool, "ms": ms, "ok": ok}) + "\n")
    with (pf / "models.jsonl").open("a") as f:
        f.write(json.dumps({"model": "haiku", "label": "A p20", "ms": 1, "ok": True}) + "\n")
    hold = 0 if resuming else float(cfg.get("fake_hold") or 0)     # a resume carries on to done
    end = time.time() + hold
    while time.time() < end:
        p.trace("Waited", "hold", descriptor="Test")
        time.sleep(0.2)
    p.page(20, "done")
    p.page(21, "done")
    state["status"] = "done"
    (pf / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "report.md").write_text("# Report\n\n## Counts\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    p.finish("done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

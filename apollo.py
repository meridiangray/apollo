#!/usr/bin/env python3
"""apollo.py: start, watch and stop hydra-mcp agent runs on this Linux (WSL) machine. No GUI; app.py is the window.

    apollo.py start <config.yaml|brief.md> [--name N] [--model M] [--dry] [--fast] [--pages A-B] [--budget B]
                                          [--archive PATH]
    apollo.py start --recipe R --source S [--name N] [--model M] [--dry] [--fast] [--pages A-B] [--budget B]
    apollo.py start --flow read --source S [--pages A-B] [--budget B] [--lag] [--without rate,escalate] [--archive draft|PATH]
    apollo.py ls [--json]                  every run Apollo knows, newest last
    apollo.py status <id> [--json]         one run: progress, calls, cost, tasks, questions, report
    apollo.py stop <id>                    SIGTERM to that run's own process group (the one this manager started)
    apollo.py tail <id> [-n 20]            the run's last trace lines
    apollo.py view <id> [--tab run|found|questions|calls|raw|report] [--json]   one tab of the window, as rows
                                          (--row i: what log row i stands for; --n k: model call k in full)
    apollo.py configs [--json]             the recipes, sources and briefs that can be started ({recipes, sources, briefs})
    apollo.py configs --legacy --json      the old flat list of configs and briefs
    apollo.py sourcelist | sourceadd PATH | sourceupdate SLUG --fields JSON | sourceremove SLUG    dropped sources
    apollo.py pagetext SOURCE PAGE         one page of a source's text (or one part of a text source)
    apollo.py collections ARCHIVE [--collection NAME]    an archive's collections, or one collection's records
    apollo.py draft [--reset]              the Draft archive every session starts with (made empty if missing)
    apollo.py logdb --sql "<SELECT ...>"   the DuckDB log (state/apollo.duckdb, runlog.py), read-only; --rebuild ingests every run folder
    apollo.py proofreadstart|proofreadget|proofreadact   Proofread a PDF source's pages with Haiku and the page image (proofread.py)

A page-flow run is a recipe (the flow, pageflow/recipes/) on a source (pageflow/sources/): `start --recipe R --source S`
composes them (pageflow/compose.py) with the pages and writes the effective config into the run folder. A `.yaml` given
to `start` is an already-composed run config; the driver runs it as it is. A `.md` is a
brief for an MCP agent session: `claude -p` with hydra's Linux MCP client config, the brief on stdin, as the earlier
agentic runs did (RUN-AGENTIC-TEST.md; hydra's Tasks-page launcher builds the same command).

- `--dry`: no model is called. A page-flow run uses canned answers (pageflow/drystub.py) and real hydra-mcp, writing a
  throwaway archive under apollo/dry/; a session replays a canned transcript (drysession.py).
- `--fast` (pending the owner's OK): the run works on WSL's own disk, ~/hydra-ws/fleet-runs/<run>, with the mirror venv
  after ~/hydra-ws/sync.py, and is copied back to hydra-mcp-testing/runs/<run> (and archives/<run>) when it ends.
- Default: runs/<run> and archives/<run> under hydra-mcp-testing, the owner's rule.

The registry is apollo/state/<id>.json (one file per run, written by `start` and `stop`); the run's own wrapper
(`apollo.py exec <id>`, started in a new session so it leads its process group) writes <id>.exit.json when the run
ends. Closing the app or this command never stops a run. `stop` signals only a group this manager started, after
checking /proc that the PID is still that run's wrapper; never a process by name.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import archives  # noqa: E402
import runfiles  # noqa: E402
import runview  # noqa: E402
import sourcedrop  # noqa: E402

TESTING = runfiles.TESTING                                   # hydra-mcp-testing: APOLLO_TESTING, else beside this folder

HOME = Path.home()
SHARED = "/mnt/e/Documents/development/stable/"
WS = HOME / "hydra-ws"
WS_PYTHON = HOME / ".venvs" / "hydra-ws" / "bin" / "python"
HYDRA_PYTHON = HOME / ".venvs" / "hydra" / "bin" / "python"      # the page-flow driver's (and hydra-ctl's) interpreter
MCP_CLIENT = TESTING / "clients" / "claude-code-hydra-linux.json"
# the tools an agent session gets besides hydra's: hydra's Tasks-page launcher's READONLY_TOOLS
# (hydra-git src/hydra/agents/launchers.py); its claude-p command is the shape built here
SESSION_TOOLS = ["ToolSearch", "Read", "Glob", "Grep", "Write"]
SESSION_MODEL = "claude-sonnet-5-5"
LOG_EVERY = 5                                                # seconds between a live run's log ingests
STOP_GRACE = 30                                             # seconds a stopped run gets before its child is killed
SCHEMA = 1                                                   # the --json shape's version
# What a run does, in the window's Flow select: key, label. `read` is the page flow (the `full` recipe plus the window's
# fields); the others are hydra workflows a session runs on an archive.
FLOWS = runview.FLOWS
FLOW_LABEL = dict(FLOWS)
BASE_RECIPE = "full"                                         # the recipe every Flow-select run is composed from


# ---- places ---------------------------------------------------------------------------------------------------------

def state_dir() -> Path:
    return Path(os.environ.get("APOLLO_STATE") or HERE / "state")


def root_for(dry: bool) -> Path:
    """Where runs/ and archives/ go: hydra-mcp-testing (the owner's rule), apollo/dry/ for a dry run."""
    env = os.environ.get("APOLLO_ROOT")
    if env:
        return Path(env) / ("dry" if dry else "")
    return HERE / "dry" if dry else TESTING


def driver() -> Path:
    return Path(os.environ.get("APOLLO_DRIVER") or TESTING / "pageflow" / "pageflow.py")


def driver_python() -> str:
    if os.environ.get("APOLLO_RUN_PYTHON"):
        return os.environ["APOLLO_RUN_PYTHON"]
    return str(HYDRA_PYTHON) if HYDRA_PYTHON.exists() else sys.executable


def claude_path() -> str:
    """`claude` lives in ~/.local/bin, which a bare `sh -c` PATH in WSL lacks: name it by path."""
    if os.environ.get("APOLLO_CLAUDE"):
        return os.environ["APOLLO_CLAUDE"]
    p = HOME / ".local" / "bin" / "claude"
    return str(p) if p.exists() else (shutil.which("claude") or "claude")


def to_ws(path: str) -> str:
    """A path on the Windows drive as the mirror holds it (unchanged when it is not under the shared source)."""
    return str(WS / path[len(SHARED):]) if str(path).startswith(SHARED) else str(path)


# ---- the registry ---------------------------------------------------------------------------------------------------

def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save(rec: dict) -> None:
    _write(state_dir() / f"{rec['id']}.json", rec)


def load(rid: str) -> dict | None:
    return runfiles.read_json(state_dir() / f"{rid}.json")


def exit_info(rid: str) -> dict | None:
    return runfiles.read_json(state_dir() / f"{rid}.exit.json")


def records() -> list[dict]:
    out = []
    for f in sorted(state_dir().glob("*.json")):
        if f.name.endswith((".exit.json", ".live.json")):
            continue
        r = runfiles.read_json(f)
        if isinstance(r, dict) and r.get("id"):
            out.append(r)
    return sorted(out, key=lambda r: r.get("started") or "")


def find(rid: str) -> dict | None:
    """A run by its id, or by a unique part of it."""
    r = load(rid)
    if r:
        return r
    hits = [x for x in records() if rid in x["id"]]
    return hits[0] if len(hits) == 1 else None


# ---- processes ------------------------------------------------------------------------------------------------------

def marker(rid: str) -> str:
    return f"exec {rid}"


def alive(rec: dict) -> bool:
    """The run's wrapper is running: its PID exists, is no zombie, and its command line is this run's wrapper."""
    pid = rec.get("pid")
    if not pid:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    if stat.rsplit(")", 1)[-1].split()[0] in ("Z", "X"):
        return False
    return "apollo.py" in cmd and marker(rec["id"]) in cmd


_children: list = []                                       # Popen objects this process started (reaped by reap())


def reap() -> None:
    for p in list(_children):
        if p.poll() is not None:
            _children.remove(p)


# ---- start ----------------------------------------------------------------------------------------------------------

def kind_of(path: Path) -> str:
    if path.suffix in (".yaml", ".yml"):
        return "pageflow"
    if path.suffix in (".md", ".txt"):
        return "mcp-session"
    raise ValueError(f"{path.name}: a page-flow config (.yaml) or a brief (.md)")


def slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:50] or "run"


def new_id(base: str, dry: bool, fast: bool) -> str:
    day = datetime.now().strftime("%Y-%m-%d")
    base = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", base)          # a config's dated run name: the date is today's
    stem = f"{day}-{'dry-' if dry else ''}{slug(base)}"
    root = root_for(dry)
    for i in range(1, 1000):
        rid = stem if i == 1 else f"{stem}-{i}"
        taken = (state_dir() / f"{rid}.json").exists() or (root / "runs" / rid).exists() or \
            (root / "archives" / rid).exists() or (fast and (WS / "fleet-runs" / rid).exists())
        if not taken:
            return rid
    raise RuntimeError(f"no free run id for {stem}")


def parse_pages(pages: str | None):
    if not pages:
        return None
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", pages.strip())
    if not m:
        raise ValueError("--pages: A-B, e.g. 20-22")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if b < a:
        raise ValueError("--pages: A-B with A <= B")
    return a, b


def default_budget(pages: int) -> float:
    """The cost cap a run of `pages` pages starts with: $1 up to 11 pages, else about $0.045 a page (the full recipe's $5 over
    its 107 pages) rounded up to the next $0.50, at least $1 and at most $5. The window's Cost cap field shows the same
    number and lets the owner change it."""
    import math
    return max(1.0, min(5.0, math.ceil(pages * 0.045 / 0.5) * 0.5)) if pages > 11 else 1.0


def _budget(v):
    if v is None:
        return "keep"
    if str(v).strip().lower() in ("none", "no", "unlimited"):
        return None
    return float(v)


def sources_extra() -> str:
    """PAGEFLOW_SOURCES_EXTRA for any process that composes or runs a page flow: what is set already, then the folder of
    the sources the owner dropped in (compose.source_dirs() reads it)."""
    mine = str(sourcedrop.folder())
    have = [d for d in os.environ.get("PAGEFLOW_SOURCES_EXTRA", "").split(os.pathsep) if d.strip() and d != mine]
    return os.pathsep.join(have + [mine])


def composer():
    """pageflow/compose.py of the testing folder (recipes x sources), loaded by path so no other `compose` can answer."""
    import importlib.util
    os.environ["PAGEFLOW_SOURCES_EXTRA"] = sources_extra()
    spec = importlib.util.spec_from_file_location("pageflow_compose", TESTING / "pageflow" / "compose.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plan(path=None, name=None, model=None, dry=False, fast=False, pages=None, budget=None, archive=None,
         recipe=None, source=None, rate=None, workflow=None, models=None, without=None, flow=None, lag=False,
         after=None, scope=None, limits=True, for_source=None, agents=None) -> dict:
    """The run's record and files, decided; nothing written, nothing started. A page-flow run is a `recipe` on a `source`
    (composed here) or an already-composed `path` (.yaml); a session is a brief (.md) whose {archive}, {source} and
    {run_folder} are filled from `archive`, `source` (a source slug) and `rate` (the id of the run it rates).
    `for_source`: the source a workflow session was started for (recorded on the run; the brief does not see it).
    `agents`: {haiku, sonnet, opus} counts for the flow (see agents_for); missing grades take the flow's default."""
    final_root = root_for(dry)
    if flow:                                                 # the window's Flow select: read = the full recipe + the fields
        if flow not in FLOW_LABEL:
            raise ValueError(f"no flow {flow!r} (one of {', '.join(FLOW_LABEL)})")
        if path or recipe or workflow:
            raise ValueError("--flow is a start of its own")
        if flow == "read":
            if not source:
                raise ValueError("--flow read goes with --source")
            recipe = BASE_RECIPE
        else:
            workflow = flow
    if lag and flow != "read":
        raise ValueError("--lag goes with --flow read")
    if agents and not flow:
        raise ValueError("--agents goes with --flow")
    counts = agents_for(flow, agents) if flow else None
    if after and (flow != "read" or after not in AFTER):
        raise ValueError(f"--after goes with --flow read, and is one of {', '.join(AFTER)}")
    existing = None
    if archive and str(archive).lower() == "draft" and (workflow or recipe):
        existing = archives.ensure_draft()["path"]
        archive = existing
    elif archive and str(archive).lower() == "new":
        archive = None
    elif archive and recipe:                                 # a page flow adds to an archive of Apollo's own, nowhere else
        import tools
        existing = str(tools.allowed_archive(str(archive)))
        archive = existing
    if workflow:
        if workflow not in {w[0] for w in WORKFLOWS}:
            raise ValueError(f"no workflow {workflow!r} (one of {', '.join(w[0] for w in WORKFLOWS)})")
        if not archive:
            raise ValueError("--workflow goes with --archive")
        if recipe or path:
            raise ValueError("--workflow is a start of its own")
        path, name = (WIKI_BRIEF if workflow == "wiki" else WORKFLOW_BRIEF), name or f"workflow-{workflow}"
    if models is not None and not re.fullmatch(r"[a-z]+=[A-Za-z0-9._:/-]+(,[a-z]+=[A-Za-z0-9._:/-]+)*", models):
        raise ValueError("--models role=model,role=model")
    if recipe and not source:
        raise ValueError("--recipe goes with --source")
    if source and not recipe and not (path and Path(path).suffix in (".md", ".txt")):
        raise ValueError("--source goes with --recipe, or with a brief")
    if recipe:
        cp = composer()
        cp.load_recipe(recipe)                                   # unknown names fail before an id is taken
        cp.load_source(source)
        if existing and fast:
            raise ValueError("--fast cannot add to an existing archive")
        kind, text, cfg = "pageflow", None, None
        base = name or (f"read-{source}" if flow else f"{recipe}-{source}")
        path = cp.recipe_path(recipe)
    else:
        if not path:
            raise ValueError("a config (.yaml), a brief (.md), or --recipe and --source")
        path = Path(path).resolve()
        if not path.is_file():
            raise ValueError(f"{path} not found")
        kind = kind_of(path)
        text = path.read_text(encoding="utf-8")
        cfg = yaml.safe_load(text) if kind == "pageflow" else None
        base = name or (cfg.get("run") if cfg else None) or path.stem
    rid = new_id(base, dry, fast)
    if recipe:
        over = {}
        if lag:                                                  # the lag recipe's lag settings and the tools it is allowed
            lr = composer().load_recipe("lag")
            over = {"lag": lr["lag"], "authorized": lr["authorized"]}
        cfg = composer().compose(recipe, source, pages=pages, name=rid, root=final_root, overrides=over or None)
        if flow == "read" and budget is None:                    # the window's Cost cap default, by the pages of the run
            budget = default_budget(cfg["pages"]["to"] - cfg["pages"]["from"] + 1)
        if existing:
            composer().use_archive(cfg, existing)
    live = WS / "fleet-runs" / rid if fast else None
    run_folder = live / "run" if fast else final_root / "runs" / rid
    rec = {"id": rid, "name": name or (f"read-{source}" if flow else f"{recipe}-{source}" if recipe else path.stem),
           "kind": kind, "config": str(path),
           "recipe": recipe, "source": source, "dry": bool(dry), "fast": bool(fast), "flow": flow,
           "opts": {"lag": bool(lag), "archive": existing, "without": without, "models": models, "after": after},
           "run_folder": str(run_folder), "final_folder": str(final_root / "runs" / rid),
           "started": None, "pid": None, "pgid": None, "status": "starting", "files": {}}
    b = _budget(budget)
    if kind == "pageflow":
        prefix = (cfg.get("archive") or {}).get("prefix") or "archive"
        arc = Path(existing) if existing else (live / "archive" / prefix) if fast else final_root / "archives" / rid / prefix
        cfg["run"] = rid
        cfg["run_folder"] = str(run_folder)
        cfg.setdefault("archive", {})["path"] = str(arc)
        cfg["archive"]["prefix"] = prefix
        if cfg["archive"].get("view_copy"):
            cfg["archive"]["view_copy"] = str(arc.with_name(prefix + "-view"))
        cfg["window"] = False                                # the Apollo app is the window
        if model:
            cfg.setdefault("models", {})["haiku"] = model
        for part in (models or "").split(","):                 # --models haiku=M,sonnet=M: the recipe's model roles
            if part:
                role, _, m = part.partition("=")
                if role not in (cfg.get("models") or {}):
                    raise ValueError(f"--models: no role {role!r} (roles: {', '.join(cfg.get('models') or {})})")
                cfg["models"][role] = m
        if pages:
            a, z = parse_pages(pages)
            cfg["pages"] = {"from": a, "to": z}
        if counts:                                           # Haiku agents = the pages worked at once (`parallel`), Sonnet = hard pages at once
            cfg["parallel"] = counts["haiku"]
            cfg["sonnet_parallel"] = counts["sonnet"]
        if b != "keep":
            cfg["budget_usd"] = b                                # the run's dollar cap (none: no limit)
        for role in (without or "").split(","):                # roles the run leaves out: lag, rate, supervise, escalate
            if role == "lag":
                cfg.setdefault("lag", {})["enabled"] = False
            elif role in ("rate", "supervise"):
                cfg.pop("rubric", None)
            elif role == "escalate":                             # no hard-page retries with the stronger model
                cfg.setdefault("budgets", {})["escalations"] = 0
            elif role:
                raise ValueError(f"--without lag, rate, supervise or escalate, not {role!r}")
        if not limits:                                       # limits: the run starts with no run-level caps (the driver reads it)
            cfg["limits"] = False
            rec["limits"] = False
        if dry:
            # pageflow/drystub.py: canned answers, no model call; no per-page caps (the stub's empty island answers
            # would only stop the flow), the Haiku and Sonnet call budgets stay; no network; no lag-check
            composer().apply_dry(cfg)
        if fast:
            cfg["shared_source"] = str(WS) + "/"
            cfg.setdefault("hydra", {})["command"] = str(run_folder / "hydra-mcp-ws.sh")
            for k in ("demo_archive", "rubric"):
                if cfg.get(k):
                    cfg[k] = to_ws(cfg[k])
            if (cfg.get("reference_archive") or {}).get("path"):
                cfg["reference_archive"]["path"] = to_ws(cfg["reference_archive"]["path"])
            if (cfg.get("source") or {}).get("file"):
                cfg["source"]["file"] = to_ws(cfg["source"]["file"])
        drv = to_ws(str(driver())) if fast else str(driver())
        py = str(WS_PYTHON) if fast else driver_python()
        cfg_path = run_folder / "fleet-config.yaml"
        rec.update({"model": (cfg.get("models") or {}).get("haiku"), "archive": str(arc),
                    "source_title": (cfg.get("source") or {}).get("title"),
                    "escalate": (cfg.get("models") or {}).get("sonnet"),
                    "budgets": cfg.get("budgets"), "budget_usd": cfg.get("budget_usd"),
                    "command": [py, drv, "run", str(cfg_path), "--no-window"], "cwd": str(run_folder),
                    "io": {"stdin": None, "stdout": str(run_folder / "driver.log")}})
        rec["files"] = {str(cfg_path): yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=110)}
        rec["agents"] = counts or {"haiku": cfg.get("parallel"), "sonnet": cfg.get("sonnet_parallel")}
        if fast:
            rec["files"][str(run_folder / "hydra-mcp-ws.sh")] = (
                f"#!/bin/sh\n# hydra-mcp on the WSL mirror (apollo --fast)\nexec {WS_PYTHON} -m hydra_mcp.server \"$@\"\n")
        if fast:
            rec["final_archive"] = str(root_for(False) / "archives" / rid / prefix)
    else:
        client = json.loads(MCP_CLIENT.read_text(encoding="utf-8"))
        if fast:
            client["mcpServers"]["hydra"].update({"command": str(WS_PYTHON), "args": ["-m", "hydra_mcp.server"]})
        mcp_path, prompt = run_folder / "mcp.json", run_folder / "prompt.md"
        m = model or SESSION_MODEL
        head = [str(HERE / "drysession.py")] if dry else []
        exe = [sys.executable, *head] if dry else [claude_path()]
        subs = sub_agents(counts, m) if counts else {}
        tools_ = [*SESSION_TOOLS, "Agent"] if subs else SESSION_TOOLS    # the Agent tool only when the session may dispatch
        cmd = [*exe, "-p", "--model", m, "--mcp-config", str(mcp_path), "--strict-mcp-config",
               "--tools", *tools_, "--allowedTools", "mcp__hydra", *tools_,
               "--output-format", "stream-json", "--verbose"]
        usd = None if b in ("keep", None) else b
        if usd is not None:
            cmd += ["--max-budget-usd", f"{usd:g}"]
        rec.update({"model": m, "archive": archive, "budget_usd": usd, "command": cmd, "cwd": str(run_folder),
                    "io": {"stdin": str(prompt), "stdout": str(run_folder / "transcript.jsonl"),
                           "stderr": str(run_folder / "stderr.txt")}})
        src_file = composer().load_source(source)["file"] if source else None
        if src_file and not src_file.startswith("/"):
            src_file = str(TESTING / src_file)
        rated = find(rate) if rate else None
        if rate and not rated:
            raise ValueError(f"no run {rate} to rate")
        text = composer().fill_placeholders(text, archive=archive or (rated or {}).get("archive"), source=src_file or ("none" if workflow else None),
                                            run_folder=(rated or {}).get("run_folder"), workflow=workflow)
        if workflow == "wiki":
            text += scope_line(archive, scope)
        if workflow == "links":
            text += scope_line(archive, scope) + LINKS_BRIEF
        text += "".join(SUBAGENT_LINE.format(n=n, grade=g) for g, n in subs.items())
        rec["agents"] = counts
        rec.update({"source": source or for_source, "rates": rated["id"] if rated else None,
                    "archive": archive or (rated or {}).get("archive")})
        rec["files"] = {str(mcp_path): json.dumps(client, indent=2) + "\n", str(prompt): text}
    return rec


# What a start's `agents` come to: {haiku, sonnet, opus} for the flow's grades (runview.FLOW_SPEC), missing ones at the default.
SUBAGENT_LINE = ("\nYou may dispatch up to {n} parallel sub-agents (model: {grade}, with the Agent tool) for independent parts of "
                 "the work, such as separate collections. The task's steps, its checks and every save stay with you.\n")


def grade_of(model: str | None) -> str:
    m = str(model or SESSION_MODEL).lower()
    return next((g for g in runview.GRADES if g in m), "sonnet")


def agents_for(flow: str, agents: dict | None) -> dict:
    """The agent counts of a run of `flow`: the flow's grades only, each an integer from 0 (Read: from 1) up to its max,
    default when not given. A grade the flow cannot use must be missing or 0."""
    spec = (runview.FLOW_SPEC.get(flow) or {}).get("grades") or {}
    given = dict(agents or {})
    for g, v in given.items():
        if g not in runview.GRADES:
            raise ValueError(f"agents: no grade {g!r} (haiku, sonnet, opus)")
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"agents: {g} is a whole number from 0, not {v!r}")
        if g not in spec and v:
            raise ValueError(f"agents: the {runview.FLOW_LABEL[flow]} flow cannot use {g} agents")
    out = {}
    for g, lim in spec.items():
        v = given.get(g)
        v = lim["default"] if v is None else v
        low = 1 if flow == "read" else 0
        if not low <= v <= lim["max"]:
            raise ValueError(f"agents: {g} is {low} to {lim['max']} for {runview.FLOW_LABEL[flow]}, not {v}")
        out[g] = v
    return out


def sub_agents(counts: dict, model: str | None) -> dict:
    """What a workflow session may dispatch: the counts, less the session itself in its own grade; only grades left above 0."""
    own = grade_of(model)
    return {g: n - (g == own) for g, n in counts.items() if n - (g == own) > 0}


def parse_agents(text: str | None) -> dict | None:
    """`haiku=4,sonnet=2` (the --agents option) as {haiku: 4, sonnet: 2}."""
    if not text:
        return None
    out = {}
    for part in text.split(","):
        g, _, n = part.partition("=")
        if not n.isdigit():
            raise ValueError("--agents grade=number,grade=number")
        out[g.strip()] = int(n)
    return out


LINKS_BRIEF = (
    "\nThe whole job, not a sample: links_sweep handles every open suggestion of these collections (Merge, Resolve, "
    "casts) in one call; run it with confirm=true (the task authorizes it). Within one source a short form the page names "
    "one candidate for is merged on that passage; two candidates are a question. Wikidata matching is on: the person asks "
    "for it. The task's count step ticks only when suggestions left unhandled = 0; do not stop before it does.\n")


def scope_line(archive, scope=None) -> str:
    """The links workflow works on the entities of the collections a run added: those named, else every collection a run added
    to this archive (state/collections.json); an imported one is not a run's."""
    names = list(scope or [])
    if not names and archive:
        names = sorted(n for n, t in archives.tracked(str(archive)).items() if t.get("run"))
    if not names:
        return "\nCollections: none are named; ask the person which collections to work on before you start.\n"
    return f"\nCollections (the run's own; work on their entities only): {', '.join(names)}\n"


def start(path=None, name=None, model=None, dry=False, fast=False, pages=None, budget=None, archive=None,
          recipe=None, source=None, rate=None, workflow=None, models=None, without=None, flow=None, lag=False,
          after=None, scope=None, limits=True, for_source=None, agents=None) -> dict:
    """Write the run's files and registry entry, then start its wrapper in a new session. Returns the record."""
    rec = plan(path, name, model, dry, fast, pages, budget, archive, recipe, source, rate, workflow, models, without,
               flow, lag, after, scope, limits, for_source, agents)
    for f, text in rec["files"].items():
        Path(f).parent.mkdir(parents=True, exist_ok=True)
        Path(f).write_text(text, encoding="utf-8")
        if f.endswith(".sh"):
            os.chmod(f, 0o755)
    rec["files"] = sorted(rec["files"])
    rec["started"] = datetime.now().isoformat(timespec="seconds")
    save(rec)
    log = open(Path(rec["run_folder"]) / "fleet-exec.log", "ab")
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "exec", rec["id"]], cwd=rec["run_folder"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    log.close()
    _children.append(p)
    rec.update({"pid": p.pid, "pgid": p.pid, "status": "running"})
    save(rec)
    return rec


# ---- the wrapper: runs the command, records how it ended, copies a --fast run back ----------------------------------

def final_status(rec: dict, code: int | None, stopped: bool) -> tuple[str, str]:
    """(status, detail): done | stopped | failed, and the run's own word for it."""
    folder = Path(rec["run_folder"])
    if rec["kind"] == "pageflow":
        st = (runfiles.read_json(folder / "pageflow" / "state.json", {}) or {}).get("status") or ""
        if stopped:
            return "stopped", st or f"signal (exit {code})"
        if st == "done":
            return "done", st
        if st.startswith("stopped"):
            return "stopped", st
        return ("done" if code == 0 else "failed"), st or f"exit {code}"
    s = runfiles.Session()
    s.feed(runfiles.Tail(folder / "transcript.jsonl").all())
    r = s.result or {}
    if stopped:
        return "stopped", f"signal (exit {code})"
    if r and not r.get("is_error") and r.get("subtype") in (None, "success"):
        return "done", "success"
    return "failed", str(r.get("subtype") or f"exit {code}")


def copy_back(rec: dict) -> dict:
    """A --fast run's folder (and archive) from the WSL disk to hydra-mcp-testing/runs/<run> (archives/<run>)."""
    out = {}
    src, dst = Path(rec["run_folder"]), Path(rec["final_folder"])
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        out["run"] = str(dst)
    if rec.get("final_archive") and rec.get("archive") and Path(rec["archive"]).is_dir():
        shutil.copytree(rec["archive"], rec["final_archive"], dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("write.lock"))
        out["archive"] = rec["final_archive"]
    return out


def finish_archive(rec: dict) -> bool:
    """After a run that wrote to an archive ends: note which collections it added (state/collections.json), then build the
    archive once (hydra skips one that is fresh). Not for --fast runs (their archive is copied back) or an archive outside
    the roots Apollo builds in. True when it noted collections."""
    arch = rec.get("archive")
    if not arch or rec.get("fast") or not archives.is_archive(arch):
        return False
    import tools
    try:
        tools.allowed_archive(str(arch))
    except ValueError:
        return False
    noted = False
    if rec["kind"] == "pageflow":
        have = set(__import__("hydra.archive.collections_io", fromlist=["names"]).names(arch))
        names = [n for n in (run_config(rec).get("collections") or {}) if n in have]
        if names:
            archives.track(rec["id"], str(arch), names)
            noted = True
    tools.build_now(str(arch))
    return noted


def log_run(rid: str, live: bool = False, status: dict | None = None) -> None:
    """Bring the DuckDB log up to date for one run (runlog.py). Never raises: the files stay the truth, and the next call
    catches up. `live`: the run's wrapper is calling, so a run with no exit record yet is `running`. `status`: the run's
    {status, detail, ended, exit_code} when the caller knows it before the exit record is written."""
    try:
        import runlog
        rec = load(rid)
        if not rec:
            return
        if status is None:
            ex = exit_info(rid) or {}
            status = {"status": ex.get("status") or ("running" if live or alive(rec) else "died"), "detail": ex.get("detail"),
                      "ended": ex.get("ended"), "exit_code": ex.get("code")}
        runlog.ingest_run(rec, status)
    except Exception as e:                                   # noqa: BLE001 - said in the wrapper's own log, never raised
        print(f"{time.strftime('%H:%M:%S')} log not updated: {type(e).__name__}: {str(e)[:200]}", flush=True)


def _log_status(rec: dict) -> dict:
    ex = exit_info(rec["id"]) or {}
    return {"status": ex.get("status") or ("running" if alive(rec) else "died"), "detail": ex.get("detail"),
            "ended": ex.get("ended"), "exit_code": ex.get("code")}


def logdb(a) -> dict:
    """`logdb`: --sql/--sql64 (a read-only SELECT or WITH), or --rebuild (every run folder still on disk, and the tool runs)."""
    import runlog
    if a.rebuild:
        return {"ok": True, **runlog.rebuild(records(), _log_status)}
    if a.sql64:
        return runlog.sql(runlog.decode_sql(a.sql64))
    if a.sql:
        return runlog.sql(a.sql)
    return {"ok": False, "why": "--sql QUERY, or --rebuild"}


def chain(rec: dict) -> None:
    """A Read that ended well starts Links on the same archive (its Run after Read switch), for the collections it added."""
    arch = rec.get("archive")
    if not arch or not archives.is_archive(arch):
        return
    names = [n for n, t in archives.tracked(str(arch)).items() if t.get("run") == rec["id"]]
    nxt = start(archive=str(arch), workflow="links", dry=bool(rec.get("dry")), scope=names, name=f"links-{rec['source'] or rec['id']}",
                for_source=rec.get("source"))
    print(f"{time.strftime('%H:%M:%S')} started Links: {nxt['id']}", flush=True)


PREP_WAIT = 6 * 3600                                          # seconds a source's OCR or Text may take before the run goes on without it


def _wait_tool(tool: str, source: str, stopping: dict, poll: float = 1.0) -> dict:
    """Wait for a tool's job on a source to leave `running` (or for the run to be stopped, or PREP_WAIT); its last state."""
    import tools
    t0 = time.time()
    while True:
        got = tools.get(tool, source)
        if got.get("status") != "running" or stopping.get("at") or time.time() - t0 > PREP_WAIT:
            return got
        time.sleep(poll)


def prepare_source(source: str, stopping: dict | None = None, say=None) -> list[dict]:
    """What a Read needs of its source before the page flow starts, each step a no-op when it is already done: analyse
    (the Analyse tool), OCR of a PDF's pages that have no text layer, the Text tool's extraction of a PDF, and the
    waiting catalogue record (catalogue.basic). Never raises; every step is logged to the run's exec log (`say`) and the
    DuckDB log (history). Returns [{step, status: done | cached | skipped | failed, note}]."""
    import tools
    stopping = stopping if stopping is not None else {"at": None}
    out = []

    def note(step, status, why=""):
        out.append({"step": step, "status": status, "note": why})
        if say:
            say(f"prepare {step}: {status}" + (f" ({why})" if why else ""))

    bare, pdf = [], False
    try:
        pdf = tools.source_file(source).suffix.lower() == ".pdf"
    except ValueError as e:
        note("source", "failed", str(e))
        return out
    t0 = time.time()
    try:
        got = analyse(source)
        ok = got.get("ok", True)
        log_action("analyse", f"{source}-{int(t0 * 1000)}", {"source": source, "started": t0, "status": "done" if ok else "failed",
                                                             "error": None if ok else got.get("why")})
        note("analyse", "done" if ok else "failed", "" if ok else str(got.get("why") or ""))
        bare = [int(r["page"]) for r in got.get("pages") or [] if r.get("needs_ocr")]
    except Exception as e:                                   # noqa: BLE001 - the run's own preflight says it again
        note("analyse", "failed", str(e)[:200])
    for tool in ("ocr", "text"):
        if stopping.get("at"):
            break
        if not pdf or (tool == "ocr" and not bare):
            note(tool, "skipped", "not a PDF" if not pdf else "every page has text")
            continue
        try:
            if tools.get(tool, source).get("status") == "done":
                note(tool, "cached")
                continue
            if tool == "ocr" and not tools.ocr_ready():
                note(tool, "skipped", "OCR is not installed here")
                continue
            tools.begin(tool, source, args={"pages": toolargs_compact(bare)} if tool == "ocr" else None)
            last = _wait_tool(tool, source, stopping)
            note(tool, last.get("status") or "failed", str(last.get("error") or "") if last.get("status") != "done" else "")
        except ValueError as e:
            note(tool, "failed", str(e)[:200])
    if not stopping.get("at"):
        try:
            import catalogue
            basic = getattr(catalogue, "basic", None)
            if basic is None:
                note("catalogue", "skipped", "catalogue.basic is not there")
            else:
                basic(source)
                log_action("catalogue", f"{source}-basic-{int(time.time() * 1000)}", {"source": source})
                note("catalogue", "done")
        except Exception as e:                               # noqa: BLE001
            note("catalogue", "failed", str(e)[:200])
    return out


def toolargs_compact(nums: list[int]) -> str:
    import toolargs
    return toolargs.compact(nums)


def execute(rid: str) -> int:
    rec = load(rid)
    if not rec:
        print(f"no run {rid}")
        return 2
    stopping = {"at": None}

    def on_term(signum, frame):                              # the whole group got it; the child stops on its own
        stopping["at"] = stopping["at"] or time.time()
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PAGEFLOW_SOURCES_EXTRA": sources_extra(),
           "PATH": f"{HOME / '.local' / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    code, copied = None, {}
    if rec.get("fast"):
        print(f"{time.strftime('%H:%M:%S')} sync the mirror", flush=True)
        subprocess.run([str(WS_PYTHON), str(WS / "sync.py")], check=False)
    if rec["kind"] == "pageflow" and rec.get("flow") == "read" and rec.get("source") and not rec.get("dry"):
        try:                                                 # Analyse, OCR, Text and Catalogue run themselves before the page flow
            prepare_source(rec["source"], stopping, lambda t: print(f"{time.strftime('%H:%M:%S')} {t}", flush=True))
        except Exception as e:                               # noqa: BLE001 - the page flow's own preflight stands
            print(f"{time.strftime('%H:%M:%S')} prepare failed: {e}", flush=True)
    io = rec["io"]
    fin = open(io["stdin"], "rb") if io.get("stdin") else subprocess.DEVNULL
    fout = open(io["stdout"], "ab")
    ferr = open(io["stderr"], "ab") if io.get("stderr") else subprocess.STDOUT
    print(f"{time.strftime('%H:%M:%S')} start {' '.join(rec['command'])}", flush=True)
    try:
        child = subprocess.Popen(rec["command"], cwd=rec["cwd"], env=env, stdin=fin, stdout=fout, stderr=ferr)
        _write(state_dir() / f"{rid}.live.json", {"child_pid": child.pid, "wrapper_pid": os.getpid()})
        logged = 0.0
        while True:
            try:
                code = child.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if stopping["at"] and time.time() - stopping["at"] > STOP_GRACE:
                    child.kill()                             # our own child, by its Popen
                if time.time() - logged > LOG_EVERY:         # the DuckDB log follows the live run
                    log_run(rid, live=True)
                    logged = time.time()
    except OSError as e:
        print(f"could not start: {e}", flush=True)
        code = 127
    finally:
        for f in (fin, fout, ferr):
            if hasattr(f, "close"):
                f.close()
    stopped = bool(stopping["at"]) or bool((load(rid) or {}).get("stop_requested"))
    status, detail = final_status(rec, code, stopped)
    if rec.get("fast"):
        try:
            copied = copy_back(rec)
        except OSError as e:
            copied = {"error": str(e)}
    ended = datetime.now().isoformat(timespec="seconds")
    log_run(rid, status={"status": status, "detail": detail, "ended": ended, "exit_code": code})   # the whole run, before it reads as ended
    _write(state_dir() / f"{rid}.exit.json", {"code": code, "ended": ended, "status": status, "detail": detail, "copied": copied})
    print(f"{time.strftime('%H:%M:%S')} ended: {status} ({detail}), exit {code}", flush=True)
    try:
        if finish_archive(rec):                              # it noted collections: the log's `collections` rows follow
            log_run(rid)
        if status == "done" and (rec.get("opts") or {}).get("after") == "links":
            chain(rec)
    except Exception as e:                                   # noqa: BLE001 - the run is over; this follow-up never changes how it ended
        print(f"{time.strftime('%H:%M:%S')} archive follow-up failed: {e}", flush=True)
    return 0


# ---- status ---------------------------------------------------------------------------------------------------------

def _files(rec: dict) -> runfiles.RunFiles:
    return runfiles.RunFiles(rec["run_folder"], rec["kind"], rec.get("archive"))


def export_run(rec: dict, fmt: str, group: str | None = None, page: int | None = None, by: str = "none") -> dict:
    """Write a finished run's results to <run folder>/exports/ and answer {ok, files, count, status}: what the run found and saved (pageview.
    export_records), as one file (`by` none), or one per group or per page; `fmt` json is one {meta, records} object, jsonl a {"meta": ...}
    line then a record a line. A run that is still running is refused; done, stopped and failed are exported and say which in the meta."""
    import pageview
    if rec["kind"] != "pageflow":
        return {"ok": False, "why": "only a page-flow run has results to export"}
    if fmt not in ("json", "jsonl") or by not in ("none", "group", "page") or (group is not None and group not in pageview.GROUPS) or (page is not None and page < 1):
        return {"ok": False, "why": "format json or jsonl; group one of " + ", ".join(pageview.GROUPS) + "; page a positive number; by none, group or page"}
    ex = exit_info(rec["id"]) or {}
    status = (ex.get("status") or "done") if ex else ("stopping" if rec.get("stop_requested") else "running") if alive(rec) else "died"
    if status in ("running", "stopping", "starting"):
        return {"ok": False, "why": f"the run is still {status}: export it once it has ended"}
    files = _files(rec)
    records = pageview.export_records(files, rec.get("archive"), group, page)
    folder = Path(rec["run_folder"]) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    base = {"run": rec["id"], "name": rec.get("name"), "status": status, "source": rec.get("source"), "source_title": rec.get("source_title"),
            "archive": rec.get("archive"), "exported": datetime.now().isoformat(timespec="seconds"), "format": fmt,
            "group": group, "page": page, "by": by}
    if by == "none":
        parts = {"-".join(x for x in (group, f"p{page}" if page else "") if x): records}
    else:
        key = (lambda x: x["group"]) if by == "group" else (lambda x: f"p{x['page']}")
        parts = {}
        for x in records:
            parts.setdefault(key(x), []).append(x)
    written = []
    for tag, recs in parts.items():
        meta = {**base, "count": len(recs)}
        if by == "group":
            meta["group"] = tag
        elif by == "page":
            meta["page"] = int(tag[1:])
        path = folder / (f"{rec['id']}-{tag}.{fmt}" if tag else f"{rec['id']}.{fmt}")
        if fmt == "json":
            text = json.dumps({"meta": meta, "records": recs}, ensure_ascii=False, indent=1) + "\n"
        else:
            text = "\n".join([json.dumps({"meta": meta}, ensure_ascii=False)] + [json.dumps(x, ensure_ascii=False) for x in recs]) + "\n"
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return {"ok": True, "files": written, "count": len(records), "status": status}


def run_config(rec: dict) -> dict:
    """The run's own config as it ran (run-config.yaml, else fleet-config.yaml); {} when there is none."""
    for name in ("run-config.yaml", "fleet-config.yaml"):
        try:
            cfg = yaml.safe_load((Path(rec["run_folder"]) / name).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(cfg, dict):
            return cfg
    return {}


def _flush(rec: dict, files: runfiles.RunFiles) -> int:
    """Pages to a chunk of facts and ties: the run's own config (`flush_pages`), else 10."""
    v = run_config(rec).get("flush_pages")
    return v if isinstance(v, int) and v > 0 else 10


def _now(rec: dict, files: runfiles.RunFiles, status: str, detail: str, elapsed, p: dict | None, trace: list[dict],
         calls: list[dict]) -> str:
    """The plain sentence on what the run is doing (runview.now_line)."""
    wf = rec.get("workflow") or (rec.get("name", "")[len("workflow-"):] if str(rec.get("name", "")).startswith("workflow-") else None)
    if rec["kind"] != "pageflow":
        return runview.now_line(rec["kind"], status, detail, None, {}, 0, None, elapsed, None, len(calls), wf)
    found = files.found()
    records = len({(r.get("schema"), r.get("thing")) for r in found})
    pages_done = sum(1 for v in ((p or {}).get("pages") or {}).values() if v in ("done", "escalated", "gave-up"))
    last = next((r.get("page") for r in reversed(trace) if r.get("page")), None)
    return runview.now_line("pageflow", status, detail, p, files.state(), records, run_config(rec).get("parallel"),
                            elapsed, last, len(calls), None, pages_done)


_SOURCES_MEMO: dict = {"at": 0.0, "titles": {}}


def known_sources() -> dict:
    """{source slug: title} of the sources the manager knows (read again at most every 5 s)."""
    if time.time() - _SOURCES_MEMO["at"] > 5:
        titles = {}
        try:
            for d in composer().source_dirs():
                for f in sorted(d.glob("*.y*ml")) if d.is_dir() else []:
                    try:
                        titles.setdefault(f.stem, (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("title") or f.stem)
                    except (OSError, yaml.YAMLError):
                        titles.setdefault(f.stem, f.stem)
        except Exception:                                    # noqa: BLE001 - no sources known: no source derived
            pass
        _SOURCES_MEMO.update(at=time.time(), titles=titles)
    return _SOURCES_MEMO["titles"]


def _old_agents(rec: dict) -> dict | None:
    """The agents of a run started before the counts were recorded: a page flow's Haiku readers are its `parallel`."""
    if rec.get("kind") != "pageflow":
        return None
    return {"haiku": run_config(rec).get("parallel"), "sonnet": None}


def run_source(rec: dict) -> str | None:
    """The source a run is for: the one it records, else (a workflow run) the longest known source slug its name ends with."""
    if rec.get("source"):
        return rec["source"]
    name = str(rec.get("name") or "")
    hits = [slug for slug in known_sources() if name.endswith("-" + slug)]
    return max(hits, key=len) if hits else None


def archive_name(rec: dict) -> str | None:
    if rec.get("archive"):
        a = Path(str(rec["archive"]))
        return "Draft" if archives.is_draft(a) else a.name
    return None


def source_title(rec: dict) -> str | None:
    """What the run works on, in words: the source's title, else (a workflow session with no source) the archive's name."""
    if rec.get("source_title"):
        return rec["source_title"]
    if rec["kind"] == "pageflow":
        return ((run_config(rec).get("source") or {}).get("title")) or rec.get("source")
    src = run_source(rec)
    if src:
        return known_sources().get(src) or src
    return archive_name(rec)


def run_window(rec: dict, ex: dict | None = None) -> tuple[float, float] | None:
    """(start, end) epoch seconds of a run (end: now while it runs): what tells its calls and questions from the other runs'
    on the same archive. None before it started."""
    ex = ex if ex is not None else (exit_info(rec["id"]) or {})
    try:
        t0 = datetime.fromisoformat(rec["started"]).timestamp()
        t1 = datetime.fromisoformat(ex["ended"]).timestamp() if ex.get("ended") else time.time()
    except (KeyError, TypeError, ValueError):
        return None
    return (t0, t1)


def summary(rec: dict, files: runfiles.RunFiles | None = None, full: bool = False) -> dict:
    """The run as stable JSON (`ls --json` rows; `status --json` with full=True). Keys never change meaning; new keys
    may be added."""
    files = files or _files(rec)
    ex = exit_info(rec["id"]) or {}
    files.window = run_window(rec, ex)
    live = alive(rec)
    if ex:
        status, detail = ex.get("status"), ex.get("detail")
    elif live:
        status, detail = ("stopping" if rec.get("stop_requested") else "running"), ""
    else:
        status, detail = "died", "the wrapper is gone without an exit record"
    started = rec.get("started")
    t0 = datetime.fromisoformat(started).timestamp() if started else None
    t1 = datetime.fromisoformat(ex["ended"]).timestamp() if ex.get("ended") else time.time()
    p = files.progress()
    calls = files.calls()
    cost = files.cost()
    tasks = files.tasks() if (full or rec["kind"] == "mcp-session") else []
    fraction = p.get("fraction") if p else None
    if fraction is None and tasks and sum(t["steps_total"] for t in tasks):
        fraction = round(sum(t["steps_done"] for t in tasks) / sum(t["steps_total"] for t in tasks), 4)
    if status == "done" and rec["kind"] == "mcp-session":
        fraction = 1.0
    trace = files.trace()
    last = runfiles.tail_lines(trace, 1)
    pages = {}
    for v in ((p or {}).get("pages") or {}).values():
        pages[v] = pages.get(v, 0) + 1
    chunk, cells, read, content = None, [], 0, 0
    if rec["kind"] == "pageflow":
        cells = runfiles.cells(p, files.found())
        content = len(runfiles.content_pages(p))
        read = sum(1 for _, st, _ in cells if st in ("done", "escalated", "gave-up"))
        chunk = runfiles.route(trace, p, _flush(rec, files), status == "done")["chunk"]
    out = {
        "schema": SCHEMA, "id": rec["id"], "name": rec.get("name"), "kind": rec["kind"], "model": rec.get("model"),
        "status": status, "detail": detail, "alive": live, "pid": rec.get("pid"), "dry": rec.get("dry", False),
        "fast": rec.get("fast", False), "started": started, "ended": ex.get("ended"),
        "elapsed_s": round(t1 - t0, 1) if t0 else None, "exit_code": ex.get("code"),
        "run_folder": rec["run_folder"], "archive": files.archive(), "config": rec.get("config"),
        "recipe": rec.get("recipe"), "source": run_source(rec), "flow": runview.flow_label(rec), "flow_key": runview.flow_key(rec),
        "agents": rec.get("agents") or _old_agents(rec),
        "source_title": source_title(rec), "archive_name": archive_name(rec),
        "progress": {"fraction": fraction, "stage": (p or {}).get("stage"), "label": (p or {}).get("label"),
                     "done": (p or {}).get("done"), "total": (p or {}).get("total"), "pages": pages,
                     "chunk": chunk, "pages_read": read, "pages_content": content},
        "cells": cells,
        "calls": {"hydra": sum(bool(c.get("hydra", True)) for c in calls),
                  "errors": sum(c.get("ok") is False for c in calls),
                  "model": files.model_calls() if rec["kind"] == "pageflow" else None},
        "cost": {**cost, "budget_usd": rec.get("budget_usd")},
        "limits": limits_of(rec), "resumable": resumable(rec) is None,
        "last": last[0] if last else "",
        "now": _now(rec, files, status, detail or "", round(t1 - t0, 1) if t0 else None, p, trace, calls),
        "report": str(Path(rec["run_folder"]) / "report.md") if files.report() is not None else None,
        "copied": ex.get("copied"),
    }
    if full:
        qs = files.questions()
        out["questions"] = {s: sum(runfiles.question_status(q) == s for q in qs) for s in ("open", "answered", "dismissed")}
        out["tasks"] = tasks
        out["budgets"] = rec.get("budgets")
        out["command"] = rec.get("command")
        slow = sorted((c for c in calls if isinstance(c.get("ms"), (int, float))), key=lambda c: -c["ms"])[:5]
        out["slowest_calls"] = [{"tool": c["tool"], "ms": c["ms"], "at": c.get("at")} for c in slow]
        out["errors"] = [{"tool": c["tool"], "at": c.get("at"), "error": str(c.get("error") or "")[:200]}
                         for c in calls if c.get("ok") is False][-10:]
    return out


# ---- stop -----------------------------------------------------------------------------------------------------------

# limits: a page-flow run's run-level caps (the Haiku, Sonnet and escalation call budgets, the dollar cap) switched on or
# off while it runs. The driver re-reads <run_folder>/pageflow/limits.json by mtime at each check.
def limits_file(rec: dict) -> Path:
    return Path(rec["run_folder"]) / "pageflow" / "limits.json"


def limits_of(rec: dict) -> bool:
    try:
        v = json.loads(limits_file(rec).read_text(encoding="utf-8")).get("limits")
        if isinstance(v, bool):
            return v
    except (OSError, ValueError, AttributeError):
        pass
    return rec.get("limits", True) is not False


def set_limits(rid: str, on: bool) -> dict:
    rec = find(rid)
    if not rec:
        return {"ok": False, "id": rid, "why": "no such run"}
    if rec.get("kind") != "pageflow":
        return {"ok": False, "id": rec["id"], "why": "not a page-flow run"}
    if not alive(rec):
        return {"ok": False, "id": rec["id"], "why": "not running"}
    f = limits_file(rec)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"limits": bool(on), "at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    os.replace(tmp, f)
    return {"ok": True, "id": rec["id"], "limits": bool(on)}


def stop(rid: str) -> dict:
    """SIGTERM to the run's process group, which this manager made (the wrapper leads it), after checking the PID is
    still that wrapper. Nothing else is signalled."""
    rec = find(rid)
    if not rec:
        return {"ok": False, "id": rid, "why": "no such run"}
    if not alive(rec):
        return {"ok": False, "id": rec["id"], "why": "not running"}
    pid, pgid = rec["pid"], rec.get("pgid")
    rec["stop_requested"] = datetime.now().isoformat(timespec="seconds")
    save(rec)
    try:
        if pgid and pgid == pid and os.getpgid(pid) == pgid:
            os.killpg(pgid, signal.SIGTERM)
            how = f"SIGTERM to process group {pgid}"
        else:
            os.kill(pid, signal.SIGTERM)
            how = f"SIGTERM to PID {pid}"
    except ProcessLookupError:
        return {"ok": False, "id": rec["id"], "why": "gone before the signal"}
    return {"ok": True, "id": rec["id"], "how": how}


# ---- clear: a finished run and everything it made, for good ------------------------------------------------------------

def _clear_roots() -> list[Path]:
    """The only places clear may delete in: where runs and their archives are made (and the mirror's run copies)."""
    roots = []
    for dry in (True, False):
        base = root_for(dry)
        roots += [base / "runs", base / "archives"]
    return [r.resolve() for r in roots + [WS / "fleet-runs"]]


def _inside(path: Path, roots: list[Path]) -> bool:
    p = path.resolve()
    return any(p != r and r in p.parents for r in roots)


def clear(rid: str) -> dict:
    """Delete a run that is not running: its run folder, its archive (the run's own archives/<id>/ folder), the mirror's
    copy, and its registry files. Deletes only inside the run roots; anything else is listed as kept."""
    rec = find(rid)
    if not rec:
        return {"ok": False, "id": rid, "why": "no such run"}
    if alive(rec):
        return {"ok": False, "id": rec["id"], "why": "still running: stop it first"}
    roots, deleted, kept = _clear_roots(), [], []
    targets = []
    for key in ("run_folder", "final_folder"):
        if rec.get(key):
            targets.append(Path(rec[key]))
    if rec.get("archive"):
        a = Path(rec["archive"])
        if a.parent.name == rec["id"]:                          # only the run's own archives/<id>/ (archive + view copy);
            targets.append(a.parent)                             # a workflow session works on another run's archive: kept
        elif a.exists():
            kept.append(str(a))
    for t in dict.fromkeys(targets):
        if not t.exists():
            continue
        if _inside(t, roots):
            shutil.rmtree(t, ignore_errors=False)
            deleted.append(str(t))
        else:
            kept.append(str(t))
    if (rec.get("opts") or {}).get("archive") and rec.get("archive"):     # a run that added to a shared archive (the Draft)
        mine = [n for t in archives._read_tracking() if t.get("run") == rec["id"] for n in t.get("collections") or []]
        if mine:
            try:
                archives.remove_collections(rec["archive"], mine)
                deleted.append(f"{len(mine)} collections of {Path(rec['archive']).name}")
            except Exception as e:                           # noqa: BLE001 - say what stayed, never fail the clear
                kept.append(f"collections of {Path(rec['archive']).name}: {e}")
    archives.untrack(rec["id"])
    log_action("clear", rec["id"], {"source": rec.get("source"), "detail": {"name": runview.flow_label(rec) or rec.get("name")}})
    try:
        import runlog
        runlog.delete_run(rec["id"])
    except Exception:                                        # noqa: BLE001 - a busy log never blocks a clear; --rebuild drops it
        pass
    for suffix in (".json", ".exit.json", ".live.json"):
        f = state_dir() / f"{rec['id']}{suffix}"
        if f.exists():
            f.unlink()
    return {"ok": True, "id": rec["id"], "deleted": deleted, "kept": kept}


def rerun(rid: str) -> dict:
    """Run again from scratch: the same recipe, source, pages, dry or not, and models (or the same workflow on the same
    archive), after clearing this run's own folder and archive. Refuses a running run."""
    rec = find(rid)
    if not rec:
        return {"ok": False, "id": rid, "why": "no such run"}
    if alive(rec):
        return {"ok": False, "id": rec["id"], "why": "still running: stop it first"}
    pages, models, cfg = None, None, None
    try:
        import yaml
        cfg = yaml.safe_load((Path(rec["run_folder"]) / "run-config.yaml").read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001 - no composed config: the defaults stand
        cfg = None
    wf = rec.get("workflow") or (rec.get("name", "")[len("workflow-"):] if str(rec.get("name", "")).startswith("workflow-") else None)
    if isinstance(cfg, dict):
        p = cfg.get("pages") or {}
        if p.get("from") and p.get("to"):
            pages = f"{p['from']}-{p['to']}"
        m = cfg.get("models") or {}
        models = ",".join(f"{k}={v}" for k, v in m.items() if isinstance(v, str)) or None
    if not (rec.get("recipe") and rec.get("source")) and not wf:
        return {"ok": False, "id": rec["id"], "why": "this run was not started from a recipe or a workflow"}
    name = None if wf else rec.get("name")
    cleared = clear(rec["id"])
    if not cleared["ok"]:
        return cleared
    if rec.get("flow") == "read":
        o = rec.get("opts") or {}
        cap = rec.get("budget_usd")
        new = start(None, None, None, bool(rec.get("dry")), False, pages, "none" if cap is None else str(cap), o.get("archive"),
                    None, rec.get("source"), None, None, o.get("models") or models, o.get("without"), "read", bool(o.get("lag")), None, None, rec.get("limits", True), None, rec.get("agents"))
        return {"ok": True, "id": new["id"], "was": rec["id"], "deleted": cleared["deleted"]}
    new = start(None, name, rec.get("model") if wf else None, bool(rec.get("dry")), False, None if wf else pages, None,
                rec.get("archive") if wf else None, None if wf else rec.get("recipe"), None if wf else rec.get("source"),
                None, wf, None if wf else models, None, for_source=rec.get("source") if wf else None)
    return {"ok": True, "id": new["id"], "was": rec["id"], "deleted": cleared["deleted"]}


def resumable(rec: dict) -> str | None:
    """Why this run cannot be resumed (one line), or None when it can."""
    if rec.get("kind") != "pageflow":
        return "a workflow session has no saved state to carry on from"
    folder = Path(rec.get("run_folder") or "")
    if not folder.is_dir():
        return "its run folder is gone"
    if not (folder / "run-config.yaml").is_file():
        return "it has no run-config.yaml"
    if not (folder / "pageflow" / "state.json").is_file():
        return "it has no saved state"
    return None


def resume(rid: str) -> dict:
    """Carry a stopped page-flow run on from its state.json: the same id, record and run folder, through the same
    wrapper. Nothing is cleared. Refuses a live run, a workflow session, and a run without its config and state."""
    rec = find(rid)
    if not rec:
        return {"ok": False, "id": rid, "why": "no such run"}
    if alive(rec):
        return {"ok": False, "id": rec["id"], "why": "still running: stop it first"}
    why = resumable(rec)
    if why:
        return {"ok": False, "id": rec["id"], "why": why}
    cmd = list(rec.get("command") or [])
    at = next((i for i, c in enumerate(cmd) if i >= 1 and c in ("run", "resume")), None)
    if at is None or at + 1 >= len(cmd):
        return {"ok": False, "id": rec["id"], "why": "its command is not a page-flow driver command"}
    cmd[at] = "resume"
    cmd[at + 1] = str(Path(rec["run_folder"]) / "run-config.yaml")
    now = datetime.now().isoformat(timespec="seconds")
    rec.update({"command": cmd, "status": "running"})
    rec.pop("stop_requested", None)
    rec["resumes"] = [*(rec.get("resumes") or []), now]
    for suffix in (".exit.json", ".live.json"):
        (state_dir() / f"{rec['id']}{suffix}").unlink(missing_ok=True)
    log = open(Path(rec["run_folder"]) / "fleet-exec.log", "ab")
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "exec", rec["id"]], cwd=rec["run_folder"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                         env={**os.environ, "APOLLO_STATE": str(state_dir())})
    log.close()
    _children.append(p)
    rec.update({"pid": p.pid, "pgid": p.pid, "resumed": now})
    save(rec)
    return {"ok": True, "id": rec["id"], "pid": p.pid, "resumed": now}


# ---- view: one tab as JSON; configs: what can be started ------------------------------------------------------------

TABS = ("run", "found", "questions", "calls", "raw", "report")


def _live_flow(rec: dict, files: runfiles.RunFiles, trace: list[dict], p: dict | None) -> dict:
    """The run view's right column: {flow: {title, steps}, config: [{label, value}]}; the Flow card of the run's own
    button, each step marked done | now | next | skipped."""
    ex = exit_info(rec["id"]) or {}
    status = ex.get("status") or ("running" if alive(rec) else "died")
    try:
        if rec["kind"] == "pageflow":
            detail = recipe_detail_cached(rec.get("recipe") or "full", rec.get("source"))
            cfg = run_config(rec)
            steps = [dict(x) for x in detail["flows"]["read"]]
            for x in steps:                                  # the recipe's own defaults, then this run's fields
                if x["label"] == "Lag check":
                    x["on"] = bool((cfg.get("lag") or {}).get("enabled"))
                elif x["label"] == "Hard pages" and (cfg.get("budgets") or {}).get("escalations") == 0:
                    x["on"] = False
            rt = runfiles.route(trace, p, _flush(rec, files), status == "done")
            return {"flow": {"title": "Read", "steps": runview.flow_steps(steps, rt, trace, files.state(), status == "done", cfg)},
                    "config": runview.config_rows(cfg, rec, files.state())}
        wf = rec.get("workflow") or str(rec.get("name") or "").removeprefix("workflow-")
        steps = recipe_detail_cached("full", None)["flows"].get(wf) or []
        return {"flow": {"title": wf.capitalize(), "steps": runview.session_flow(steps, files.tasks(), status)},
                "config": runview.session_config_rows(rec)}
    except Exception as e:                                   # noqa: BLE001 - the card is a help, never a reason to fail the view
        return {"flow": None, "config": [], "flow_error": str(e)[:200]}


def view(rec: dict, tab: str = "run", files: runfiles.RunFiles | None = None, row: int | None = None,
         call: int | None = None) -> dict:
    """One tab of the window as rows with fields (runfiles' structured builders), with the page strip
    (`pages`: [[page, status]], page flow only). Keys never change meaning; new keys may be added."""
    if tab not in TABS:
        raise ValueError(f"--tab: one of {', '.join(TABS)}")
    files = files or _files(rec)
    files.window = run_window(rec)
    p = files.progress() or {}
    pages = []
    for k, v in (p.get("pages") or {}).items():
        try:
            pages.append([int(k), v])
        except (TypeError, ValueError):
            continue
    out = {"schema": SCHEMA, "id": rec["id"], "kind": rec["kind"], "tab": tab, "run_folder": rec["run_folder"],
           "pages": sorted(pages)}
    if rec["kind"] == "pageflow":
        ex = exit_info(rec["id"]) or {}
        out["route"] = runfiles.route(files.trace(), p, _flush(rec, files), ex.get("status") == "done")
        if tab in ("run", "report", "found"):              # Found draws the Records row and the Named row (owner: the 24 Nov sketch)
            out["heat"] = runfiles.heat(p, files.state(), files.found())
    if row is not None:                                      # what one log row stands for
        trace = files.trace()
        links = runview.trace_links(trace, files.found(), files.models())
        out["detail"] = runview.row_detail(row, trace, files.found(), files.models(), links, files.state())
        return out
    if call is not None:                                     # one model call, answer in full
        m = next((m for j, m in enumerate(files.models()) if (m.get("n") or j + 1) == call), None)
        out["call"] = runview.model_call(m) if m else None
        return out
    if tab == "run":
        rows = files.trace()
        links = runview.trace_links(rows, files.found(), files.models())
        out.update({"rows": runview.run_rows_linked(rows, links, files.found()), "total": len(rows)})
        out.update(_live_flow(rec, files, rows, p))
    elif tab == "found":
        out.update(runfiles.found_groups(files.found()))
        out.update(runview.found_entities(runview.named(files.found(), rec.get("archive"))))   # ids said by name
        if rec["kind"] != "pageflow":                        # a workflow run found nothing: it changed what its tasks recorded
            out["changes"] = runfiles.workflow_changes(files.archive(), files.window)
    elif tab == "raw":
        out["raw"] = runview.raw_view(files, rec["kind"])
    elif tab == "questions":
        out.update(runfiles.question_rows(files.questions(), runview.archive_names(files.archive())))
    elif tab == "calls":
        out.update(runfiles.calls_view(files.calls()))
    else:
        md = files.report()
        out.update({"path": str(Path(rec["run_folder"]) / "report.md") if md is not None else None,
                    "blocks": runfiles.report_blocks(md)})
        if rec["kind"] == "pageflow":
            out["measures"] = runfiles.measures(files.state(), files.calls(), p)
    return out


def config_dirs() -> list[tuple[Path, str]]:
    """Where a flat list of startable files is read from: composed configs (a folder of run-config files, if any) and
    briefs. Recipes and sources are listed apart (`recipes_and_sources`)."""
    return [(TESTING / "pageflow" / "runs", "*.y*ml"), (TESTING / "briefs", "*.md")]


def recipes_and_sources() -> dict:
    """What `start --recipe R --source S` can take: {recipes: [...], sources: [...], briefs: [...]}. A recipe row: name,
    path, description, defaults (model, budgets per page, span). A source row: name, path, title, publisher, file, pages,
    and whether it has a reference archive. A brief row is the flat list's session row."""
    cp = composer()
    recipes, sources = [], []
    for f in sorted(cp.RECIPES.glob("*.y*ml")):
        try:
            r = cp.load_recipe(f.stem)
        except (ValueError, yaml.YAMLError, OSError):
            continue
        b = r.get("budgets") or {}
        recipes.append({"name": f.stem, "path": str(f.resolve()), "description": r.get("description") or "",
                        "defaults": {"model": (r.get("models") or {}).get("haiku"), "span": r.get("span"),
                                     "budget_usd": r.get("budget_usd"), "lag": bool((r.get("lag") or {}).get("enabled"))}})
    seen = set(hidden_sources())                                  # built-ins the owner hid never reach the Source select
    for d in cp.source_dirs():                                   # pageflow/sources first, then the folder of dropped sources
        for f in sorted(d.glob("*.y*ml")) if d.is_dir() else []:
            if f.stem in seen:
                continue
            try:
                s = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            seen.add(f.stem)
            pg = s.get("pages") if isinstance(s.get("pages"), dict) else {}
            dropped = bool(s.get("dropped"))
            sources.append({"name": f.stem, "path": str(f.resolve()), "title": s.get("title") or f.stem,
                            "publisher": s.get("publisher"), "file": s.get("file"),
                            "pages": f"{pg['from']}-{pg['to']}" if pg.get("from") and pg.get("to") else None,
                            "reference": bool(s.get("reference_archive")), "dropped": dropped,
                            "needs": sourcedrop.needs_of(s) if dropped else []})
    briefs = [r for r in configs(dirs=[(TESTING / "briefs", "*.md")])]
    d = archives.draft_path()
    return {"recipes": recipes, "sources": sources, "briefs": briefs,
            "flows": runview.flow_catalogue(),
            "draft": {"path": str(d), "exists": archives.is_archive(d), "name": archives.DRAFT_NAME}}


def describe(path: Path) -> str:
    """A config's one line: its first sentence of leading `#` comments; a brief's first heading, `Brief: ` dropped."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if path.suffix in (".yaml", ".yml"):
        head = []
        for ln in lines:
            if not ln.startswith("#"):
                break
            head.append(ln.lstrip("#").strip())
        s = " ".join(x for x in head if x)
        m = re.match(r"(.+?[.!?])(?=\s+[A-Z]|\s*$)", s)          # "pp. 20" is no sentence end
        s = m.group(1) if m else s
    else:
        s = next((ln.lstrip("#").strip() for ln in lines if ln.startswith("#")), "")
        s = re.sub(r"^Brief:\s*", "", s)
        s = s[:1].upper() + s[1:]
    return s if len(s) <= 160 else s[:159] + "…"


PROMPTS = runfiles.PAGEFLOW / "prompts"
ROLE_PROMPTS = {"Reader": "A C T I", "Escalation": "A C T", "Roster": "roster", "Lag-check": "L", "Rater": "rate",
                "Supervisor": "supervisor"}


WORKFLOW_BRIEF = HERE / "briefs" / "workflow.md"
WIKI_BRIEF = HERE / "briefs" / "wiki.md"                      # the Wiki flow: two hydra tasks (collection-shape, view-prep) in one session
# Hydra's workflows a session can run on a run's archive (hydra-git/src/hydra/workflows/<key>.yaml): key, button label.
WORKFLOWS = [("stubs", "Stubs", "Turns names that have no entry yet into entries when a source backs them up."),
             ("places", "Places", "Puts the archive's people and things on the map from what their sources say."),
             ("enrich", "Enrich", "Adds more facts to chosen entries from new sources and Wikidata."),
             ("wiki", "Wiki", "Names and groups the archive's collections, then gets each one ready for the views it should show in."),
             ("links", "Links", "Goes through the entities a run added and links, merges or parents the ones a kept source supports; the rest become questions.")]
AFTER = ("links",)                                           # what a Read may chain on its own archive when it ends


def prompt_names() -> list[str]:
    return sorted(f.stem for f in PROMPTS.glob("*.md"))


def prompt_text(name: str) -> dict:
    """A prompt file of the page flow, read only: {name, path, lines, text}; the name must be one of the folder's files
    (or `workflow`, the generic brief a workflow session starts from)."""
    if name == "workflow":
        text = WORKFLOW_BRIEF.read_text(encoding="utf-8")
        return {"name": name, "path": str(WORKFLOW_BRIEF), "lines": len(text.splitlines()), "text": text}
    if name not in prompt_names():
        raise ValueError(f"no prompt {name!r} (one of {', '.join(prompt_names())})")
    f = PROMPTS / f"{name}.md"
    text = f.read_text(encoding="utf-8")
    return {"name": name, "path": str(f), "lines": len(text.splitlines()), "text": text}


_DRIVER_MODULES: dict = {}
_DRIVER_LOCK = __import__("threading").Lock()


def driver_module():
    """The page-flow driver as a module, loaded once per process and path (it pulls in hydra, ~3 s the first time)."""
    path = str(driver())
    with _DRIVER_LOCK:
        os.environ["PAGEFLOW_SOURCES_EXTRA"] = sources_extra()
        if path not in _DRIVER_MODULES:
            import importlib.util
            spec = importlib.util.spec_from_file_location("pageflow_driver", path)
            mod = importlib.util.module_from_spec(spec)
            sys.path.insert(0, str(TESTING / "pageflow"))
            spec.loader.exec_module(mod)
            _DRIVER_MODULES[path] = mod
        return _DRIVER_MODULES[path]


def warm() -> None:
    """Load the heavy imports once, off the request path, so the first Configure or Analyse after launch is quick."""
    archives.ensure_draft_later()                            # the empty Draft archive a session starts with, made off the request path
    try:
        driver_module()
        import hydra_mcp.profile  # noqa: F401
    except Exception:                                        # noqa: BLE001 - a warm-up is a help, never a failure
        pass


def scaled_caps(cp, recipe: str, source: str) -> dict | None:
    """The per-page caps the driver would set for this source (scaled from its profile), or None if it cannot be profiled."""
    try:
        mod = driver_module()
        b =cp.compose(recipe, source, profile=mod.profile_of).get("budgets") or {}
        return {k: b[k] for k in ("calls_per_page", "seconds_per_page") if b.get(k) is not None}
    except Exception:                                        # noqa: BLE001 - the recipe's fixed caps then stand
        return None


# a flow that runs several hydra workflows in one session: its key -> the workflows, in order
WORKFLOW_PARTS = {"wiki": ("collection-shape", "view-prep")}


def workflow_steps(key: str) -> list[str]:
    """The step titles of a hydra workflow (hydra-git/src/hydra/workflows/<key>.yaml), in order; [] if unreadable."""
    parts = WORKFLOW_PARTS.get(key, (key,))
    return [t for p in parts for t in _workflow_step_titles(p)][:12]


def _workflow_step_titles(key: str) -> list[str]:
    f = HERE.parent / "hydra-git" / "src" / "hydra" / "workflows" / f"{key}.yaml"
    try:
        import yaml
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:                                             # noqa: BLE001 - the card then shows the session alone
        return []
    out = []
    blocks = doc.get("blocks") or {}
    for part in (blocks.values() if isinstance(blocks, dict) else blocks):
        for step in (part or {}).get("steps") or []:
            if isinstance(step, dict) and step.get("text"):
                out.append(str(step["text"]))
    return out


def _inputs_stamp(source: str | None) -> str:
    """One stamp over everything a recipe's detail is computed from: the page flow's recipes, sources, contexts and
    prompts, compose.py and the driver, this file, and the source's own file. Any change gives a new stamp."""
    pf = TESTING / "pageflow"
    files = [pf / "compose.py", pf / "pageflow.py", Path(__file__)]
    for sub in ("recipes", "sources", "contexts", "prompts"):
        d = pf / sub
        if d.is_dir():
            files += sorted(d.iterdir())
    if source:
        try:
            prof = composer().source_path(source)
            files.append(prof)
            m = re.search(r"^file:\s*(\S+)", prof.read_text(encoding="utf-8"), re.M)
            if m:
                files.append(Path(m.group(1)) if m.group(1).startswith("/") else TESTING / m.group(1))
        except OSError:
            pass
    parts = []
    for f in files:
        try:
            st = f.stat()
            parts.append(f"{f.name}:{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append(f"{f.name}:-")
    import hashlib
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def recipe_detail_cached(name: str, source: str | None = None) -> dict:
    """recipe_detail, kept on disk per recipe and source until any of its inputs change (it costs ~3.7 s cold: the
    driver and hydra's source profile load to scale the per-page caps)."""
    cache = HERE / "state" / ".cache"
    hit = cache / f"recipe-{name}-{source or '_'}-{_inputs_stamp(source)}.json"
    try:
        return json.loads(hit.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    got = recipe_detail(name, source)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        for old in cache.glob(f"recipe-{name}-{source or '_'}-*.json"):
            old.unlink(missing_ok=True)
        tmp = hit.with_suffix(".tmp")
        tmp.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
        tmp.replace(hit)
    except OSError:
        pass                                                     # a cache that cannot be written is no error
    return got


def recipe_detail(name: str, source: str | None = None) -> dict:
    """One recipe's configuration for the Configure window, read only: the run (source, pages), the agents (role, model,
    budget, prompts, on), the limits, the prompt files, the authorized tools. Budgets are totals: the per-page rates
    times the pages of the run (the source's default span, else the recipe's `span`)."""
    cp = composer()
    r = cp.load_recipe(name)
    srcs = recipes_and_sources()["sources"]
    slug = source or (srcs[0]["name"] if srcs else None)
    src = cp.load_source(slug) if slug else {}
    full = cp.parse_pages(src.get("pages"))
    want = full
    if full and r.get("span"):
        want = (full[0], min(full[1], full[0] + int(r["span"]) - 1))
    npages = (want[1] - want[0] + 1) if want else None
    b = r.get("budgets") or {}

    def total(rate):
        return None if npages is None or b.get(rate) is None else int(round(float(b[rate]) * npages))
    lag = r.get("lag") or {}
    models = r.get("models") or {}
    haiku, sonnet = models.get("haiku"), models.get("sonnet")
    rubric = bool(r.get("rubric"))
    calls = {"Reader": total("haiku_per_page"), "Escalation": total("sonnet_per_page")}
    agents = [
        {"role": "Reader", "model": haiku, "budget": calls["Reader"], "unit": "calls", "prompts": ["A", "C", "T", "I"], "on": True},
        {"role": "Escalation", "model": sonnet, "budget": calls["Escalation"], "unit": "calls", "prompts": ["A", "C", "T"], "on": True},
        {"role": "Roster", "model": sonnet, "budget": None, "unit": "", "prompts": ["roster"], "on": True},
        {"role": "Lag-check", "model": lag.get("model") or haiku, "budget": (f"{lag.get('haiku')} + {lag.get('sonnet')}"
                                                                          if lag.get("haiku") is not None else None),
         "unit": "calls", "prompts": ["L"], "on": bool(lag.get("enabled"))},
        {"role": "Rater", "model": sonnet, "budget": 1 if rubric else None, "unit": "call", "prompts": ["rate"], "on": rubric},
        {"role": "Supervisor", "model": sonnet, "budget": None, "unit": "", "prompts": ["supervisor"], "on": rubric}]
    tr = r.get("triage") or {}
    caps = scaled_caps(cp, name, slug) if slug else None
    limits = [["calls", "Calls a page", (caps or {}).get("calls_per_page", b.get("calls_per_page"))],
              ["seconds", "Seconds a page", (caps or {}).get("seconds_per_page", b.get("seconds_per_page"))], ["escalations", "Escalations", total("escalations_per_page")],
              ["flush", "Save every", r.get("flush_pages")], ["ties", "Ties batch", r.get("ties_batch")],
              ["min_words", "Min words", tr.get("min_words")], ["redacted", "Redacted", tr.get("max_redacted_share")]]

    def per(key, noun):
        rate = b.get(key)
        if rate is None:
            return None
        n = f"{npages} pages" if npages else "the pages"
        return f"{total(key) if npages else '?'} {noun}: {rate:g} a page over {n}"
    lag_model = lag.get("model") or haiku
    stages = [
        {"key": "analyse", "label": "Analyse", "kind": "analyse", "about": "Measures the source page by page without using any model.", "role": None, "model": None, "budget": "No model calls", "on": True,
         "toggle": False, "prompts": []},
        {"key": "read", "label": "Read", "kind": "flow", "about": 'Reads every page of the source and builds a new archive from what it finds.', "role": "haiku", "model": haiku, "budget": (f"Stops at ${r['budget_usd']:g} and writes the report" if r.get("budget_usd") is not None else "No dollar cap"),
         "on": True, "toggle": False, "prompts": ["A", "C", "T", "I"]},
        {"key": "escalate", "label": "Escalate", "kind": "flow", "hidden": True, "about": 'When a page fails its checks twice, a stronger model retries that page.', "role": "sonnet", "model": sonnet,
         "budget": per("sonnet_per_page", "calls"), "on": True, "toggle": False, "prompts": ["A", "C", "T"]},
        {"key": "rate", "label": "Rate", "kind": "flow", "hidden": True, "about": 'After the run, one call scores it 0 to 4 on coverage, scope, accuracy, identity, tool use, workflow, path and efficiency, each with evidence (the rubric).', "role": "sonnet", "model": sonnet, "budget": "One call" if rubric else None,
         "on": rubric, "toggle": rubric, "prompts": ["rate"]}]
    stages += [{"key": k, "label": lab, "kind": "workflow", "role": None, "model": SESSION_MODEL, "budget": None, "about": about,
                "on": True, "toggle": False, "prompts": ["workflow"]} for k, lab, about in WORKFLOWS]
    prompts = [{"name": n, "lines": prompt_text(n)["lines"]} for n in prompt_names()]
    # what each button sets off, in order (the driver's run(): preflight, setup, triage, A, B, CT with escalation, I,
    # close; the lag check trails the pages when on; the rating closes). `per`: once, page (in parallel), failing page.
    par = r.get("parallel") or 4
    flows = {
        "analyse": [
            {"label": "Read the file", "who": "code", "per": "once", "note": "Opens the document and splits it into pages."},
            {"label": "Text layer", "who": "code", "per": "page", "note": "Checks whether each page has readable text or needs scanning."},
            {"label": "Words and redaction", "who": "code", "per": "page", "note": "Counts the words and blacked-out text on every page."},
            {"label": "Triage", "who": "code", "per": "once", "note": "Marks which pages are worth reading."}],
        "read": [
            {"label": "Preflight", "who": "code", "per": "once", "note": "Makes sure the document and tools are ready."},
            {"label": "Setup", "who": "code", "per": "once", "note": "Creates a fresh archive for this run."},
            {"label": "Triage", "who": "code", "per": "once", "note": "Picks the pages worth reading."},
            {"label": "Names", "who": "haiku", "role": "haiku", "per": "page", "par": par, "note": "Reads four pages at a time and lists everyone and everything each one mentions.", "prompt": "A"},
            {"label": "Roster", "who": "sonnet", "role": "sonnet", "per": "once", "note": "Merges those names into one list for the whole document.", "prompt": "roster"},
            {"label": "Records and ties", "who": "haiku", "role": "haiku", "per": "page", "par": par, "note": "Writes down the facts on each page and how the people in it connect.", "prompt": "C · T"},
            {"label": "Hard pages", "who": "sonnet", "role": "sonnet", "per": "fail", "note": "Retries any page that failed twice with a stronger model.", "branch": True},
            {"label": "Lag check", "who": "haiku", "role": "haiku", "per": "window", "note": "Double-checks finished pages while the reading carries on.", "prompt": "L", "on": bool(lag.get("enabled")), "branch": True},
            {"label": "Identity", "who": "haiku", "role": "haiku", "per": "once", "note": "Works out which names belong to the same person.", "prompt": "I"},
            {"label": "Close", "who": "code", "per": "once", "note": "Runs the final checks and writes the report."},
            {"label": "Rate", "who": "sonnet", "role": "sonnet", "per": "once", "note": "Scores how well the run went.", "prompt": "rate", "toggle": "rate"}]}
    for k, lab, _about in WORKFLOWS:
        flows[k] = [{"label": "Claude Code session", "who": "session", "role": k, "per": "once", "note": f"An assistant works through the {lab.lower()} steps on the chosen archive."}] + \
            [{"label": t, "who": "session", "role": k, "per": "step", "note": "", "branch": True} for t in workflow_steps(k)]
    # what the window's Flow tab needs of each step: a key, the prompts it uses, whether it has an on/off switch, its budget
    lag_def = cp.load_recipe("lag").get("lag") or {}

    def ct(n):
        return f"{n} calls" if n is not None else None
    meta = {"Preflight": ("preflight", [], False, None), "Setup": ("setup", [], False, None), "Triage": ("triage", [], False, None),
            "Names": ("names", ["A"], False, ct(calls["Reader"])), "Roster": ("roster", ["roster"], False, None),
            "Records and ties": ("records", ["C", "T"], False, ct(calls["Reader"])),
            "Hard pages": ("hard", ["A", "C", "T"], True, ct(calls["Escalation"])),
            "Lag check": ("lag", ["L"], True, f"{lag_def['haiku']} + {lag_def['sonnet']} calls" if lag_def.get("haiku") is not None else None),
            "Identity": ("identity", ["I"], False, ct(calls["Reader"])), "Close": ("close", [], False, None),
            "Rate": ("rate", ["rate"], True, "One call")}
    for k, steps in flows.items():
        for i, st in enumerate(steps):
            m = meta.get(st["label"]) if k == "read" else None
            st["key"] = m[0] if m else f"{k}-{i}"
            st["prompts"] = m[1] if m else []
            st["switch"] = bool(m and m[2])
            st["budget"] = m[3] if m else None
    return {"name": name, "path": str(cp.recipe_path(name).resolve()), "description": r.get("description") or "",
            "flows": flows,
            "run": {"source": slug, "pages": [want[0], want[1]] if want else None, "span": r.get("span"),
                    "archive": ((src.get("archive") or {}).get("name") or src.get("title")), "runner": "Claude Code"},
            "agents": agents, "stages": stages, "limits": [{"key": k, "label": lab, "value": v} for k, lab, v in limits],
            "prompts": prompts, "authorized": list(r.get("authorized") or []), "lag": bool(lag.get("enabled"))}


def analyse(source: str, pages: str | None = None, recipe: str = "full") -> dict:
    """The page flow's analysis of a source (`pageflow.py analyse`): segments, text layer, words and redaction per page,
    triage. No model calls. Returns the driver's JSON, or {ok: false, why}."""
    import subprocess
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", source or ""):
        return {"ok": False, "why": "a source name"}
    if pages:
        parse_pages(pages)
    try:                                                     # in this process when the driver is loaded here: no new imports
        mod = driver_module()
        if hasattr(mod, "analyse") and os.environ.get("APOLLO_ANALYSE_PROCESS") != "1":
            got = mod.analyse(source, pages, recipe)
            return got if isinstance(got, dict) else {"ok": True, "pages": got}
    except Exception:                                        # noqa: BLE001 - the driver's own process answers, or says why
        pass
    os.environ["PAGEFLOW_SOURCES_EXTRA"] = sources_extra()
    cmd = [driver_python(), str(driver()), "analyse", "--source", source, "--recipe", recipe]
    if pages:
        parse_pages(pages)
        cmd += ["--pages", pages]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(TESTING / "pageflow"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "why": f"analyse: {e}"}
    try:
        got = json.loads(p.stdout)
    except ValueError:
        return {"ok": False, "why": (p.stderr or p.stdout or "no answer").strip().splitlines()[-1][:300]}
    return got if isinstance(got, dict) else {"ok": True, "pages": got}


def configs(dirs: list[tuple[Path, str]] | None = None) -> list[dict]:
    """The startable page-flow configs and briefs: {name, kind, path, description, defaults}. `defaults`: a page-flow
    config's own model, pages and Haiku budget; a session's model."""
    out = []
    for folder, pattern in dirs or config_dirs():
        for f in sorted(Path(folder).glob(pattern)):
            try:
                kind = kind_of(f)
            except ValueError:
                continue
            row = {"name": f.stem, "kind": kind, "path": str(f.resolve()), "description": describe(f), "defaults": {}}
            if kind == "pageflow":
                try:
                    cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    cfg = {}
                pg = cfg.get("pages") if isinstance(cfg.get("pages"), dict) else {}
                row["defaults"] = {"model": (cfg.get("models") or {}).get("haiku"),
                                   "pages": f"{pg['from']}-{pg['to']}" if pg.get("from") and pg.get("to") else None,
                                   "budget": cfg.get("budget_usd")}
            else:
                row["defaults"] = {"model": SESSION_MODEL, "pages": None, "budget": None}
            out.append(row)
    return out


# ---- the command line -----------------------------------------------------------------------------------------------

def _table(rows: list[dict]) -> str:
    head = f"{'id':<40} {'kind':<11} {'model':<18} {'status':<9} {'progress':<15} {'calls':>6} {'cost':<30} elapsed"
    out = [head]
    for s in rows:
        out.append(f"{s['id'][:40]:<40} {s['kind']:<11} {str(s['model'] or '')[:18]:<18} {s['status']:<9} "
                   f"{runfiles.bar_text(s['progress']['fraction']):<15} {s['calls']['hydra']:>6} "
                   f"{runfiles.cost_text(s['cost'], s['cost'].get('budget_usd'))[:30]:<30} "
                   f"{runfiles.elapsed_text(s['elapsed_s'])}")
    return "\n".join(out)


def serve() -> int:
    """One long-lived manager for the window: a JSON line in ({"args": [...]}), a JSON line out ({"code", "out"}), so a
    call costs no new process and no new imports (the window's calls drop from ~0.3 s to tens of ms). Ends at EOF."""
    import contextlib
    import io
    import threading
    threading.Thread(target=warm, daemon=True).start()
    real = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        buf, code = io.StringIO(), 0
        try:
            args = json.loads(line)["args"]
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args) or args[:1] in (["serve"], ["exec"], ["toolrun"], ["proofreadrun"]) or (args[:1] == ["logdb"] and "--rebuild" in args):
                raise ValueError("bad request")
            with contextlib.redirect_stdout(buf):
                code = main(args)
        except SystemExit as e:                                   # argparse: a bad argument
            code = e.code if isinstance(e.code, int) else 2
        except Exception as e:                                    # noqa: BLE001 - answer, never die
            code = 2
            buf = io.StringIO(json.dumps({"ok": False, "why": f"{type(e).__name__}: {e}"}))
        real.write(json.dumps({"code": code or 0, "out": buf.getvalue()}, ensure_ascii=False) + "\n")
        real.flush()
    return 0


def main(argv=None) -> int:
    if (argv if argv is not None else sys.argv[1:])[:1] == ["serve"]:
        return serve()
    ap = argparse.ArgumentParser(prog="apollo.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="start a run: --recipe R --source S, a composed config (.yaml), or a brief (.md)")
    s.add_argument("path", nargs="?")
    s.add_argument("--recipe", help="page flow: pageflow/recipes/<name>.yaml, the flow")
    s.add_argument("--source", help="page flow: pageflow/sources/<slug>.yaml, what it works on; a brief: fills {source}")
    s.add_argument("--rate", metavar="RUN", help="session: the run it rates; fills {run_folder} (and {archive})")
    s.add_argument("--name")
    s.add_argument("--model", help="page flow: the page model (models.haiku); session: the agent's model")
    s.add_argument("--dry", action="store_true", help="no model calls: canned answers")
    s.add_argument("--fast", action="store_true", help="run on WSL's disk (~/hydra-ws), copy back at the end")
    s.add_argument("--pages", help="page flow: A-B, the pages to work")
    s.add_argument("--budget", help="page flow: model calls (Haiku); session: US dollars; `none`: no limit")
    s.add_argument("--archive", help="session: the archive the brief works in (else read from its calls)")
    s.add_argument("--workflow", help="session: a hydra workflow (review, stubs, places, enrich, view-prep, close-out) on --archive")
    s.add_argument("--models", help="page flow: role=model,... (the recipe's model roles: haiku, sonnet)")
    s.add_argument("--without", help="page flow: roles to leave out (lag, rate, supervise, escalate)")
    s.add_argument("--flow", help="read (the page flow on --source), or stubs, places, enrich, wiki (a workflow on --archive)")
    s.add_argument("--lag", action="store_true", help="--flow read: the lag-check on")
    s.add_argument("--after", help="--flow read: a workflow to run on the same archive when the run ends (links)")
    s.add_argument("--no-limits", action="store_true", help="page flow: no run-level caps (calls, escalations, dollars)")  # limits
    s.add_argument("--agents", help="--flow: agents per model grade, haiku=N,sonnet=N,opus=N (missing: the flow's default)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("limits", help="turn a live page-flow run's run-level caps on or off")  # limits
    s.add_argument("id")
    s.add_argument("state", choices=("on", "off"))
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("ls")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("stop")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("rerun", help="run again from scratch with the same settings (clears this run first)")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("resume", help="carry a stopped page-flow run on from where it stopped (clears nothing)")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("clear", help="delete a finished run: its folder, its archive and its registry entry (for good)")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("tail")
    s.add_argument("id")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("view", help="one tab of the window as rows: run, found, questions, calls or report")
    s.add_argument("id")
    s.add_argument("--tab", choices=TABS, default="run")
    s.add_argument("--row", type=int, help="run tab: what the log row with this index stands for")
    s.add_argument("--n", type=int, help="raw tab: this model call, answer in full")
    s.add_argument("--json", action="store_true")
    # choices
    s = sub.add_parser("choices", help="a page-flow run's ticks and the agent's choices behind them, from its files")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    # end choices
    # pageview
    s = sub.add_parser("pageview", help="a page-flow run's page strip, with --page N one page's box, with --all the whole run's (people, bodies, places, events, ties, posts)")
    s.add_argument("id", nargs="?")
    s.add_argument("--id", dest="id_opt")
    s.add_argument("--page", type=int)
    s.add_argument("--all", action="store_true", help="the whole run's rich data (or one --page's): every item with the pages it is on")
    s.add_argument("--group", choices=["people", "bodies", "places", "events", "ties", "posts"], help="with --all: one group only")
    s.add_argument("--json", action="store_true")
    # end pageview
    # export
    s = sub.add_parser("export", help="write a finished run's results (what it found and saved) to <run folder>/exports/ as .json or .jsonl")
    s.add_argument("id", nargs="?")
    s.add_argument("--id", dest="id_opt")
    s.add_argument("--format", choices=["json", "jsonl"], default="jsonl")
    s.add_argument("--group", choices=["people", "bodies", "places", "events", "ties", "posts"])
    s.add_argument("--page", type=int)
    s.add_argument("--by", choices=["none", "group", "page"], default="none")
    s.add_argument("--json", action="store_true")
    # end export
    s = sub.add_parser("configs", help="the recipes, sources and briefs that can be started")
    s.add_argument("--legacy", action="store_true", help="the old flat list of configs and briefs")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("recipe", help="one recipe's configuration for the Configure window (read only)")
    s.add_argument("name")
    s.add_argument("--source")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("analyse", help="segment, profile and triage a source's pages (no model calls)")
    s.add_argument("--source", required=True)
    s.add_argument("--recipe", default="full")
    s.add_argument("--pages", help="A-B")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("prompt", help="a page-flow prompt file (read only)")
    s.add_argument("name")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("exec", help=argparse.SUPPRESS)
    s.add_argument("id")
    s = sub.add_parser("ready", help="is this ready for a run: the preflight without a model call, and an archive's check")
    s.add_argument("--source", required=True)
    s.add_argument("--recipe", default="full")
    s.add_argument("--pages", help="A-B")
    s.add_argument("--archive")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("toolstart", help="start a tool (ocr, text, check, build) in its own process; returns at once")
    s.add_argument("--tool", required=True)
    s.add_argument("--source")
    s.add_argument("--archive")
    s.add_argument("--pages", help="A-B")
    s.add_argument("--lang", default="en", help="ocr: en or ar")
    s.add_argument("--args", help="the tool's arguments as JSON (toolargs.py)")  # tool args
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("toolabout", help="a tool's declared arguments, their values and the sentence of what it will do (toolargs.py)")  # tool args
    s.add_argument("--tool", required=True)
    s.add_argument("--source")
    s.add_argument("--args", help="JSON")
    s = sub.add_parser("toolget", help="a tool's last result and progress for a source or archive")
    s.add_argument("--tool", required=True)
    s.add_argument("--source")
    s.add_argument("--archive")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("toolrun", help=argparse.SUPPRESS)
    s.add_argument("--tool", required=True)
    s.add_argument("--key", required=True)
    s.add_argument("--spec", required=True)
    s = sub.add_parser("logdb", help="the DuckDB log: --sql QUERY (SELECT and WITH only, read-only) or --rebuild")
    s.add_argument("--sql")
    s.add_argument("--sql64", help="--sql, urlsafe base64 (for a window that cannot quote it)")
    s.add_argument("--rebuild", action="store_true", help="empty the log and ingest every run folder still on disk")
    s = sub.add_parser("proofreadstart", help="Proofread pages of a PDF source: Haiku reads the page image (returns at once)")
    s.add_argument("--model", help="haiku (the default), sonnet or opus")
    s.add_argument("--source", required=True)
    s.add_argument("--pages", required=True, help="N or A-B, at most 20")
    s = sub.add_parser("proofreadget", help="Proofread: the job, pages waiting for a decision, accepted pages (--page N: its text)")
    s.add_argument("--source", required=True)
    s.add_argument("--page", type=int)
    s = sub.add_parser("proofreadact", help="accept, reject or revert a page's Proofread")
    s.add_argument("--source", required=True)
    s.add_argument("--page", type=int, required=True)
    s.add_argument("--action", required=True, choices=("accept", "reject", "revert"))
    s = sub.add_parser("proofreadrun", help=argparse.SUPPRESS)
    s.add_argument("--model")
    s.add_argument("--source", required=True)
    s.add_argument("--pages", required=True)
    sub.add_parser("sourcelist", help="the sources: built in, and dropped (with their text layer and what they still need)")
    s = sub.add_parser("sourceadd", help="take a file in as a source (the file is deleted if it sits in sources/incoming/)")
    s.add_argument("path")
    s.add_argument("--title")
    s = sub.add_parser("sourceupdate", help="edit a dropped source: --fields '{\"title\": ..., \"brief\": ..., \"pages\": \"A-B\"}'")
    s.add_argument("slug")
    s.add_argument("--fields", required=True)
    s = sub.add_parser("sourceremove", help="delete a dropped source and its copied files")
    s.add_argument("slug")
    s = sub.add_parser("sourcehide", help="hide a built-in source from Apollo (state/hidden-sources.json); the page flow's profile stays")
    s.add_argument("slug")
    s = sub.add_parser("sourceunhide", help="show a hidden built-in source again")
    s.add_argument("slug")
    s = sub.add_parser("cataloguestart", help="Catalogue a source: an agent reads its first pages and writes a library record (returns at once)")
    s.add_argument("--source", required=True)
    s.add_argument("--model", default="haiku", help="haiku, sonnet or opus")
    s.add_argument("--args", help="Catalogue's arguments as JSON (toolargs.py)")  # tool args
    s = sub.add_parser("briefgen", help="draft a source's brief with a model (catalogue.brief_draft); nothing is saved")
    s.add_argument("--source", required=True)
    s.add_argument("--model", default="claude-haiku-5-5")
    s = sub.add_parser("catalogueget", help="Catalogue: the job, the record waiting for Accept, the accepted record and its check")
    s.add_argument("--source", required=True)
    s = sub.add_parser("catalogueact", help="accept, reject or revert a source's Catalogue record")
    s.add_argument("--source", required=True)
    s.add_argument("--action", required=True, choices=("accept", "reject", "revert"))
    s = sub.add_parser("catalogueedit", help="change or remove one field of the accepted Catalogue record, or (--pending) of the one waiting for Accept")  # tool args
    s.add_argument("--source", required=True)
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--value", default="")
    s.add_argument("--pending", action="store_true", help="edit the record waiting for Accept")
    s = sub.add_parser("cataloguemark", help="reject (or restore) one field of the waiting Catalogue record before Accept")
    s.add_argument("--source", required=True)
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--rejected", type=int, required=True, choices=(0, 1))
    s = sub.add_parser("catalogueexport", help="the accepted record as MARCXML or CSL-JSON: {ok, filename, mime, text}")
    s.add_argument("--source", required=True)
    s.add_argument("--format", required=True, choices=("marcxml", "csl"))
    s = sub.add_parser("cataloguerun", help=argparse.SUPPRESS)
    s.add_argument("--source", required=True)
    s.add_argument("--model", default="haiku")
    s.add_argument("--args")  # tool args
    s = sub.add_parser("history", help="Action History: what the person and the agents did, newest first (--kind run|tool|source|toast)")
    s.add_argument("--kind")
    s.add_argument("--limit", type=int, default=400)
    s = sub.add_parser("notice", help="log a notice the window showed (a toast): tool_runs, tool=notice")
    s.add_argument("--level", default="info")
    s.add_argument("--text", required=True)
    s = sub.add_parser("sourcethumb", help="the first page of a dropped PDF as a small PNG (cached under state/thumbs); {ok, path}")
    s.add_argument("slug")
    s = sub.add_parser("wikipedia", help="take a Wikipedia article in as a text source (the one network call, started by the person)")
    s.add_argument("--lang", default="en")
    s.add_argument("--query", required=True)
    s = sub.add_parser("pagetext", help="one page of a source's text (a part, for a text source)")
    s.add_argument("source")
    s.add_argument("page", type=int)
    s = sub.add_parser("collections", help="an archive's collections, or one collection's records")
    s.add_argument("archive")
    s.add_argument("--collection")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=archives.PAGE)
    s = sub.add_parser("draft", help="the Draft archive: make it empty if it is missing; --reset deletes and makes it again")
    s.add_argument("--reset", action="store_true")
    s = sub.add_parser("chatstart", help="the chat bubble: start a reply ({messages, model, context} as base64 JSON); returns at once")  # chat
    s.add_argument("--req64", required=True)                                                                                   # chat
    for name in ("chatget", "chatrun"):                                                                                         # chat
        sub.add_parser(name, help="the chat bubble: a reply's state" if name == "chatget" else argparse.SUPPRESS).add_argument("--id", required=True)  # chat
    # entitytrace
    s = sub.add_parser("entitytrace", help="one entity's compact Trace (--record ID, or --name), or a source's entities (--source SLUG)")
    s.add_argument("--archive", required=True)
    s.add_argument("--record")
    s.add_argument("--name")
    s.add_argument("--schema")
    s.add_argument("--source")
    s.add_argument("--title", default="")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if getattr(a, "id_opt", None):
        a.id = a.id_opt

    if a.cmd == "exec":
        return execute(a.id)
    if a.cmd in ("chatstart", "chatget", "chatrun"):                 # chat
        import chat
        return chat.main(a, lambda rid: (lambda r: summary(r) if r else None)(find(rid)))
    if a.cmd == "entitytrace":                                      # entitytrace
        out = entitytrace_command(a)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok", True) else 2
    if a.cmd in ("sourcelist", "sourceadd", "sourceupdate", "sourceremove", "sourcehide", "sourceunhide", "sourcethumb", "wikipedia", "notice", "pagetext", "collections", "draft"):
        out = other_command(a)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok", True) else 2
    if a.cmd == "logdb":
        out = logdb(a)
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd in ("cataloguestart", "catalogueget", "catalogueact", "catalogueedit", "cataloguemark", "catalogueexport", "cataloguerun", "history", "briefgen"):
        import catalogue
        try:
            if a.cmd == "briefgen":
                out = {"ok": True, "brief": catalogue.brief_draft(a.source, a.model)}
                print(json.dumps(out, ensure_ascii=False))
                return 0
            if a.cmd == "cataloguerun":
                return catalogue.run(a.source, a.model, json.loads(a.args) if a.args else None)
            if a.cmd == "history":
                import history
                out = history.rows(a.kind, a.limit)
            else:
                out = (catalogue.begin(a.source, a.model, json.loads(a.args) if a.args else None) if a.cmd == "cataloguestart" else catalogue.get(a.source) if a.cmd == "catalogueget"
                       else catalogue.act(a.source, a.action) if a.cmd == "catalogueact" else catalogue.edit(a.source, a.index, a.value, pending=a.pending) if a.cmd == "catalogueedit" else catalogue.mark(a.source, a.index, bool(a.rejected)) if a.cmd == "cataloguemark" else catalogue.export(a.source, a.format))
        except ValueError as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd in ("proofreadstart", "proofreadget", "proofreadact", "proofreadrun"):
        import proofread
        try:
            if a.cmd == "proofreadrun":
                return proofread.run(a.source, a.pages, a.model)
            out = (proofread.begin(a.source, a.pages, a.model) if a.cmd == "proofreadstart" else
                   proofread.get(a.source, a.page) if a.cmd == "proofreadget" else proofread.act(a.source, a.page, a.action))
        except ValueError as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd == "ready":
        import tools
        try:
            parse_pages(a.pages)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", a.source) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", a.recipe):
                raise ValueError("a source and a recipe name")
            try:
                forbidden = getattr(driver_module(), "FORBIDDEN", ())
            except Exception:                                    # noqa: BLE001 - the templates check is a help
                forbidden = ()
            out = tools.ready(a.source, a.pages, a.recipe, a.archive, analyse, forbidden)
            try:                                                 # the answer joins the log's tool runs
                import runlog
                now = time.time()
                runlog.log_tool_run("ready", a.source, {"status": out.get("level"), "started": now, "ended": now, "source": a.source,
                                                        "pages_arg": a.pages, "archive": a.archive, "errors": out.get("errors"),
                                                        "warnings": out.get("warnings"), "sentence": out.get("sentence"),
                                                        "items": out.get("items")})
            except Exception:                                    # noqa: BLE001
                pass
        except ValueError as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd == "toolrun":
        import tools
        return tools.run(a.tool, a.key, json.loads(a.spec))
    if a.cmd in ("toolstart", "toolget"):
        import tools
        try:
            out = (tools.begin(a.tool, a.source, a.archive, a.pages, a.lang, json.loads(a.args) if a.args else None) if a.cmd == "toolstart"
                   else tools.get(a.tool, a.source, a.archive))
        except ValueError as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd == "toolabout":  # tool args
        import tools
        try:
            out = tools.about(a.tool, a.source, json.loads(a.args) if a.args else None)
        except ValueError as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    if a.cmd == "analyse":
        t0 = time.time()
        out = analyse(a.source, a.pages, a.recipe)
        log_action("analyse", f"{a.source}-{int(t0 * 1000)}", {"source": a.source, "pages_arg": a.pages, "started": t0, "status": "done" if out.get("ok", True) else "failed", "error": None if out.get("ok", True) else out.get("why")})
        print(json.dumps(out, ensure_ascii=False) if a.json else json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out.get("ok", True) else 2
    if a.cmd in ("recipe", "prompt"):
        try:
            got = recipe_detail_cached(a.name, a.source) if a.cmd == "recipe" else prompt_text(a.name)
        except (ValueError, OSError) as e:
            print(json.dumps({"ok": False, "why": str(e)}))
            return 2
        print(json.dumps(got, ensure_ascii=False, indent=1) if a.json else (got.get("text") or json.dumps(got, indent=1)))
        return 0
    if a.cmd == "configs":
        if a.legacy:
            rows = configs()
            print(json.dumps(rows, indent=1, ensure_ascii=False) if a.json else
                  "\n".join(f"{r['kind']:<11} {r['name']:<28} {r['description']}" for r in rows))
            return 0
        got = recipes_and_sources()
        archives.ensure_draft_later()
        if a.json:
            print(json.dumps(got, indent=1, ensure_ascii=False))
        else:
            print("\n".join([f"recipe      {r['name']:<28} {r['description']}" for r in got["recipes"]]
                            + [f"source      {s['name']:<28} {s['title']} ({s['pages'] or 'no default pages'})" for s in got["sources"]]
                            + [f"{r['kind']:<11} {r['name']:<28} {r['description']}" for r in got["briefs"]]))
        return 0
    if a.cmd == "start":
        try:
            rec = start(a.path, a.name, a.model, a.dry, a.fast, a.pages, a.budget, a.archive, a.recipe, a.source, a.rate,
                       a.workflow, a.models, a.without, a.flow, a.lag, a.after, None, not a.no_limits,
                       agents=parse_agents(a.agents))
        except ValueError as e:
            print(json.dumps({"ok": False, "why": str(e)}) if a.json else f"cannot start: {e}")
            return 2
        out = {"ok": True, "id": rec["id"], "pid": rec["pid"], "kind": rec["kind"], "run_folder": rec["run_folder"],
               "archive": rec.get("archive"), "dry": rec["dry"], "fast": rec["fast"]}
        print(json.dumps(out) if a.json else f"started {rec['id']} (pid {rec['pid']}) -> {rec['run_folder']}")
        return 0
    if a.cmd == "ls":
        rows = [summary(r) for r in records()]
        print(json.dumps(rows, indent=1) if a.json else _table(rows))
        return 0
    rec = find(a.id)
    if a.cmd == "rerun":
        try:
            out = rerun(a.id)
        except ValueError as e:
            out = {"ok": False, "id": a.id, "why": str(e)}
        print(json.dumps(out) if a.json else (f"started {out['id']} again" if out["ok"] else f"not started: {out['why']}"))
        return 0 if out["ok"] else 1
    if a.cmd == "resume":
        out = resume(a.id)
        print(json.dumps(out) if a.json else (f"resumed {out['id']} (pid {out['pid']})" if out["ok"] else f"not resumed: {out['why']}"))
        return 0 if out["ok"] else 1
    if a.cmd == "clear":
        out = clear(a.id)
        print(json.dumps(out) if a.json else (f"cleared {out['id']}: {len(out['deleted'])} folders deleted" if out["ok"]
                                              else f"not cleared: {out['why']}"))
        return 0 if out["ok"] else 1
    if a.cmd == "limits":  # limits
        out = set_limits(a.id, a.state == "on")
        print(json.dumps(out) if a.json else (f"limits {a.state}" if out["ok"] else f"not changed: {out['why']}"))
        return 0 if out["ok"] else 1
    if a.cmd == "stop":
        out = stop(a.id)
        print(json.dumps(out) if a.json else (out.get("how") if out["ok"] else f"not stopped: {out['why']}"))
        return 0 if out["ok"] else 1
    if not rec:
        print(json.dumps({"ok": False, "why": f"no run {a.id}"}) if a.json else f"no run {a.id}")
        return 2
    if a.cmd == "status":
        s = summary(rec, full=True)
        if a.json:
            print(json.dumps(s, indent=1))
        else:
            print(_table([s]))
            print(f"\nfolder   {s['run_folder']}\narchive  {s['archive']}\nlast     {s['last']}")
            print(f"tasks    " + "; ".join(f"{t['id']} {t['steps_done']}/{t['steps_total']} {t['status']}"
                                          for t in s["tasks"]) if s["tasks"] else "tasks    -")
            print(f"questions {s['questions']}  errors {s['calls']['errors']}  report {s['report'] or '-'}")
        return 0
    if a.cmd == "tail":
        lines = runfiles.tail_lines(_files(rec).trace(), a.n)
        print(json.dumps({"id": rec["id"], "lines": lines}) if a.json else "\n".join(lines))
        return 0
    # choices
    if a.cmd == "choices":
        import choices
        out = {"id": rec["id"], **choices.build(_files(rec))} if rec["kind"] == "pageflow" else {"id": rec["id"], "ticks": [], "choices": []}
        print(json.dumps(out, ensure_ascii=False))
        return 0
    # end choices
    # pageview
    if a.cmd == "pageview":
        import pageview
        if rec["kind"] != "pageflow":
            out = {"ok": False, "why": "only a page-flow run has pages"}
        else:
            try:
                if a.all:
                    out = pageview.run(_files(rec), rec.get("archive"), a.group, a.page)
                elif a.page:
                    out = pageview.page(_files(rec), a.page, rec.get("archive"))
                else:
                    out = pageview.strip(_files(rec), rec.get("archive"))
            except (OSError, ValueError) as e:
                out = {"ok": False, "why": str(e)}
        out = {"id": rec["id"], **out}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    # end pageview
    # export
    if a.cmd == "export":
        try:
            out = export_run(rec, a.format, a.group, a.page, a.by)
        except (OSError, ValueError) as e:
            out = {"ok": False, "why": str(e)}
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") else 2
    # end export
    if a.cmd == "view":
        v = view(rec, a.tab, row=a.row, call=a.n)
        if a.json:
            print(json.dumps(v, ensure_ascii=False))
        else:
            print("\n".join(_view_text(rec, a.tab)))
        return 0
    return 1


def hidden_sources() -> set[str]:
    """Built-in source slugs the owner hid from Apollo (state/hidden-sources.json, a JSON list). The page flow's own
    profiles stay on disk: its tests and golden configs use them."""
    try:
        return set(json.loads((state_dir() / "hidden-sources.json").read_text(encoding="utf-8")) or [])
    except (OSError, ValueError, TypeError):
        return set()


_PDF_PAGES: dict = {}


def _builtin_facts(sp: dict) -> dict:
    """What the Sources card shows of a built-in profile: format, the file's page count (a PDF) or parts, bytes, language, address."""
    rel = sp.get("file")
    out = {"format": None, "page_count": None, "kind": "pages", "bytes": None}
    if not rel:
        return out
    p = Path(rel)
    p = p if p.is_absolute() else runfiles.TESTING / p
    out["format"] = p.suffix.lstrip(".").lower() or None
    try:
        st = p.stat()
    except OSError:
        return out
    out["bytes"] = st.st_size
    key = (str(p), st.st_mtime_ns)
    if out["format"] == "pdf":
        if key not in _PDF_PAGES:
            import pagetext
            _PDF_PAGES[key] = pagetext._pdf_total(p)
        out["page_count"] = _PDF_PAGES[key]
    else:
        out["kind"] = "parts"
        if key not in _PDF_PAGES:
            try:
                _PDF_PAGES[key] = sourcedrop.parts_count(sourcedrop.read_text_file(p)) if out["format"] in ("txt", "md") else None
            except (OSError, ValueError):
                _PDF_PAGES[key] = None
        out["page_count"] = _PDF_PAGES[key]
    return out


def builtin_sources() -> list[dict]:
    """The sources that ship with the page flow (pageflow/sources/), but those hidden: slug, title, publisher, pages, context,
    and what the card shows (format, count, language, address). Read only: Apollo never writes these profiles."""
    out = []
    hidden = hidden_sources()
    for f in sorted(composer().SOURCES.glob("*.y*ml")):
        if f.stem in hidden:
            continue
        try:
            sp = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        pg = sp.get("pages") if isinstance(sp.get("pages"), dict) else {}
        out.append({"slug": f.stem, "title": sp.get("title") or f.stem, "publisher": sp.get("publisher"),
                    "pages": f"{pg['from']}-{pg['to']}" if pg.get("from") and pg.get("to") else None,
                    "url": sp.get("url"), "language": sp.get("language"), "builtin": True, **_builtin_facts(sp)})
    return out


def set_hidden(slug: str, hide: bool) -> dict:
    """`sourcehide` and `sourceunhide`: add the built-in to state/hidden-sources.json, or take it out. Only the page flow's own
    profiles can be hidden (a dropped source is removed)."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        raise ValueError(f"not a source name: {slug!r}")
    if not any(f.stem == slug for f in composer().SOURCES.glob("*.y*ml")):
        raise ValueError(f"{slug!r} is not a built-in source" + (" (a dropped source is removed, not hidden)" if (sourcedrop.folder() / f"{slug}.yaml").is_file() else ""))
    cur = sorted(hidden_sources() | {slug} if hide else hidden_sources() - {slug})
    path = state_dir() / "hidden-sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cur), encoding="utf-8")
    os.replace(tmp, path)
    log_action("source", f"{'hide' if hide else 'unhide'}-{slug}", {"source": slug, "detail": {"action": "hide" if hide else "unhide"}})
    return {"ok": True, "slug": slug, "hidden": cur}


def log_action(tool: str, key: str, rec: dict | None = None) -> None:
    """One row of the log's tool runs for something the person or an agent did (Action History). The log is a copy: a busy or
    missing log never stops the action."""
    try:
        import runlog
        now = time.time()
        runlog.log_tool_run(tool, key, {"status": "done", "started": now, "ended": now, **(rec or {})})
    except Exception:                                        # noqa: BLE001
        pass


def _incoming_clean(path: Path) -> None:
    """Delete an uploaded file once it is taken in (or refused) when it sits in sources/incoming/; nothing elsewhere."""
    inc = (sourcedrop.folder() / "incoming").resolve()
    try:
        f = path.resolve()
    except OSError:
        return
    if inc in f.parents and f.is_file():
        f.unlink()
        d = f.parent
        while d != inc and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent


def _run_collection_of(rid):
    """A run's name for the Collections table: its flow and source in words, else its id."""
    rec = find(rid) if rid else None
    if not rec:
        return None
    return {"id": rec["id"], "flow": runview.flow_label(rec), "source": source_title(rec)}


# entitytrace: the right rails' Trace (read only; entitytrace.py)
def entitytrace_command(a) -> dict:
    """`entitytrace --archive A --record ID` (or --name N [--schema S]): the entity's Trace; `--source SLUG`: that source's entities."""
    import entitytrace
    import tools
    try:
        arch = tools.allowed_archive(a.archive)
        if a.source:
            return entitytrace.entities(str(arch), a.source, a.title)
        return entitytrace.trace(str(arch), a.record, a.name, a.schema)
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as e:
        return {"ok": False, "why": str(e)}


def other_command(a) -> dict:
    """The sources, page text, collections and Draft commands: one JSON answer each ({ok: false, why} when refused)."""
    try:
        if a.cmd == "sourcelist":
            import catalogue                                                             # tool args: each row's Catalogue state
            marks = catalogue.marks()
            rows = {"dropped": sourcedrop.listing(), "builtin": builtin_sources()}
            for part in rows.values():
                for r in part:
                    r["catalogue"] = marks.get(r.get("slug"), "none")
            return {"ok": True, **rows}
        if a.cmd == "sourceadd":
            path = Path(a.path)
            try:
                got = sourcedrop.add(path, title=a.title)
            finally:
                _incoming_clean(path)
            log_action("source", f"add-{got['slug']}", {"source": got["slug"], "detail": {"action": "add", "title": got["profile"].get("title")}})
            return {"ok": True, "slug": got["slug"], "needs": got["needs"], "title": got["profile"].get("title")}
        if a.cmd == "sourceupdate":
            fields = json.loads(a.fields)
            if not isinstance(fields, dict):
                raise ValueError("--fields is a JSON object")
            got = sourcedrop.update(a.slug, **fields)
            return {"ok": True, "slug": got["slug"], "needs": got["needs"]}
        if a.cmd == "sourceremove":
            got = sourcedrop.remove(a.slug)
            log_action("source", f"remove-{a.slug}", {"source": a.slug, "detail": {"action": "remove"}})
            return {"ok": True, **got}
        if a.cmd in ("sourcehide", "sourceunhide"):
            return set_hidden(a.slug, a.cmd == "sourcehide")
        if a.cmd == "notice":
            import runlog
            now = time.time()
            level = a.level if a.level in ("error", "warn", "info") else "info"
            runlog.log_tool_run("notice", f"{level}-{int(now * 1000)}", {"status": level, "started": now, "ended": now, "error": a.text[:500]})
            return {"ok": True}
        if a.cmd == "sourcethumb":
            got = sourcedrop.thumb(a.slug, state_dir() / "thumbs")
            return {"ok": True, "path": str(got)} if got else {"ok": False, "why": "no picture for this source"}
        if a.cmd == "wikipedia":
            import wikipedia
            t0 = time.time()
            got = wikipedia.add(a.lang, a.query)
            log_action("wikipedia", f"{got['slug']}", {"source": got["slug"], "started": t0, "detail": {"query": a.query, "lang": a.lang}})
            return {"ok": True, "slug": got["slug"], "needs": got["needs"], "title": got["profile"].get("title")}
        if a.cmd == "pagetext":
            import pagetext
            return pagetext.page(a.source, a.page)
        if a.cmd == "draft":
            return {"ok": True, **(archives.reset_draft() if a.reset else archives.ensure_draft())}
        import tools                                                 # collections
        arch = tools.allowed_archive(a.archive)
        if a.collection:
            return archives.records(str(arch), a.collection, a.offset, a.limit)
        got = archives.collections(str(arch))
        for row in got["collections"]:
            row["run"] = _run_collection_of(row.get("run"))
        got["building"] = tools.build_if_stale(str(arch))["building"]
        return got
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as e:
        return {"ok": False, "why": str(e)}


def _view_text(rec: dict, tab: str) -> list[str]:
    """`view` without --json: the tab as the window's text (runfiles' line formatters)."""
    f = _files(rec)
    if tab == "raw":
        return f.driver_log(100)
    if tab == "run":
        return [f"{at}  {text}" for at, text, _ in runfiles.run_lines(f.trace())]
    if tab == "found":
        lines, count = runfiles.found_lines(f.found())
        return ["".join(t for t, _ in ln) for ln in lines] + [count]
    if tab == "questions":
        return [t for t, _ in runfiles.question_lines(f.questions())]
    if tab == "calls":
        calls = f.calls()
        return [runfiles.calls_head(calls)] + [runfiles.call_line(c) for c in runfiles.call_rows(calls)]
    return [t for t, _ in runfiles.report_lines(f.report())]


if __name__ == "__main__":
    sys.exit(main())

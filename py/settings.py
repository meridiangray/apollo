"""settings.py: the Settings page, the one place Apollo is set up (hydra-docs plans/model-providers.md §5, CZ330).

Each setting is kept once, where its owner keeps it:

    Models, keys    hydra's chat config (<config>/chat/models.yaml, hydra.machine.chatconf) and this machine's keystore
                    (hydra.machine.secrets). A key's value arrives on stdin, goes to the keystore and is never read back:
                    the page sees only whether it is set.
    Network         hydra's own switch: the basemap's `online` (hydra.geo.basemap, basemap.json).
    Defaults, Limits, Folders    Apollo's own: apollo.json in apollo/ (APOLLO_CONFIG overrides; not the Go app's
                    apollo.json beside the executable). A missing entry is the code's default: runview.FLOW_SPEC for
                    agents, apollo.default_budget for a run's cost cap, chat.BUDGET_USD / chat.TIMEOUT for a chat reply,
                    apollo/archives and apollo/sources for the folders (APOLLO_ARCHIVES / APOLLO_SOURCES still win).
    Appearance      the page itself (theme, mode, scroll line, cost), as before.

    apollo.py settings                       everything the page draws (read only; the window's worker answers it)
    apollo.py settingsset --req64 B64 [--stdin]    one change, {op, ...}; --stdin: a key's value on standard input
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LIMITS = {"limits.run_usd": (0.1, 1000.0), "limits.chat_usd": (0.1, 100.0), "limits.chat_secs": (30, 7200),
          "limits.page_reach": (0, 20)}
FOLDERS = {"folders.archives": "APOLLO_ARCHIVES", "folders.sources": "APOLLO_SOURCES"}
TEST_TIMEOUT = 90                                         # seconds the Test click may take (hydra's own call caps at 30 s)


def file() -> Path:
    return Path(os.environ.get("APOLLO_CONFIG") or HERE / "apollo.json")


def load() -> dict:
    try:
        got = json.loads(file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def value(key: str, default=None):
    """The stored setting, or `default` when none is stored."""
    v = load().get(key)
    return default if v is None or v == "" else v


def folder(key: str, fallback: Path) -> Path:
    """A folder setting as the code uses it: the environment variable first, then apollo.json, then `fallback`."""
    return Path(os.environ.get(FOLDERS[key]) or value(key) or fallback)


def _save(data: dict) -> None:
    f = file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, f)


def _agent_bounds(key: str) -> tuple[int, int]:
    import runview
    _, flow, grade = key.split(".", 2) if key.count(".") == 2 else ("", "", "")
    lim = ((runview.FLOW_SPEC.get(flow) or {}).get("grades") or {}).get(grade)
    if lim is None:
        raise ValueError(f"no setting {key}")
    return (1 if flow == "read" else 0), lim["max"]


def _checked(key: str, v):
    if key in LIMITS:
        lo, hi = LIMITS[key]
        try:
            v = float(v) if key.endswith("_usd") else int(v)
        except (TypeError, ValueError):
            raise ValueError("a number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{lo:g} to {hi:g}")
        return v
    if key in FOLDERS:
        p = Path(str(v)).expanduser()
        if not p.is_absolute() or not p.is_dir():
            raise ValueError(f"no folder {v}")
        return str(p)
    lo, hi = _agent_bounds(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{lo} to {hi}")
    return v


def put(key: str, v) -> None:
    """Store one of Apollo's own settings; None or "" goes back to the default. ValueError in plain words."""
    if key.startswith("agents."):
        _agent_bounds(key)
    elif key not in LIMITS and key not in FOLDERS:
        raise ValueError(f"no setting {key}")
    data = load()
    if v is None or v == "":
        data.pop(key, None)
    else:
        data[key] = _checked(key, v)
    _save(data)


# ---- hydra's settings ---------------------------------------------------------------------------------------------------

def _basemap():
    from hydra.geo import basemap
    return basemap.store()


def page() -> dict:
    """Everything the Settings page draws. Never a key's value."""
    import chat
    import runview
    from hydra.machine import chatconf, secrets
    rows = [{**m, "set": bool(m["key"]) and secrets.has(m["key"])} for m in chatconf.models()]
    flows = [{"key": f["key"], "label": f["label"], "grades": {g: {**lim, "low": 1 if f["key"] == "read" else 0,
                                                                   "code": runview.FLOW_SPEC[f["key"]]["grades"][g]["default"]}
                                                               for g, lim in f["grades"].items()}}
             for f in runview.flow_catalogue()]
    import archives
    import sourcedrop
    where = {"folders.archives": archives.folder(), "folders.sources": sourcedrop.folder()}
    folders = {key.split(".")[1]: {"path": str(where[key]), "env": bool(os.environ.get(env)), "set": bool(value(key))}
               for key, env in FOLDERS.items()}
    try:
        online = bool(_basemap().conf().get("online", True))
    except Exception:                                     # noqa: BLE001 - the page still draws
        online = None
    return {"ok": True, "models": rows, "providers": list(chatconf.PROVIDERS), "can_store": secrets.can_store(),
            "flows": flows,
            "limits": {"run_usd": value("limits.run_usd"), "chat_usd": value("limits.chat_usd", chat.BUDGET_USD),
                       "chat_secs": value("limits.chat_secs", chat.TIMEOUT)},
            "page_reach": value("limits.page_reach"), "folders": folders, "network": {"basemap": online}}


def _test(model_id: str) -> dict:
    """One tiny call to the model, through hydra's own command (`hydra-ctl env test`)."""
    import tools
    try:
        p = subprocess.run(tools._hydra_ctl() + ["env", "test", model_id], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TEST_TIMEOUT, stdin=subprocess.DEVNULL)
        got = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "No answer in time."}
    except (OSError, ValueError):
        got = None
    if not isinstance(got, dict) or "ok" not in got:
        return {"ok": False, "error": "The test could not run."}
    return {"ok": bool(got["ok"]), **({"reply": str(got.get("reply") or ""), "ms": got.get("ms")} if got["ok"]
                                      else {"error": str(got.get("error") or "Failed.")})}


def apply(req: dict, secret: str | None = None) -> dict:
    """One change from the page: {op: set|models|key|test|network, ...}; -> the page after it (Test: {ok, test})."""
    from hydra.machine import chatconf, secrets
    op = req.get("op") if isinstance(req, dict) else None
    if op == "set":
        put(str(req.get("key") or ""), req.get("value"))
    elif op == "models":
        chatconf.save_models(req.get("rows"))
    elif op == "key":
        name = req.get("name")
        if name not in chatconf.key_names():
            raise ValueError("no model uses that key")
        try:
            if req.get("remove"):
                secrets.unset(name)
            else:
                secrets.set(name, secret or "")
        except secrets.SecretsError as e:
            raise ValueError(str(e)) from None
    elif op == "test":
        if req.get("id") not in [m["id"] for m in chatconf.models()]:
            raise ValueError("no such model")
        return {"ok": True, "test": _test(req["id"])}
    elif op == "network":
        if req.get("name") != "basemap" or not isinstance(req.get("on"), bool):
            raise ValueError("network: basemap on or off")
        _basemap().save_conf(online=req["on"])
    else:
        raise ValueError("op: set, models, key, test or network")
    return page()


def main(a, stdin) -> dict:
    """`settings` / `settingsset` (apollo.py); every refusal is {ok: false, why}."""
    if a.cmd == "settings":
        return page()
    try:
        req = json.loads(base64.b64decode(a.req64, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "why": "a change as base64 JSON"}
    secret = stdin.read().strip() if a.stdin else None
    try:
        return apply(req, secret)
    except ValueError as e:
        return {"ok": False, "why": str(e)}

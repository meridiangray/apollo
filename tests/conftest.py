"""Every Apollo test works on scratch folders: its own registry (APOLLO_STATE) and run root (APOLLO_ROOT), a fake driver
for page-flow runs, and no `claude`: a session runs only dry (drysession.py)."""
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APOLLO = HERE.parent
sys.path.insert(0, str(APOLLO / "py"))
TESTING = Path(os.environ.get("APOLLO_TESTING") or APOLLO.parent / "hydra-mcp-testing")
sys.path.insert(0, str(TESTING / "pageflow"))


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("APOLLO_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("APOLLO_DRIVER", str(HERE / "fake_driver.py"))
    monkeypatch.setenv("APOLLO_RUN_PYTHON", sys.executable)
    monkeypatch.setenv("APOLLO_CLAUDE", "/bin/false")            # never a real claude
    monkeypatch.setenv("APOLLO_DRY_STEP", "0.02")
    monkeypatch.setenv("APOLLO_ARCHIVES", str(tmp_path / "archives"))      # the Draft, never the real one
    monkeypatch.setenv("APOLLO_NO_DRAFT", "1")                             # no background `hydra-ctl init` unless a test asks
    monkeypatch.delenv("PAGEFLOW_STUB_MODELS", raising=False)
    return tmp_path


def wait_for(cond, timeout=30.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        got = cond()
        if got:
            return got
        time.sleep(step)
    raise AssertionError("timed out")


@pytest.fixture
def smoke_config():
    return TESTING / "pageflow" / "tests" / "fixtures" / "legacy" / "pollard-smoke.yaml"   # an already-composed config


@pytest.fixture
def brief(tmp_path):
    p = tmp_path / "brief.md"
    p.write_text("# Read the archive and report\n\nDo nothing else.\n", encoding="utf-8")
    return p


def pid_gone(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return True
    return stat.rsplit(")", 1)[-1].split()[0] in ("Z", "X")


@pytest.fixture
def gone():
    return pid_gone


@pytest.fixture
def waiter():
    return wait_for


os.environ.setdefault("PYTHONUNBUFFERED", "1")

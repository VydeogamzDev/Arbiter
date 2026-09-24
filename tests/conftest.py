from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from arbiter_agent.daemon import lifecycle
from arbiter_agent.paths import ArbiterPaths, get_paths

IS_WIN = sys.platform == "win32"
# The environment before any isolation fixture ran (only opt-in real-client tests may use it).
ORIGINAL_ENV = dict(os.environ)


@pytest.fixture(autouse=True)
def _isolate_real_homes(tmp_path_factory, monkeypatch):
    """No test may touch the developer's real Arbiter, Codex or Claude homes: default every
    location to a throwaway directory. Tests that need specific homes override these."""
    base = tmp_path_factory.mktemp("isolated")
    default_home = base / "default-arbiter-home"
    monkeypatch.setenv("ARBITER_HOME", str(default_home))
    monkeypatch.setenv("CODEX_HOME", str(base / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(base / "claude"))
    monkeypatch.setenv("ARBITER_CLIENT_HOME", str(base))
    yield
    if (default_home / "data" / "state" / "ipc.token").exists():  # something autostarted a daemon here
        lifecycle.stop(get_paths(default_home), timeout=5)


@pytest.fixture
def home(tmp_path: Path) -> ArbiterPaths:
    return get_paths(tmp_path / "arbiter-home").ensure()


def spawn_daemon(paths: ArbiterPaths, extra_env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """Start a daemon as a plain child (tests control its lifetime; production uses WMI)."""
    env = dict(os.environ, **(extra_env or {}))
    flags = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
    return subprocess.Popen(lifecycle.daemon_argv(paths), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env, creationflags=flags)


def wait_running(paths: ArbiterPaths, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lifecycle.is_running(paths, timeout=0.5):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def daemon(home: ArbiterPaths):
    proc = spawn_daemon(home)
    assert wait_running(home), "daemon did not start"
    yield home, proc
    lifecycle.stop(home, timeout=5)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

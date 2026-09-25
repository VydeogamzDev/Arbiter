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
    monkeypatch.setenv("ARBITER_TEST_PACKAGED", "")   # tests never depend on the terminal being inside an MSIX app
    yield
    if (default_home / "data" / "state" / "ipc.token").exists():  # something autostarted a daemon here
        lifecycle.stop(get_paths(default_home), timeout=5)


@pytest.fixture
def home(tmp_path: Path) -> ArbiterPaths:
    return get_paths(tmp_path / "arbiter-home").ensure()


def cli_argv(paths: ArbiterPaths) -> list[str]:
    """Plain `arbiter --home ...` argv for CLI subprocesses. Unlike lifecycle.daemon_argv, it carries
    no --client-env, so the subprocess uses the env the test passes it."""
    return [sys.executable, "-m", "arbiter_agent", "--home", str(paths.root)]


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


def pytest_runtest_logreport(report):  # type: ignore[no-untyped-def]
    """On GitHub Actions, surface each failure as an error annotation (readable without log access)."""
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed or report.when not in ("setup", "call"):
        return
    path, line, _ = report.location
    crash = getattr(report.longrepr, "reprcrash", None)
    msg = crash.message if crash is not None else str(report.longrepr).strip().splitlines()[-1:]
    msg = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:900]
    print(f"\n::error file={path},line={(line or 0) + 1}::{report.nodeid} [{sys.platform}]: {msg}", flush=True)


def pytest_terminal_summary(terminalreporter):  # type: ignore[no-untyped-def]
    """On GitHub Actions, report the slowest tests as a notice annotation (readable without log access)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    totals: dict[str, float] = {}
    for reports in terminalreporter.stats.values():
        for r in reports:
            if hasattr(r, "duration") and hasattr(r, "nodeid"):
                totals[r.nodeid] = totals.get(r.nodeid, 0.0) + float(r.duration)
    slow = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:10]
    msg = "; ".join(f"{node} {secs:.1f}s" for node, secs in slow).replace("%", "%25")
    print(f"\n::notice title=slowest tests [{sys.platform}]::{msg}", flush=True)

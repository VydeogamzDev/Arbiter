"""Lazy autostart and restart (spec §4.4.1, §16.5). Stdlib only (called from shims).

``ensure_daemon`` is cheap when the daemon is up (one authenticated hello). When it is down it
starts one launch (debounced across processes by a launch marker) and optionally waits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable
from arbiter_agent.paths import ArbiterPaths, get_paths

LAUNCH_DEBOUNCE_S = 5.0


# Client-home overrides the daemon must honor. WMI-launched processes don't inherit our
# environment, so they travel on the command line (otherwise a custom CODEX_HOME would be ignored).
CLIENT_ENV_VARS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "ARBITER_CLIENT_HOME")


def daemon_argv(paths: ArbiterPaths) -> list[str]:
    argv = [sys.executable, "-m", "arbiter_agent"]
    if paths.root is not None:
        argv += ["--home", str(paths.root)]  # WMI-launched processes don't inherit our environment
    for name in CLIENT_ENV_VARS:
        value = os.environ.get(name)
        if value:
            argv += ["--client-env", f"{name}={value}"]
    return argv + ["daemon", "run"]


def is_running(paths: ArbiterPaths, timeout: float = 0.5) -> bool:
    try:
        with DaemonClient(paths, component="probe") as c:
            c.request("ping", timeout=timeout)
        return True
    except DaemonUnavailable:
        return False


def _claim_launch(paths: ArbiterPaths) -> bool:
    """Debounce: only one process launches within LAUNCH_DEBOUNCE_S."""
    marker = paths.state / "launch.marker"
    paths.state.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        if now - marker.stat().st_mtime < LAUNCH_DEBOUNCE_S:
            return False
    except FileNotFoundError:
        pass
    try:
        tmp = marker.with_name(f"launch.marker.{os.getpid()}")
        tmp.write_text(str(now))
        os.replace(tmp, marker)
    except OSError:
        return False
    return True


def launch(paths: ArbiterPaths | None = None, *, method: str = "auto") -> str:
    """Start a daemon process outside the caller's process tree. Returns the method used."""
    paths = paths or get_paths()
    argv = daemon_argv(paths)
    cwd = str(Path.home())
    if sys.platform == "win32":
        from arbiter_agent.daemon import launcher_windows as lw

        if method in ("auto", "wmi"):
            try:
                lw.launch_via_wmi(argv, cwd)
                return "wmi"
            except OSError:
                if method == "wmi":
                    raise
        lw.launch_detached(argv, cwd)
        return "detached"
    subprocess.Popen(argv, cwd=cwd, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return "setsid"


def ensure_daemon(paths: ArbiterPaths | None = None, *, wait: float = 0.0, method: str = "auto") -> bool:
    """True if a daemon answers (possibly after launching one and waiting up to ``wait``)."""
    paths = paths or get_paths()
    if is_running(paths):
        return True
    if _claim_launch(paths):
        try:
            launch(paths, method=method)
        except OSError:
            return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if is_running(paths):
            return True
    return False


def stop(paths: ArbiterPaths | None = None, timeout: float = 10.0) -> bool:
    paths = paths or get_paths()
    try:
        with DaemonClient(paths, component="cli") as c:
            c.request("shutdown", {"drain": True}, timeout=2.0)
    except DaemonUnavailable:
        return not is_running(paths)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(paths, timeout=0.3):
            return True
        time.sleep(0.1)
    return False

"""POSIX smoke test without pytest (used where pytest isn't installed, e.g. a bare WSL/Linux box).

Run:  PYTHONPATH=src python3 tests/posix_smoke.py
Exercises the POSIX-only paths: Unix socket endpoint + 0600 permissions, fcntl single-instance
lock, setsid launch, token auth, all three hook transports, drain on stop, replay equality.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import ipc, lifecycle
from arbiter_agent.daemon.client import DaemonClient, read_record
from arbiter_agent.paths import get_paths


def check(cond: bool, what: str) -> None:
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SystemExit(1)


def main() -> int:
    assert sys.platform != "win32", "POSIX only"
    home = get_paths(tempfile.mkdtemp(prefix="arbiter-posix-")).ensure()
    t = time.perf_counter()
    method = lifecycle.launch(home)
    up = lifecycle.ensure_daemon(home, wait=15)
    check(up and method == "setsid", f"daemon launched via {method} in {time.perf_counter() - t:.2f}s")
    try:
        rec = read_record(home)
        check(rec["endpoint"]["kind"] == "unix", "unix socket endpoint")
        mode = stat.S_IMODE(os.stat(home.pipe_address).st_mode)
        check(mode & 0o077 == 0, f"socket mode {oct(mode)} is user-only")
        check(stat.S_IMODE(os.stat(Path(home.pipe_address).parent).st_mode) & 0o077 == 0, "socket dir is 0700")
        check(stat.S_IMODE(os.stat(home.token_file).st_mode) & 0o077 == 0, "token file is 0600")
        try:
            ipc.connect(rec["endpoint"], home.pipe_address, b"wrong", timeout=1)
            check(False, "wrong token rejected")
        except ipc.IPCAuthError:
            check(True, "wrong token rejected")
        second = subprocess.run(lifecycle.daemon_argv(home), capture_output=True, text=True, timeout=20)
        check(second.returncode == 0 and "already running" in second.stderr, "fcntl single-instance lock")
        cwd = tempfile.mkdtemp()
        n = 0
        for client, transport in (("codex", "mcp"), ("claude_code", "http"), ("codex", "command")):
            with FakeHost(home, client=client, transport=transport, cwd=cwd, autostart=False) as h:
                h.session_start()
                h.prompt("hello")
                h.tool("pytest -q", "3 passed", 0)
                h.stop("Done.")
            ok = all(c.ok and c.response == {} for c in h.calls)
            n += len(h.calls)
            lat = sorted(c.latency_ms for c in h.calls)
            check(ok, f"{client} via {transport}: {len(h.calls)} hooks, max {lat[-1]:.1f} ms")
        with DaemonClient(home) as c:
            st = c.request("status")
            check(st["events"] == n, f"{n} events stored")
            check(c.request("replay_check")["equal"], "replay reproduces reducer state")
    finally:
        check(lifecycle.stop(home), "daemon stopped (drained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

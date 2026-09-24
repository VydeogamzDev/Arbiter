import subprocess
import sys
import time

import pytest

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient, read_record
from arbiter_agent.state.store import Migration
from tests.conftest import spawn_daemon, wait_running

IS_WIN = sys.platform == "win32"


def status(home):
    with DaemonClient(home) as c:
        return c.request("status")


def _proc_info(pid):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
                          f"$pp=Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\"; "
                          f"\"$($p.ParentProcessId)|$($pp.Name)|$($pp.ParentProcessId)\""],
                         capture_output=True, text=True)
    return out.stdout.strip().split("|")


def _in_job(pid):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, pid)
    r = wintypes.BOOL()
    k32.IsProcessInJob(h, None, ctypes.byref(r))
    k32.CloseHandle(h)
    return bool(r.value)


def _ensure_in_job():
    """Put this test process in a job object if it isn't in one (e.g. on a CI runner), so the
    escape check is meaningful everywhere. No kill-on-close flag: closing it harms nothing."""
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    job = k32.CreateJobObjectW(None, None)
    assert job and k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()), ctypes.get_last_error()
    return job


@pytest.mark.slow
@pytest.mark.skipif(not IS_WIN, reason="WMI launch is Windows-specific")
def test_autostart_launches_outside_client_job(home):
    """Decision 0018: the lazily started daemon must not live in the caller's job/process tree."""
    if not _in_job(__import__("os").getpid()):  # Claude desktop runs us in a job; a CI runner may not
        _ensure_in_job()
    assert _in_job(__import__("os").getpid())
    t = time.perf_counter()
    assert lifecycle.ensure_daemon(home, wait=15)
    started_in = time.perf_counter() - t
    try:
        pid = status(home)["pid"]
        parent_pid, parent_name, _ = _proc_info(pid)
        # A venv python.exe is a launcher stub that runs the real interpreter as its child, inside
        # the stub's own job (so the pair lives and dies together). The process WMI created is the
        # top of our tree, and that must be outside any client job.
        top = pid
        if parent_name.lower() == "python.exe":
            top = int(parent_pid)
            parent_name = _proc_info(top)[1]
        assert parent_name == "WmiPrvSE.exe", parent_name
        assert not _in_job(top)
        assert started_in < 15
        # WMI drops our environment: client-home overrides must still reach the daemon, or it
        # would watch the developer's real Codex/Claude folders.
        homes = status(home)["client_homes"]
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "ARBITER_CLIENT_HOME"):
            assert homes[name] == __import__("os").environ[name], name
    finally:
        assert lifecycle.stop(home)


def test_launch_is_debounced(home):
    assert lifecycle._claim_launch(home) is True
    assert lifecycle._claim_launch(home) is False  # a second shim within 5 s does not launch again


def test_single_instance_second_daemon_exits(home):
    p1 = spawn_daemon(home)
    assert wait_running(home)
    first_pid = status(home)["pid"]
    p2 = spawn_daemon(home)
    assert p2.wait(timeout=20) == 0  # "already running" is a clean exit, not a crash
    assert status(home)["pid"] == first_pid
    lifecycle.stop(home)
    p1.wait(10)


def test_restart_keeps_port_state_and_reducer(home, tmp_path):
    p = spawn_daemon(home)
    assert wait_running(home)
    port = read_record(home)["http"]["port"]
    with FakeHost(home, client="codex", transport="mcp", cwd=str(tmp_path), autostart=False) as h:
        h.prompt("persist me")
        h.tool("pytest", "ok", 0, emit_post=False)  # leaves one pending tool call
    lifecycle.stop(home)
    p.wait(10)
    p = spawn_daemon(home)
    assert wait_running(home)
    try:
        assert read_record(home)["http"]["port"] == port  # stable port -> client hook config stays valid
        st = status(home)
        assert st["events"] == 2
        with DaemonClient(home) as c:
            assert c.request("replay_check")["equal"] is True  # reducer rebuilt from the log at start
    finally:
        lifecycle.stop(home)
        p.wait(10)


def test_degraded_store_still_fails_open(home, tmp_path, monkeypatch):
    """A failed migration restores the backup, opens read-only, and hooks still pass through."""
    from arbiter_agent.daemon.server import Daemon
    from arbiter_agent.state import store

    first = Daemon(home).start()
    first.shutdown()
    bad = store.packaged_migrations() + [Migration(99, "broken", "NOT VALID SQL;")]
    monkeypatch.setattr(store, "packaged_migrations", lambda: bad)
    d = Daemon(home).start()
    try:
        assert d.degraded and "0099_broken" in d.migration_error
        out = d.ingest_hook("codex", {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t",
                                      "cwd": str(tmp_path)}, surface="mcp")
        assert out["status"] == "failed" and out["response"] == {}
        assert d.status()["degraded"] is True
        assert list(home.backups.glob("*.sqlite"))
    finally:
        d.shutdown()


def test_stop_drains_queued_writes(home, tmp_path):
    p = spawn_daemon(home)
    assert wait_running(home)
    with FakeHost(home, client="claude_code", transport="http", cwd=str(tmp_path), autostart=False) as h:
        for i in range(40):
            h.prompt(f"burst {i}")
    lifecycle.stop(home)
    p.wait(10)
    from arbiter_agent.state.store import connect

    c = connect(home.db, readonly=True)
    try:
        # client events only; the engine also appends internal audit events (epoch changes)
        assert c.execute("SELECT COUNT(*) FROM event_log WHERE surface != 'internal'").fetchone()[0] == 40
    finally:
        c.close()

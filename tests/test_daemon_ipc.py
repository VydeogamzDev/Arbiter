import json
import sys
import urllib.error
import urllib.request

import pytest

from arbiter_agent.daemon import ipc, protocol
from arbiter_agent.daemon.auth import HOOK_TOKEN_HEADER, read_token
from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable, read_record

IS_WIN = sys.platform == "win32"


def test_status_and_record(daemon):
    home, _ = daemon
    with DaemonClient(home) as c:
        st = c.request("status")
    assert not st["degraded"]
    rec = read_record(home)
    # On Windows a venv python.exe is a launcher stub, so the daemon's own pid can differ from proc.pid.
    assert rec["pid"] == st["pid"] and rec["endpoint"]["kind"] == ("pipe" if IS_WIN else "unix")
    assert rec["http"]["port"] == st["http_port"]


def test_wrong_token_rejected(daemon):
    home, _ = daemon
    with pytest.raises(ipc.IPCAuthError):
        ipc.connect(read_record(home).get("endpoint"), home.pipe_address, b"not-the-token", timeout=1)
    with DaemonClient(home) as c:
        assert c.request("status")["auth_failures"] >= 1


def test_token_rotates_on_restart(home):
    from arbiter_agent.daemon import lifecycle
    from tests.conftest import spawn_daemon, wait_running

    p1 = spawn_daemon(home)
    assert wait_running(home)
    t1 = read_token(home.token_file)
    lifecycle.stop(home)
    p1.wait(10)
    p2 = spawn_daemon(home)
    assert wait_running(home)
    t2 = read_token(home.token_file)
    hook1 = read_token(home.hook_token_file)
    lifecycle.stop(home)
    p2.wait(10)
    assert t1 != t2  # shim tokens rotate per start
    p3 = spawn_daemon(home)
    assert wait_running(home)
    assert read_token(home.hook_token_file) == hook1  # hook token is stable
    lifecycle.stop(home)
    p3.wait(10)


def test_requests_before_hello_refused(daemon):
    home, _ = daemon
    conn = ipc.connect(read_record(home).get("endpoint"), home.pipe_address, read_token(home.token_file), 1)
    reply = ipc.call(conn, {"id": 1, "method": "status"}, 2)
    assert reply["ok"] is False and "hello required" in reply["error"]
    conn.close()


def test_version_negotiation_rules():
    assert protocol.negotiate([protocol.PROTOCOL_MAJOR, 99])["action"] == "serve"
    assert protocol.negotiate([protocol.PROTOCOL_MAJOR + 1, 0])["action"] == "drain_restart"
    assert protocol.negotiate([0, 0])["action"] == "passthrough"
    assert protocol.negotiate("garbage")["action"] == "passthrough"


def test_old_shim_gets_passthrough(daemon, monkeypatch):
    home, _ = daemon
    monkeypatch.setattr(protocol, "hello_params", lambda comp: {"protocol": [0, 1], "version": "0.0.0"})
    with pytest.raises(DaemonUnavailable) as exc:
        DaemonClient(home).request("ping")
    assert exc.value.action == "passthrough"


def test_newer_shim_drains_daemon(daemon, monkeypatch):
    home, proc = daemon
    monkeypatch.setattr(protocol, "hello_params",
                        lambda comp: {"protocol": [protocol.PROTOCOL_MAJOR + 1, 0], "version": "9.0.0"})
    with pytest.raises(DaemonUnavailable) as exc:
        DaemonClient(home).request("ping")
    assert exc.value.action == "drain_restart"
    assert proc.wait(timeout=10) == 0  # daemon drained and exited cleanly


@pytest.mark.skipif(not IS_WIN, reason="named pipe DACL is Windows-specific")
def test_pipe_dacl_is_current_user_only(daemon):
    import _winapi

    from arbiter_agent.winsec import current_user_sid, handle_dacl_sddl

    home, _ = daemon
    _winapi.WaitNamedPipe(home.pipe_address, 1000)
    h = _winapi.CreateFile(home.pipe_address, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, _winapi.NULL,
                           _winapi.OPEN_EXISTING, 0, _winapi.NULL)
    try:
        sddl = handle_dacl_sddl(h)
    finally:
        _winapi.CloseHandle(h)
    sid = current_user_sid()
    assert sddl.startswith("D:P")  # protected: no inherited ACEs
    aces = sddl[3:].strip("()").split(")(")
    owner_ok = aces[0].endswith(sid) or (sid.endswith("-500") and aces[0].endswith(";LA"))  # LA = RID 500 alias
    assert len(aces) == 1 and owner_ok, sddl


@pytest.mark.skipif(IS_WIN, reason="POSIX socket permissions")
def test_socket_permissions_user_only(daemon):
    import os
    import stat

    home, _ = daemon
    mode = stat.S_IMODE(os.stat(home.pipe_address).st_mode)
    assert mode & 0o077 == 0


def _post(home, path, body, token=None):
    port = read_record(home)["http"]["port"]
    tok = token if token is not None else read_token(home.hook_token_file).decode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", HOOK_TOKEN_HEADER: tok})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def test_http_hook_requires_token_and_ingests(daemon):
    home, _ = daemon
    assert _post(home, "/hook/claude_code/Stop", {"hook_event_name": "Stop"}, token="nope")[0] == 401
    assert _post(home, "/hook/claude_code/Stop", {"hook_event_name": "Stop"}, token="")[0] == 401
    assert _post(home, "/nothere", {})[0] == 404
    code, body = _post(home, "/hook/claude_code/Stop", {"hook_event_name": "Stop", "session_id": "c1",
                                                         "prompt_id": "p1", "stop_hook_active": False,
                                                         "last_assistant_message": "Done.", "cwd": "C:/x"})
    assert code == 200 and body == {}
    with DaemonClient(home) as c:
        st = c.request("status")
    assert st["events"] == 1 and st["http_stats"]["unauthorized"] == 2


def test_http_health_is_minimal(daemon):
    home, _ = daemon
    port = read_record(home)["http"]["port"]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
        assert json.loads(r.read()) == {"ok": True}

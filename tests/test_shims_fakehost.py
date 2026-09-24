import json
import os
import subprocess
import sys
import time

import pytest

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient
from tests.conftest import cli_argv, spawn_daemon, wait_running


def status(home):
    with DaemonClient(home) as c:
        return c.request("status")


NO_AUTOSTART = {**os.environ, "ARBITER_NO_AUTOSTART": "1"}


def mcp_session(home):
    import tempfile

    argv = cli_argv(home) + ["mcp"]
    err = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                         text=True, encoding="utf-8", env=NO_AUTOSTART)
    p.errfile = err  # type: ignore[attr-defined]
    return p


def rpc(p, i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    if not line.strip():  # the shim died or printed nothing: show why (CI has no log access)
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            pass
        p.errfile.seek(0)
        raise AssertionError(f"mcp shim gave no reply to {method} (rc={p.poll()}): {p.errfile.read()[-1500:]!r}")
    return json.loads(line)


def test_mcp_protocol_basics(daemon):
    home, _ = daemon
    p = mcp_session(home)
    try:
        init = rpc(p, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "t", "version": "0"}})["result"]
        assert init["protocolVersion"] == "2025-06-18" and init["capabilities"]["tools"] is not None
        old = rpc(p, 2, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        assert old["protocolVersion"] == "2025-06-18"  # unknown version -> latest supported
        names = [t["name"] for t in rpc(p, 3, "tools/list")["result"]["tools"]]
        assert names == ["arbiter_hook", "arbiter_status", "arbiter_ping", "arbiter_contract_propose",
                         "arbiter_contracts", "arbiter_scope_change", "arbiter_finish_check", "arbiter_verify",
                         "arbiter_search", "arbiter_symbol", "arbiter_related", "arbiter_controls"]
        st = rpc(p, 4, "tools/call", {"name": "arbiter_status", "arguments": {}})["result"]
        assert json.loads(st["content"][0]["text"])["pid"]
        assert rpc(p, 5, "tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
        assert rpc(p, 6, "bogus/method")["error"]["code"] == -32601
        assert rpc(p, 7, "ping") == {"jsonrpc": "2.0", "id": 7, "result": {}}
    finally:
        p.stdin.close()
        p.wait(5)


@pytest.mark.parametrize("client", ["codex", "claude_code"])
@pytest.mark.parametrize("transport", ["mcp", "http", "command"])
def test_fake_host_all_transports(daemon, tmp_path, client, transport):
    home, _ = daemon
    with FakeHost(home, client=client, transport=transport, cwd=str(tmp_path), transcript_dir=tmp_path) as h:
        h.session_start()
        h.prompt("please fix the parser")
        h.tool("pytest -q", "3 passed in 0.10s", 0)
        h.stop("Done. All tests pass.")
        h.session_end()
    assert all(c.ok for c in h.calls), [c.error for c in h.calls]
    assert all(c.response == {} for c in h.calls)  # M1 makes no decisions: pass-through
    st = status(home)
    assert st["events"] == len(h.calls) == 6
    assert st["ingest"].get("stored") == 6


def test_transcript_watcher_dedupes_against_hooks_and_replays(daemon, tmp_path):
    home, _ = daemon
    with FakeHost(home, client="codex", transport="mcp", cwd=str(tmp_path), transcript_dir=tmp_path) as h:
        h.session_start()
        h.prompt("run tests")
        tid = h.tool("pytest -q", "1 failed", 1)
        h.stop("Tests ran; one failed. Should I fix it?")
    with DaemonClient(home) as c:
        c.request("watch_add", {"client": "codex", "path": str(h.transcript_path), "parser": "fake_v1",
                                "cwd": str(tmp_path)})
        assert c.request("watch_poll")["records"] == 4  # meta, user, tool_result, assistant
        assert c.request("watch_poll")["records"] == 0  # offset saved: nothing re-read
        with open(h.transcript_path, "a", encoding="utf-8") as f:
            f.write('{"id": "partial", "session_id": "x"')  # incomplete line is not consumed
        assert c.request("watch_poll")["records"] == 0
        with open(h.transcript_path, "a", encoding="utf-8") as f:
            f.write(', "type": "note"}\n')
        assert c.request("watch_poll")["records"] == 1
        st = c.request("status")
        assert st["ingest"]["duplicate"] == 1  # transcript tool_result == hook PostToolUse (same tool_use_id)
        assert c.request("replay_check")["equal"] is True
    assert tid.startswith("call_")


def test_hook_cli_fails_open_fast_when_daemon_down(home):
    argv = cli_argv(home) + ["hook", "codex", "Stop"]
    t = time.perf_counter()
    out = subprocess.run(argv, input='{"hook_event_name":"Stop","session_id":"s"}', capture_output=True, text=True,
                         timeout=20, env=NO_AUTOSTART)
    dt = time.perf_counter() - t
    assert out.returncode == 0 and out.stdout == ""
    assert dt < 5.0
    assert (home.logs / "failopen.log").exists()


def test_hook_cli_garbage_input_fails_open(home):
    argv = cli_argv(home) + ["hook", "codex"]
    out = subprocess.run(argv, input="{not json", capture_output=True, text=True, timeout=20, env=NO_AUTOSTART)
    assert out.returncode == 0 and out.stdout == ""


def test_mcp_hook_fails_open_when_daemon_down(home):
    p = mcp_session(home)
    try:
        rpc(p, 1, "initialize", {"protocolVersion": "2025-06-18"})
        t = time.perf_counter()
        r = rpc(p, 2, "tools/call", {"name": "arbiter_hook", "arguments": {"client": "codex",
                                                                          "hook_event_name": "Stop"}})
        dt = time.perf_counter() - t
        assert r["result"] == {"content": [{"type": "text", "text": ""}], "isError": False}
        assert dt < 2.0
    finally:
        p.stdin.close()
        p.wait(5)
        lifecycle.stop(home, timeout=5)  # the shim may have launched a daemon; clean it up


def test_daemon_crash_never_blocks_fake_host(home, tmp_path):
    proc = spawn_daemon(home)
    assert wait_running(home)
    pid = status(home)["pid"]
    host = FakeHost(home, client="codex", transport="mcp", cwd=str(tmp_path), transcript_dir=tmp_path,
                    autostart=False)
    http_host = FakeHost(home, client="claude_code", transport="http", cwd=str(tmp_path), transcript_dir=tmp_path,
                         autostart=False)
    host.prompt("before crash")
    subprocess.run(["taskkill", "/PID", str(pid), "/F"] if sys.platform == "win32" else ["kill", "-9", str(pid)],
                   capture_output=True)
    proc.wait(10)
    try:
        for h in (host, http_host):
            for _ in range(3):
                t = time.perf_counter()
                call = h.prompt("after crash")
                assert (time.perf_counter() - t) < 3.0, "hook blocked the host"
                assert call.response == {}
    finally:
        host.close()
        lifecycle.stop(home, timeout=5)

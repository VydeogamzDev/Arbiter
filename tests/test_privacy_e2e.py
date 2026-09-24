import subprocess

import pytest

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient
from arbiter_agent.state.store import connect

SECRET = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0" * 2
PROMPT_SECRET = "sk-ant-api03-" + "Sup3rS3cr3tV4lu3" * 3


def all_bytes(home) -> bytes:
    blob = b""
    for p in list(home.data.rglob("*")) + list(home.logs.rglob("*")):
        if p.is_file():
            blob += p.read_bytes()
    return blob


@pytest.mark.parametrize("transport", ["mcp", "http", "command"])
def test_secrets_never_reach_disk(daemon, tmp_path, transport):
    home, _ = daemon
    with FakeHost(home, client="codex", transport=transport, cwd=str(tmp_path), transcript_dir=tmp_path / "tr",
                  autostart=False) as h:
        h.prompt(f"use this key {PROMPT_SECRET} please")
        h.tool(f"curl -H 'Authorization: token {SECRET}' https://api.example.com", f"echoed {SECRET}", 0)
        h.stop("Done")
    with DaemonClient(home) as c:
        c.request("watch_add", {"client": "codex", "path": str(h.transcript_path), "parser": "fake_v1",
                                "cwd": str(tmp_path)})
        c.request("watch_poll")
        assert c.request("status")["events"] >= 4
    lifecycle.stop(home)  # flush WAL/logs to disk before scanning
    data = all_bytes(home)
    assert SECRET.encode() not in data
    assert PROMPT_SECRET.encode() not in data
    assert b"[REDACTED:" in data


def test_excluded_project_never_stored(daemon, tmp_path):
    home, _ = daemon
    private = tmp_path / "private-client-work"
    private.mkdir()
    argv = lifecycle.daemon_argv(home)[:-2] + ["exclude", str(private)]
    assert subprocess.run(argv, capture_output=True, text=True).returncode == 0
    marker = "UNIQUE-MARKER-7f3a9c"
    with FakeHost(home, client="claude_code", transport="http", cwd=str(private), transcript_dir=tmp_path,
                  autostart=False) as h:
        h.session_start()
        h.prompt(f"confidential {marker}")
        h.tool("dir", marker, 0)
        h.stop(f"done {marker}")
    with DaemonClient(home) as c:
        c.request("watch_add", {"client": "claude_code", "path": str(h.transcript_path), "parser": "fake_v1",
                                "cwd": str(private)})
        assert c.request("watch_poll")["records"] == 0  # scope checked before reading the file
        st = c.request("status")
    assert st["events"] == 0 and st["sessions"] == 0
    assert st["scope_skips"]["cli_exclude"] == len(h.calls)
    lifecycle.stop(home)
    assert marker.encode() not in all_bytes(home).replace(str(h.transcript_path).encode(), b"")
    conn = connect(home.db, readonly=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM blob").fetchone()[0] == 0
    finally:
        conn.close()


def test_arbiterignore_excludes_subtree(daemon, tmp_path):
    home, _ = daemon
    repo = tmp_path / "repo"
    (repo / "pkg" / "mod").mkdir(parents=True)
    (repo / ".arbiterignore").write_text("")
    with FakeHost(home, client="codex", transport="mcp", cwd=str(repo / "pkg" / "mod"), autostart=False) as h:
        h.prompt("hello")
    with DaemonClient(home) as c:
        st = c.request("status")
    assert st["events"] == 0 and st["scope_skips"] == {"arbiterignore": 1}

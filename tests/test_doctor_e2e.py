"""M2 exit: after setup, doctor shows the verified tier set per client (sandboxed client homes)."""

import datetime as dt
import io
import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

from arbiter_agent.clients.client_env import current_env
from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient
from arbiter_agent.setup.doctor import gather, render
from arbiter_agent.setup.setup import SetupOptions, run_setup
from tests.conftest import spawn_daemon, wait_running

FIX = Path(__file__).parent / "fixtures"


def shim_session(command, client_name):
    p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": client_name, "version": "9.9"}}}) + "\n")
    p.stdin.flush()
    p.stdout.readline()
    return p


@pytest.fixture
def sandbox(tmp_path, monkeypatch, home):
    env_vars = {"CODEX_HOME": str(tmp_path / "codex_home"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude_dir"),
                "ARBITER_CLIENT_HOME": str(tmp_path)}
    for k, v in env_vars.items():
        Path(v).mkdir(exist_ok=True)
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    assert run_setup(home, SetupOptions(yes=True, start_daemon=False, clients=["codex", "claude_code"], out=out)) == 0
    proc = spawn_daemon(home, env_vars)
    assert wait_running(home)
    yield home, current_env(), tmp_path
    lifecycle.stop(home)
    proc.wait(10)


def test_doctor_reports_verified_tiers(sandbox):
    home, env, tmp = sandbox
    work = tmp / "work"
    work.mkdir()
    # T1: each client launches the MCP server exactly as configured
    codex_cfg = tomllib.loads(env.codex_config.read_text())["mcp_servers"]["arbiter"]
    claude_cfg = json.loads(env.claude_global_json.read_text())["mcpServers"]["arbiter"]
    shims = [shim_session([codex_cfg["command"], *codex_cfg["args"]], "codex_mcp_client"),
             shim_session([claude_cfg["command"], *claude_cfg["args"]], "claude-code")]
    # T2: hooks over each client's transport
    with FakeHost(home, client="codex", transport="mcp", cwd=str(work), autostart=False) as h:
        h.prompt("hello")
    with FakeHost(home, client="claude_code", transport="http", cwd=str(work), autostart=False) as h:
        h.prompt("hello")
    # T3: transcripts appear where each client writes them
    day = dt.datetime.now()
    rollout = env.codex_sessions / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "rollout-2026-01-01T00-00-00-x.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text((FIX / "codex_rollout.jsonl").read_text().replace("C:/work/proj", str(work).replace("\\", "/")))
    proj = env.claude_projects / "C--work-proj"
    proj.mkdir(parents=True)
    (proj / "csess.jsonl").write_text((FIX / "claude_session.jsonl").read_text()
                                      .replace("C:/work/proj", str(work).replace("\\", "/")))
    import time

    with DaemonClient(home) as c:
        assert c.request("watch_poll", timeout=10)["records"] > 0
        deadline = time.monotonic() + 10  # shims report their client asynchronously
        while True:
            probe = {x["client"]: x for x in c.request("probe", timeout=30)["clients"]}
            if all("T1" in probe[k]["verified"] for k in ("codex", "claude_code")) or time.monotonic() > deadline:
                break
            time.sleep(0.3)
    for s in shims:
        s.stdin.close()
        s.wait(5)
    assert probe["codex"]["verified"] == ["T1", "T2", "T3"], probe["codex"]
    assert probe["claude_code"]["verified"] == ["T1", "T2", "T3"], probe["claude_code"]
    assert any("trust" in n for n in probe["codex"]["notes"])  # hooks not trusted in the real Codex sense

    report = gather(home)
    text = render(report)
    assert "Codex (desktop + CLI): installed  verified tiers: T1 T2 T3" in text
    assert "Claude Code: installed  verified tiers: T1 T2 T3" in text
    by = {c["client"]: c for c in report["clients"]}
    assert by["codex"]["mcp_round_trip"]["ok"] and by["claude_code"]["http_round_trip"]["ok"]


def test_setup_cli_end_to_end(tmp_path, home):
    """`arbiter setup --yes` from the command line, then doctor, then uninstall."""
    env = dict(os.environ, CODEX_HOME=str(tmp_path / "c"), CLAUDE_CONFIG_DIR=str(tmp_path / "d"),
               ARBITER_CLIENT_HOME=str(tmp_path), ARBITER_NO_AUTOSTART="1")
    (tmp_path / "c").mkdir()
    (tmp_path / "d").mkdir()
    base = lifecycle.daemon_argv(home)[:-2]
    r = subprocess.run(base + ["setup", "--yes", "--no-start"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Codex" in r.stdout and "Claude Code" in r.stdout
    r = subprocess.run(base + ["doctor", "--quick"], capture_output=True, text=True, env=env, timeout=60)
    assert "verified tiers" in r.stdout and "configured: T1 T2" in r.stdout
    r = subprocess.run(base + ["uninstall"], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "deleted" in r.stdout
    assert not (tmp_path / "c" / "config.toml").exists()

"""Shell evidence from real Claude Code runs (benchmark findings, 2026-09-25):

- inline verification snippets (``python -c``) aren't file writes unless their code writes, so
  they don't make earlier test runs stale;
- redirect-looking characters inside quoted code aren't redirects;
- a Claude Code PostToolUse means the tool succeeded (failures go to PostToolUseFailure), so
  exit-status contracts can pass and fail on hooks alone.
"""

from __future__ import annotations

import pytest

from arbiter_agent.clients.fake_host.host import FakeHost
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient
from arbiter_agent.state.facts import shell_writes
from tests.conftest import spawn_daemon, wait_running


@pytest.mark.parametrize(("cmd", "writes"), [
    ('python -c "import time; from fib import fib; fib(10000); assert time.perf_counter() < 1e9"',
     False),
    ('python -c "from greet import main; main([\'Ada\', \'--verbose\'])"', False),
    ('python -c "x = 3; print(x > 0)"', False),
    ("python -B -c \"import sys; sys.setrecursionlimit(100)\"", False),
    ('python -c "open(\'out.txt\', \'w\').write(\'x\')"', True),
    ('python -c "import pathlib; pathlib.Path(\'a\').write_text(\'b\')"', True),
    ('python -c "import os; os.remove(\'a\')"', True),
    ('node -e "require(\'fs\').writeFileSync(\'a\', \'b\')"', True),
    ("echo hi > out.txt", True),
    ('bash -c "echo hi > out.txt"', True),
    ("rm -rf build", True),
    ("git checkout -- src", True),
    ("python -m pytest -q", False),
    ("git status --short", False),
])
def test_shell_writes(cmd, writes):
    assert shell_writes(cmd) is writes


def test_claude_code_exit_contracts_and_snippets_on_hooks(home, tmp_path):
    (tmp_path / "fib.py").write_text("def fib(n):\n    return n\n")
    proc = spawn_daemon(home)
    assert wait_running(home)
    try:
        with FakeHost(home, client="claude_code", transport="http", cwd=str(tmp_path), autostart=False) as h:
            h.session_start()
            h.prompt("Make fib fast and keep the tests green.")
            with DaemonClient(home) as c:
                c.request("engine_drain", {"timeout": 10}, timeout=15)
                sid = c.request("sessions", {}, timeout=5)["sessions"][0]["session_id"]
                c.request("contract_propose", {"session_id": sid, "contracts": [
                    {"text": "fast", "quotes": ["Make fib fast"],
                     "recipe": {"type": "command_exit", "command": 'python -c "from fib import fib; fib(10)"',
                                "exit_code": 0}},
                    {"text": "green", "quotes": ["keep the tests green"],
                     "recipe": {"type": "test_command", "command": "pytest"}}]}, timeout=10)
            h.tool("pytest", "1 passed in 0.01s", 0)
            h.tool('python -c "from fib import fib; fib(10)"', "", 0)   # a verification snippet after the tests
            h.stop("Done. All tests pass.")
            with DaemonClient(home) as c:
                c.request("engine_drain", {"timeout": 10}, timeout=15)
                st = c.request("session_status", {"session_id": sid}, timeout=10)
        assert st["last_verdict"] == "verified", st
    finally:
        lifecycle.stop(home)
        proc.wait(10)


@pytest.mark.parametrize(("cmd", "fp"), [
    ('python -m pytest -q; python cli.py "  Hello,   World!! "', "pytest -q"),
    ("cd app && python -m pytest -q", "pytest -q"),
    ("python -m pytest -q\npython greet.py Ada --verbose", "pytest -q"),
    ("python cli.py a; python cli.py b", "python cli.py b"),
])
def test_compound_commands_keep_the_runner_segment(cmd, fp):
    from arbiter_agent.telemetry.runner_parsers.base import fingerprint

    assert fingerprint(cmd) == fp

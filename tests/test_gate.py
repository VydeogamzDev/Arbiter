"""M4: claim detection, gate modes and wording, breakers (fault injection), status injection,
verify runner, and the agent-facing MCP tools against a real daemon."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.completion import gate
from arbiter_agent.completion.breakers import Breaker, Breakers, LatencyBreaker
from arbiter_agent.completion.claim_detection import classify
from arbiter_agent.completion.verify_runner import VerifyCommand, VerifyConfigError, load, result_fact, run_one
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.client import DaemonClient, DaemonError
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.ui.status import approx_tokens, injection
from tests.conftest import spawn_daemon, wait_running

CALC = {"calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def _session(h: Harness, *, verified: bool) -> None:
    for p, t in CALC.items():
        h.write(p, t)
    h.prompt("Fix the addition bug in calc.py.")
    h.baseline()
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])
    if verified:
        h.edit("calc.py", "def add(a, b):\n    return b + a\n")
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)


# ------------------------------------------------------------------ claims
@pytest.mark.parametrize(("msg", "claim"), [
    ("I've fixed the bug and all tests pass.", "claim"),
    ("Done - here's a summary of the changes.", "claim"),
    ("Should I also update the docs?", "not_claim"),
    ("I fixed part of it. Would you like me to continue?", "not_claim"),
    ("I've fixed the parser, but the CLI still fails.", "uncertain"),
    ("Here is how the scheduler works: it polls.", "not_claim"),
    ("", "not_claim"),
    ("The suite passes now (2 passed). I haven't committed the change.", "claim"),
    ("The bug was in `local_date`. I changed `dt - x` to `dt + x`.", "claim"),
    ("I changed the parser, but 3 tests still fail.", "uncertain"),
    ("The suite takes 40 seconds to run.", "not_claim"),
])
def test_claim_classification(msg, claim):
    assert classify(msg).claim == claim


def test_finish_check_is_strongest_signal():
    assert classify("Should I continue?", finish_check_called=True).gated


# ------------------------------------------------------------------ gate
def test_annotate_never_blocks_and_records_ledger():
    with Harness() as h:
        _session(h, verified=False)
        assert h.stop("Done. All tests pass.") == {}
        from arbiter_agent.state.store import connect

        rc = connect(h.db, readonly=True)
        try:
            row = rc.execute("SELECT verdict, mode, blocked, trigger FROM finish_ledger").fetchone()
        finally:
            rc.close()
        assert tuple(row) == ("unverified", "annotate", 0, "stop_hook")


def test_block_mode_wording_and_bound():
    with Harness(config={"completion": {"gate_mode": "block", "max_stop_blocks_per_epoch": 2}}) as h:
        _session(h, verified=False)
        r1 = h.stop("Done.")
        assert r1["decision"] == "block" and gate.check_wording(r1["reason"]) == []
        assert r1["reason"].startswith("[Arbiter]") and "current turn" in r1["reason"]
        assert h.stop("The task is complete.")["decision"] == "block"
        assert h.stop("All done.") == {}                      # bound reached: allowed, unverified
        assert h.stop("Which option do you prefer?") == {}    # questions are never gated
        h.prompt("New task: add docs to calc.py.")            # new epoch resets the block budget
        assert h.stop("Docs added. Done.")["decision"] == "block"


def test_block_mode_lets_verified_stop_through():
    with Harness(config={"completion": {"gate_mode": "block"}}) as h:
        _session(h, verified=True)
        assert h.stop("I've fixed it; all tests pass.") == {}


def test_session_gate_mode_override():
    with Harness() as h:
        _session(h, verified=False)
        h.engine.set_gate_mode(h.sid, "block")
        assert h.stop("Done.").get("decision") == "block"
        h.engine.set_gate_mode(h.sid, "default")
        assert h.stop("All done.") == {}


def test_completion_gate_flag_off_disables_gate():
    with Harness(config={"completion": {"gate_mode": "block"}, "features": {"completion_gate": False}}) as h:
        _session(h, verified=False)
        assert h.stop("Done.") == {}


def test_wording_checker_catches_standing_instructions():
    assert gate.check_wording("Always run tests before finishing.")
    assert "not scoped to the current turn" in gate.check_wording("[Arbiter] missing tests")


# ------------------------------------------------------------------ breakers (fault injection: 100% trip)
def test_gate_error_breaker_trips_and_fails_open(monkeypatch):
    with Harness(config={"completion": {"gate_mode": "block"}}) as h:
        _session(h, verified=False)

        def boom(*a, **k):
            raise RuntimeError("injected fault")

        monkeypatch.setattr(h.engine, "evaluate", boom)
        for i in range(3):
            assert h.stop(f"Done ({i}).") == {}           # errors never block
        assert h.engine.breakers.gate_errors.is_open()
        monkeypatch.undo()
        assert h.stop("Done, really.") == {}              # open breaker: no evaluation, no block, no PASS
        from arbiter_agent.state.store import connect

        rc = connect(h.db, readonly=True)
        try:
            verdicts = [r[0] for r in rc.execute("SELECT verdict FROM finish_ledger")]
        finally:
            rc.close()
        assert verdicts and set(verdicts) == {"unverified"}


def test_latency_breaker_trips_on_slow_gate():
    b = LatencyBreaker("hook_latency", budget_ms=300, samples=50, min_samples=20)
    for _ in range(25):
        b.record_latency(450)
    assert b.is_open() and b.trips == 1
    bs = Breakers()
    bs.hook_latency = b
    assert bs.gate_open() == "hook latency breaker open"


def test_breaker_cooldown_half_open():
    b = Breaker("x", threshold=2, cooldown_s=10)
    b.record_failure(now=100)
    b.record_failure(now=101)
    assert b.is_open(now=105) and not b.is_open(now=112)
    b.record_success(now=112)
    assert b.opened_at is None


def test_parser_breaker_turns_runner_results_unknown():
    with Harness() as h:
        _session(h, verified=False)
        for _ in range(3):   # a recognized runner command whose output no parser recognizes
            h.shell("pytest", "garbled output", exit_code=0)
        h.drain()
        assert h.engine.breakers.parser_open("pytest")
        h.edit("calc.py", "def add(a, b):\n    return b + a\n")
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)
        assert h.ledger().contracts[0][1].status == "unknown"


# ------------------------------------------------------------------ status injection
def test_status_injection_only_on_change_and_capped():
    state = {"goal_epoch": 2, "counts": {"pass": 1, "fail": 0, "unknown": 1}}
    text, h1 = injection(state, last_hash=None, max_tokens=300)
    assert text and text.startswith("[Arbiter]") and approx_tokens(text) <= 300
    assert injection(state, last_hash=h1, max_tokens=300)[0] is None
    short, _ = injection(state, last_hash=None, max_tokens=10)
    assert short is not None and approx_tokens(short) <= 11


def test_status_injection_default_off_and_on_when_enabled():
    with Harness() as h:
        assert h.prompt("Fix the addition bug in calc.py.") == {}
    with Harness(config={"ui": {"inject_status": True}}) as h:
        r = h.prompt("Fix the addition bug in calc.py.")
        ctx = r["hookSpecificOutput"]["additionalContext"]
        assert r["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit" and "goal epoch 1" in ctx
        assert h.prompt("yes, continue") == {}            # unchanged -> nothing injected


# ------------------------------------------------------------------ verify runner
def test_verify_config_validation(tmp_path):
    p = tmp_path / "verify.yaml"
    p.write_text("commands:\n  - run: pytest -q\n    kind: test\n  - run: mypy src\n    kind: typecheck\n")
    cmds = load(p)
    assert [c.kind for c in cmds] == ["test", "typecheck"]
    p.write_text("commands:\n  - run: x\n    kind: nope\n")
    with pytest.raises(VerifyConfigError):
        load(p)


def test_verify_run_with_junit_report(tmp_path):
    (tmp_path / "t.py").write_text("import pathlib\npathlib.Path('r.xml').write_text("
                                   "\"<testsuite tests='2' failures='0' errors='0' skipped='0'/>\")\n")
    cmd = VerifyCommand("unit", f'"{sys.executable}" t.py', "test", 60, report="r.xml")
    r = run_one(cmd, tmp_path)
    assert r.exit_code == 0 and r.status == "pass" and r.report["total"] == 2
    fact = result_fact(r)
    assert fact["kind"] == "test_run" and fact["status"] == "pass"


def test_verify_test_without_recognized_output_is_unknown(tmp_path):
    (tmp_path / "p.py").write_text("print(1)\n")
    r = run_one(VerifyCommand("x", f'"{sys.executable}" p.py', "test", 60), tmp_path)
    assert r.exit_code == 0 and r.status == "unknown"


# ------------------------------------------------------------------ MCP tools against a real daemon
def test_mcp_task_tools_bind_to_the_hook_session(home, tmp_path):
    for p, t in CALC.items():
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(t)
    proc = spawn_daemon(home)
    assert wait_running(home)
    try:
        with FakeHost(home, client="codex", transport="mcp", cwd=str(tmp_path), autostart=False) as h:
            h.session_start()
            h.prompt("Fix the addition bug in calc.py. Don't change `tests/test_calc.py`.")
            text, err = h.mcp_tool("arbiter_contract_propose", {"contracts": [
                {"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                 "recipe": {"type": "test_command", "command": "pytest"}},
                {"text": "made up", "quotes": ["please rewrite everything"], "recipe": {"type": "manual"}}]})
            assert not err and "accepted C2" in text and "rejected" in text
            text, _ = h.mcp_tool("arbiter_contracts")
            assert "C1" in text and "C2" in text
            text, _ = h.mcp_tool("arbiter_finish_check", {"claims": ["tests pass"]})
            assert text.startswith("Not verified yet") and "C2" in text
            h.tool("pytest", "1 passed in 0.01s", 0)
            with DaemonClient(home) as c:
                c.request("engine_drain", {"timeout": 10}, timeout=15)
            text, _ = h.mcp_tool("arbiter_verify")
            assert "not run" in text and "verify.yaml" in text
            with DaemonClient(home) as c:
                with pytest.raises(DaemonError, match="interactive"):
                    c.request("verify_trust", {"path": "x", "sha256": "y", "channel": "cli"}, timeout=5)
                sessions = c.request("sessions", {}, timeout=5)["sessions"]
            assert sessions and sessions[0]["contracts"] >= 2
    finally:
        lifecycle.stop(home)
        proc.wait(10)


def test_mcp_tools_bind_by_cwd_for_http_hook_clients(home, tmp_path):
    proc = spawn_daemon(home)
    assert wait_running(home)
    shim = None
    try:
        with FakeHost(home, client="claude_code", transport="http", cwd=str(tmp_path), autostart=False) as h:
            h.prompt("Add a --verbose flag to the CLI.")
            shim = subprocess.Popen([sys.executable, "-m", "arbiter_agent", "--home", str(home.root), "mcp"],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, cwd=str(tmp_path), env={**h.env})

            def rpc(i, method, params):
                shim.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
                shim.stdin.flush()
                return json.loads(shim.stdout.readline())

            rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "claude-code", "version": "2"}})
            time.sleep(0.2)
            out = rpc(2, "tools/call", {"name": "arbiter_contract_propose", "arguments": {"contracts": [
                {"text": "verbose flag", "quotes": ["Add a --verbose flag to the CLI"],
                 "recipe": {"type": "file_contains", "path": "cli.py", "text": "--verbose"}}]}})
            text = out["result"]["content"][0]["text"]
            assert "accepted C1" in text, text
    finally:
        if shim:
            shim.stdin.close()
            shim.wait(10)
        lifecycle.stop(home)
        proc.wait(10)


def _p95(xs):
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]


@pytest.mark.parametrize(("client", "transport"), [("codex", "mcp"), ("claude_code", "http")])
def test_gating_hook_p95_with_gate_logic_real_daemon(home, tmp_path, client, transport):
    """Spec 20.17: gating hook p95 <= 300 ms with the gate evaluating a real session."""
    for p, t in CALC.items():
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text(t)
    proc = spawn_daemon(home)
    assert wait_running(home)
    try:
        with FakeHost(home, client=client, transport=transport, cwd=str(tmp_path), autostart=False) as h:
            h.session_start()
            h.prompt("Fix the addition bug in calc.py. Don't change `tests/test_calc.py`.")
            with DaemonClient(home) as c:
                c.request("engine_drain", {"timeout": 10}, timeout=15)
                sid = c.request("sessions", {}, timeout=5)["sessions"][0]["session_id"]
                c.request("contract_propose", {"session_id": sid, "contracts": [
                    {"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                     "recipe": {"type": "test_command", "command": "pytest"}}]}, timeout=10)
            h.tool("pytest", "1 passed in 0.01s", 0)
            lat = [h.stop(f"Done ({i}). All tests pass.").latency_ms for i in range(40)]
            assert all(c.ok for c in h.calls)
            with DaemonClient(home) as c:
                n = c.request("session_status", {"session_id": sid}, timeout=10)
        assert n["last_verdict"] in ("verified", "unverified")
        assert _p95(lat) <= 300, (sorted(lat)[-5:], client)
        print(f"\n{client}/{transport}: gating p50 {sorted(lat)[len(lat) // 2]:.1f} ms, p95 {_p95(lat):.1f} ms")
    finally:
        lifecycle.stop(home)
        proc.wait(10)

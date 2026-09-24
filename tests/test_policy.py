"""M8: policy core: fail modes, breakers (recovery window, latching, flag forcing, persistence),
cascade, conflict priority, authority, budgets, shedding, controls, and the fault-injection gate."""

from __future__ import annotations

import time
from typing import Any

import pytest

from arbiter_agent.daemon.client import DaemonClient, DaemonError
from arbiter_agent.eval import fault_injection
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.flags import REGISTRY, FeatureFlags
from arbiter_agent.policy import authority, cascade
from arbiter_agent.policy.authority import Actor
from arbiter_agent.policy.budgets import Budgets
from arbiter_agent.policy.circuit_breaker import SPECS, Breaker, BreakerBoard, LatencyBreaker
from arbiter_agent.policy.conflict_resolution import resolve
from arbiter_agent.policy.constraints import Constraint, Level
from arbiter_agent.policy.fail_modes import MATRIX, Mode
from arbiter_agent.policy.overload import LoadShedder
from arbiter_agent.semif.types import Judgment
from arbiter_agent.ui.overrides import Overrides, load_breakers, persist_breakers


# ------------------------------------------------------------------ fail modes
def test_fail_mode_matrix_covers_every_breaker_and_spec_rows():
    assert all(s.component in MATRIX for s in SPECS.values())
    assert MATRIX["completion_gate"].mode == Mode.CONSERVATIVE
    assert MATRIX["client_adapters"].mode == Mode.OPEN
    assert MATRIX["speculation"].mode == Mode.CLOSED and MATRIX["branch_isolation"].mode == Mode.CLOSED
    assert MATRIX["semif"].mode == Mode.ABSTAIN
    assert all(f in REGISTRY for s in SPECS.values() for f in s.flags)


# ------------------------------------------------------------------ breakers
def test_recovery_window_needs_consecutive_clean_outcomes():
    b = Breaker("x", threshold=2, cooldown_s=10, recovery_successes=3)
    b.record_failure(now=100)
    b.record_failure(now=101)
    assert b.state(now=105) == "open" and b.state(now=112) == "half_open"
    b.record_success(now=112)
    b.record_success(now=113)
    assert b.opened_at is not None                      # two clean outcomes aren't enough
    b.record_failure("trial failed", now=114)           # a failed trial reopens immediately
    assert b.state(now=115) == "open" and b.trips == 2
    for t in (125, 126, 127):
        b.record_success(now=t)
    assert b.state(now=128) == "closed"


def test_latched_breaker_only_manual_reset():
    b = Breaker("false_complete:s", threshold=1, cooldown_s=1, latched=True)
    b.record_failure("incident", now=100)
    b.record_success(now=10_000)
    assert b.state(now=10_000) == "open"
    assert b.reset() and b.state() == "closed"


def test_board_forces_flags_off_and_tick_allows_trials():
    flags = FeatureFlags()
    events: list[tuple[str, str]] = []
    board = BreakerBoard(flags=flags, on_event=lambda e, b, r: events.append((e, b.name)),
                         overrides={"retrieval": {"cooldown_s": 0.05, "recovery_successes": 1}})
    for _ in range(SPECS["retrieval"].threshold):
        board.failure("retrieval", "index broke")
    assert not flags.enabled("repo_index") and flags.snapshot()["repo_index"]["forced_off"]
    time.sleep(0.08)
    board.tick()
    assert flags.enabled("repo_index")                  # half-open: trial work allowed
    board.failure("retrieval", "still broken")
    assert not flags.enabled("repo_index")              # failed trial: forced off again
    time.sleep(0.08)
    board.tick()
    board.success("retrieval")
    assert flags.enabled("repo_index") and not board.is_open("retrieval")
    assert [e for e, _ in events] == ["trip", "trip", "close"]


def test_board_disabled_never_trips():
    board = BreakerBoard(enabled=False)
    for _ in range(50):
        board.failure("client", "x", key="cursor")
    assert not board.is_open("client", "cursor")


def test_breaker_state_persists_across_restart(tmp_path):
    with Harness() as h:
        flags = FeatureFlags()
        board = BreakerBoard(flags=flags)
        board.failure("stale_result", "consumed a stale result")
        board.failure("false_complete", "C1 contradicted", key="claude_code:s1")
        h.writer.run(lambda c: persist_breakers(c, board))
        flags2 = FeatureFlags({"speculation": True})
        board2 = BreakerBoard(flags=flags2)
        board2.restore(load_breakers(h.db))
        assert board2.is_open("false_complete", "claude_code:s1") and board2.is_open("stale_result")
        assert not flags2.enabled("speculation")
        board2.reset("stale_result")
        h.writer.run(lambda c: persist_breakers(c, board2))
        assert {r["name"] for r in load_breakers(h.db)} == {"false_complete:claude_code:s1"}


def test_latency_breaker_reopens_on_slow_trial():
    lb = LatencyBreaker("hook_latency", budget_ms=100, samples=20, min_samples=5, cooldown_s=0.01)
    for _ in range(6):
        lb.record_latency(500)
    assert lb.is_open()
    time.sleep(0.02)
    lb.record_latency(500)
    assert lb.is_open() and lb.trips == 2


# ------------------------------------------------------------------ cascade
def _judgment(choice: str | None, abstain: bool = False) -> Judgment:
    return Judgment("scope_change", choice, {"new_task": 0.9, "continue": 0.1}, 0.8, 0.4, 0.02, 2, abstain,
                    "thin margin" if abstain else "")


def test_cascade_order_and_gates():
    rule = cascade.RuleOutcome("candidate", decisive=False, default="continue")
    routed = frozenset({"scope_change"})
    assert cascade.decide("scope_change", cascade.RuleOutcome("confirm_new", True, "continue")).source == "rule"
    assert cascade.decide("scope_change", rule, _judgment("new_task")).source == "abstain"      # not routed
    d = cascade.decide("scope_change", rule, _judgment("new_task"), routed=routed)
    assert d.source == "semif" and d.action == "new_task"
    assert cascade.decide("scope_change", rule, _judgment(None, True), routed=routed).action == "continue"
    assert cascade.decide("scope_change", rule, _judgment("new_task"), routed=routed,
                          breaker_open=True).source == "abstain"
    hc = cascade.decide("scope_change", rule, _judgment("new_task"), routed=routed, high_consequence=True)
    assert hc.source == "abstain" and "high-consequence" in hc.reason


# ------------------------------------------------------------------ conflict priority
def test_priority_order_and_upstream_is_absolute():
    r = resolve([
        Constraint(Level.REASONING_COST, "effort", "low", "optimizer"),
        Constraint(Level.USER_INSTRUCTION, "effort", "high", "user"),
        Constraint(Level.SEMIF_PREFERENCE, "effort", "medium", "semif"),
    ])
    assert r.value("effort") == "high" and len(r.overridden) == 2
    r2 = resolve([Constraint(Level.UPSTREAM_SAFETY, "network", "deny", "platform"),
                  Constraint(Level.USER_INSTRUCTION, "network", "allow", "user")])
    assert r2.value("network") == "deny" and r2.refused


def test_evidence_floor_unknown_never_becomes_pass():
    r = resolve([Constraint(Level.INTEGRITY, "verdict", "unverified", "evidence"),
                 Constraint(Level.USER_INSTRUCTION, "verdict", "verified", "user")])
    assert r.value("verdict") == "unverified" and r.refused
    # a user may finish anyway: marked unverified-accepted, never verified
    r = resolve([Constraint(Level.INTEGRITY, "verdict", "unverified", "evidence"),
                 Constraint(Level.USER_INSTRUCTION, "verdict", "unverified_accepted", "user")])
    assert r.value("verdict") == "unverified_accepted"
    r = resolve([Constraint(Level.SEMIF_PREFERENCE, "contract_status", "pass", "semif")])
    assert "contract_status" not in r.values and r.refused
    r = resolve([Constraint(Level.INTEGRITY, "contract_status", "fail", "evidence"),
                 Constraint(Level.USER_INSTRUCTION, "contract_status", "waived", "user")])
    assert r.value("contract_status") == "waived"


# ------------------------------------------------------------------ authority
def test_authority_agent_can_only_request_next_turn_bypasses():
    assert authority.actor_for("mcp_shim", "cli_tty") == Actor.AGENT          # channel claims don't matter
    assert authority.actor_for("cli", "cli_tty") == Actor.USER_TTY
    assert authority.check(Actor.AGENT, "next_turn:full_review") is None
    assert authority.check(Actor.AGENT, "module:repo_index", False)
    assert authority.check(Actor.USER_CLI, "module:repo_index", False) is None
    assert "interactive" in (authority.check(Actor.USER_CLI, "module:completion_gate", False) or "")
    assert authority.check(Actor.USER_TTY, "module:completion_gate", False) is None
    assert authority.check(Actor.USER_CLI, "module:completion_gate", True) is None    # re-enabling is fine
    assert authority.check(Actor.USER_CLI, "controller", "off")
    assert authority.check(Actor.USER_CLI, "breaker_reset:integrity")
    assert authority.check(Actor.USER_CLI, "breaker_reset:optimization") is None


# ------------------------------------------------------------------ budgets + shedding
def test_budgets_per_turn():
    b = Budgets({"sensor_calls_per_turn": 2})
    assert b.charge("s", "sensor_calls_per_turn") and b.charge("s", "sensor_calls_per_turn")
    assert not b.charge("s", "sensor_calls_per_turn") and b.charge("t", "sensor_calls_per_turn")
    b.new_turn("s")
    assert b.charge("s", "sensor_calls_per_turn")


def test_shedding_levels_never_shed_integrity():
    fill = {"v": 0}
    lb = LatencyBreaker("hook_latency", budget_ms=100, min_samples=3)
    shed = LoadShedder({"q": lambda: (fill["v"], 10)}, latency=lb)
    assert shed.level() == 0 and shed.allow("background")
    fill["v"] = 6
    assert shed.level() == 1 and not shed.allow("background") and shed.allow("foreground")
    fill["v"] = 9
    assert shed.level() == 2 and not shed.allow("foreground") and shed.allow("integrity")
    fill["v"] = 0
    for _ in range(3):
        lb.record_latency(90)                     # p95 above 80% of budget: shed background
    assert shed.level() == 1
    for _ in range(3):
        lb.record_latency(500)
    assert lb.is_open() and shed.level() == 2


def test_shadow_respects_budget_and_shedding():
    from arbiter_agent.semif.service import SemIfService
    from arbiter_agent.semif.shadow import ShadowHarness

    budgets = Budgets({"sensor_calls_per_turn": 1})
    shadow = ShadowHarness(SemIfService(), writer=None, budgets=budgets)
    shadow.on_decision("s", "stop", {"message": "Done.", "claim": "claim"})
    shadow.on_decision("s", "stop", {"message": "Done again.", "claim": "claim"})
    assert shadow.stats["skipped"] == 1
    busy = LoadShedder({"q": lambda: (10, 10)})
    shadow2 = ShadowHarness(SemIfService(), writer=None, shedder=busy)
    shadow2.on_decision("s", "stop", {"message": "Done.", "claim": "claim"})
    assert shadow2.stats["skipped"] == 1 and busy.stats["shed_background"] == 1


# ------------------------------------------------------------------ controls
def _overrides(h: Harness, audit: list[Any] | None = None) -> Overrides:
    return Overrides(h.db, h.writer, h.engine.flags, h.engine.board,
                     audit=(lambda e, p: audit.append((e, p))) if audit is not None else None).load()


def test_controls_persist_apply_and_check_authority():
    with Harness() as h:
        audit: list[Any] = []
        ov = _overrides(h, audit)
        with pytest.raises(PermissionError):
            ov.set("module:repo_index", "off", actor=Actor.AGENT)
        with pytest.raises(PermissionError, match="interactive"):
            ov.set("module:completion_gate", "off", actor=Actor.USER_CLI)
        ov.set("module:repo_index", "off", actor=Actor.USER_CLI, reason="index too slow")
        assert not h.engine.flags.enabled("repo_index")
        flags2 = FeatureFlags()
        Overrides(h.db, h.writer, flags2).load()
        assert not flags2.enabled("repo_index")                 # survives a restart
        with pytest.raises(PermissionError):
            ov.clear("module:repo_index", actor=Actor.AGENT)
        assert ov.clear("module:repo_index", actor=Actor.USER_CLI) and h.engine.flags.enabled("repo_index")
        with pytest.raises(KeyError):
            ov.set("module:nope", "off", actor=Actor.USER_TTY)
        with pytest.raises(ValueError):
            ov.set("next_turn:full_review", True, actor=Actor.AGENT)           # needs a session scope
        ov.set("next_turn:full_review", True, actor=Actor.AGENT, scope=h.sid)
        assert ov.take_next_turn(h.sid, "next_turn:full_review") and not ov.take_next_turn(h.sid,
                                                                                          "next_turn:full_review")
        assert [e for e, _ in audit].count("internal.control") >= 4


def test_controller_off_returns_baseline_but_still_records():
    with Harness(config={"completion": {"gate_mode": "block"}}) as h:
        fault_injection._session(h, verified=False)
        ov = _overrides(h)
        h.engine.overrides = ov
        ov.set("controller", "off", actor=Actor.USER_TTY, scope=h.sid)
        assert h.stop("Done, all tests pass.") == {}            # would block if the controller were on
        from arbiter_agent.state.store import connect

        rc = connect(h.db, readonly=True)
        try:
            stops = rc.execute("SELECT COUNT(*) FROM event_log WHERE event_type = 'stop'").fetchone()[0]
            verdicts = rc.execute("SELECT COUNT(*) FROM finish_ledger").fetchone()[0]
        finally:
            rc.close()
        assert stops == 1 and verdicts == 0
        ov.clear("controller", actor=Actor.USER_CLI, scope=h.sid)
        assert h.stop("Finished: everything is done.") != {}    # back on: block mode blocks the unverified claim


def test_breaker_reset_authority():
    with Harness() as h:
        ov = _overrides(h)
        h.engine.board.failure("false_complete", "x", key=h.sid)
        name = f"false_complete:{h.sid}"
        with pytest.raises(PermissionError):
            ov.reset_breaker(name, actor=Actor.USER_CLI)
        assert ov.reset_breaker(name, actor=Actor.USER_TTY)["was_open"]
        for _ in range(SPECS["client"].threshold):
            h.engine.board.failure("client", "x", key="cursor")
        assert ov.reset_breaker("client:cursor", actor=Actor.USER_CLI)["was_open"]


# ------------------------------------------------------------------ exit gate
def test_fault_injection_trip_rate_is_100_percent():
    rep = fault_injection.run_all()
    bad = {k: v for k, v in rep["scenarios"].items() if not (v["tripped"] and v["fail_mode_ok"])}
    assert rep["trip_rate"] == 1.0 and rep["passed"] and not rep["missing"], bad


def test_daemon_control_authority_over_ipc(daemon):
    home, _ = daemon
    with DaemonClient(home, component="mcp_shim") as agent:
        with pytest.raises(DaemonError, match="only be changed by the user"):
            agent.request("control_set", {"key": "module:repo_index", "value": "off", "channel": "cli_tty"})
        listing = agent.request("control_list", {})
        assert listing["actor"] == "agent" and listing["controller"] is True
    with DaemonClient(home, component="cli") as user:
        with pytest.raises(DaemonError, match="interactive"):
            user.request("control_set", {"key": "controller", "value": "off", "channel": "cli"})
        res = user.request("control_set", {"key": "module:repo_index", "value": "off", "channel": "cli"})
        assert res["value"] is False
        st = user.request("status")
        assert st["flags"]["repo_index"] is False and st["controller"] is True and st["open_breakers"] == []
        user.request("control_clear", {"key": "module:repo_index", "channel": "cli"})
        assert user.request("status")["flags"]["repo_index"] is True

"""Fault injection (M8 exit): every breaker kind is driven by an injected fault and must trip,
and its component must then behave as the §18.6 fail-mode matrix says. The trip rate must be 100%.

Faults run in-process against the replay harness (no daemon, no network, no model). Breakers
whose module doesn't exist yet (speculation, context scheduling, learned policy, sensor parity)
are driven through the board directly and checked for latching and flag forcing; they're marked
``synthetic``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.flags import FeatureFlags
from arbiter_agent.policy.circuit_breaker import SPECS, BreakerBoard
from arbiter_agent.policy.fail_modes import MATRIX

CALC = {"calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


def _session(h: Harness, *, verified: bool) -> None:
    for p, t in CALC.items():
        h.write(p, t)
    h.prompt("Fix the addition bug in calc.py.")
    h.baseline()
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])
    if verified:
        h.edit("calc.py", "def add(a, b):\n    return a + b\n")
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)


def _verdicts(h: Harness) -> list[str]:
    from arbiter_agent.state.store import connect

    rc = connect(h.db, readonly=True)
    try:
        return [r[0] for r in rc.execute("SELECT verdict FROM finish_ledger ORDER BY id")]
    finally:
        rc.close()


def _block(config: dict[str, Any] | None = None) -> Harness:
    return Harness(config={"completion": {"gate_mode": "block"}, **(config or {})})


# ------------------------------------------------------------------ scenarios: (tripped, behaved, detail)
def gate_errors() -> tuple[bool, bool, str]:
    with _block() as h:
        _session(h, verified=True)
        real = h.engine.evaluate

        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("injected fault")

        h.engine.evaluate = boom  # type: ignore[method-assign]
        responses = [h.stop(f"Done ({i}).") for i in range(3)]
        tripped = h.engine.board.is_open("gate_errors")
        h.engine.evaluate = real  # type: ignore[method-assign]
        after = h.stop("Done, all tests pass.")
        ok = all(r == {} for r in responses) and after == {} and _verdicts(h)[-1] == "unverified"
        return tripped, ok, "errors never block; while open no PASS"


def hook_latency() -> tuple[bool, bool, str]:
    with _block() as h:
        _session(h, verified=True)
        lb = h.engine.breakers.hook_latency
        for _ in range(lb.min_samples + 5):
            lb.record_latency(lb.budget_ms * 2)
        tripped = lb.is_open()
        resp = h.stop("Done, all tests pass.")
        return tripped, resp == {} and _verdicts(h)[-1] == "unverified", "slow gate: stop allowed, unverified"


def parser() -> tuple[bool, bool, str]:
    with Harness() as h:
        _session(h, verified=False)
        for _ in range(3):
            h.shell("pytest", "garbled output", exit_code=0)
        h.drain()
        tripped = h.engine.breakers.parser_open("pytest")
        h.edit("calc.py", "def add(a, b):\n    return a + b\n")
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)
        return tripped, h.ledger().contracts[0][1].status == "unknown", "runner results UNKNOWN, never PASS"


def false_complete() -> tuple[bool, bool, str]:
    with Harness() as h:
        _session(h, verified=True)
        h.stop("Done, all tests pass.")
        first = _verdicts(h)[-1]
        h.shell("pytest", "1 failed in 0.01s", exit_code=1)       # same code, now failing
        h.stop("Done: the fix is in and the tests pass.")
        tripped = h.engine.board.is_open("false_complete", h.sid)
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)
        h.edit("calc.py", "def add(a, b):\n    return b + a\n")
        h.shell("pytest", "1 passed in 0.01s", exit_code=0)
        h.stop("Finished: everything passes now.")
        # latched: even fresh passing evidence can't verify until a user resets it
        return tripped, first == "verified" and _verdicts(h)[-1] == "unverified", "latched until manual reset"


def client() -> tuple[bool, bool, str]:
    with Harness() as h:
        calls = {"n": 0}

        def boom(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            raise RuntimeError("injected dialect fault")

        h.engine.after_hook = boom  # type: ignore[method-assign]
        spec = SPECS["client"]
        responses = [h.prompt(f"step {i}") for i in range(spec.threshold + 2)]
        tripped = h.engine.board.is_open("client", h.client)
        ok = all(r == {} for r in responses) and calls["n"] == spec.threshold   # open: engine not called
        return tripped, ok, "client passes through unchanged; never blocked"


def schema_miss() -> tuple[bool, bool, str]:
    with Harness() as h:
        resp = [h.hook(f"MysteryEvent{i}") for i in range(SPECS["schema_miss"].threshold)]
        tripped = h.engine.board.is_open("schema_miss", h.client)
        return tripped, all(r == {} for r in resp), "unrecognized events recorded and passed through"


def controller() -> tuple[bool, bool, str]:
    with Harness(config={"ui": {"inject_status": True}}) as h:
        _session(h, verified=False)
        for _ in range(SPECS["controller"].threshold):
            h.engine.respond(h.client, {"hook_event_name": "UserPromptSubmit", "session_id": h.session,
                                        "prompt": "x"}, "UserPromptSubmit", Deadline(0.0))
        tripped = h.engine.board.is_open("controller")
        flags = h.engine.flags
        ok = not flags.enabled("status_injection") and not flags.enabled("semif") and flags.enabled("completion_gate")
        return tripped, ok, "optimization shed (status injection, sensor); integrity untouched"


def retrieval() -> tuple[bool, bool, str]:
    flags = FeatureFlags()
    board = BreakerBoard(flags=flags, overrides={"retrieval": {"cooldown_s": 0.05}})

    def broken() -> Any:
        raise OSError("injected index fault")

    for _ in range(SPECS["retrieval"].threshold):
        try:
            board.guard("retrieval", broken, flag="repo_index")
        except OSError:
            pass
    tripped = board.is_open("retrieval")
    refused = False
    try:
        board.guard("retrieval", lambda: "hits", flag="repo_index")
    except RuntimeError:
        refused = True
    time.sleep(0.08)
    recovered = [board.guard("retrieval", lambda: "hits", flag="repo_index")
                 for _ in range(SPECS["retrieval"].recovery_successes)]
    ok = refused and recovered[-1] == "hits" and not board.is_open("retrieval") and flags.enabled("repo_index")
    return tripped, ok, "fails open to the agent's own search; recovers after a clean window"


def semif() -> tuple[bool, bool, str]:
    from arbiter_agent.semif.service import SemIfService
    from arbiter_agent.semif.shadow import question
    from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult, StateSection

    class NaNBackend:
        name, model, revision, precision, tokenizer, max_tokens = "nan", "m", "r", "fp32", "t", 100_000

        def count_tokens(self, text: str) -> int | None:
            return None

        def score(self, reqs: Any, deadline: Any) -> list[ScoreResult]:
            return [ScoreResult(list(r.options), [float("nan"), 0.5], "m", "r", "nan", "fp32", TEMPLATE_VERSION, "t",
                                r.state_hash, r.criterion_hash, 1.0) for r in reqs]

        def health(self) -> dict[str, Any]:
            return {}

        def close(self) -> None:
            pass

    board = BreakerBoard()
    svc = SemIfService(NaNBackend(), board=board)
    q = question("completion_claim", [StateSection("final assistant message", "Done.", 0, True)])
    judgments = [svc.judge(q) for _ in range(3)]
    tripped = board.is_open("semif", "completion_claim")
    after = svc.judge(q)
    ok = all(j.abstain and j.choice is None for j in judgments + [after]) and "breaker" in after.reason
    return tripped, ok, "malformed scores never consumed; family abstains while open"


def _synthetic(kind: str) -> Callable[[], tuple[bool, bool, str]]:
    def run() -> tuple[bool, bool, str]:
        flags = FeatureFlags({f: True for f in SPECS[kind].flags})
        board = BreakerBoard(flags=flags)
        board.failure(kind, "injected")
        tripped = board.is_open(kind)
        latched = SPECS[kind].latched and board.get(kind).state(time.time() + 10 ** 7) == "open"
        forced = all(not flags.enabled(f) for f in SPECS[kind].flags)
        board.reset(kind)
        restored = all(flags.enabled(f) for f in SPECS[kind].flags) and not board.is_open(kind)
        return tripped, latched and forced and restored, "latched, module forced off, manual reset restores"
    return run


SCENARIOS: dict[str, Callable[[], tuple[bool, bool, str]]] = {
    "gate_errors": gate_errors, "hook_latency": hook_latency, "parser": parser, "false_complete": false_complete,
    "client": client, "schema_miss": schema_miss, "controller": controller, "retrieval": retrieval, "semif": semif,
    **{k: _synthetic(k) for k in ("semif_parity", "stale_result", "context_restore", "learned_policy")},
}
SYNTHETIC = {"semif_parity", "stale_result", "context_restore", "learned_policy"}


def run_all() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for kind, fn in SCENARIOS.items():
        try:
            tripped, behaved, detail = fn()
            err = None
        except Exception as exc:
            tripped, behaved, detail, err = False, False, "", f"{type(exc).__name__}: {exc}"
        results[kind] = {"tripped": tripped, "fail_mode_ok": behaved, "detail": detail, "error": err,
                         "fail_mode": MATRIX[SPECS[kind].component].mode.value, "synthetic": kind in SYNTHETIC}
    missing = sorted(set(SPECS) - set(SCENARIOS))
    n = len(results)
    rate = sum(1 for r in results.values() if r["tripped"]) / n if n else 0.0
    ok = rate == 1.0 and all(r["fail_mode_ok"] for r in results.values()) and not missing
    return {"trip_rate": rate, "passed": ok, "missing": missing, "scenarios": results}


def render(report: dict[str, Any]) -> str:
    lines = [f"Fault injection: trip rate {report['trip_rate']:.0%}  "
             f"{'PASS' if report['passed'] else 'FAIL'}"]
    for k, r in report["scenarios"].items():
        mark = "ok " if r["tripped"] and r["fail_mode_ok"] else "BAD"
        lines.append(f"  {mark} {k:<16} {r['fail_mode']:<18} {r['detail'] or r['error']}"
                     f"{'  (synthetic)' if r['synthetic'] else ''}")
    if report["missing"]:
        lines.append(f"  breakers without a scenario: {', '.join(report['missing'])}")
    return "\n".join(lines)

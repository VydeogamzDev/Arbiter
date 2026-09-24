"""M3: intent log, goal epochs, contracts, facts, evidence, integrity, loops (in-process harness)."""

from __future__ import annotations

import sqlite3

import pytest

from arbiter_agent.completion.evidence_grades import command_match
from arbiter_agent.eval.trace_replay import Harness
from arbiter_agent.reasoning.loop_detector import LoopDetector
from arbiter_agent.state import goal_epochs
from arbiter_agent.state.store import connect
from arbiter_agent.telemetry import errors as err
from arbiter_agent.telemetry.runner_parsers import detect, fingerprint, strip_wrappers

CALC = {"calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}


@pytest.fixture
def h():
    with Harness() as harness:
        for p, t in CALC.items():
            harness.write(p, t)
        yield harness


def _contracts(h: Harness) -> list[sqlite3.Row]:
    rc = connect(h.db, readonly=True)
    rc.row_factory = sqlite3.Row
    try:
        return rc.execute("SELECT * FROM contract WHERE thread_id = ? ORDER BY rowid", (h.sid,)).fetchall()
    finally:
        rc.close()


# ------------------------------------------------------------------ parsers / commands
def test_wrapper_stripping_and_fingerprints():
    assert strip_wrappers("powershell.exe -NoProfile -Command 'cd D:\\r; uv run pytest -q'") == "pytest -q"
    assert strip_wrappers('bash -lc "cd /r && npx vitest run"') == "vitest run"
    assert fingerprint("python3 -m pytest tests") == "pytest tests"


@pytest.mark.parametrize(("recipe", "run", "want"), [
    ("pytest", "pytest -q", "exact"),
    ("pytest tests/test_a.py", "pytest", "superset"),
    ("pytest tests/unit/test_a.py", "pytest tests/unit", "superset"),
    ("pytest", "pytest -k add", None),              # narrowing filter never matches
    ("pytest", "pytest tests/test_a.py", None),     # narrower run
    ("pytest", "pytest --lf", None),
    ("npm test", "npm test -- --ci", "exact"),
    ("go test ./...", "go test ./... -count=1", "exact"),
    ("cargo test", "cargo nextest run", None),
])
def test_command_match(recipe, run, want):
    assert command_match(recipe, run) == want


def test_parser_contradiction_is_unknown_never_pass():
    assert detect("pytest", "5 passed in 0.1s", 1).status == "unknown"
    assert detect("pytest", "5 passed in 0.1s", 0).status == "pass"
    assert detect("echo hi", "5 passed in 0.1s", 0) is None


def test_error_fingerprints_normalize_numbers_and_paths():
    a = err.fingerprints("E KeyError: 'id' at line 10 in /home/u/x.py")
    b = err.fingerprints("E KeyError: 'id' at line 99 in /tmp/y/x.py")
    assert a and a[0][0] == b[0][0]


# ------------------------------------------------------------------ epochs
@pytest.mark.parametrize(("text", "want"), [
    ("yes, continue", "join"), ("ok go ahead", "join"), ("lgtm", "join"), ("continue with the plan", "join"),
    ("New task: add a flag", "confirm_new"), ("Scratch that, start over", "confirm_new"),
    ("Also handle the empty case", "candidate"), ("use a lock instead", "candidate"),
])
def test_epoch_rules(text, want):
    assert goal_epochs.decide(text, first_in_session=False).action == want


def test_first_prompt_and_resume_confirm():
    assert goal_epochs.decide("anything", first_in_session=True).action == "confirm_new"
    assert goal_epochs.decide("fix the flaky test", first_in_session=False, after_resume=True).action == "confirm_new"
    assert goal_epochs.decide("continue", first_in_session=False, after_resume=True).action == "join"


def test_intent_log_is_append_only(h):
    h.prompt("Fix the addition bug in calc.py.")
    h.drain()
    c = connect(h.db)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute("UPDATE intent SET goal_epoch = 9")
        with pytest.raises(sqlite3.DatabaseError, match="retention"):
            c.execute("DELETE FROM intent")
    finally:
        c.close()


def test_continuation_never_supersedes_contracts(h):
    h.prompt("Fix the addition bug in calc.py.")
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])
    for reply in ("yes, continue", "ok", "go ahead", "sounds good, proceed"):
        h.prompt(reply)
    h.drain()
    st = h.engine.session_status(h.sid, light=True)
    assert st["goal_epoch"] == 1 and st["counts"]["unknown"] == 1


def test_new_task_marker_supersedes_and_scope_change_carries(h):
    h.prompt("Fix the addition bug in calc.py.")
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])
    h.prompt("also keep the old behaviour for negative numbers")          # candidate only
    h.drain()
    assert h.engine.session_status(h.sid, light=True)["goal_epoch"] == 1
    res = h.engine.scope_change(h.sid, "added constraint", carry=["C1"])
    assert res["goal_epoch"] == 2 and res["starts_at_intent"] == "U2"
    assert h.engine.session_status(h.sid, light=True)["counts"]["unknown"] == 1   # C1 carried
    h.prompt("New task: write a README.")
    h.drain()
    st = h.engine.session_status(h.sid, light=True)
    assert st["goal_epoch"] == 3 and st["counts"] == {}


# ------------------------------------------------------------------ contracts
def test_provenance_and_status_authority(h):
    h.prompt("Add caching to the user lookup.")
    res = h.propose([
        {"text": "cache", "quotes": ["Add caching to the user lookup"], "status": "pass",
         "recipe": {"type": "test_command", "command": "pytest"}},
        {"text": "invented", "quotes": ["make sure all tests pass"], "recipe": {"type": "test_command",
                                                                                 "command": "pytest"}},
        {"text": "bad recipe", "quotes": ["Add caching to the user lookup"], "recipe": {"type": "test_command"}},
    ])
    assert [a["id"] for a in res["accepted"]] == ["C1"]
    assert {r["reason"] for r in res["rejected"]} == {"quote not found in the user's messages",
                                                      "recipe 'test_command' needs command"}
    assert _contracts(h)[0]["status"] == "unknown"   # the agent can't set PASS


def test_quotes_from_another_session_are_rejected(h):
    h.prompt("Add caching.", session="other")
    h.prompt("Fix the addition bug in calc.py.")
    res = h.propose([{"text": "x", "quotes": ["Add caching."], "recipe": {"type": "manual"}}])
    assert not res["accepted"] and res["rejected"]


def test_rule_extraction_and_coverage(h):
    h.prompt("Fix the bug in calc.py. Don't change `tests/test_calc.py`. Make sure `pytest -q` passes. "
             "Also add a docstring.")
    h.drain()
    rows = _contracts(h)
    assert {r["proposed_by"] for r in rows} == {"rules"}
    recipes = sorted(r["verification_recipe_json"] for r in rows)
    assert any('"paths_unchanged"' in x for x in recipes) and any('"pytest -q"' in x for x in recipes)
    led = h.ledger()
    uncovered = [u.text for u in led.uncovered]
    assert "Fix the bug in calc.py." in uncovered and "Also add a docstring." in uncovered


def test_weak_contract_flagged_and_not_verified(h):
    h.prompt("Fix the addition bug in calc.py.")
    res = h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                      "recipe": {"type": "file_exists", "path": "calc.py"}}])
    assert res["accepted"][0]["strength"] == "low"
    led = h.ledger()
    assert led.contracts[0][1].status == "pass" and led.verdict == "unverified"
    h.engine.decide_contract(h.sid, "C1", "waive")
    led = h.ledger()
    assert led.contracts[0][1].status == "waived" and led.verdict == "verified"


def test_manual_contract_only_user_confirms(h):
    h.prompt("Make the error messages friendlier.")
    h.propose([{"text": "friendlier", "quotes": ["Make the error messages friendlier"],
                "recipe": "the user likes the wording"}])
    assert h.ledger().verdict == "unverified"
    with pytest.raises(ValueError, match="only manual"):
        h.engine.add_contract(h.sid, "tests", {"type": "test_command", "command": "pytest"}, None)
        h.engine.decide_contract(h.sid, "C2", "confirm")
    h.engine.decide_contract(h.sid, "C1", "confirm")
    assert h.ledger().contracts[0][1].status == "pass"


# ------------------------------------------------------------------ evidence
def _fix_session(h: Harness) -> None:
    h.prompt("Fix the addition bug in calc.py.")
    h.baseline()
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])


def test_passing_run_verifies_and_later_edit_makes_it_stale(h):
    _fix_session(h)
    h.edit("calc.py", "def add(a, b):\n    return b + a\n")
    h.shell("pytest -q", "1 passed in 0.01s", exit_code=0)
    assert h.ledger().verdict == "verified"
    h.edit("calc.py", "def add(a, b):\n    return a + b + 0\n")
    led = h.ledger()
    assert led.verdict == "unverified" and "stale" in led.contracts[0][1].reason
    h.edit("README.md", "docs")          # doc edits don't make test evidence stale
    h.shell("pytest", "1 passed in 0.01s", exit_code=0)
    h.edit("README.md", "more docs")
    assert h.ledger().verdict == "verified"


def test_exit_code_join_from_transcript_duplicate(h):
    _fix_session(h)
    h.shell("pytest", "1 passed in 0.01s", exit_code=1)   # hook says pass; transcript exit 1
    led = h.ledger()
    assert led.contracts[0][1].status == "unknown"


def test_flaky_runs_are_conflicted(h):
    _fix_session(h)
    h.shell("pytest", "1 failed in 0.01s", exit_code=1)
    h.shell("pytest", "1 passed in 0.01s", exit_code=0)
    ev = h.ledger().contracts[0][1]
    assert ev.status == "unknown" and ev.grade == "conflicted"


def test_agent_assertions_never_satisfy(h):
    _fix_session(h)
    out = h.engine.finish_check(h.sid, "done", ["pytest passes", "all green"])
    assert out["verdict"] == "unverified"
    rc = connect(h.db, readonly=True)
    try:
        origins = {r[0] for r in rc.execute("SELECT origin FROM fact WHERE kind = 'assertion'")}
    finally:
        rc.close()
    assert origins == {"agent_asserted"}


def test_integrity_unknown_without_baseline_blocks_test_evidence(h):
    h.prompt("Fix the addition bug in calc.py.")
    h.propose([{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                "recipe": {"type": "test_command", "command": "pytest"}}])
    h.engine._baseline_pending.add(h.sid)       # no automatic capture for this test
    h.shell("pytest", "1 passed in 0.01s", exit_code=0)
    led = h.ledger()
    assert led.integrity.status == "unknown" and led.verdict == "unverified"
    assert any("integrity is UNKNOWN" in m for m in led.missing)


def test_integrity_detects_skip_and_ack_clears(h):
    _fix_session(h)
    h.edit("tests/test_calc.py", "import pytest\nfrom calc import add\n\n@pytest.mark.skip\ndef test_add():\n"
                                 "    assert add(1, 2) == 3\n")
    h.shell("pytest", "1 skipped, 0 passed in 0.01s", exit_code=0)
    h.shell("pytest", "== 1 passed in 0.01s ==", exit_code=0)
    led = h.ledger()
    kinds = {f.kind for f in led.integrity.findings}
    assert "skip_added" in kinds and led.verdict == "unverified"
    h.engine.ack_integrity(h.sid, ["skip_added:tests/test_calc.py"])
    assert "skip_added" not in {f.kind for f in h.ledger().integrity.findings}


def test_sessions_are_isolated(h):
    h.prompt("Fix the addition bug in calc.py.", session="A")
    h.prompt("Write docs.", session="B")
    h.baseline("A")
    h.engine.propose("claude_code:A", [{"text": "fixed", "quotes": ["Fix the addition bug in calc.py"],
                                        "recipe": {"type": "test_command", "command": "pytest"}}])
    h.shell("pytest", "1 passed in 0.01s", exit_code=0, session="B")
    assert h.ledger("A").verdict == "unverified"
    h.shell("pytest", "1 passed in 0.01s", exit_code=0, session="A")
    assert h.ledger("A").verdict == "verified"


def test_engine_resumes_from_cursor_after_restart(tmp_path):
    with Harness(workdir=tmp_path) as h1:
        for p, t in CALC.items():
            h1.write(p, t)
        h1.prompt("Fix the addition bug in calc.py.")
        h1.drain()
    with Harness(workdir=tmp_path) as h2:
        h2.prompt("yes, continue")
        h2.drain()
        st = h2.engine.session_status(h2.sid, light=True)
        assert st["intents"] == 2 and st["goal_epoch"] == 1


# ------------------------------------------------------------------ loops
def test_loop_detector_same_error_and_reset():
    d = LoopDetector()
    fail = {"kind": "test_run", "subject": "pytest", "status": "fail",
            "data": {"failed": 1, "error_fps": [("abc", "E KeyError: x")]}}
    alerts = []
    for _ in range(3):
        alerts += d.feed({"kind": "file_change", "subject": "a.py"})
        alerts += d.feed(fail)
    assert [a.signal for a in alerts] == ["same_error"]
    d.feed({"kind": "test_run", "subject": "pytest", "status": "pass", "data": {}})
    assert d.feed(fail) == []


def test_loop_alerts_recorded_as_facts(h):
    h.prompt("Fix the addition bug in calc.py.")
    for _ in range(3):
        h.shell("pytest", "E AssertionError: assert 3 == 4\n== 1 failed in 0.1s ==", exit_code=1)
    h.drain()
    st = h.engine.session_status(h.sid, light=True)
    assert st["loop_alerts"] >= 1

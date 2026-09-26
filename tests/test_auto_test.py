"""Arbiter runs the tests itself (completion.auto_test) and picks the context pack per model.

Benchmark findings, 2026-09-26: test runs were 26% of Opus's API calls and ~15% of Sonnet's, and
the pack's file contents saved Opus 14% while making Sonnet 5 think 53% longer.
"""

from __future__ import annotations

import json
import sys
import time

import pytest

from arbiter_agent.completion.auto_test import AutoTester, detect_command, is_code_path
from arbiter_agent.config.loader import build_config
from arbiter_agent.daemon.session_engine import transcript_model
from arbiter_agent.eval.trace_replay import Harness

CALC_OK = "def add(a, b):\n    return a + b\n"
CALC_BAD = "def add(a, b):\n    return a - b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def _repo(h: Harness, calc: str = CALC_BAD) -> None:
    h.write("calc.py", calc)
    h.write("tests/test_calc.py", TEST)
    h.write("tests/__init__.py", "")


def _write_tool(h: Harness, rel: str, text: str) -> dict:
    p = h.repo / rel
    p.write_text(text, encoding="utf-8")
    return h.hook("PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": str(p)}, "tool_response": {},
                                  "tool_use_id": f"e-{time.time_ns()}"})


def _cfg(**completion) -> dict:
    return {"completion": {"auto_test": "after_edit", "auto_test_command": f'"{sys.executable}" -m pytest -q',
                           "auto_test_settle_s": 0.0, **completion}}


def test_detect_command_and_code_paths(tmp_path):
    assert detect_command(tmp_path) is None
    (tmp_path / "tests").mkdir()
    (tmp_path / "app.py").write_text("x = 1\n")
    assert detect_command(tmp_path) == "python -m pytest -q"
    assert is_code_path("src/app.py") and not is_code_path("README.md")


def test_result_comes_back_with_the_edit_and_counts_as_evidence():
    with Harness(config=_cfg()) as h:
        _repo(h)
        h.prompt("Fix the addition bug in calc.py.")
        h.baseline()
        bad = _write_tool(h, "calc.py", CALC_BAD + "\n")
        ctx = bad["hookSpecificOutput"]["additionalContext"]
        assert "[Arbiter] Ran" in ctx and "FAIL" in ctx
        good = _write_tool(h, "calc.py", CALC_OK)
        assert "PASS" in good["hookSpecificOutput"]["additionalContext"]
        # the same files aren't announced twice
        again = h.hook("PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": str(h.repo / "calc.py")},
                                       "tool_response": {}, "tool_use_id": "r1"})
        assert again == {}
        led = h.ledger()
        assert led.verdict == "verified", led.missing


def test_docs_edits_and_agent_test_runs_dont_trigger_a_run():
    with Harness(config=_cfg()) as h:
        _repo(h, CALC_OK)
        h.prompt("Update the docs.")
        assert _write_tool(h, "README.md", "# docs\n") == {}


def test_claim_without_tests_is_verified_by_arbiter_not_blocked():
    with Harness(config=_cfg(auto_test="at_stop", gate_mode="block")) as h:
        _repo(h)
        h.prompt("Fix the addition bug in calc.py.")
        h.baseline()
        h.edit("calc.py", CALC_OK)
        assert h.stop("Done. I fixed add().") == {}              # Arbiter ran the tests: verified, no block
        h.edit("calc.py", CALC_BAD)
        r = h.stop("Done again.")
        assert r.get("decision") == "block" and "failed" in r["reason"]   # a real failure still blocks


def test_slow_suite_is_not_rerun_after_edits(tmp_path):
    cfg = build_config({"completion": {"auto_test": "after_edit", "auto_test_max_s": 0.0,
                                       "auto_test_command": f'"{sys.executable}" -c "print(1)"'}})
    t = AutoTester(cfg)
    (tmp_path / "a.py").write_text("x = 1\n")
    assert t.run(tmp_path, 10) is not None                  # first run happens (and is slower than 0 s)
    (tmp_path / "a.py").write_text("x = 2\n")
    assert t.run(tmp_path, 10) is None                      # remembered as slow: not after edits
    assert t.run(tmp_path, 10, at_stop=True) is not None    # still runs once at a completion claim
    t.stop()


@pytest.mark.parametrize(("model", "want"), [("claude-opus-5-5", "full"), ("claude-sonnet-5", "map")])
def test_pack_waits_for_the_model_then_picks_full_or_map(model, want):
    def provider(cwd, ctx, budget, tokens):
        return {"text": "[Arbiter] Task context, pre-read FULL", "map_text": "[Arbiter] Task context MAP",
                "picks": ["calc.py"], "tokens": 10}

    with Harness(config={"retrieval": {"auto_context": True}, "completion": {"auto_test": "off"}}) as h:
        h.engine.pack_provider = provider
        h.engine.context_provider = lambda *a: {}
        _repo(h, CALC_OK)
        assert h.prompt("Fix the addition bug in calc.py.") == {}        # model unknown: held back
        (h.dir / "t.jsonl").write_text(json.dumps({"type": "assistant", "message": {"model": model}}) + "\n",
                                       encoding="utf-8")
        r = h.hook("PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": str(h.repo / "calc.py")},
                                   "tool_response": {}, "tool_use_id": "r1"})
        text = r["hookSpecificOutput"]["additionalContext"]
        assert ("FULL" in text) == (want == "full") and ("MAP" in text) == (want == "map")


def test_codex_payload_model_decides_at_the_first_prompt():
    def provider(cwd, ctx, budget, tokens):
        return {"text": "FULL pack", "map_text": "MAP pack", "picks": ["calc.py"], "tokens": 10}

    with Harness(config={"retrieval": {"auto_context": True}, "completion": {"auto_test": "off"}},
                 client="codex") as h:
        h.engine.pack_provider = provider
        h.engine.context_provider = lambda *a: {}
        _repo(h, CALC_OK)
        r = h.hook("UserPromptSubmit", {"prompt": "Fix the addition bug in calc.py.", "model": "claude-sonnet-5",
                                        "turn_id": "t1"})
        assert "MAP pack" in json.dumps(r)


def test_transcript_model_reads_the_latest_assistant(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(o) for o in [
        {"type": "user", "message": {"content": "hi"}},
        {"type": "assistant", "message": {"model": "claude-opus-5-5"}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5"}}]) + "\n", encoding="utf-8")
    assert transcript_model(str(p)) == "claude-sonnet-5"
    assert transcript_model(str(tmp_path / "missing.jsonl")) is None

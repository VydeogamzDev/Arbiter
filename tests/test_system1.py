"""System 1 roles in the session engine (brief, judge) and the trust note, with a fake cheap model."""

from __future__ import annotations

import sys
import time

from arbiter_agent.eval.trace_replay import Harness, git_init
from arbiter_agent.system1 import parse_effort, parse_judge

CALC_OK = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


class FakeS1:
    enabled = True

    def __init__(self, answers):
        self.answers = answers
        self.prompts = []

    def complete(self, prompt, **_):
        self.prompts.append(prompt)
        for key, answer in self.answers.items():
            if key in prompt:
                return answer
        return None


def _cfg(**extra):
    cfg = {"completion": {"auto_test": "after_edit", "auto_test_command": f'"{sys.executable}" -m pytest -q',
                          "auto_test_budget_s": 20.0, "auto_test_stop_budget_s": 20.0, "auto_test_settle_s": 0.0}}
    for k, v in extra.items():
        cfg.setdefault(k, {}).update(v)
    return cfg


def _repo(h):
    h.write("calc.py", CALC_OK)
    h.write("tests/test_calc.py", TEST)
    h.write("tests/__init__.py", "")


def _edit(h, text=CALC_OK + "\n"):
    p = h.repo / "calc.py"
    p.write_text(text, encoding="utf-8")
    return h.hook("PostToolUse", {"tool_name": "Edit", "tool_input": {"file_path": str(p)}, "tool_response": {},
                                  "tool_use_id": f"e-{time.time_ns()}"})


def test_parsers():
    assert parse_judge("OK") == [] and parse_judge(None) is None
    assert parse_judge("1. negatives are not handled\n2. no export") == ["1. negatives are not handled", "2. no export"]
    assert parse_effort("Low.") == "low" and parse_effort("high") == "high" and parse_effort("?") is None


def test_trust_note_announces_a_pending_run():
    with Harness(config=_cfg(completion={"auto_test_trust_note": True, "auto_test_budget_s": 0.0})) as h:
        _repo(h)
        h.prompt("Tidy calc.py.")
        out = _edit(h)["hookSpecificOutput"]["additionalContext"]
        assert "is running on the current files" in out and "don't need to run these tests yourself" in out
        assert _edit(h) in ({},) or "is running" not in str(_edit(h))     # said once per change set


def test_judge_sends_the_agent_back_once_with_its_findings():
    with Harness(config=_cfg(system1={"judge": True})) as h:
        h.engine.system1 = FakeS1({"THE CHANGE": "1. zero is not handled"})
        _repo(h)
        h.prompt("Make add() handle zero specially.")
        git_init(h.repo)
        _edit(h, CALC_OK + "# changed\n")
        r = h.stop("Done.")
        assert r.get("decision") == "block" and "1. zero is not handled" in r["reason"]
        assert "zero is not handled" not in str(h.stop("Done."))          # once per task
        assert h.engine.system1.prompts and "Make add() handle zero" in h.engine.system1.prompts[-1]


def test_judge_ok_lets_the_stop_through():
    with Harness(config=_cfg(system1={"judge": True})) as h:
        h.engine.system1 = FakeS1({"THE CHANGE": "OK"})
        _repo(h)
        h.prompt("Add a comment to calc.py.")
        git_init(h.repo)
        _edit(h, CALC_OK + "# note\n")
        assert "second look" not in str(h.stop("Done."))


def test_brief_arrives_at_the_next_tool_hook():
    def provider(cwd, ctx, budget, tokens):
        return {"text": "[Arbiter] Task context, read from disk", "map_text": "map", "picks": ["calc.py"],
                "tokens": 10}

    with Harness(config=_cfg(retrieval={"auto_context": True}, system1={"brief": True})) as h:
        h.engine.pack_provider = provider
        h.engine.context_provider = lambda *a: {}
        h.engine.system1 = FakeS1({"TASK:": "The change goes in calc.py:add (line 1)."})
        _repo(h)
        h.prompt("Make add() handle zero specially.")
        deadline = time.monotonic() + 10
        out = {}
        while time.monotonic() < deadline:
            out = h.hook("PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": str(h.repo / "calc.py")},
                                         "tool_response": {}, "tool_use_id": f"r-{time.time_ns()}"})
            if "Task brief" in str(out):
                break
            time.sleep(0.2)
        assert "calc.py:add (line 1)" in str(out)


def test_effort_advice_once_per_thread_codex_message_and_pi_level():
    with Harness(config=_cfg(system1={"effort_advice": True}), client="codex") as h:
        h.engine.system1 = FakeS1({"How much reasoning effort": "high"})
        _repo(h)
        h.prompt("`prevprime(n)` sometimes skips a prime. Find the cause and fix it.")
        deadline = time.monotonic() + 10
        out = {}
        while time.monotonic() < deadline and "systemMessage" not in out:
            out = h.hook("PostToolUse", {"tool_name": "Read", "tool_input": {"file_path": str(h.repo / "calc.py")},
                                         "tool_response": {}, "tool_use_id": f"r-{time.time_ns()}"})
            time.sleep(0.1)
        assert "Raise this thread's effort now" in out.get("systemMessage", "")
        n = len(h.engine.system1.prompts)
        h.prompt("Also add a test for it.")                      # later prompts: no new call, no new message
        assert len(h.engine.system1.prompts) == n
    with Harness(config=_cfg(system1={"effort_advice": True}), client="pi") as h:
        h.engine.system1 = FakeS1({"How much reasoning effort": "low"})
        _repo(h)
        r = h.prompt("Add a `words` option to `ordinal`.")
        assert r.get("arbiterRouting") == {"thinking": "low"}

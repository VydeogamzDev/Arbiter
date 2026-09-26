"""The A/B benchmark harness scores reference, sloppy and empty outcomes correctly, and its gate
condition wires hooks + MCP to a per-run daemon (scripted agent; no model calls)."""

from __future__ import annotations

import argparse
import shutil

import pytest

from bench import harness, report
from bench.conditions import CONDITIONS

pytestmark = pytest.mark.skipif(not (harness.TASKS / "tz_fix" / "task.yaml").is_file(),
                                reason="bench tasks not generated (python bench/make_tasks.py)")


def _args(agent: str) -> argparse.Namespace:
    return argparse.Namespace(agent=agent, model=harness.MODEL, force=True, keep_workspaces=False, encoder=None,
                              effort=None, warmup=False,
                              budget=1.0, timeout=60)


@pytest.fixture
def tasks():
    return {t["id"]: t for t in harness.load_tasks("tz_fix,discount_tiers")}


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_scoring_separates_solution_sloppy_and_noop(tmp_path, tasks):
    out = tmp_path / "runs" / "r"
    got = {}
    for agent in ("fake-solution", "fake-sloppy", "fake-noop"):
        for tid, t in tasks.items():
            got[(agent, tid)] = harness.run_one(t, CONDITIONS["baseline"], 0, out / agent, _args(agent))["score"]
    assert all(got[("fake-solution", t)]["full_pass"] for t in tasks)
    assert not any(got[("fake-solution", t)]["tampered"] for t in tasks)
    assert got[("fake-sloppy", "tz_fix")]["tampered"]            # skipped the failing test
    assert not got[("fake-sloppy", "tz_fix")]["full_pass"]
    assert got[("fake-sloppy", "discount_tiers")]["coverage"] < 1.0
    assert got[("fake-sloppy", "discount_tiers")]["false_complete"]
    assert not any(got[("fake-noop", t)]["full_pass"] for t in tasks)


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_gate_condition_blocks_then_verifies_with_contracts(tmp_path, tasks):
    t = tasks["tz_fix"]
    noop = harness.run_one(t, CONDITIONS["gate_only"], 0, tmp_path / "runs" / "a", _args("fake-noop"))
    assert noop["arbiter"]["stop_blocks"] >= 1                   # a bare "done" claim is blocked
    ok = harness.run_one(t, CONDITIONS["gate_only"], 0, tmp_path / "runs" / "b", _args("fake-solution"))
    assert [row["verdict"] for row in ok["arbiter"]["ledger"]] == ["verified"]
    assert ok["arbiter"]["stop_blocks"] == 0


def test_report_renders(tmp_path, tasks):
    out = tmp_path / "runs" / "r"
    for cond in ("baseline", "tools_only"):
        harness.run_one(tasks["discount_tiers"], CONDITIONS[cond], 0, out,
                        _args("fake-solution" if cond == "baseline" else "fake-sloppy"))
    text = report.write(out)
    assert "Against baseline" in text and "task success" in text
    assert (out / "report.md").is_file()

"""M1 exit gates (docs/milestones.md, spec §20.17), measured on the real transports.

Results are written to ``docs/spikes/m1/exit_gates.json`` when ARBITER_RECORD_GATES=1.
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

import pytest

from arbiter_agent.clients.fake_host import FakeHost
from arbiter_agent.daemon import lifecycle
from tests.conftest import spawn_daemon, wait_running

GATING_P95_MS = 300        # §20.17 gating hook p95
TELEMETRY_P95_MS = 50      # §20.17 telemetry hook added latency p95
COLD_START_P95_S = 2.0     # §20.17 daemon cold start to first served request
RESULTS: dict[str, object] = {}


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, round(0.95 * (len(xs) - 1)))]


def _record():
    if os.environ.get("ARBITER_RECORD_GATES"):
        out = Path(__file__).resolve().parents[1] / "docs" / "spikes" / "m1" / "exit_gates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        prev = json.loads(out.read_text()) if out.exists() else {}
        prev.update(RESULTS)
        prev["_platform"] = sys.platform
        out.write_text(json.dumps(prev, indent=1))


@pytest.mark.slow
def test_daemon_cold_start_p95(home):
    # The very first launch on a fresh machine also compiles bytecode (seen: 3.5 s on a CI runner,
    # then ~0.77 s). It's recorded separately; the gate covers ordinary cold starts.
    t = time.perf_counter()
    lifecycle.launch(home)
    assert wait_running(home, timeout=30)
    first_launch = time.perf_counter() - t
    assert lifecycle.stop(home)
    time.sleep(0.3)
    samples = []
    for _ in range(5):
        t = time.perf_counter()
        method = lifecycle.launch(home)  # production path (WMI on Windows)
        assert wait_running(home, timeout=15)
        samples.append(time.perf_counter() - t)
        assert lifecycle.stop(home)
        time.sleep(0.3)
    RESULTS["cold_start_s"] = {"method": method, "first_launch": round(first_launch, 3),
                               "samples": [round(s, 3) for s in samples],
                               "p95": round(p95(samples), 3), "gate": COLD_START_P95_S}
    _record()
    assert p95(samples) <= COLD_START_P95_S, samples


@pytest.mark.slow
@pytest.mark.parametrize("transport", ["mcp", "http"])
def test_primary_hook_transports_meet_budgets(home, tmp_path, transport):
    proc = spawn_daemon(home)
    assert wait_running(home)
    client = "codex" if transport == "mcp" else "claude_code"
    try:
        with FakeHost(home, client=client, transport=transport, cwd=str(tmp_path), autostart=False) as h:
            h.prompt("warm up")
            h.calls.clear()
            for i in range(60):
                h.prompt(f"turn {i}")
                h.tool("pytest -q", "3 passed", 0)
                h.stop("Done.")
        lat = [c.latency_ms for c in h.calls]
        assert all(c.ok for c in h.calls)
        RESULTS[f"hook_{transport}_ms"] = {"n": len(lat), "p50": round(statistics.median(lat), 2),
                                            "p95": round(p95(lat), 2), "max": round(max(lat), 2),
                                            "gating_gate": GATING_P95_MS, "telemetry_gate": TELEMETRY_P95_MS}
        _record()
        assert p95(lat) <= GATING_P95_MS
        assert p95(lat) <= TELEMETRY_P95_MS
    finally:
        lifecycle.stop(home)
        proc.wait(10)


@pytest.mark.slow
def test_command_fallback_measured_not_gated(home, tmp_path):
    """Decision 0021: the command CLI is a fallback; it's measured and must stay under the hard
    deadline, but it's excluded from gating paths when its p95 exceeds 300 ms."""
    proc = spawn_daemon(home)
    assert wait_running(home)
    try:
        with FakeHost(home, client="codex", transport="command", cwd=str(tmp_path), autostart=False) as h:
            for i in range(15):
                h.prompt(f"turn {i}")
        lat = [c.latency_ms for c in h.calls]
        RESULTS["hook_command_fallback_ms"] = {"n": len(lat), "p50": round(statistics.median(lat), 1),
                                               "p95": round(p95(lat), 1), "hard_deadline": 1500,
                                               "eligible_for_gating": p95(lat) <= GATING_P95_MS}
        _record()
        assert all(c.ok for c in h.calls)
        assert p95(lat) < 1500
    finally:
        lifecycle.stop(home)
        proc.wait(10)

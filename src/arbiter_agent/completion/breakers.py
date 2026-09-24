"""Gate-facing breakers (spec §15.3, decision 0009), backed by the M8 breaker board.

- ``hook_latency``: gating-hook latency p95 over a sliding window above budget.
- ``gate_errors``: repeated exceptions while evaluating the gate.
- ``parser:<runner>``: a runner's parser failing on output from a command it should recognize,
  or a parsed PASS contradicted by the exit status.
- ``false_complete``: a verified completion later contradicted (latched until manual reset).

An open breaker never blocks anything: the gate reports no PASS and marks the session
unverified, and parser breakers make that runner's results UNKNOWN.
"""

from __future__ import annotations

from typing import Any

from arbiter_agent.policy.circuit_breaker import Breaker, BreakerBoard, LatencyBreaker

__all__ = ["Breaker", "Breakers", "LatencyBreaker"]


class Breakers:
    def __init__(self, gating_budget_ms: float = 300.0, enabled: bool = True,
                 board: BreakerBoard | None = None) -> None:
        self.board = board or BreakerBoard(enabled=enabled)
        self.board.enabled = enabled
        self._latency = LatencyBreaker("hook_latency", gating_budget_ms)
        self.board.add(self._latency, "hook_latency")
        self.gate_errors = self.board.get("gate_errors")

    @property
    def enabled(self) -> bool:
        return self.board.enabled

    @property
    def hook_latency(self) -> LatencyBreaker:
        return self._latency

    @hook_latency.setter
    def hook_latency(self, b: LatencyBreaker) -> None:
        self.board.add(b, "hook_latency")
        self._latency = b

    def parser(self, runner: str) -> Breaker:
        return self.board.get("parser", runner)

    def gate_open(self, session: str | None = None) -> str | None:
        if not self.enabled:
            return None
        if self.hook_latency.is_open():
            return "hook latency breaker open"
        if self.gate_errors.is_open():
            return "gate error breaker open"
        if session and self.board.is_open("false_complete", session):
            return (f"false-completion incident for this session: inspect, then "
                    f"`arbiter breakers reset false_complete:{session}`")
        return None

    def parser_open(self, runner: str | None) -> bool:
        return bool(runner) and self.board.is_open("parser", runner)

    def snapshot(self) -> dict[str, Any]:
        return self.board.snapshot()

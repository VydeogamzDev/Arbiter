"""Minimal circuit breakers for v0.1 (spec §15.3 subset, decision 0009).

- ``hook_latency``: gating-hook latency p95 over a sliding window above budget.
- ``gate_errors``: repeated exceptions while evaluating the gate.
- ``parser:<runner>``: a runner's parser failing on output from a command it should recognize,
  or a parsed PASS contradicted by the exit status.

An open breaker never blocks anything: the gate reports no PASS and marks the session
unverified, and parser breakers make that runner's results UNKNOWN. Breakers close again after
a cooldown (half-open: the next outcome decides).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Breaker:
    name: str
    threshold: int = 3                 # failures within window to trip
    window_s: float = 600.0
    cooldown_s: float = 600.0
    failures: deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    trips: int = 0
    last_reason: str = ""

    def record_failure(self, reason: str = "", now: float | None = None) -> None:
        now = now or time.time()
        self.failures.append(now)
        self.last_reason = reason[:200]
        while self.failures and self.failures[0] < now - self.window_s:
            self.failures.popleft()
        if self.opened_at is None and len(self.failures) >= self.threshold:
            self.opened_at = now
            self.trips += 1

    def record_success(self, now: float | None = None) -> None:
        now = now or time.time()
        if self.opened_at is not None and now - self.opened_at >= self.cooldown_s:
            self.opened_at = None
            self.failures.clear()

    def is_open(self, now: float | None = None) -> bool:
        now = now or time.time()
        if self.opened_at is None:
            return False
        if now - self.opened_at >= self.cooldown_s:
            return False   # half-open: allow a trial
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"open": self.is_open(), "trips": self.trips, "recent_failures": len(self.failures),
                "last_reason": self.last_reason}


class LatencyBreaker(Breaker):
    def __init__(self, name: str, budget_ms: float, samples: int = 50, min_samples: int = 20,
                 cooldown_s: float = 600.0) -> None:
        super().__init__(name, threshold=1, cooldown_s=cooldown_s)
        self.budget_ms = budget_ms
        self.lat: deque[float] = deque(maxlen=samples)
        self.min_samples = min_samples

    def record_latency(self, ms: float, now: float | None = None) -> None:
        self.lat.append(ms)
        if len(self.lat) >= self.min_samples and self.p95() > self.budget_ms:
            if self.opened_at is None:
                self.record_failure(f"p95 {self.p95():.0f} ms > {self.budget_ms:.0f} ms", now)
        else:
            self.record_success(now)

    def p95(self) -> float:
        if not self.lat:
            return 0.0
        s = sorted(self.lat)
        return s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "p95_ms": round(self.p95(), 1), "samples": len(self.lat)}


class Breakers:
    def __init__(self, gating_budget_ms: float = 300.0, enabled: bool = True) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self.hook_latency = LatencyBreaker("hook_latency", gating_budget_ms)
        self.gate_errors = Breaker("gate_errors", threshold=3)
        self.parsers: dict[str, Breaker] = {}

    def parser(self, runner: str) -> Breaker:
        with self._lock:
            b = self.parsers.get(runner)
            if b is None:
                b = self.parsers[runner] = Breaker(f"parser:{runner}", threshold=3)
            return b

    def gate_open(self) -> str | None:
        if not self.enabled:
            return None
        if self.hook_latency.is_open():
            return "hook latency breaker open"
        if self.gate_errors.is_open():
            return "gate error breaker open"
        return None

    def parser_open(self, runner: str | None) -> bool:
        return bool(self.enabled and runner and runner in self.parsers and self.parsers[runner].is_open())

    def snapshot(self) -> dict[str, Any]:
        out = {"hook_latency": self.hook_latency.to_dict(), "gate_errors": self.gate_errors.to_dict()}
        out.update({b.name: b.to_dict() for b in self.parsers.values()})
        return out

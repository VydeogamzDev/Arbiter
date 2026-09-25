"""Effort leases with hysteresis (spec §8.4).

An escalation takes a lease on a logical level for a number of calls (``reasoning.high_lease_calls``;
the most expensive tiers get ``expensive_tier_lease_calls``). While the lease holds, the level
doesn't drop. After it expires, de-escalating one level needs ``deescalate_progress_checkpoints``
consecutive calls with verified progress: de-escalation needs stronger evidence than escalation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class Lease:
    level: int = 0
    calls_left: int = 0
    progress_streak: int = 0


class LeaseBook:
    def __init__(self, high_lease_calls: int = 2, expensive_lease_calls: int = 1, deescalate_checkpoints: int = 2):
        self.high_lease_calls = high_lease_calls
        self.expensive_lease_calls = expensive_lease_calls
        self.deescalate_checkpoints = deescalate_checkpoints
        self._leases: dict[str, Lease] = {}
        self._lock = threading.Lock()

    def apply(self, key: str, wanted: int, progress: bool) -> tuple[int, str]:
        """Level to use for this call given the level the signals ask for. Returns (level, reason)."""
        with self._lock:
            ls = self._leases.setdefault(key, Lease())
            ls.progress_streak = ls.progress_streak + 1 if progress else 0
            if wanted > ls.level:
                ls.level = wanted
                ls.calls_left = self.expensive_lease_calls if wanted >= 4 else self.high_lease_calls
                return wanted, f"escalated to level {wanted}; lease for {ls.calls_left} call(s)"
            if ls.calls_left > 0:
                ls.calls_left -= 1
                return ls.level, f"lease holds level {ls.level} ({ls.calls_left} call(s) left)"
            if wanted < ls.level and ls.progress_streak >= self.deescalate_checkpoints:
                ls.level -= 1
                ls.progress_streak = 0
                return ls.level, f"de-escalated one level after {self.deescalate_checkpoints} progress checkpoints"
            if wanted < ls.level:
                return ls.level, "holding level until verified progress (hysteresis)"
            return ls.level, "steady"

    def reset(self, key: str) -> None:
        with self._lock:
            self._leases.pop(key, None)

"""Monotonic deadlines. Gating hooks carry one so nothing on the hook path waits past it."""

from __future__ import annotations

import time


class Deadline:
    def __init__(self, seconds: float) -> None:
        self.start = time.monotonic()
        self.at = self.start + max(0.0, seconds)

    @classmethod
    def never(cls) -> Deadline:
        return cls(10 * 365 * 86400.0)

    def remaining(self) -> float:
        return max(0.0, self.at - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= self.at

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.start) * 1000.0

    def sub(self, fraction: float) -> Deadline:
        """A child deadline covering ``fraction`` of the remaining time."""
        return Deadline(self.remaining() * fraction)

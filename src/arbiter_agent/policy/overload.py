"""Overload shedding (spec §15.2): low-value optimization goes first, before anything can delay
the host. Priority classes, highest first:

- ``integrity``: verification, the gate, facts, the audit log. Never shed.
- ``foreground``: routing and status for the current turn (status injection, retrieval warming).
- ``background``: learning and speculation (sensor shadow scoring, calibration data).

The level is recomputed from queue fill and the gating-hook latency on every check (cheap).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Source = Callable[[], tuple[int, int]]          # () -> (backlog, capacity)

SHED_BACKGROUND_FILL = 0.5
SHED_FOREGROUND_FILL = 0.85


class LoadShedder:
    def __init__(self, sources: dict[str, Source] | None = None, latency: Any = None) -> None:
        self.sources = dict(sources or {})
        self.latency = latency                  # LatencyBreaker (p95 vs budget), optional
        self.stats = {"shed_background": 0, "shed_foreground": 0}

    def level(self) -> int:
        """0 normal, 1 shed background, 2 shed background and foreground optimization."""
        fill = 0.0
        for fn in self.sources.values():
            try:
                backlog, cap = fn()
            except Exception:  # noqa: S112 - a broken source never raises the shed level
                continue
            if cap > 0:
                fill = max(fill, backlog / cap)
        lvl = 2 if fill >= SHED_FOREGROUND_FILL else 1 if fill >= SHED_BACKGROUND_FILL else 0
        if self.latency is not None:
            if self.latency.is_open():
                lvl = 2
            elif len(self.latency.lat) >= self.latency.min_samples and \
                    self.latency.p95() > 0.8 * self.latency.budget_ms:
                lvl = max(lvl, 1)
        return lvl

    def allow(self, klass: str) -> bool:
        if klass == "integrity":
            return True
        lvl = self.level()
        if klass == "background" and lvl >= 1:
            self.stats["shed_background"] += 1
            return False
        if klass == "foreground" and lvl >= 2:
            self.stats["shed_foreground"] += 1
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {"level": self.level(), **self.stats}

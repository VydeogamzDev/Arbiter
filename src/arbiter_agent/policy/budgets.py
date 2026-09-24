"""Hard controller budgets (spec §15.2, §15.5 level 7): per-session, per-turn caps on what the
controller may spend (sensor calls, injected tokens). A budget that's exhausted sheds the work;
it never delays the host."""

from __future__ import annotations

import threading
from typing import Any

DEFAULT_LIMITS = {"sensor_calls_per_turn": 4, "inject_tokens_per_turn": 600}


class Budgets:
    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._used: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self.stats = {"charged": 0, "refused": 0}

    def charge(self, session: str, kind: str, amount: int = 1) -> bool:
        limit = self.limits.get(kind)
        with self._lock:
            used = self._used.get((session, kind), 0)
            if limit is not None and used + amount > limit:
                self.stats["refused"] += 1
                return False
            self._used[(session, kind)] = used + amount
            self.stats["charged"] += 1
            return True

    def new_turn(self, session: str) -> None:
        with self._lock:
            for k in [k for k in self._used if k[0] == session and k[1].endswith("_per_turn")]:
                del self._used[k]

    def snapshot(self) -> dict[str, Any]:
        return {"limits": dict(self.limits), **self.stats}

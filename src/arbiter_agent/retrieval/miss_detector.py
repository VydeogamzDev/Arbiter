"""Retrieval miss detection (spec §11.6).

A miss means the context Arbiter suggested didn't contain what the work needed. Signals:
- the agent edits a file Arbiter never surfaced;
- a new error points at a file that was never surfaced;
- the agent searches for the same concept repeatedly.

Misses widen the next retrieval (``QueryContext.misses``) before any reasoning escalation, and are
reported to hosts through ``session_signals``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.retrieval.candidates import words

WINDOW_S = 1800.0
REPEAT_OVERLAP = 0.6


@dataclass
class _State:
    surfaced: set[str] = field(default_factory=set)
    queries: list[set[str]] = field(default_factory=list)
    misses: list[tuple[float, str]] = field(default_factory=list)


class MissDetector:
    def __init__(self) -> None:
        self._s: dict[str, _State] = {}
        self._lock = threading.Lock()

    def _st(self, sid: str) -> _State:
        return self._s.setdefault(sid, _State())

    def _miss(self, st: _State, why: str) -> None:
        st.misses.append((time.time(), why))

    def surfaced(self, sid: str, paths: list[str], query: str = "") -> None:
        with self._lock:
            st = self._st(sid)
            qw = set(words(query))
            if qw and sum(1 for q in st.queries[-5:] if len(q & qw) / max(1, len(q | qw)) >= REPEAT_OVERLAP) >= 2:
                self._miss(st, f"repeated search for the same concept: {' '.join(sorted(qw))[:60]}")
            if qw:
                st.queries.append(qw)
            st.surfaced.update(paths)

    def edited(self, sid: str, path: str) -> None:
        with self._lock:
            st = self._st(sid)
            if st.surfaced and path not in st.surfaced:
                self._miss(st, f"edited {path}, which retrieval never surfaced")
            st.surfaced.add(path)

    def error_refs(self, sid: str, paths: list[str]) -> None:
        with self._lock:
            st = self._st(sid)
            for p in paths:
                if st.surfaced and p not in st.surfaced:
                    self._miss(st, f"an error points at {p}, which retrieval never surfaced")

    def recent(self, sid: str, window_s: float = WINDOW_S) -> list[str]:
        cutoff = time.time() - window_s
        with self._lock:
            st = self._s.get(sid)
            return [w for t, w in st.misses if t >= cutoff] if st else []

    def signal(self, sid: str) -> dict[str, Any]:
        rec = self.recent(sid)
        return {"retrieval_miss": bool(rec), "misses": len(rec), "reasons": rec[-3:]}

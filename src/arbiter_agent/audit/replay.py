"""Deterministic session reducer (spec §6.1): tolerates duplicate, delayed, missing and
out-of-order events. The live daemon applies it incrementally; :func:`replay` rebuilds the
same state from the event log alone, which is how "replay reproduces reducer state" is tested.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    session_id: str
    client_id: str
    events: int = 0                     # primary (non-duplicate) events
    duplicates: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    turns: set[str] = field(default_factory=set)
    pending_tools: dict[str, int] = field(default_factory=dict)   # tool_use_id -> seq of pre_tool
    resolved_tools: int = 0
    orphan_results: int = 0             # tool results with no pre_tool seen (missing/out of order)
    stops: int = 0
    continued_stops: int = 0            # stops with stop_hook_active=true
    ended: bool = False
    unresolved_at_end: list[str] = field(default_factory=list)
    last_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id, "client_id": self.client_id, "events": self.events,
            "duplicates": self.duplicates, "by_type": dict(sorted(self.by_type.items())),
            "turns": sorted(self.turns), "pending_tools": sorted(self.pending_tools),
            "resolved_tools": self.resolved_tools, "orphan_results": self.orphan_results, "stops": self.stops,
            "continued_stops": self.continued_stops, "ended": self.ended,
            "unresolved_at_end": sorted(self.unresolved_at_end), "last_seq": self.last_seq,
        }


@dataclass(frozen=True)
class LogRow:
    seq: int
    client_id: str
    session_id: str | None
    event_type: str
    turn_id: str | None
    duplicate_of: int | None
    attrs: dict[str, Any]


class Reducer:
    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self._resolved_ids: dict[str, set[str]] = {}

    def apply(self, row: LogRow) -> None:
        if not row.session_id:
            return
        st = self.sessions.get(row.session_id)
        if st is None:
            st = self.sessions[row.session_id] = SessionState(row.session_id, row.client_id)
        st.last_seq = max(st.last_seq, row.seq)
        if row.duplicate_of is not None:
            st.duplicates += 1
            return
        st.events += 1
        st.by_type[row.event_type] = st.by_type.get(row.event_type, 0) + 1
        if row.turn_id:
            st.turns.add(row.turn_id)
        tool = row.attrs.get("tool_use_id")
        resolved = self._resolved_ids.setdefault(row.session_id, set())
        if row.event_type == "pre_tool" and tool:
            if tool not in resolved:
                st.pending_tools.setdefault(tool, row.seq)
        elif row.event_type in ("post_tool", "tool_result") and tool:
            if tool in st.pending_tools:
                del st.pending_tools[tool]
                st.resolved_tools += 1
            elif tool not in resolved:
                st.orphan_results += 1       # result arrived before (or without) its pre_tool
            resolved.add(tool)
        elif row.event_type == "stop":
            st.stops += 1
            if row.attrs.get("stop_hook_active"):
                st.continued_stops += 1
        elif row.event_type == "session_end":
            st.ended = True
            st.unresolved_at_end = sorted(st.pending_tools)

    def apply_many(self, rows: Iterable[LogRow]) -> Reducer:
        for r in rows:
            self.apply(r)
        return self

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {sid: st.to_dict() for sid, st in sorted(self.sessions.items())}


def row_from_sql(r: sqlite3.Row) -> LogRow:
    return LogRow(seq=r["seq"], client_id=r["client_id"], session_id=r["session_id"], event_type=r["event_type"],
                  turn_id=r["turn_id"], duplicate_of=r["duplicate_of"], attrs=json.loads(r["attrs_json"] or "{}"))


def replay(conn: sqlite3.Connection) -> Reducer:
    rows = conn.execute("SELECT seq, client_id, session_id, event_type, turn_id, duplicate_of, attrs_json "
                        "FROM event_log ORDER BY seq")
    return Reducer().apply_many(row_from_sql(r) for r in rows)

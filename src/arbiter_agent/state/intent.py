"""Immutable intent log (spec §6.3, §6.4): one row per user turn, never updated.

The text itself lives in the content-addressed blob table (already redacted at ingest). Quote
provenance checks (§6.8.1 step 2) run against these rows only.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from arbiter_agent.state.text_norm import norm, norm_loose

MAX_INTENT_CHARS = 20000


@dataclass(frozen=True)
class Intent:
    id: str            # "<session>#U<ordinal>"
    session_id: str
    ordinal: int
    goal_epoch: int
    text: str
    timestamp: float
    source: str

    @property
    def short_id(self) -> str:
        return f"U{self.ordinal}"


def put_text_blob(conn: sqlite3.Connection, text: str, keyer: Callable[[bytes], str], now: float) -> str:
    data = text.encode("utf-8")
    h = keyer(b"intent:" + data)
    conn.execute("INSERT OR IGNORE INTO blob(hash, size, truncated, full_size, created_at, data) VALUES (?,?,0,?,?,?)",
                 (h, len(data), len(data), now, data))
    return h


def append_intent(conn: sqlite3.Connection, session_id: str, ordinal: int, goal_epoch: int, text: str, source: str,
                  keyer: Callable[[bytes], str], now: float | None = None) -> Intent:
    now = now or time.time()
    text = (text or "")[:MAX_INTENT_CHARS]
    pointer = put_text_blob(conn, text, keyer, now)
    iid = f"{session_id}#U{ordinal}"
    conn.execute("INSERT INTO intent(id, thread_id, goal_epoch, raw_text_pointer, timestamp, supersedes_intent_id, "
                 "user_visible_hash, ordinal, source) VALUES (?,?,?,?,?,NULL,?,?,?)",
                 (iid, session_id, goal_epoch, pointer, now, keyer(norm(text).encode("utf-8")), ordinal, source))
    return Intent(iid, session_id, ordinal, goal_epoch, text, now, source)


def load_intents(conn: sqlite3.Connection, session_id: str, from_ordinal: int = 0) -> list[Intent]:
    rows = conn.execute(
        "SELECT i.id, i.ordinal, i.goal_epoch, i.timestamp, i.source, b.data FROM intent i "
        "LEFT JOIN blob b ON b.hash = i.raw_text_pointer WHERE i.thread_id = ? AND i.ordinal >= ? ORDER BY i.ordinal",
        (session_id, from_ordinal)).fetchall()
    out = []
    for r in rows:
        data = r[5]
        text = bytes(data).decode("utf-8", "replace") if data is not None else ""
        out.append(Intent(r[0], session_id, int(r[1] or 0), int(r[2] or 0), text, float(r[3] or 0), r[4] or ""))
    return out


def find_quote(quote: str, intents: list[Intent]) -> Intent | None:
    """The intent containing ``quote`` verbatim (modulo case, whitespace and quote style).
    Very short quotes are rejected: they can't carry provenance."""
    q = norm(quote).strip(" .,;:!?\"'")
    if len(q) < 8 or len(q.split()) < 2:
        return None
    for it in reversed(intents):
        if q in norm(it.text):
            return it
    ql = norm_loose(quote)
    for it in reversed(intents):
        if ql and ql in norm_loose(it.text):
            return it
    return None

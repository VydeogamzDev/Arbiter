"""Retention and storage caps (spec §16.3.3, §18.7).

- Raw payloads (blobs) expire ``raw_payload_retention_days`` after their session closed.
- Event rows expire ``ledger_and_metrics_retention_days`` after their session closed.
- If storage exceeds ``storage_cap_gb``, blobs of *closed* sessions are deleted oldest-first.
- Active-session evidence is never deleted. A session counts as closed when it has ended, or
  when it has had no events for ``idle_close_hours``.
Deletions on ``event_log`` pass through the retention gate (the table is otherwise append-only).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from arbiter_agent.state.store import storage_bytes

DAY = 86400.0


@dataclass(frozen=True)
class RetentionPolicy:
    raw_payload_days: float = 30
    rows_days: float = 180
    storage_cap_bytes: int = 2 * 1024**3
    idle_close_hours: float = 24


@dataclass
class RetentionReport:
    blobs_deleted: int = 0
    rows_deleted: int = 0
    cap_blobs_deleted: int = 0
    bytes_before: int = 0
    bytes_after: int = 0


_CLOSED_AT = "COALESCE(s.ended_at, CASE WHEN s.last_event_at < :idle_cutoff THEN s.last_event_at END)"


def _protected_pointers_sql() -> str:
    # Blobs still referenced by any session that is not closed, or by events without a session.
    return (f"SELECT DISTINCT e.payload_pointer FROM event_log e LEFT JOIN client_session s ON s.id = e.session_id "
            f"WHERE e.payload_pointer IS NOT NULL AND (s.id IS NULL OR {_CLOSED_AT} IS NULL "
            f"OR {_CLOSED_AT} >= :blob_cutoff)")


def run_retention(conn: sqlite3.Connection, db_path: Path, policy: RetentionPolicy,
                  now: float | None = None) -> RetentionReport:
    """Must run inside a write transaction (the Writer provides one)."""
    now = now or time.time()
    rep = RetentionReport(bytes_before=storage_bytes(db_path))
    params = {"idle_cutoff": now - policy.idle_close_hours * 3600,
              "blob_cutoff": now - policy.raw_payload_days * DAY,
              "row_cutoff": now - policy.rows_days * DAY}
    # 1. Expired raw payloads of closed sessions (unless still referenced by a live session).
    cur = conn.execute(
        f"DELETE FROM blob WHERE hash IN (SELECT e.payload_pointer FROM event_log e JOIN client_session s "
        f"ON s.id = e.session_id WHERE {_CLOSED_AT} IS NOT NULL AND {_CLOSED_AT} < :blob_cutoff) "
        f"AND hash NOT IN ({_protected_pointers_sql()})", params)
    rep.blobs_deleted = cur.rowcount
    # 2. Expired event rows of closed sessions, through the retention gate.
    conn.execute("UPDATE retention_gate SET open = 1 WHERE id = 1")
    try:
        cur = conn.execute(
            f"DELETE FROM event_log WHERE session_id IN (SELECT s.id FROM client_session s "
            f"WHERE {_CLOSED_AT} IS NOT NULL AND {_CLOSED_AT} < :row_cutoff) "
            f"AND seq NOT IN (SELECT duplicate_of FROM event_log WHERE duplicate_of IS NOT NULL "
            f"AND session_id NOT IN (SELECT s.id FROM client_session s WHERE {_CLOSED_AT} IS NOT NULL "
            f"AND {_CLOSED_AT} < :row_cutoff))", params)
        rep.rows_deleted = cur.rowcount
    finally:
        conn.execute("UPDATE retention_gate SET open = 0 WHERE id = 1")
    # 3. Storage cap: oldest closed-session blobs first, never active ones.
    if storage_bytes(db_path) > policy.storage_cap_bytes:
        protected = {r[0] for r in conn.execute(
            f"SELECT DISTINCT e.payload_pointer FROM event_log e LEFT JOIN client_session s ON s.id = e.session_id "
            f"WHERE e.payload_pointer IS NOT NULL AND (s.id IS NULL OR {_CLOSED_AT} IS NULL)", params)}
        candidates = conn.execute(
            f"SELECT b.hash, b.size FROM blob b JOIN event_log e ON e.payload_pointer = b.hash "
            f"JOIN client_session s ON s.id = e.session_id WHERE {_CLOSED_AT} IS NOT NULL "
            f"GROUP BY b.hash ORDER BY MIN({_CLOSED_AT}) ASC", params).fetchall()
        excess = storage_bytes(db_path) - policy.storage_cap_bytes
        freed = 0
        for h, size in candidates:
            if freed >= excess:
                break
            if h in protected:
                continue
            conn.execute("DELETE FROM blob WHERE hash = ?", (h,))
            freed += size
            rep.cap_blobs_deleted += 1
    rep.bytes_after = storage_bytes(db_path)
    return rep


def policy_from_config(cfg: object) -> RetentionPolicy:
    get = cfg.get  # type: ignore[attr-defined]
    return RetentionPolicy(
        raw_payload_days=float(get("privacy.raw_payload_retention_days", 30)),
        rows_days=float(get("privacy.ledger_and_metrics_retention_days", 180)),
        storage_cap_bytes=int(float(get("storage.storage_cap_gb", 2)) * 1024**3),
    )

"""Append-only event log writes (runs on the single writer thread).

Payloads are stored once in the content-addressed ``blob`` table, keyed by an HMAC of the
(already redacted) content. Oversized payloads are truncated with the full size and keyed
hash recorded (spec §16.3.3)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from arbiter_agent.audit.replay import LogRow, Reducer
from arbiter_agent.clients import event_dedupe
from arbiter_agent.clients.event_normalizer import NormalizedEvent, canonical_bytes
from arbiter_agent.privacy import sensitivity


@dataclass(frozen=True)
class AppendResult:
    seq: int | None            # None when dropped as an idempotent re-delivery
    duplicate_of: int | None
    payload_pointer: str | None
    truncated: bool


def encode_payload(payload: object, max_bytes: int, keyer: Callable[[bytes], str]) -> tuple[bytes, int, bool, str]:
    data = canonical_bytes(payload)
    full = len(data)
    truncated = False
    if full > max_bytes:
        truncated = True
        head = data[: max(0, max_bytes - 256)].decode("utf-8", "ignore")
        data = canonical_bytes({"_truncated": True, "_full_size": full, "_full_hmac": keyer(data), "head": head})
    return data, full, truncated, keyer(data)


def append_event(conn: sqlite3.Connection, ev: NormalizedEvent, *, redactions: int, max_payload_bytes: int,
                 keyer: Callable[[bytes], str], reducer: Reducer | None = None,
                 goal_epoch: int = 0, state_version: int = 0) -> AppendResult:
    if event_dedupe.windowed(ev):
        # Same payload in the previous window bucket and within the window: a redelivery.
        bucket = int(ev.received_at // event_dedupe.REDELIVERY_WINDOW_S)
        prev_key = ev.idempotency_key.rsplit(":w", 1)[0] + f":w{bucket - 1}"
        row = conn.execute("SELECT received_at FROM event_log WHERE idempotency_key = ?", (prev_key,)).fetchone()
        if row and ev.received_at - float(row[0]) < event_dedupe.REDELIVERY_WINDOW_S:
            return AppendResult(None, None, None, False)
    data, full, truncated, pointer = encode_payload(ev.payload, max_payload_bytes, keyer)
    conn.execute("INSERT OR IGNORE INTO blob(hash, size, truncated, full_size, created_at, data) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (pointer, len(data), int(truncated), full, ev.received_at, data))
    primary = None
    if ev.dedupe_key:
        row = conn.execute("SELECT seq FROM event_log WHERE dedupe_key = ? AND duplicate_of IS NULL "
                           "ORDER BY seq LIMIT 1", (ev.dedupe_key,)).fetchone()
        primary = row[0] if row else None
    cur = conn.execute(
        "INSERT OR IGNORE INTO event_log(client_id, client_profile_version, session_id, native_session_id, surface, "
        "thread_id, turn_id, goal_epoch, state_version, upstream_id, event_type, raw_hash, upstream_ts, received_at, "
        "payload_pointer, sensitivity_class, idempotency_key, dedupe_key, duplicate_of, ingest_channel, attrs_json) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ev.client_id, ev.profile_version, ev.session_id, ev.native_session_id, ev.surface, ev.turn_id,
         goal_epoch, state_version, ev.upstream_id, ev.event_type, ev.raw_hash, ev.upstream_ts, ev.received_at,
         pointer, sensitivity.classify(redactions, truncated), ev.idempotency_key, ev.dedupe_key, primary,
         ev.ingest_channel, json.dumps(ev.attrs, sort_keys=True, default=str)))
    if cur.rowcount == 0:
        return AppendResult(None, None, pointer, truncated)
    seq = int(cur.lastrowid or 0)
    if ev.session_id:
        _touch_session(conn, ev, keyer)
    if reducer is not None:
        reducer.apply(LogRow(seq, ev.client_id, ev.session_id, ev.event_type, ev.turn_id, primary, dict(ev.attrs)))
    return AppendResult(seq, primary, pointer, truncated)


def _touch_session(conn: sqlite3.Connection, ev: NormalizedEvent, keyer: Callable[[bytes], str]) -> None:
    cwd_hmac = keyer(ev.cwd.encode("utf-8")) if ev.cwd else None
    ended = ev.received_at if ev.event_type == "session_end" else None
    conn.execute(
        "INSERT INTO client_session(id, client_id, native_session_id, cwd_hmac, transcript_path, started_at, "
        "last_event_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET last_event_at = MAX(last_event_at, excluded.last_event_at), "
        "cwd_hmac = COALESCE(client_session.cwd_hmac, excluded.cwd_hmac), "
        "transcript_path = COALESCE(client_session.transcript_path, excluded.transcript_path), "
        "ended_at = COALESCE(excluded.ended_at, client_session.ended_at)",
        (ev.session_id, ev.client_id, ev.native_session_id, cwd_hmac, ev.transcript_path, ev.received_at,
         ev.received_at, ended))


def bump_scope_skip(conn: sqlite3.Connection, reason: str, now: float) -> None:
    conn.execute("INSERT INTO scope_skip(reason, count, last_at) VALUES (?, 1, ?) "
                 "ON CONFLICT(reason) DO UPDATE SET count = count + 1, last_at = excluded.last_at", (reason, now))

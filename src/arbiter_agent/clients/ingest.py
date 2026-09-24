"""Ingest pipeline: scope -> redact -> normalize -> append (spec §4.4.4, §16.3).

Order matters:
1. Project scope is decided first; excluded sessions are dropped with only a counter recorded.
2. Redaction runs before normalization, so raw hashes and stored payloads never contain secrets.
3. The append runs on the single writer thread, which also advances the live reducer in seq order.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from arbiter_agent.audit.event_log import AppendResult, append_event, bump_scope_skip
from arbiter_agent.audit.replay import Reducer
from arbiter_agent.clients import event_normalizer as en
from arbiter_agent.privacy.redaction import Redactor
from arbiter_agent.privacy.scope import ProjectScope
from arbiter_agent.state.writer import ReadOnlyDegraded, Writer, WriterBusy


@dataclass
class IngestResult:
    status: str                  # stored | duplicate | redelivered | excluded | pending | failed
    seq: int | None = None
    duplicate_of: int | None = None
    reason: str | None = None
    event_type: str | None = None
    redactions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class Ingestor:
    def __init__(self, *, key: bytes, writer: Writer, reducer: Reducer, scope: ProjectScope,
                 max_payload_bytes: int = 512 * 1024, redaction_enabled: bool = True) -> None:
        self.key = key
        self.writer = writer
        self.reducer = reducer
        self.scope = scope
        self.max_payload_bytes = max_payload_bytes
        self.redaction_enabled = redaction_enabled
        self._stats_lock = threading.Lock()
        self.stats: dict[str, int] = {}
        # Called as fn(event, result) after a successful append, on the ingesting thread.
        self.observers: list[Callable[[en.NormalizedEvent, IngestResult], None]] = []

    def keyer(self, data: bytes) -> str:
        return hmac.new(self.key, data, hashlib.sha256).hexdigest()

    def _count(self, status: str) -> None:
        with self._stats_lock:
            self.stats[status] = self.stats.get(status, 0) + 1

    def _excluded(self, reason: str) -> IngestResult:
        now = time.time()
        try:
            self.writer.submit(lambda c: bump_scope_skip(c, reason, now))
        except (ReadOnlyDegraded, WriterBusy):
            pass
        self._count("excluded")
        return IngestResult("excluded", reason=reason)

    def _append(self, ev: en.NormalizedEvent, redactions: int, wait: float) -> IngestResult:
        def job(conn: Any) -> AppendResult:
            return append_event(conn, ev, redactions=redactions, max_payload_bytes=self.max_payload_bytes,
                                keyer=self.keyer, reducer=self.reducer)

        try:
            fut = self.writer.submit(job, timeout=min(wait, 0.5))
        except (ReadOnlyDegraded, WriterBusy) as exc:
            self._count("failed")
            return IngestResult("failed", reason=type(exc).__name__, event_type=ev.event_type)
        try:
            res = fut.result(timeout=wait)
        except FutureTimeout:
            self._count("pending")  # still written; the caller just stops waiting (fail open)
            return IngestResult("pending", event_type=ev.event_type, redactions=redactions)
        except Exception as exc:
            self._count("failed")
            return IngestResult("failed", reason=f"{type(exc).__name__}: {exc}"[:200], event_type=ev.event_type)
        if res.seq is None:
            self._count("redelivered")
            return IngestResult("redelivered", event_type=ev.event_type)
        status = "duplicate" if res.duplicate_of else "stored"
        self._count(status)
        result = IngestResult(status, seq=res.seq, duplicate_of=res.duplicate_of, event_type=ev.event_type,
                              redactions=redactions)
        for fn in self.observers:
            try:
                fn(ev, result)
            except Exception:  # an observer must never break ingestion
                self._count("observer_error")
        return result

    def ingest_hook(self, client: str, payload: Any, *, surface: str, event_hint: str | None = None,
                    channel: str | None = None, wait: float = 1.0) -> IngestResult:
        if not isinstance(payload, dict):
            self._count("failed")
            return IngestResult("failed", reason="payload must be an object")
        decision = self.scope.decide(payload.get("cwd") if isinstance(payload.get("cwd"), str) else None)
        if not decision.in_scope:
            return self._excluded(decision.reason)
        red = Redactor(self.key, enabled=self.redaction_enabled)
        clean = red.redact(payload)
        try:
            ev = en.normalize_hook(client, clean, surface=surface, keyer=self.keyer, event_hint=event_hint,
                                   channel=channel)
        except en.NormalizationError as exc:
            self._count("failed")
            return IngestResult("failed", reason=str(exc))
        return self._append(ev, red.total, wait)

    def ingest_transcript_record(self, client: str, record: dict[str, Any], *, session_id: str | None,
                                 record_id: str, record_type: str, tool_use_id: str | None = None,
                                 cwd: str | None = None, transcript_path: str | None = None,
                                 extra_attrs: dict[str, Any] | None = None, wait: float = 5.0) -> IngestResult:
        red = Redactor(self.key, enabled=self.redaction_enabled)
        clean = red.redact(record)
        ev = en.normalize_transcript_record(client, clean, session_id=session_id, record_id=record_id,
                                            record_type=record_type, keyer=self.keyer, tool_use_id=tool_use_id,
                                            cwd=cwd, transcript_path=transcript_path, extra_attrs=extra_attrs)
        return self._append(ev, red.total, wait)

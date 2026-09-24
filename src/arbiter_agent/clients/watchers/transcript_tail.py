"""Offset-tailing transcript watcher (decision 0020).

- Reads only new complete lines from a saved byte offset (live Codex rollouts exceed 1 GB).
- A file that's large when first seen is followed in *tail mode*: its metadata line is parsed
  for context, then reading starts at the end, so setup never back-fills gigabytes of history.
- Detects truncation/rotation via a file identity fingerprint and restarts from 0; idempotency
  keys make any re-read harmless.
- Project scope is decided from the session metadata (cwd) *before* any content is ingested;
  excluded files are skipped entirely.
- Parse context (session id, cwd) is persisted with the offset so restarts resume correctly.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from arbiter_agent.clients.ingest import Ingestor
from arbiter_agent.clients.watchers.parsers import PARSERS, TranscriptRecord
from arbiter_agent.state.writer import Writer

__all__ = ["PARSERS", "TranscriptRecord", "TranscriptTailer", "WatcherManager"]

READ_CHUNK = 1024 * 1024
TAIL_MODE_BYTES = 20 * 1024 * 1024


def file_identity(st: os.stat_result) -> str:
    return f"{getattr(st, 'st_ino', 0)}:{getattr(st, 'st_birthtime', st.st_ctime):.0f}"


class TranscriptTailer:
    def __init__(self, *, client: str, path: Path, parser: str, ingestor: Ingestor, writer: Writer,
                 cwd_hint: str | None = None, tail_mode_bytes: int = TAIL_MODE_BYTES) -> None:
        if parser not in PARSERS:
            raise ValueError(f"unknown transcript parser '{parser}'")
        self.client = client
        self.path = path
        self.parser_name = parser
        self.parser_version, self.parse = PARSERS[parser]
        self.ingestor = ingestor
        self.writer = writer
        self.cwd_hint = cwd_hint
        self.tail_mode_bytes = tail_mode_bytes
        self.source_id = f"{client}:{str(path.resolve()).replace(os.sep, '/').lower()}"
        self.offset, self.identity, self.ctx, self.known = self._load_offset()
        self.errors = 0
        self.records = 0
        self.excluded = False
        self.last_poll_ok: float | None = None

    def _load_offset(self) -> tuple[int, str | None, dict[str, Any], bool]:
        def job(conn: Any) -> tuple[int, str | None, dict[str, Any], bool]:
            row = conn.execute("SELECT offset, file_identity, parser_version, context_json FROM watcher_offset "
                               "WHERE source_id = ?", (self.source_id,)).fetchone()
            if row and row["parser_version"] == self.parser_version:
                return int(row["offset"]), row["file_identity"], json.loads(row["context_json"] or "{}"), True
            return 0, None, {}, False

        return self.writer.run(job)

    def _save_offset(self) -> None:
        now = time.time()
        vals = (self.source_id, str(self.path), self.identity, self.offset, self.parser_name, self.parser_version,
                now, json.dumps(self.ctx))
        self.writer.run(lambda c: c.execute(
            "INSERT INTO watcher_offset(source_id, path, file_identity, offset, parser, parser_version, updated_at, "
            "context_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
            "file_identity = excluded.file_identity, offset = excluded.offset, parser = excluded.parser, "
            "parser_version = excluded.parser_version, updated_at = excluded.updated_at, "
            "context_json = excluded.context_json", vals))

    def _in_scope(self, cwd: str | None) -> bool:
        cwd = cwd or self.ctx.get("cwd") or self.cwd_hint
        return self.ingestor.scope.decide(cwd).in_scope if cwd else True

    def _enter_tail_mode(self, size: int) -> None:
        """Parse only the first line for context, then continue from the end of the file."""
        with open(self.path, "rb") as f:
            first = f.readline()
        try:
            self.parse(first.decode("utf-8", "replace"), self.ctx)
        except (ValueError, KeyError, TypeError):
            pass
        self.offset = size
        self.ctx["tail_mode_from"] = size

    def poll(self) -> int:
        """Ingest any new complete lines. Returns the number of records ingested."""
        if self.cwd_hint is not None and not self._in_scope(self.cwd_hint):
            self.excluded = True
            return 0
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return 0
        ident = file_identity(st)
        if self.identity is not None and (ident != self.identity or st.st_size < self.offset):
            self.offset, self.ctx = 0, {}  # rotated or truncated
        elif not self.known and self.offset == 0 and st.st_size > self.tail_mode_bytes:
            self._enter_tail_mode(st.st_size)
            if not self._in_scope(None):
                self.excluded = True
        self.known = True
        self.identity = ident
        n = 0
        if st.st_size > self.offset:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                pending = b""
                while True:
                    chunk = f.read(READ_CHUNK)
                    if not chunk:
                        break
                    pending += chunk
                    *lines, pending = pending.split(b"\n")
                    for raw in lines:
                        self.offset += len(raw) + 1
                        n += self._ingest_line(raw)
                # a partial trailing line stays unconsumed until it is completed
        self._save_offset()
        self.last_poll_ok = time.time()
        return n

    def _ingest_line(self, raw: bytes) -> int:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return 0
        try:
            recs = self.parse(line, self.ctx)
        except (ValueError, KeyError, TypeError, AttributeError):
            self.errors += 1
            return 0
        n = 0
        for r in recs:
            if not self._in_scope(r.cwd):
                self.excluded = True
                continue
            self.excluded = False
            self.ingestor.ingest_transcript_record(
                self.client, r.payload, session_id=r.session_id or self.ctx.get("session_id"),
                record_id=r.record_id, record_type=r.record_type, tool_use_id=r.tool_use_id,
                cwd=r.cwd or self.ctx.get("cwd") or self.cwd_hint, transcript_path=str(self.path),
                extra_attrs=r.attrs)
            self.records += 1
            n += 1
        return n


class WatcherManager:
    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self._tailers: dict[str, TranscriptTailer] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="arbiter-watchers", daemon=True)
        self.discovery: Any = None  # set by the daemon (clients/watchers/discovery.py)

    def add(self, tailer: TranscriptTailer) -> None:
        with self._lock:
            self._tailers.setdefault(tailer.source_id, tailer)

    def has(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._tailers

    def start(self) -> None:
        self._thread.start()

    def poll_all(self) -> int:
        if self.discovery is not None:
            try:
                self.discovery.scan(self)
            except Exception:
                pass
        with self._lock:
            tailers = list(self._tailers.values())
        total = 0
        for t in tailers:
            try:
                total += t.poll()
            except Exception:  # one bad source must not stop the others
                t.errors += 1
        return total

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll_all()

    def stop(self) -> None:
        self._stop.set()

    def describe(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"source": t.source_id, "client": t.client, "parser": t.parser_name, "offset": t.offset,
                     "records": t.records, "errors": t.errors, "excluded": t.excluded,
                     "last_poll_ok": t.last_poll_ok} for t in self._tailers.values()]

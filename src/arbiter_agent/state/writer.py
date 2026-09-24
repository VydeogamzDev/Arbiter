"""Single-writer queue (spec §16.1): one thread owns the only write connection.

Jobs are callables ``fn(conn) -> result`` run inside ``BEGIN IMMEDIATE ... COMMIT``. The queue
is bounded; a full queue raises instead of blocking a hook indefinitely."""

from __future__ import annotations

import queue
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, TypeVar

from arbiter_agent.state.store import connect

T = TypeVar("T")


class ReadOnlyDegraded(RuntimeError):
    """The store is in read-only degraded mode (failed migration)."""


class WriterBusy(RuntimeError):
    """The bounded write queue is full."""


_STOP = object()


class Writer:
    def __init__(self, db: Path, *, capacity: int = 1024, degraded: bool = False) -> None:
        self.db = db
        self.degraded = degraded
        self._q: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self._thread = threading.Thread(target=self._run, name="arbiter-writer", daemon=True)
        self._conn: sqlite3.Connection | None = None
        self.jobs_done = 0
        self._started = False

    def start(self) -> Writer:
        if not self._started:
            self._started = True
            self._thread.start()
        return self

    def submit(self, fn: Callable[[sqlite3.Connection], T], timeout: float = 1.0) -> Future[T]:
        if self.degraded:
            raise ReadOnlyDegraded("store is read-only (degraded after failed migration)")
        fut: Future[T] = Future()
        try:
            self._q.put((fn, fut), timeout=timeout)
        except queue.Full as exc:
            raise WriterBusy("write queue full") from exc
        return fut

    def run(self, fn: Callable[[sqlite3.Connection], T], timeout: float = 5.0) -> T:
        return self.submit(fn, timeout=timeout).result(timeout=timeout)

    def _run(self) -> None:
        self._conn = connect(self.db)
        conn = self._conn
        while True:
            item = self._q.get()
            if item is _STOP:
                break
            fn, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = fn(conn)
                conn.execute("COMMIT")
                self.jobs_done += 1
                fut.set_result(result)
            except BaseException as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                fut.set_exception(exc)
        conn.close()

    def stop(self, timeout: float = 10.0) -> None:
        """Drain queued writes, then close (spec §16.5 drain-and-restart)."""
        if not self._started:
            return
        self._q.put(_STOP)
        self._thread.join(timeout=timeout)

    @property
    def backlog(self) -> int:
        return self._q.qsize()

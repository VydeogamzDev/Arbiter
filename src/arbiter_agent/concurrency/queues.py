"""Bounded priority work queue with one worker thread.

- Full queue: the lowest-priority work is shed (counted) instead of blocking the producer.
- Jobs may carry a :class:`CancelToken`; cancelled jobs are skipped when dequeued.
- Keys coalesce duplicates: re-submitting a pending key replaces nothing and returns False.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
from collections.abc import Callable
from typing import Any

from arbiter_agent.concurrency.cancellation import CancelToken
from arbiter_agent.concurrency.priorities import Priority

log = logging.getLogger("arbiter.queue")


class WorkQueue:
    def __init__(self, name: str, capacity: int = 256) -> None:
        self.name = name
        self.capacity = capacity
        self._heap: list[tuple[int, int, str | None, Callable[[], Any], CancelToken | None]] = []
        self._keys: set[str] = set()
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._stop = False
        self._thread: threading.Thread | None = None
        self.stats = {"done": 0, "shed": 0, "cancelled": 0, "errors": 0, "coalesced": 0}
        self._busy = False

    def start(self) -> WorkQueue:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f"arbiter-{self.name}", daemon=True)
            self._thread.start()
        return self

    def submit(self, fn: Callable[[], Any], priority: Priority = Priority.TELEMETRY, key: str | None = None,
               token: CancelToken | None = None) -> bool:
        with self._cv:
            if key is not None and key in self._keys:
                self.stats["coalesced"] += 1
                return False
            if len(self._heap) >= self.capacity:
                worst = max(self._heap)
                if worst[0] <= int(priority):
                    self.stats["shed"] += 1
                    return False
                self._heap.remove(worst)
                heapq.heapify(self._heap)
                if worst[2] is not None:
                    self._keys.discard(worst[2])
                self.stats["shed"] += 1
            heapq.heappush(self._heap, (int(priority), next(self._seq), key, fn, token))
            if key is not None:
                self._keys.add(key)
            self._cv.notify()
            return True

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._heap and not self._stop:
                    self._cv.wait()
                if self._stop and not self._heap:
                    return
                _, _, key, fn, token = heapq.heappop(self._heap)
                if key is not None:
                    self._keys.discard(key)
                self._busy = True
            try:
                if token is not None and token.cancelled:
                    self.stats["cancelled"] += 1
                    continue
                fn()
                self.stats["done"] += 1
            except Exception:
                self.stats["errors"] += 1
                log.exception("background job failed", extra={"fields": {"queue": self.name}})
            finally:
                with self._cv:
                    self._busy = False
                    self._cv.notify_all()

    def drain(self, timeout: float = 10.0) -> bool:
        """Wait until the queue is empty and idle (tests, shutdown)."""
        with self._cv:
            return self._cv.wait_for(lambda: not self._heap and not self._busy, timeout=timeout)

    def stop(self, timeout: float = 5.0) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout)

    @property
    def backlog(self) -> int:
        return len(self._heap)

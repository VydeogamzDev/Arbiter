"""Cancellation tokens. Work tied to a goal epoch is cancelled when the epoch moves on (§6.3)."""

from __future__ import annotations

import threading


class CancelToken:
    def __init__(self) -> None:
        self._ev = threading.Event()
        self.reason: str | None = None

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self._ev.set()

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()

    def wait(self, timeout: float) -> bool:
        return self._ev.wait(timeout)


class EpochCancel:
    """Hands out tokens per (session, epoch); bumping an epoch cancels the older tokens."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: dict[str, tuple[int, CancelToken]] = {}

    def token(self, session_id: str, epoch: int) -> CancelToken:
        with self._lock:
            cur = self._tokens.get(session_id)
            if cur and cur[0] == epoch:
                return cur[1]
            if cur and cur[0] < epoch:
                cur[1].cancel(f"goal epoch {cur[0]} superseded by {epoch}")
            tok = CancelToken()
            if not cur or cur[0] <= epoch:
                self._tokens[session_id] = (epoch, tok)
            else:
                tok.cancel("stale epoch")
            return tok

    def bump(self, session_id: str, epoch: int) -> None:
        self.token(session_id, epoch)

"""Daemon client used by shims and the CLI. Stdlib only; every failure is fail-open-friendly.

``DaemonClient.request`` raises :class:`DaemonUnavailable` for anything that means "proceed
without Arbiter" (not running, auth failure, incompatible version, timeout). Callers decide how
to fail open; hooks never block the host agent (spec §4.4.5).
"""

from __future__ import annotations

import itertools
import json
import time
from typing import Any

from arbiter_agent.daemon import ipc, protocol
from arbiter_agent.daemon.auth import read_token
from arbiter_agent.paths import ArbiterPaths, get_paths


class DaemonUnavailable(RuntimeError):
    def __init__(self, reason: str, *, action: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.action = action


class DaemonError(DaemonUnavailable):
    """The daemon answered, but the request failed (bad params, no session...). Subclasses
    DaemonUnavailable so fail-open callers keep working; never triggers a relaunch."""

    def __init__(self, error: str, method: str = "") -> None:
        super().__init__(f"{method} error: {error}" if method else error, action="server_error")
        self.error = error


def read_record(paths: ArbiterPaths) -> dict[str, Any]:
    try:
        data = json.loads(paths.daemon_record.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class DaemonClient:
    def __init__(self, paths: ArbiterPaths | None = None, component: str = "cli") -> None:
        self.paths = paths or get_paths()
        self.component = component
        self._conn: Any = None
        self._ids = itertools.count(1)
        self.server_info: dict[str, Any] = {}

    def _open(self, timeout: float) -> None:
        token = read_token(self.paths.token_file)
        if token is None:
            raise DaemonUnavailable("no ipc token (daemon never started)")
        record = read_record(self.paths)
        try:
            conn = ipc.connect(record.get("endpoint"), self.paths.pipe_address, token, timeout=timeout)
        except ipc.IPCAuthError as exc:
            raise DaemonUnavailable(f"auth failed: {exc}") from exc
        except ipc.IPCUnavailable as exc:
            raise DaemonUnavailable(f"unreachable: {exc}") from exc
        try:
            reply = ipc.call(conn, {"id": 0, "method": "hello", "params": protocol.hello_params(self.component)},
                             timeout)
        except (TimeoutError, OSError, EOFError, protocol.ProtocolError) as exc:
            conn.close()
            raise DaemonUnavailable(f"hello failed: {exc}") from exc
        info = reply.get("result") or {}
        if not info.get("compatible"):
            conn.close()
            raise DaemonUnavailable("incompatible daemon", action=info.get("action"))
        self._conn = conn
        self.server_info = info

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 1.0) -> Any:
        deadline = time.monotonic() + timeout
        if self._conn is None:
            self._open(timeout)
        remaining = max(0.01, deadline - time.monotonic())
        try:
            reply = ipc.call(self._conn, {"id": next(self._ids), "method": method, "params": params or {}}, remaining)
        except (TimeoutError, OSError, EOFError, protocol.ProtocolError) as exc:
            self.close()
            raise DaemonUnavailable(f"{method} failed: {exc}") from exc
        if not reply.get("ok"):
            raise DaemonError(str(reply.get("error")), method)
        return reply.get("result")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def __enter__(self) -> DaemonClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

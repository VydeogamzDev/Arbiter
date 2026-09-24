"""Loopback HTTP endpoint for client ``http`` hooks (decision 0021, spec §18.8).

``POST /hook/<client>/<event>`` with header ``X-Arbiter-Token``; body is the hook JSON.
The response body is the client decision object (``{}`` = no decision). Any internal failure
still answers ``{}`` quickly: hooks fail open. ``GET /health`` answers ``{"ok": true}``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from arbiter_agent.daemon.auth import HOOK_TOKEN_HEADER, tokens_equal

MAX_BODY = 8 * 1024 * 1024
HookHandler = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler], token: bytes,
                 on_hook: HookHandler) -> None:
        super().__init__(addr, handler)
        self.token = token
        self.on_hook = on_hook
        self.stats = {"ok": 0, "unauthorized": 0, "bad_request": 0, "errors": 0}


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a: object) -> None:
        pass

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {})

    def do_POST(self) -> None:
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) != 3 or parts[0] != "hook":
            self.server.stats["bad_request"] += 1
            self._send(404, {})
            return
        if not tokens_equal(self.headers.get(HOOK_TOKEN_HEADER), self.server.token):
            self.server.stats["unauthorized"] += 1
            self._send(401, {})
            return
        try:
            n = int(self.headers.get("content-length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.server.stats["bad_request"] += 1
            self._send(413, {})
            return
        raw = self.rfile.read(n)
        if parts[2] == "ArbiterDoctorPing":  # authenticated liveness check; nothing is stored
            self._send(200, {"pong": True})
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self.server.stats["bad_request"] += 1
            self._send(200, {})  # malformed hook input: fail open
            return
        try:
            out = self.server.on_hook(parts[1], parts[2], body if isinstance(body, dict) else {})
            self.server.stats["ok"] += 1
        except Exception:
            self.server.stats["errors"] += 1
            out = {}
        self._send(200, out)


class HookHTTPServer:
    def __init__(self, port: int, token: bytes, on_hook: HookHandler) -> None:
        self._srv = _Server(("127.0.0.1", port), _Handler, token, on_hook)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.2},
                                        name="arbiter-http-hooks", daemon=True)
        self.started_at = time.time()

    def start(self) -> HookHTTPServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._srv.stats)

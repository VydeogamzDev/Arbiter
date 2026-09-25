"""A minimal MCP stdio client for adopted servers (spec §10.7).

**No scope expansion.** The server is spawned with exactly the launch spec from the client's
original config entry (command, args, env overlay, cwd), on top of the environment the client gave
Arbiter's own MCP server: the same process the client would have started. The gateway offers the
server no roots, sampling or elicitation of its own; server-to-client requests are refused.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

PROTOCOL = "2025-06-18"


@dataclass(frozen=True)
class LaunchSpec:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> LaunchSpec:
        """A client MCP entry (Codex TOML table, Claude/Cursor JSON, ...) -> launch spec (stdio only)."""
        if entry.get("url") or entry.get("httpUrl") or entry.get("serverUrl"):
            raise ValueError("remote (HTTP/SSE) servers can't be proxied yet; only stdio servers can be adopted")
        cmd = entry.get("command")
        if isinstance(cmd, list):             # some clients store the full argv in command
            argv = [str(x) for x in cmd]
            cmd, args = argv[0], argv[1:] + [str(a) for a in entry.get("args") or []]
        else:
            args = [str(a) for a in entry.get("args") or []]
        if not cmd:
            raise ValueError("entry has no command")
        env = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
        return cls(str(cmd), args, env, entry.get("cwd"))


class UpstreamError(RuntimeError):
    pass


class Upstream:
    def __init__(self, name: str, spec: LaunchSpec, timeout_s: float = 30.0) -> None:
        self.name, self.spec, self.timeout_s = name, spec, timeout_s
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next = 0
        self.tools: list[dict[str, Any]] = []

    def start(self) -> Upstream:
        env = {**os.environ, **self.spec.env}
        self._proc = subprocess.Popen([self.spec.command, *self.spec.args], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                      encoding="utf-8", env=env, cwd=self.spec.cwd, bufsize=1)
        self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                     "clientInfo": {"name": "arbiter-gateway", "version": "1"}})
        self._notify("notifications/initialized")
        self.tools = list(self._request("tools/list", {}).get("tools") or [])
        return self

    def _write(self, obj: dict[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise UpstreamError(f"{self.name}: server isn't running")
            self._next += 1
            mid = f"gw-{self._next}"
            self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            deadline = time.monotonic() + self.timeout_s
            assert self._proc.stdout is not None
            while time.monotonic() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    raise UpstreamError(f"{self.name}: server closed the connection")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == mid and "method" not in msg:
                    if "error" in msg:
                        raise UpstreamError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                    return dict(msg.get("result") or {})
                if "method" in msg and "id" in msg:      # server -> client request: nothing is offered
                    self._write({"jsonrpc": "2.0", "id": msg["id"],
                                 "error": {"code": -32601, "message": "not supported by the Arbiter gateway"}})
            raise UpstreamError(f"{self.name}: {method} timed out")

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": tool, "arguments": arguments})

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
            self._proc = None

"""Fake host client.

Payload shapes follow what the M0 spikes observed (decisions 0015, 0017):
- ``codex``: ``session_id``, ``turn_id``, ``transcript_path``, ``cwd``, ``model``, ``permission_mode``;
  shell tool ``Bash``; ``tool_response`` is stdout text.
- ``claude_code``: ``session_id``, ``prompt_id``, ``transcript_path``, ``cwd``, ``permission_mode``;
  shell tool ``PowerShell`` on Windows; ``tool_response`` is ``{stdout, stderr, interrupted}``.

Transports: ``mcp`` (spawns ``arbiter mcp`` and calls ``arbiter_hook``, as Codex ``mcp_tool``
hooks do), ``http`` (POSTs like Claude Code ``http`` hooks), ``command`` (runs ``arbiter hook``).
Transcripts are written in the ``fake_v1`` format; tool results reuse the hook's ``tool_use_id``
so cross-surface dedupe can be exercised.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.daemon.auth import HOOK_TOKEN_HEADER, read_token
from arbiter_agent.daemon.client import read_record
from arbiter_agent.paths import ArbiterPaths


@dataclass
class HookCall:
    event: str
    response: dict[str, Any]
    latency_ms: float
    ok: bool = True
    error: str | None = None


@dataclass
class FakeHost:
    paths: ArbiterPaths
    client: str = "codex"
    transport: str = "mcp"
    cwd: str = field(default_factory=lambda: str(Path.cwd()))
    transcript_dir: Path | None = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timeout: float = 5.0
    autostart: bool = True
    calls: list[HookCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._mcp: subprocess.Popen[str] | None = None
        self._mcp_id = 0
        self._turn = 0
        self._turn_id = ""
        self.transcript_path = (self.transcript_dir or self.paths.data) / f"fake-{self.client}-{self.session_id}.jsonl"
        self._record_n = 0
        self.env = dict(os.environ)
        if not self.autostart:
            self.env["ARBITER_NO_AUTOSTART"] = "1"

    # ------------------------------------------------------------ transports
    def _base_argv(self) -> list[str]:
        argv = [sys.executable, "-m", "arbiter_agent"]
        if self.paths.root is not None:
            argv += ["--home", str(self.paths.root)]
        return argv

    def _mcp_rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._mcp is None:
            self._mcp = subprocess.Popen(self._base_argv() + ["mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=self.env)
            self._mcp_id += 1
            self._write_mcp({"jsonrpc": "2.0", "id": self._mcp_id, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": f"fake-{self.client}", "version": "0"}}})
            self._read_mcp()
            self._write_mcp({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._mcp_id += 1
        self._write_mcp({"jsonrpc": "2.0", "id": self._mcp_id, "method": method, "params": params})
        return self._read_mcp()

    def _write_mcp(self, obj: dict[str, Any]) -> None:
        assert self._mcp and self._mcp.stdin
        self._mcp.stdin.write(json.dumps(obj) + "\n")
        self._mcp.stdin.flush()

    def _read_mcp(self) -> dict[str, Any]:
        assert self._mcp and self._mcp.stdout
        line = self._mcp.stdout.readline()
        if not line:
            raise RuntimeError("mcp shim exited")
        return json.loads(line)

    def mcp_tool(self, name: str, arguments: dict[str, Any] | None = None) -> tuple[str, bool]:
        """Call an Arbiter MCP tool as the agent would: (text, is_error)."""
        reply = self._mcp_rpc("tools/call", {"name": name, "arguments": arguments or {}})
        result = reply.get("result") or {}
        content = result.get("content") or [{}]
        return str(content[0].get("text", "")), bool(result.get("isError"))

    def send_hook(self, event: str, payload: dict[str, Any]) -> HookCall:
        payload = {"hook_event_name": event, **payload}
        t = time.perf_counter()
        try:
            if self.transport == "mcp":
                reply = self._mcp_rpc("tools/call", {"name": "arbiter_hook",
                                                     "arguments": {"client": self.client, **payload}})
                text = reply["result"]["content"][0]["text"]
                response = json.loads(text) if text else {}
            elif self.transport == "http":
                rec = read_record(self.paths)
                token = read_token(self.paths.hook_token_file) or b""
                port = (rec.get("http") or {}).get("port")
                if not port:
                    raise RuntimeError("no http hook endpoint")
                req = urllib.request.Request(f"http://127.0.0.1:{port}/hook/{self.client}/{event}",
                                             data=json.dumps(payload).encode(), method="POST",
                                             headers={"content-type": "application/json",
                                                      HOOK_TOKEN_HEADER: token.decode()})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 - loopback http only
                    response = json.loads(r.read() or b"{}")
            elif self.transport == "command":
                out = subprocess.run(self._base_argv() + ["hook", self.client, event], input=json.dumps(payload),
                                     capture_output=True, text=True, timeout=self.timeout, encoding="utf-8",
                                     env=self.env)
                response = json.loads(out.stdout) if out.stdout.strip() else {}
            else:
                raise ValueError(f"unknown transport {self.transport}")
            call = HookCall(event, response, (time.perf_counter() - t) * 1000)
        except (OSError, RuntimeError, ValueError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
            call = HookCall(event, {}, (time.perf_counter() - t) * 1000, ok=False, error=str(exc))
        self.calls.append(call)
        return call

    # ------------------------------------------------------------ payload shapes
    def _common(self) -> dict[str, Any]:
        base = {"session_id": self.session_id, "transcript_path": str(self.transcript_path), "cwd": self.cwd,
                "permission_mode": "default"}
        if self.client == "claude_code":
            base["prompt_id"] = self._turn_id
        else:
            base["turn_id"] = self._turn_id
            base["model"] = "fake-model"
        return base

    def _shell_tool(self) -> str:
        return "PowerShell" if self.client == "claude_code" else "Bash"

    def _transcript(self, obj: dict[str, Any]) -> None:
        self._record_n += 1
        rec = {"id": f"r{self._record_n}", "session_id": self.session_id, "cwd": self.cwd, **obj}
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    # ------------------------------------------------------------ scenario steps
    def session_start(self, source: str = "startup") -> HookCall:
        self._transcript({"type": "session_meta", "originator": f"fake_{self.client}"})
        return self.send_hook("SessionStart", {**self._common(), "source": source})

    def prompt(self, text: str) -> HookCall:
        self._turn += 1
        self._turn_id = f"turn-{self._turn}-{uuid.uuid4().hex[:6]}"
        self._transcript({"type": "user_message", "text": text})
        return self.send_hook("UserPromptSubmit", {**self._common(), "prompt": text})

    def tool(self, command: str, stdout: str = "", exit_code: int = 0, *, emit_post: bool = True,
             emit_pre: bool = True) -> str:
        tool_use_id = f"call_{uuid.uuid4().hex[:10]}"
        tool_input = {"command": command}
        if emit_pre:
            self.send_hook("PreToolUse", {**self._common(), "tool_name": self._shell_tool(),
                                          "tool_input": tool_input, "tool_use_id": tool_use_id})
        response: Any = stdout if self.client != "claude_code" else {"stdout": stdout, "stderr": "",
                                                                     "interrupted": False}
        if emit_post:
            self.send_hook("PostToolUse", {**self._common(), "tool_name": self._shell_tool(),
                                           "tool_input": tool_input, "tool_response": response,
                                           "tool_use_id": tool_use_id})
        self._transcript({"type": "tool_result", "tool_use_id": tool_use_id, "tool_name": self._shell_tool(),
                          "exit_code": exit_code, "stdout": stdout})
        return tool_use_id

    def stop(self, last_message: str, *, stop_hook_active: bool = False) -> HookCall:
        self._transcript({"type": "assistant_message", "text": last_message})
        return self.send_hook("Stop", {**self._common(), "stop_hook_active": stop_hook_active,
                                       "last_assistant_message": last_message})

    def session_end(self, reason: str = "other") -> HookCall:
        payload = {"session_id": self.session_id, "transcript_path": str(self.transcript_path), "cwd": self.cwd,
                   "reason": reason}
        return self.send_hook("SessionEnd", payload)

    def close(self) -> None:
        if self._mcp is not None:
            try:
                if self._mcp.stdin:
                    self._mcp.stdin.close()
                self._mcp.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self._mcp.kill()
            self._mcp = None

    def __enter__(self) -> FakeHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run_scenario(host: FakeHost, steps: list[dict[str, Any]]) -> list[HookCall]:
    """Run a JSON scenario: [{"do": "prompt", "text": "..."}, {"do": "tool", "command": "..."}, ...]."""
    for s in steps:
        kind = s["do"]
        if kind == "session_start":
            host.session_start(s.get("source", "startup"))
        elif kind == "prompt":
            host.prompt(s["text"])
        elif kind == "tool":
            host.tool(s["command"], s.get("stdout", ""), int(s.get("exit_code", 0)))
        elif kind == "stop":
            host.stop(s["message"], stop_hook_active=bool(s.get("stop_hook_active", False)))
        elif kind == "session_end":
            host.session_end(s.get("reason", "other"))
        else:
            raise ValueError(f"unknown step {kind}")
    return host.calls


def main(argv: list[str] | None = None) -> int:
    import argparse

    from arbiter_agent.paths import get_paths

    p = argparse.ArgumentParser(prog="python -m arbiter_agent.clients.fake_host")
    p.add_argument("scenario", help="JSON file with a list of steps")
    p.add_argument("--client", default="codex", choices=["codex", "claude_code", "fake"])
    p.add_argument("--transport", default="mcp", choices=["mcp", "http", "command"])
    p.add_argument("--home")
    p.add_argument("--cwd", default=os.getcwd())
    a = p.parse_args(argv)
    paths = get_paths(a.home)
    steps = json.loads(Path(a.scenario).read_text(encoding="utf-8"))
    with FakeHost(paths, client=a.client, transport=a.transport, cwd=a.cwd) as host:
        calls = run_scenario(host, steps)
    for c in calls:
        print(json.dumps({"event": c.event, "ok": c.ok, "latency_ms": round(c.latency_ms, 1),
                          "response": c.response, "error": c.error}))
    return 0 if all(c.ok for c in calls) else 1

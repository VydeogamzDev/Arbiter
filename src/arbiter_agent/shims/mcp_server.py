"""Stateless stdio MCP server (spec §4.4.1, decision 0021). Stdlib only.

Clients spawn one per session. It forwards to the daemon and never blocks the host:

- ``arbiter_hook``: target of Codex ``mcp_tool`` hooks. Arguments are the templated hook
  payload plus a literal ``client`` field. The text result is the client decision JSON
  (empty string = no decision). Fails open.
- ``arbiter_status`` / ``arbiter_ping``: health and daemon status.
- Task-state tools (M3/M4): ``arbiter_contract_propose``, ``arbiter_contracts``,
  ``arbiter_scope_change``, ``arbiter_finish_check`` and ``arbiter_verify``. They bind to the
  calling session by the last hook session seen on this shim, else by client + working directory.

The MCP wire protocol is newline-delimited JSON-RPC 2.0 over stdio. It's implemented here
directly (not via an SDK) to keep start-up fast and dependency-free.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any

from arbiter_agent import __version__
from arbiter_agent.daemon.client import DaemonClient, DaemonError, DaemonUnavailable
from arbiter_agent.daemon.diagnostics import record_failopen
from arbiter_agent.paths import ArbiterPaths, get_paths
from arbiter_agent.shims.common import DEFAULT_DEADLINE_S, forward_hook, trigger_launch

SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL = SUPPORTED_PROTOCOLS[-1]

TOOLS = [
    {
        "name": "arbiter_hook",
        "description": "Internal: receives client hook events for Arbiter (used by mcp_tool hooks). "
                       "Agents should not call this directly.",
        "inputSchema": {"type": "object", "properties": {"client": {"type": "string"},
                                                          "hook_event_name": {"type": "string"}},
                        "additionalProperties": True},
    },
    {
        "name": "arbiter_status",
        "description": "Show Arbiter daemon health and what it has recorded for this machine.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "arbiter_ping",
        "description": "Check that the Arbiter daemon is reachable.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

_SESSION = {"session_id": {"type": "string", "description": "Optional; defaults to the calling session."}}
_RECIPE = {
    "description": "How Arbiter can check it. One of: {type: test_command, command}, {type: command_exit, command, "
                   "exit_code}, {type: file_exists, path}, {type: file_contains, path, text|regex}, "
                   "{type: paths_unchanged, paths: [...]}, {type: public_surface_unchanged, paths: [...]}, "
                   "{type: manual, description} (only the user can confirm manual ones).",
    "anyOf": [{"type": "object"}, {"type": "string"}],
}
TOOLS += [
    {
        "name": "arbiter_contract_propose",
        "description": "Record the user's requirements for this task as checkable contracts. Each contract needs "
                       "verbatim quotes copied from the user's own messages (Arbiter rejects quotes it can't find) "
                       "and a recipe Arbiter can evaluate. Status always starts UNKNOWN; only evidence (test runs, "
                       "file checks) or the user can change it.",
        "inputSchema": {"type": "object", "properties": {
            "contracts": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string", "description": "The obligation in plain words."},
                "quotes": {"type": "array", "items": {"type": "string"},
                           "description": "Exact excerpts from the user's messages this obligation comes from."},
                "recipe": _RECIPE, "scope": {"type": "string"}}, "required": ["text", "quotes"]}},
            "supersedes": {"type": "array", "items": {"type": "string"},
                           "description": "Contract ids replaced because the user changed the request."},
            "new_task": {"type": "boolean", "description": "True when the user's latest message starts a new task."},
            **_SESSION}, "required": ["contracts"]},
    },
    {
        "name": "arbiter_contracts",
        "description": "Show this session's contracts with their current evidence status, requests that no contract "
                       "covers yet, test-integrity status and the latest test results.",
        "inputSchema": {"type": "object", "properties": {**_SESSION}},
    },
    {
        "name": "arbiter_scope_change",
        "description": "Tell Arbiter the user's latest message changed the task. Starts a new goal epoch: contracts "
                       "from the previous task stop counting unless listed in carry.",
        "inputSchema": {"type": "object", "properties": {
            "summary": {"type": "string"}, "carry": {"type": "array", "items": {"type": "string"}},
            **_SESSION}, "required": ["summary"]},
    },
    {
        "name": "arbiter_finish_check",
        "description": "Check the evidence before reporting the task as complete. Returns the finish ledger: which "
                       "contracts pass, which lack evidence, uncovered requests and test-integrity findings. "
                       "Claims passed here are recorded as the agent's assertions and never count as evidence.",
        "inputSchema": {"type": "object", "properties": {
            "summary": {"type": "string"}, "claims": {"type": "array", "items": {"type": "string"}},
            **_SESSION}},
    },
    {
        "name": "arbiter_verify",
        "description": "Run the repository's user-approved verification commands (.arbiter/verify.yaml) and record "
                       "the results as Arbiter-observed evidence. Only works after the user has trusted the file.",
        "inputSchema": {"type": "object", "properties": {
            "only": {"type": "array", "items": {"type": "string"}, "description": "Command names to run."},
            **_SESSION}},
    },
]
TASK_TOOLS = {"arbiter_contract_propose": "contract_propose", "arbiter_contracts": "session_status",
              "arbiter_scope_change": "scope_change", "arbiter_finish_check": "finish_check"}

INSTRUCTIONS = ("Arbiter records and checks this coding session locally. Its hook tool is internal. "
                "For tasks with requirements: propose contracts with arbiter_contract_propose, quoting the user's "
                "words verbatim, and call arbiter_finish_check before reporting completion. "
                "Use arbiter_status to see whether Arbiter is healthy.")


class MCPShim:
    def __init__(self, paths: ArbiterPaths | None = None, out: Any = None) -> None:
        self.paths = paths or get_paths()
        self.out = out or sys.stdout
        self._client: DaemonClient | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.client_name = ""
        self.last_session: str | None = None

    # ---------------------------------------------------------------- wire
    def _send(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with self._write_lock:
            self.out.write(line + "\n")
            self.out.flush()

    def _result(self, mid: Any, result: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _error(self, mid: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    # ---------------------------------------------------------------- tools
    def _text(self, text: str, is_error: bool = False) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "arbiter_hook":
            client = str(args.pop("client", "codex") or "codex")
            event = args.get("hook_event_name")
            if args.get("session_id"):
                self.last_session = f"{client}:{args['session_id']}"
            with self._lock:
                response, self._client = forward_hook(self._client, self.paths, "mcp_shim", client, args,
                                                      surface="mcp", event=event, deadline=DEFAULT_DEADLINE_S)
            return self._text(json.dumps(response) if response else "")
        if name in ("arbiter_status", "arbiter_ping"):
            try:
                with self._lock:
                    c = self._client or DaemonClient(self.paths, component="mcp_shim")
                    self._client = c
                    res = c.request("status" if name == "arbiter_status" else "ping", timeout=2.0)
                return self._text(json.dumps(res, indent=1))
            except DaemonUnavailable as exc:
                with self._lock:
                    if self._client:
                        self._client.close()
                    self._client = None
                record_failopen(self.paths.logs, "mcp_shim", exc.reason)
                trigger_launch(self.paths)
                return self._text(f"Arbiter daemon unavailable ({exc.reason}); starting it in the background.")
        if name in TASK_TOOLS or name == "arbiter_verify":
            return self._task_tool(name, args)
        raise KeyError(name)

    def _binding(self, args: dict[str, Any]) -> dict[str, Any]:
        import os

        from arbiter_agent.daemon.clients_runtime import client_from_info

        return {"client": client_from_info(self.client_name) if self.client_name else None, "cwd": os.getcwd(),
                "session_hint": self.last_session, "session_id": args.get("session_id")}

    def _task_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        params = {**args, **self._binding(args)}
        try:
            with self._lock:
                c = self._client or DaemonClient(self.paths, component="mcp_shim")
                self._client = c
            if name == "arbiter_verify":
                return self._text(self._verify(c, params))
            method = TASK_TOOLS[name]
            res = c.request(method, params, timeout=15.0)
            return self._text(render_task_result(name, res))
        except DaemonError as exc:
            return self._text(f"Arbiter: {exc.error}", is_error=True)
        except DaemonUnavailable as exc:
            with self._lock:
                if self._client:
                    self._client.close()
                self._client = None
            record_failopen(self.paths.logs, "mcp_shim", exc.reason)
            trigger_launch(self.paths)
            return self._text(f"Arbiter daemon unavailable ({exc.reason}); nothing was recorded.", is_error=True)
        except Exception as exc:
            return self._text(f"Arbiter: {exc}", is_error=True)

    def _verify(self, c: DaemonClient, params: dict[str, Any]) -> str:
        from arbiter_agent.completion import verify_runner as vr

        plan = c.request("verify_plan", params, timeout=10.0)
        if not plan.get("ok"):
            return f"Arbiter verify not run: {plan.get('error')}"
        cmds = [vr.VerifyCommand(**d) for d in plan["commands"]]
        results = vr.run_all(cmds, plan["root"], params.get("only") or None)
        c.request("verify_submit", {**params, "results": [vr.result_fact(r) for r in results],
                                    "channel": "mcp_shim"}, timeout=10.0)
        lines = [f"{r.name}: {r.status.upper()} (exit {r.exit_code}, {r.duration_s:.1f}s) `{r.command}`"
                 for r in results]
        return "Arbiter verify results (recorded as arbiter_observed evidence):\n" + "\n".join(lines)

    def _report_client(self, info: dict[str, Any]) -> None:
        """Tell the daemon which client launched this shim (T1 evidence). Fail-open."""
        import os
        import time

        for _ in range(20):  # the daemon may still be starting
            try:
                c = DaemonClient(self.paths, component="mcp_shim")
                c.request("client_seen", {"name": info.get("name"), "version": info.get("version"),
                                          "cwd": os.getcwd()}, timeout=1.0)
                c.close()
                return
            except DaemonUnavailable:
                time.sleep(0.5)
            except Exception:
                return

    # ---------------------------------------------------------------- dispatch
    def handle(self, msg: dict[str, Any]) -> None:
        method, mid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
            self._result(mid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": {"name": "arbiter", "version": __version__},
                               "instructions": INSTRUCTIONS})
            trigger_launch(self.paths)  # warm the daemon early; never waits
            info = params.get("clientInfo") or {}
            self.client_name = str(info.get("name") or "")
            threading.Thread(target=self._report_client, args=(info,), daemon=True).start()
        elif method == "ping":
            self._result(mid, {})
        elif method == "tools/list":
            self._result(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                self._result(mid, self.call_tool(str(name), dict(args) if isinstance(args, dict) else {}))
            except KeyError:
                self._error(mid, -32602, f"unknown tool: {name}")
            except Exception as exc:
                record_failopen(self.paths.logs, "mcp_shim", f"{type(exc).__name__}: {exc}")
                self._result(mid, self._text(""))
        elif method in ("resources/list", "prompts/list"):
            self._result(mid, {"resources": []} if method == "resources/list" else {"prompts": []})
        elif mid is not None and method is not None:
            self._error(mid, -32601, f"method not found: {method}")
        # notifications (no id) are ignored

    def serve(self, inp: Any = None) -> int:
        inp = inp or sys.stdin
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._error(None, -32700, "parse error")
                continue
            if isinstance(msg, list):
                for m in msg:
                    if isinstance(m, dict):
                        self.handle(m)
            elif isinstance(msg, dict):
                self.handle(msg)
        if self._client:
            self._client.close()
        return 0


def render_task_result(name: str, res: Any) -> str:
    if not isinstance(res, dict):
        return json.dumps(res)
    if name == "arbiter_contract_propose":
        lines = [f"Goal epoch {res.get('goal_epoch')}" + (f" (new epoch confirmed: {res['epoch_confirmed']})"
                                                          if res.get("epoch_confirmed") else "")]
        for a in res.get("accepted", []):
            note = f" [{a['strength']}: {a.get('strength_note', '')}]" if a.get("strength") not in (None, "ok") else ""
            lines.append(f"accepted {a['id']}: {a.get('text', '')} -> {a.get('recipe', 'existing')}{note}")
        for r in res.get("rejected", []):
            lines.append(f"rejected: {r.get('text', '')!r}: {r.get('reason')}"
                         + (f" {r['quotes']}" if r.get("quotes") else ""))
        if res.get("superseded"):
            lines.append("superseded: " + ", ".join(res["superseded"]))
        return "\n".join(lines)
    if name in ("arbiter_contracts", "arbiter_finish_check") and "ledger_text" in res:
        text = str(res["ledger_text"])
        if name == "arbiter_finish_check":
            head = "Verified to the configured evidence standard." if res.get("verdict") == "verified" else \
                "Not verified yet. Missing evidence:\n" + "\n".join(f"- {m}" for m in res.get("missing", []))
            return head + "\n\n" + text + (f"\n\n{res['note']}" if res.get("note") else "")
        return text
    if name == "arbiter_scope_change":
        return (f"Goal epoch {res.get('goal_epoch')} starts at {res.get('starts_at_intent')}. "
                f"Carried contracts: {', '.join(res.get('carried') or []) or 'none'}.")
    return json.dumps(res, indent=1)


def main(paths: ArbiterPaths | None = None) -> int:
    try:
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    try:
        return MCPShim(paths).serve()
    except KeyboardInterrupt:
        return 0

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
        "description": "Optional: see the evidence Arbiter has for completion. Returns the finish ledger: which "
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
TOOLS += [
    {
        "name": "arbiter_search",
        "description": "Search this repository (full-text over code and docs, plus file paths) using Arbiter's index. "
                       "Results are fresh (the index updates before answering), exclude secrets and generated files, "
                       "and cite path:line.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "path_glob": {"type": "string", "description": "Optional filter, e.g. 'src/**/*.py'."}, **_SESSION},
            "required": ["query"]},
    },
    {
        "name": "arbiter_symbol",
        "description": "Find where a function, class or type is defined, where it's referenced, and which files import "
                       "its module.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, **_SESSION}, "required": ["name"]},
    },
    {
        "name": "arbiter_related",
        "description": "For one file: what it imports, which files import it, and which tests cover it.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, **_SESSION}, "required": ["path"]},
    },
]
TOOLS += [
    {
        "name": "arbiter_controls",
        "description": "Arbiter's controls and circuit breakers. action=list shows modules, open breakers and load "
                       "shedding. action=request asks for a one-turn bypass for this session: "
                       "next_turn:bypass_retrieval_narrowing, next_turn:normal_tool_surface or next_turn:full_review. "
                       "Anything that weakens verification (gate mode, disabling modules, resetting breakers) can only "
                       "be changed by the user with `arbiter control`.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["list", "request"]},
            "control": {"type": "string"}, **_SESSION}},
    },
]
TOOLS += [
    {
        "name": "arbiter_context",
        "description": "Which files to read for a task, before you start: files referenced by the error, files "
                       "you named or changed, definitions of named symbols (pinned), then the most relevant files "
                       "ranked across full-text, path, symbol and import-graph signals. Pass the task, and the error "
                       "text if there is one.",
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string"}, "errors": {"type": "string", "description": "Error or stack trace text."},
            "paths": {"type": "array", "items": {"type": "string"}}, **_SESSION}, "required": ["task"]},
    },
    {
        "name": "arbiter_advice",
        "description": "Arbiter's advisory read of this session: suggested reasoning effort with reasons, whether "
                       "to broaden retrieval or replan (loops, no progress, retrieval misses), and the risk level of "
                       "the current diff with its suggested review and test order. Advisory only.",
        "inputSchema": {"type": "object", "properties": {**_SESSION}},
    },
]
RETRIEVAL_TOOLS = {"arbiter_search": "search", "arbiter_symbol": "symbol", "arbiter_related": "related"}
TASK_TOOLS = {"arbiter_contract_propose": "contract_propose", "arbiter_contracts": "session_status",
              "arbiter_scope_change": "scope_change", "arbiter_finish_check": "finish_check"}

# Kept neutral on purpose: instructions that described what completion evidence Arbiter wants made
# agents run extra test passes (benchmark, 2026-09-26). When evidence is actually missing, the Stop
# hook says so for that turn only.
INSTRUCTIONS = ("Arbiter records this coding session locally. Its hook tool is internal, and its other tools "
                "are optional: repository search and context, contracts for requirements you want checked, "
                "and arbiter_status for what it has recorded.")


class MCPShim:
    def __init__(self, paths: ArbiterPaths | None = None, out: Any = None) -> None:
        self.paths = paths or get_paths()
        self.out = out or sys.stdout
        self._client: DaemonClient | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.client_name = ""
        self.last_session: str | None = None
        self.client_caps: dict[str, Any] = {}
        self._gateway: Any = None
        self._gateway_active: bool | None = None
        self._upstreams: dict[str, Any] = {}
        self._pending: dict[str, Any] = {}           # our requests to the client (elicitation) -> Future
        self._req_seq = 0

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

    def request_client(self, method: str, params: dict[str, Any], timeout: float = 120.0) -> Any:
        """Send a request to the client (e.g. ``elicitation/create``) and wait for its response."""
        from concurrent.futures import Future

        with self._write_lock:
            self._req_seq += 1
            rid = f"arbiter-{self._req_seq}"
        fut: Future[Any] = Future()
        self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return fut.result(timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    # ---------------------------------------------------------------- gateway (M10)
    def _profile_id(self) -> str | None:
        from arbiter_agent.daemon.clients_runtime import client_from_info

        return client_from_info(self.client_name) if self.client_name else None

    def gateway_active(self) -> bool:
        """Gateway mode for this client: forced on/off by config, else on when the client has adopted
        servers (their tools only exist behind the gateway) or the benchmark enabled it."""
        if self._gateway_active is not None:
            return self._gateway_active
        active = False
        try:
            from arbiter_agent.config import load_config
            from arbiter_agent.gateway.registry import Registry

            mode = str(load_config(self.paths).get("gateway.enabled", "auto")).lower()
            pid = self._profile_id()
            if mode in ("on", "true"):
                active = True
            elif mode not in ("off", "false") and pid:
                active = bool(Registry.load(self.paths).for_client(pid)) or self._policy_enabled(pid)
        except Exception:
            active = False
        self._gateway_active = active
        return active

    def _policy_enabled(self, pid: str) -> bool:
        import json as _json

        try:
            data = _json.loads((self.paths.state / "gateway_decisions.json").read_text(encoding="utf-8"))
            return bool(data.get(pid, {}).get("enabled"))
        except (OSError, ValueError):
            return False

    def gateway(self) -> Any:
        if self._gateway is not None:
            return self._gateway
        from arbiter_agent.config import load_config
        from arbiter_agent.gateway import semantic
        from arbiter_agent.gateway.authorization_bridge import AuthorizationBridge
        from arbiter_agent.gateway.call import Gateway
        from arbiter_agent.gateway.catalog import Catalog, from_server, own_tools
        from arbiter_agent.gateway.registry import Registry
        from arbiter_agent.gateway.upstream import LaunchSpec, Upstream

        catalog = Catalog(own_tools(TOOLS))
        pid = self._profile_id()
        for a in Registry.load(self.paths).for_client(pid) if pid else []:
            try:
                up = Upstream(a.server, LaunchSpec.from_entry(a.entry)).start()
            except Exception as exc:
                record_failopen(self.paths.logs, "gateway", f"{a.server}: {exc}"[:200])
                continue
            self._upstreams[a.server] = up
            catalog.add(from_server(a.server, up.tools, a.confirmed_read_only))
        elicitation = "elicitation" in self.client_caps
        config = load_config(self.paths)
        self._gateway = Gateway(catalog, AuthorizationBridge(elicitation), local_call=self.call_tool,
                                upstream_call=lambda server, tool, args: self._upstreams[server].call(tool, args),
                                elicit=(lambda params: self.request_client("elicitation/create", params))
                                if elicitation else None)
        self._gateway.ranker = semantic.ranker_from_config(config)
        return self._gateway

    def tools_for_client(self) -> list[dict[str, Any]]:
        if not self.gateway_active():
            return TOOLS
        from arbiter_agent.gateway.call import GATEWAY_TOOLS
        from arbiter_agent.gateway.catalog import CORE_TOOLS

        return [t for t in TOOLS if t["name"] in CORE_TOOLS] + GATEWAY_TOOLS

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
        if name in TASK_TOOLS or name in ("arbiter_verify", "arbiter_controls", "arbiter_advice"):
            return self._task_tool(name, args)
        if name in RETRIEVAL_TOOLS:
            return self._retrieval_tool(name, args)
        if name == "arbiter_context":
            return self._retrieval_tool(name, {**args, "context": {"query": args.get("task") or "",
                                                                    "errors": args.get("errors") or "",
                                                                    "paths": args.get("paths") or []}})
        if name == "arbiter_advice":
            return self._task_tool(name, args)
        if name in ("tool_search", "tool_describe", "tool_call") and self.gateway_active():
            return dict(self.gateway().handle(name, args))
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
            if name == "arbiter_advice":
                return self._text(render_advice(c.request("advice", params, timeout=20.0)))
            if name == "arbiter_controls":
                if args.get("action") == "request":
                    res = c.request("control_set", {**params, "key": str(args.get("control") or ""), "value": True,
                                                    "scope": "session"}, timeout=10.0)
                    return self._text(f"{res['key']} set for this session (one turn).")
                return self._text(render_controls(c.request("control_list", params, timeout=10.0)))
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

    def _retrieval_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        params = {**args, **self._binding(args), "op": RETRIEVAL_TOOLS.get(name, "context")}
        try:
            with self._lock:
                c = self._client or DaemonClient(self.paths, component="mcp_shim")
                self._client = c
            res = c.request("retrieve", params, timeout=20.0)
            return self._text(render_retrieval(name, res))
        except DaemonError as exc:
            return self._text(f"Arbiter: {exc.error}", is_error=True)
        except DaemonUnavailable as exc:
            with self._lock:
                if self._client:
                    self._client.close()
                self._client = None
            record_failopen(self.paths.logs, "mcp_shim", exc.reason)
            trigger_launch(self.paths)
            return self._text(f"Arbiter daemon unavailable ({exc.reason}).", is_error=True)

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
            self.client_caps = dict(params.get("capabilities") or {})
            threading.Thread(target=self._report_client, args=(info,), daemon=True).start()
        elif method == "ping":
            self._result(mid, {})
        elif method == "tools/list":
            self._result(mid, {"tools": self.tools_for_client()})
        elif method is None and mid is not None and mid in self._pending:
            fut = self._pending.get(mid)
            if fut is not None and not fut.done():
                fut.set_result(msg.get("result") if "result" in msg else {"action": "cancel"})
        elif method == "tools/call" and params.get("name") == "tool_call":
            # may wait on an elicitation answer from the client: run it off the read loop
            threading.Thread(target=self._call_async, args=(mid, dict(params.get("arguments") or {})),
                             daemon=True).start()
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

    def _call_async(self, mid: Any, args: dict[str, Any]) -> None:
        try:
            self._result(mid, self.call_tool("tool_call", args))
        except KeyError:
            self._error(mid, -32602, "unknown tool: tool_call")
        except Exception as exc:
            record_failopen(self.paths.logs, "mcp_shim", f"{type(exc).__name__}: {exc}")
            self._result(mid, self._text(""))

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
        for up in self._upstreams.values():
            up.close()
        return 0


def render_advice(res: dict[str, Any]) -> str:
    r = res.get("reasoning") or {}
    lines = [f"Suggested effort: {r.get('effort')} ({'; '.join(r.get('reasons') or [])})"]
    lines += [f"- {a}" for a in r.get("advice") or []]
    sig = res.get("signals") or {}
    if sig.get("retrieval_miss_reasons"):
        lines.append("Retrieval misses: " + "; ".join(sig["retrieval_miss_reasons"]))
    if sig.get("evidence_gaps"):
        lines.append(f"Evidence gaps: {len(sig['evidence_gaps'])} (see arbiter_contracts)")
    risk = res.get("diff_risk")
    if risk:
        lines.append(risk.get("summary", ""))
        for f in [f for f in risk.get("files", []) if f["level"] in ("high", "critical")][:6]:
            lines.append(f"  {f['level'].upper()} {f['path']}: {'; '.join(f['reasons'][:2])}")
        if risk.get("test_order"):
            lines.append("Suggested test order: " + ", ".join(risk["test_order"][:6]))
    lines.append("(advisory only; Arbiter doesn't change your model or effort)")
    return "\n".join(lines)


def render_controls(res: dict[str, Any]) -> str:
    lines = [f"controller: {'on' if res.get('controller') else 'OFF'}   "
             f"load shedding level: {res.get('shedding', {}).get('level', 0)}"]
    opened = {n: b for n, b in (res.get("breakers") or {}).items() if b.get("state") != "closed"}
    lines.append("breakers: " + (", ".join(f"{n} ({b['state']}: {b['last_reason']})" for n, b in opened.items())
                                 or "none open"))
    off = [n for n, m in (res.get("modules") or {}).items() if not m.get("enabled")]
    lines.append("modules off: " + (", ".join(off) or "none"))
    for o in res.get("overrides") or []:
        lines.append(f"control {o['key']} = {o['value']} ({o['scope']}, by {o['actor']})")
    return "\n".join(lines)


def render_retrieval(name: str, res: Any) -> str:
    if name == "arbiter_context":
        out = [f"Pinned ({len(res.get('pins', []))}):"]
        out += [f"  {p['path']}:{p.get('line', 1)}  ({p['reason']})" for p in res.get("pins", [])]
        k = int(res.get("k") or 0)
        out.append(f"Ranked (top {k}; {'; '.join(res.get('reasons') or [])}):")
        out += [f"  {r['path']}:{r.get('line', 1)}  [{', '.join(r.get('channels', []))}]"
                for r in res.get("ranked", [])[:k]]
        if res.get("note"):
            out.append(res["note"])
        return "\n".join(out)
    if not isinstance(res, dict):
        return json.dumps(res)
    idx = res.get("index") or {}
    head = f"[index v{idx.get('version')} gen {idx.get('generation')} @ {str(idx.get('head') or 'no-git')[:10]}]"
    lines: list[str] = []
    if res.get("error"):
        return f"{res['error']} {head}"
    if name == "arbiter_search":
        for h in res.get("hits", []):
            snippet = " | ".join(s.strip() for s in str(h.get("snippet", "")).splitlines() if s.strip())[:200]
            lines.append(f"{h['path']}:{h['line']}  {snippet}" if snippet else f"{h['path']}  (path match)")
        if not lines:
            lines.append("no matches")
    elif name == "arbiter_symbol":
        for d in res.get("definitions", []):
            sig = d.get("signature") or ""
            lines.append(f"def {d['kind']} {d['path']}:{d['line']}-{d['end_line']}  {sig}".rstrip())
        for r in res.get("references", [])[:30]:
            lines.append(f"ref {r['path']}:{r['line']} ({r['channel']})")
        if res.get("importers"):
            lines.append("imported by: " + ", ".join(res["importers"][:20]))
        if not lines:
            lines.append("no definition found")
    else:
        for key, label in (("imports", "imports"), ("importers", "imported by"), ("tests", "tests"),
                           ("tests_cover", "covers")):
            if res.get(key):
                lines.append(f"{label}: " + ", ".join(res[key][:30]))
        if not lines:
            lines.append("no related files found")
    if res.get("note"):
        lines.append(f"note: {res['note']}")
    return "\n".join(lines + [head])


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

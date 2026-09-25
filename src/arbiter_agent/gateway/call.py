"""The gateway itself (spec §10.2): three stable tools in front of the catalog.

``tool_search`` ranks, ``tool_describe`` returns canonical schemas, ``tool_call`` validates the tool id
against the current catalog and the arguments against its canonical schema, applies the
authorization bridge, then dispatches (Arbiter's own tools in-process, adopted tools to their
server). Every failure is a structured error the agent can act on; nothing is silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from arbiter_agent.gateway import approval_mirror, schema_validation, search
from arbiter_agent.gateway.authorization_bridge import AuthorizationBridge
from arbiter_agent.gateway.catalog import Catalog

GATEWAY_TOOLS = [
    {
        "name": "tool_search",
        "description": "Find tools for a task among the tools behind Arbiter's gateway (your adopted MCP servers and "
                       "Arbiter's own tools). Returns tool ids with short descriptions; use tool_describe for the "
                       "full schema, then tool_call. If nothing fits, search again with broaden=true.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "family": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20}, "broaden": {"type": "boolean"}},
            "required": ["query"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "tool_describe",
        "description": "Full input schemas for tool ids returned by tool_search.",
        "inputSchema": {"type": "object", "properties": {"tool_ids": {"type": "array", "items": {"type": "string"},
                                                                      "maxItems": 10}},
                        "required": ["tool_ids"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "tool_call",
        "description": "Call a tool behind the gateway by id with arguments matching its schema. Tools that may "
                       "change things ask the user first, for that call only.",
        "inputSchema": {"type": "object", "properties": {"tool_id": {"type": "string"},
                                                         "arguments": {"type": "object"}},
                        "required": ["tool_id"]},
    },
]

LocalCall = Callable[[str, dict[str, Any]], dict[str, Any]]
Elicit = Callable[[dict[str, Any]], Any]


def _text(obj: Any, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1)}],
            "isError": is_error}


def error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return _text({"error": {"code": code, "message": message, **extra}}, is_error=True)


class Gateway:
    def __init__(self, catalog: Catalog, bridge: AuthorizationBridge, *, local_call: LocalCall,
                 upstream_call: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
                 elicit: Elicit | None = None) -> None:
        self.catalog, self.bridge = catalog, bridge
        self.local_call, self.upstream_call, self.elicit = local_call, upstream_call, elicit
        self.stats = {"searches": 0, "describes": 0, "calls": 0, "errors": 0, "misses": 0}
        self.ranker: Any = None             # semantic.EncoderRanker (optional, tier 0)

    def handle(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "tool_search":
            self.stats["searches"] += 1
            res = search.search(self.catalog, str(args.get("query") or ""), family=args.get("family"),
                                max_results=int(args.get("max_results") or 8), broaden=bool(args.get("broaden")),
                                ranker=self.ranker)
            if args.get("broaden"):
                self.stats["misses"] += 1            # a broadened search means the first one missed (§10.4)
            return _text(res)
        if name == "tool_describe":
            self.stats["describes"] += 1
            ids = [str(i) for i in (args.get("tool_ids") or [])][:10]
            found = [t.canonical() for i in ids if (t := self.catalog.get(i))]
            unknown = [i for i in ids if not self.catalog.get(i)]
            return _text({"tools": found, **({"unknown": unknown} if unknown else {})})
        if name == "tool_call":
            return self._call(str(args.get("tool_id") or ""), args.get("arguments") or {})
        return error("unknown_gateway_tool", f"{name} isn't a gateway tool")

    def _call(self, tool_id: str, arguments: Any) -> dict[str, Any]:
        self.stats["calls"] += 1
        tool = self.catalog.get(tool_id)
        if tool is None:
            self.stats["errors"] += 1
            return error("unknown_tool", f"{tool_id!r} isn't in the gateway catalog; use tool_search")
        if not isinstance(arguments, dict):
            self.stats["errors"] += 1
            return error("invalid_arguments", "arguments must be an object")
        problems = schema_validation.validate(arguments, tool.input_schema)
        if problems:
            self.stats["errors"] += 1
            return error("invalid_arguments", "arguments don't match the tool's schema", problems=problems[:10],
                         schema=tool.input_schema)
        d = self.bridge.decide(tool, arguments)
        if d.action == "deny":
            return error("denied", d.reason, tool_id=tool_id)
        if d.action == "elicit":
            if self.elicit is None:
                return error("denied", "approval can't be requested from this client", tool_id=tool_id)
            try:
                response = self.elicit(approval_mirror.elicitation_request(tool, arguments))
            except Exception:
                response = None
            if not approval_mirror.approved(response):
                self.bridge.record_denial(tool_id, arguments)
                return error("denied", "the user didn't approve this call", tool_id=tool_id)
        try:
            if tool.local:
                return self.local_call(tool.name, arguments)
            if self.upstream_call is None:
                return error("unavailable", f"server {tool.server!r} isn't reachable")
            return self.upstream_call(tool.server, tool.name, arguments)
        except Exception as exc:
            self.stats["errors"] += 1
            return error("unavailable", f"{tool_id}: {exc}"[:300])

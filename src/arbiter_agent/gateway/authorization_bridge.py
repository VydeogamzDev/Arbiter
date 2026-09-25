"""Approval preservation for proxied tools (spec §10.5, §10.7).

Decisions, per call:
- **allow**: the tool is confirmed read-only (or it's one of Arbiter's own read-only tools);
- **elicit**: the tool may mutate and the client supports MCP elicitation, so the user is asked for
  *this* tool and *these* arguments. The approval covers this call only: approving ``tool_call`` for
  one tool id never approves another (no inheritance);
- **deny**: the tool may mutate and approval can't be mirrored, or the user denied the same call
  earlier in this session (sticky denials: the gateway never retries a denied call by another route).

The sensor can rank tools; it has no say here.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

from arbiter_agent.gateway.catalog import Tool


@dataclass(frozen=True)
class Decision:
    action: str          # allow | elicit | deny
    reason: str


def call_key(tool_id: str, args: dict[str, Any]) -> str:
    return tool_id + ":" + hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]


class AuthorizationBridge:
    def __init__(self, elicitation_supported: bool) -> None:
        self.elicitation_supported = elicitation_supported
        self._denied: set[str] = set()
        self._lock = threading.Lock()
        self.stats = {"allowed": 0, "elicited": 0, "denied": 0, "sticky_denials": 0}

    def decide(self, tool: Tool, args: dict[str, Any]) -> Decision:
        key = call_key(tool.tool_id, args)
        with self._lock:
            if key in self._denied:
                self.stats["sticky_denials"] += 1
                return Decision("deny", "you denied this exact call earlier in this session")
        if tool.read_only:
            self.stats["allowed"] += 1
            return Decision("allow", "confirmed read-only")
        if tool.local:
            self.stats["allowed"] += 1       # Arbiter's own tools keep their daemon-side authority checks
            return Decision("allow", "Arbiter tool (authority enforced by the daemon)")
        if not self.elicitation_supported:
            self.stats["denied"] += 1
            return Decision("deny", "this tool may change things and this client can't confirm approvals "
                                    "through the gateway; use the server directly")
        self.stats["elicited"] += 1
        return Decision("elicit", "may change things: ask the user for this call")

    def record_denial(self, tool_id: str, args: dict[str, Any]) -> None:
        with self._lock:
            self._denied.add(call_key(tool_id, args))

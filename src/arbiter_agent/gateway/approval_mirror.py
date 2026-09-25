"""Mirroring the client's per-tool approval through MCP elicitation (spec §10.7).

The request names the exact tool and shows the exact arguments; the schema is a single required
boolean, so nothing else can be smuggled through the response. Only ``action: accept`` with
``approve: true`` counts as approval; decline, cancel, a malformed response or a timeout all deny.
"""

from __future__ import annotations

import json
from typing import Any

from arbiter_agent.gateway.catalog import Tool

MAX_ARGS_SHOWN = 1500


def elicitation_request(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    shown = json.dumps(args, indent=1, sort_keys=True, default=str)
    if len(shown) > MAX_ARGS_SHOWN:
        shown = shown[:MAX_ARGS_SHOWN] + "\n... (truncated)"
    return {
        "message": (f"Allow the Arbiter gateway to run {tool.tool_id}?\n"
                    f"{tool.description[:300]}\n\nArguments:\n{shown}\n\n"
                    "This approval covers this one call only."),
        "requestedSchema": {"type": "object", "properties": {
            "approve": {"type": "boolean", "title": f"Run {tool.tool_id} with these arguments"}},
            "required": ["approve"]},
    }


def approved(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    content = response.get("content")
    return response.get("action") == "accept" and isinstance(content, dict) and content.get("approve") is True

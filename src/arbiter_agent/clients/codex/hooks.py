"""Codex hook definitions: ``mcp_tool`` handlers pointing at the Arbiter MCP shim.

Decision 0015: a ``${field}`` placeholder for a field the event doesn't carry makes the hook fail,
so each event's template lists only fields that event always carries (taken from the schemas
embedded in codex-cli 0.155). Definitions contain no paths, ports or versions, so their hash
(and therefore the user's one-time trust) survives Arbiter upgrades.
"""

from __future__ import annotations

from typing import Any

SERVER = "arbiter"
TOOL = "arbiter_hook"
HOOK_TIMEOUT_S = 5  # hard stop; the shim itself answers within the gating deadline or fails open

_COMMON = ["hook_event_name", "session_id", "transcript_path", "cwd"]
EVENT_FIELDS: dict[str, list[str]] = {
    "SessionStart": [*_COMMON, "model", "permission_mode", "source"],
    "UserPromptSubmit": [*_COMMON, "turn_id", "model", "permission_mode", "prompt"],
    "PreToolUse": [*_COMMON, "turn_id", "model", "permission_mode", "tool_name", "tool_input", "tool_use_id"],
    "PostToolUse": [*_COMMON, "turn_id", "model", "permission_mode", "tool_name", "tool_input", "tool_response",
                    "tool_use_id"],
    "Stop": [*_COMMON, "turn_id", "model", "permission_mode", "stop_hook_active", "last_assistant_message"],
    "SessionEnd": [*_COMMON, "reason"],
    "PreCompact": [*_COMMON, "turn_id", "model", "trigger"],
    "PostCompact": [*_COMMON, "turn_id", "model", "trigger"],
    "SubagentStart": [*_COMMON, "turn_id", "model", "permission_mode", "agent_id", "agent_type"],
    "SubagentStop": [*_COMMON, "turn_id", "model", "permission_mode", "agent_id", "agent_type",
                     "stop_hook_active", "last_assistant_message"],
}


def handler(event: str) -> dict[str, Any]:
    inp: dict[str, Any] = {"client": "codex"}
    inp.update({f: "${" + f + "}" for f in EVENT_FIELDS[event]})
    return {"type": "mcp_tool", "server": SERVER, "tool": TOOL, "input": inp, "timeout": HOOK_TIMEOUT_S}


def hook_groups() -> dict[str, dict[str, Any]]:
    return {event: {"hooks": [handler(event)]} for event in EVENT_FIELDS}


def is_ours(h: dict[str, Any]) -> bool:
    return h.get("type") == "mcp_tool" and h.get("server") == SERVER and h.get("tool") == TOOL


def mcp_entry(command: list[str]) -> dict[str, Any]:
    """``[mcp_servers.arbiter]`` values for config.toml."""
    return {"command": command[0], "args": command[1:]}

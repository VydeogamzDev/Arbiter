"""Claude Code hook definitions: ``http`` handlers to the daemon's loopback endpoint.

User-level hooks need no trust step (decision 0017). The hook token travels in a header; it's
written into ``~/.claude/settings.json``, which only the user can read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arbiter_agent.daemon.auth import HOOK_TOKEN_HEADER

EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStop",
          "PreCompact", "SessionEnd"]   # PostToolUseFailure carries failed tools, incl. non-zero shell exits
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure"}
HOOK_TIMEOUT_S = 5
URL_MARK = "/hook/claude_code/"


def handler(event: str, port: int, token: str) -> dict[str, Any]:
    return {"type": "http", "url": f"http://127.0.0.1:{port}{URL_MARK}{event}",
            "headers": {HOOK_TOKEN_HEADER: token}, "timeout": HOOK_TIMEOUT_S}


def hook_groups(port: int, token: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for event in EVENTS:
        g: dict[str, Any] = {"hooks": [handler(event, port, token)]}
        if event in TOOL_EVENTS:
            g = {"matcher": "*", **g}
        groups[event] = g
    return groups


def is_ours(h: dict[str, Any]) -> bool:
    url = str(h.get("url", ""))
    return h.get("type") == "http" and URL_MARK in url and url.startswith(("http://127.0.0.1:", "http://localhost:"))


def mcp_entry(command: list[str]) -> dict[str, Any]:
    """``mcpServers.arbiter`` value for ``~/.claude.json``."""
    return {"type": "stdio", "command": command[0], "args": command[1:], "env": {}}


def mcp_is_ours(entry: Any) -> bool:
    if not isinstance(entry, dict) or list(entry.get("args") or [])[-1:] != ["mcp"]:
        return False
    return "arbiter" in " ".join([str(entry.get("command", ""))] + [str(a) for a in entry.get("args") or []])


def plugin_enabled(settings_file: Path) -> str | None:
    """The enabled Arbiter plugin id (e.g. ``arbiter@arbiter-local``) in Claude Code settings, if any.
    Setup skips Claude Code when the plugin already provides the MCP server and hooks (spec 4.5.5)."""
    import json

    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    enabled = data.get("enabledPlugins") if isinstance(data, dict) else None
    if isinstance(enabled, dict):
        for key, on in enabled.items():
            if str(key).split("@", 1)[0] == "arbiter" and on:
                return str(key)
    return None

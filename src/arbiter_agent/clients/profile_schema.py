"""Client profile schema (spec §4.4.2). Profiles are data; a profile with an unsupported
schema version is ignored and reported, never partially applied (spec §16.5)."""

from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.clients.client_env import ClientEnv

SUPPORTED_SCHEMA_VERSIONS = {1}
MCP_FORMATS = {"toml_block", "json_named_entry", "print_only"}
HOOK_FORMATS = {"json_hook_groups"}
TRANSPORTS = {"mcp_tool", "http", "command"}


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    id: str
    display_name: str
    profile_version: int
    detect: dict[str, list[str]]
    mcp: dict[str, Any]
    hooks: dict[str, Any] | None
    transcripts: dict[str, Any] | None
    tools: dict[str, list[str]] = field(default_factory=dict)
    completion_claim: dict[str, Any] = field(default_factory=dict)
    effort_control: str = "advisory"
    native_tool_deferral: bool = False
    source: str = "builtin"

    def expand(self, template: str | None, env: ClientEnv) -> Path | None:
        if template is None:
            return None
        return Path(_expand(template, env))

    def detected(self, env: ClientEnv) -> tuple[bool, list[str]]:
        hits: list[str] = []
        for t in self.detect.get("any_path", []):
            p = Path(_expand(t, env))
            if p.exists():
                hits.append(f"path {p}")
        for c in self.detect.get("any_command", []):
            w = shutil.which(c)
            if w:
                hits.append(f"command {w}")
        for g in self.detect.get("any_glob", []):
            if glob.glob(_expand(g, env)):
                hits.append(f"install {g.split('/')[-1]}")
        return bool(hits), hits


def _expand(template: str, env: ClientEnv) -> str:
    local = os.environ.get("LOCALAPPDATA", str(env.home / "AppData" / "Local"))
    return (template.replace("{codex_home}", str(env.codex_home)).replace("{claude_dir}", str(env.claude_dir))
            .replace("{claude_global_json}", str(env.claude_global_json)).replace("{home}", str(env.home))
            .replace("{localappdata}", local))


def parse_profile(data: dict[str, Any], source: str = "builtin") -> Profile:
    if not isinstance(data, dict):
        raise ProfileError("profile must be a mapping")
    sv = data.get("schema_version")
    if sv not in SUPPORTED_SCHEMA_VERSIONS:
        raise ProfileError(f"unsupported profile schema_version {sv!r}")
    for key in ("id", "display_name", "profile_version", "detect", "mcp"):
        if key not in data:
            raise ProfileError(f"profile missing '{key}'")
    mcp = data["mcp"] or {}
    if mcp.get("format") not in MCP_FORMATS:
        raise ProfileError(f"unknown mcp format {mcp.get('format')!r}")
    hooks = data.get("hooks")
    if hooks:
        if hooks.get("format") not in HOOK_FORMATS:
            raise ProfileError(f"unknown hooks format {hooks.get('format')!r}")
        if hooks.get("transport") not in TRANSPORTS:
            raise ProfileError(f"unknown hook transport {hooks.get('transport')!r}")
    detect = data["detect"] or {}
    return Profile(
        id=str(data["id"]), display_name=str(data["display_name"]), profile_version=int(data["profile_version"]),
        detect={k: list(detect.get(k) or []) for k in ("any_path", "any_command", "any_glob")},
        mcp=dict(mcp), hooks=dict(hooks) if hooks else None,
        transcripts=dict(data["transcripts"]) if data.get("transcripts") else None,
        tools={k: list(v) for k, v in (data.get("tools") or {}).items()},
        completion_claim=dict(data.get("completion_claim") or {}),
        effort_control=str(data.get("effort_control", "advisory")),
        native_tool_deferral=bool(data.get("native_tool_deferral", False)), source=source)

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
MCP_FORMATS = {"toml_block", "json_named_entry", "jsonc_named_entry", "yaml_named_entry", "print_only"}
HOOK_FORMATS = {"json_hook_groups", "cursor_hooks_v1", "vscode_hooks_file", "gemini_hook_groups"}
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
    # MCP ``clientInfo.name`` values this client reports (lower-case, exact or ``prefix*``), so a
    # launched shim can be attributed to the right client (T1 evidence).
    mcp_client_names: tuple[str, ...] = ()
    source: str = "builtin"

    def matches_client_name(self, name: str) -> bool:
        n = name.lower().strip()
        for pat in self.mcp_client_names:
            if (pat.endswith("*") and n.startswith(pat[:-1])) or n == pat:
                return True
        return False

    def expand(self, template: Any, env: ClientEnv) -> Path | None:
        """A path template; or a mapping of platform -> template (``windows``/``macos``/``linux``,
        ``default`` as fallback); or a list of candidates, resolved to the first existing file,
        else the first whose parent directory exists, else the first. ``None`` when the client has
        no file on this platform."""
        if isinstance(template, dict):
            template = template.get(env.platform, template.get("default"))
        if template is None:
            return None
        if isinstance(template, list):
            cands = [Path(_expand(str(t), env)) for t in template]
            for c in cands:
                if c.is_file():
                    return c
            for c in cands:
                if c.parent.is_dir():
                    return c
            return cands[0] if cands else None
        return Path(_expand(str(template), env))

    def detected(self, env: ClientEnv) -> tuple[bool, list[str]]:
        hits: list[str] = []
        for t in self.detect.get("any_path", []):
            p = Path(_expand(t, env))
            if p.exists():
                hits.append(f"path {p}")
        for t in self.detect.get("any_path_" + env.platform, []):
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
    local = str(env.localappdata) if env.localappdata else os.environ.get(
        "LOCALAPPDATA", str(env.home / "AppData" / "Local"))
    appdata = str(env.appdata) if env.appdata else str(env.home / "AppData" / "Roaming")
    xdg = str(env.xdg_config) if env.xdg_config else str(env.home / ".config")
    return (template.replace("{codex_home}", str(env.codex_home)).replace("{claude_dir}", str(env.claude_dir))
            .replace("{claude_global_json}", str(env.claude_global_json)).replace("{home}", str(env.home))
            .replace("{localappdata}", local).replace("{appdata}", appdata).replace("{xdg_config}", xdg))


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
        detect={k: list(detect.get(k) or []) for k in ("any_path", "any_command", "any_glob", "any_path_windows",
                                                          "any_path_macos", "any_path_linux")},
        mcp=dict(mcp), hooks=dict(hooks) if hooks else None,
        transcripts=dict(data["transcripts"]) if data.get("transcripts") else None,
        tools={k: list(v) for k, v in (data.get("tools") or {}).items()},
        completion_claim=dict(data.get("completion_claim") or {}),
        effort_control=str(data.get("effort_control", "advisory")),
        native_tool_deferral=bool(data.get("native_tool_deferral", False)),
        mcp_client_names=tuple(str(x).lower() for x in data.get("mcp_client_names") or []), source=source)

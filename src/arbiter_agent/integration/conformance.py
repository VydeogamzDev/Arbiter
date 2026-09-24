"""Conformance checks: is each client surface still configured the way Arbiter set it up?

These are *configured* checks (static). The probe combines them with *observed* evidence
(the client actually launched the shim, fired hooks, wrote transcripts) to get verified tiers.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients import mcp_entry, text_config
from arbiter_agent.clients.claude_code import hooks as claude_hooks
from arbiter_agent.clients.client_env import ClientEnv
from arbiter_agent.clients.codex import hooks as codex_hooks
from arbiter_agent.clients.profile_schema import Profile
from arbiter_agent.clients.watchers.parsers import PARSERS


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def check_mcp(profile: Profile, env: ClientEnv) -> Check:
    path = profile.expand(profile.mcp.get("file"), env)
    if path is None:
        return Check("mcp", False, "print-only client")
    raw = cm.read_bytes(path)
    if raw is None:
        return Check("mcp", False, f"{path} missing")
    try:
        fmt = profile.mcp["format"]
        if fmt == "toml_block":
            data = tomllib.loads(raw.decode("utf-8"))
            entry = cm._dig(data, profile.mcp["table"])
            ok = isinstance(entry, dict) and cm.toml_block_present(raw)
        elif profile.id == "claude_code":
            data = json.loads(raw.decode("utf-8-sig"))
            entry = (data.get(profile.mcp["container"]) or {}).get(profile.mcp["name"])
            ok = claude_hooks.mcp_is_ours(entry)
        else:
            if fmt == "yaml_named_entry":
                data = text_config._yaml_load(raw, path)
            elif fmt == "jsonc_named_entry":
                data = text_config.load_jsonc(raw, path)
            else:
                data = json.loads(raw.decode("utf-8-sig"))
            box: Any = data
            for k in mcp_entry.container_path(profile.mcp):
                box = box.get(k) if isinstance(box, dict) else None
            entry = box.get(str(profile.mcp.get("name", "arbiter"))) if isinstance(box, dict) else None
            ok = mcp_entry.is_ours(entry)
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError, cm.ConfigParseError) as exc:
        return Check("mcp", False, f"{path} unreadable: {exc}")
    return Check("mcp", ok, f"entry {'present' if ok else 'missing'} in {path}", {"entry": entry})


def check_hooks(profile: Profile, env: ClientEnv) -> Check:
    if not profile.hooks:
        return Check("hooks", False, "client has no hook system profile")
    path = profile.expand(profile.hooks["file"], env)
    assert path is not None
    raw = cm.read_bytes(path)
    from arbiter_agent.clients.hook_dialects import DIALECTS

    dialect = DIALECTS.get(profile.id)
    if dialect is not None:
        n, expected = dialect.count(raw, path), dialect.expected_handlers
        ok = n >= expected
        return Check("hooks", ok, f"{n}/{expected} Arbiter hooks in {path}", {"groups": n, "expected": expected})
    if profile.id == "codex":
        is_ours, expected = codex_hooks.is_ours, len(codex_hooks.EVENT_FIELDS)
    else:
        is_ours, expected = claude_hooks.is_ours, len(claude_hooks.EVENTS)
    n = cm.count_hook_groups(raw, path, is_ours)
    ok = n >= expected
    return Check("hooks", ok, f"{n}/{expected} Arbiter hook groups in {path}", {"groups": n, "expected": expected})


def codex_trust_state(env: ClientEnv) -> Check:
    """Best-effort static trust check: does config.toml carry a trusted_hash for Arbiter's
    hooks.json entries? (Exact verification needs the app-server's hooks/list.)"""
    raw = cm.read_bytes(env.codex_config)
    if raw is None:
        return Check("codex_trust", False, "config.toml missing")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return Check("codex_trust", False, f"config.toml unreadable: {exc}")
    state = ((data.get("hooks") or {}).get("state") or {})
    hooks_path = str(env.codex_hooks).lower().replace("/", "\\")
    ours = {k: v for k, v in state.items() if k.lower().replace("/", "\\").startswith(hooks_path)}
    trusted = [k for k, v in ours.items() if isinstance(v, dict) and v.get("trusted_hash")]
    ok = len(trusted) >= len(codex_hooks.EVENT_FIELDS)
    detail = (f"{len(trusted)} of {len(codex_hooks.EVENT_FIELDS)} Arbiter hooks have a trust record"
              + ("" if ok else " (trust them in /hooks or the desktop Hooks settings)"))
    return Check("codex_trust", ok, detail, {"trusted": len(trusted)})


def latest_transcript(profile: Profile, env: ClientEnv) -> Path | None:
    if not profile.transcripts:
        return None
    root = profile.expand(profile.transcripts["root"], env)
    if root is None or not root.is_dir():
        return None
    files = list(root.glob(profile.transcripts["glob"]))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def check_transcripts(profile: Profile, env: ClientEnv, max_lines: int = 50) -> Check:
    if not profile.transcripts:
        return Check("transcripts", False, "no transcript profile")
    latest = latest_transcript(profile, env)
    if latest is None:
        return Check("transcripts", False, "no transcripts found yet")
    version, parse = PARSERS[profile.transcripts["parser"]]
    ctx: dict[str, Any] = {}
    parsed = errors = 0
    with open(latest, "rb") as f:
        for i, raw in enumerate(f):
            if i >= max_lines:
                break
            try:
                parsed += len(parse(raw.decode("utf-8", "replace"), ctx))
            except (ValueError, KeyError, TypeError, AttributeError):
                errors += 1
    # tolerate an occasional bad line (e.g. a half-written last line), not a format change
    ok = parsed > 0 and errors <= max(1, int(0.05 * (parsed + errors)))
    return Check("transcripts", ok, f"{profile.transcripts['parser']} v{version}: {parsed} records, {errors} errors "
                                    f"in {latest.name}", {"parser_version": version, "file": str(latest)})

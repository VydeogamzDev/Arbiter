"""``arbiter slim``: turn off Claude Code features a coding session doesn't use, so every model call
carries a smaller context (opt-in, reversible).

About two thirds of a benchmark run's cost was Claude Code's own fixed context, not the agent's work:
~39k tokens of tool definitions and system prompt on every call, most of it for features coding
tasks never touch (the desktop app's Artifact tool alone is ~11k tokens). Measured on a trivial
prompt (2026-09-26):

- standard: 39.3k -> 22.0k tokens per call. Off: artifacts, workflows, scheduled tasks and wake-ups,
  the bundled claude-api / claude-code skills, and desktop-only tools (ReportFindings, ListAgents,
  ShareOnboardingGuide, DesignSync, PushNotification, RemoteTrigger).
- lean: 16.5k. Also off: the Explore/Plan subagents, the advisor tool, agent view, background shell
  tasks and auto memory.

Kept: Read/Edit/Write/Glob/Grep, the shell, subagents (Task), web fetch/search, ToolSearch (it loads
MCP tools, Arbiter's included) and everything MCP.

It writes only to Claude Code's user settings (``env`` keys the user hasn't set, and
``permissions.deny`` entries for tools), backs the file up first, and records exactly what it added
so ``arbiter slim off`` removes that and nothing else. Some switches are read at startup, so they
take effect in new sessions. This is the one place Arbiter writes a permission rule: deny-only,
explicit opt-in, never part of ``arbiter setup``.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients.client_env import ClientEnv, current_env
from arbiter_agent.paths import ArbiterPaths, write_private

STANDARD_ENV = ["CLAUDE_CODE_DISABLE_ARTIFACT", "CLAUDE_CODE_DISABLE_WORKFLOWS", "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS",
                "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL", "CLAUDE_CODE_DISABLE_CLAUDE_CODE_SKILL"]
LEAN_ENV = ["CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS", "CLAUDE_CODE_DISABLE_ADVISOR_TOOL",
            "CLAUDE_CODE_DISABLE_AGENT_VIEW", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"]
# CLAUDE_CODE_DISABLE_CRON is read before settings' env applies, so cron tools are denied instead.
DENY = ["ScheduleWakeup", "ReportFindings", "ListAgents", "ShareOnboardingGuide", "DesignSync", "PushNotification",
        "RemoteTrigger", "CronCreate", "CronDelete", "CronList"]

PROFILES: dict[str, dict[str, Any]] = {
    "standard": {"env": STANDARD_ENV, "deny": DENY, "tokens": "39.3k -> 22.0k per call",
                 "off": "artifacts, workflows, scheduled tasks and wake-ups, the bundled claude-api/claude-code "
                        "skills, and desktop-only tools (ReportFindings, ListAgents, ShareOnboardingGuide, DesignSync, "
                        "PushNotification, RemoteTrigger)"},
    "lean": {"env": STANDARD_ENV + LEAN_ENV, "deny": DENY, "tokens": "39.3k -> 16.5k per call",
             "off": "everything in standard, plus the Explore/Plan subagents, the advisor tool, agent view, background "
                    "shell tasks and auto memory"},
}


def settings_for(profile: str) -> dict[str, Any]:
    """The settings fragment a profile adds (also used by the benchmark harness)."""
    p = PROFILES[profile]
    return {"env": {k: "1" for k in p["env"]}, "permissions": {"deny": list(p["deny"])}}


def _record_path(paths: ArbiterPaths) -> Path:
    return paths.state / "slim.json"


def load_record(paths: ArbiterPaths) -> dict[str, Any] | None:
    try:
        return dict(json.loads(_record_path(paths).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _apply(data: dict[str, Any], profile: str) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    frag = settings_for(profile)
    env = dict(data.get("env") or {})
    added_env = {k: v for k, v in frag["env"].items() if k not in env}
    env.update(added_env)
    perms = dict(data.get("permissions") or {})
    deny = list(perms.get("deny") or [])
    added_deny = [t for t in frag["permissions"]["deny"] if t not in deny]
    deny += added_deny
    if added_env:
        data["env"] = env
    if added_deny:
        perms["deny"] = deny
        data["permissions"] = perms
    return data, added_env, added_deny


def plan(env: ClientEnv, profile: str) -> tuple[Path, bytes | None, bytes, dict[str, str], list[str]]:
    target = env.claude_settings
    before = cm.read_bytes(target)
    holder: dict[str, Any] = {}

    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        new, holder["env"], holder["deny"] = _apply(d, profile)
        return new

    after = cm.json_transform(before, target, mutate)
    return target, before, after, holder["env"], holder["deny"]


def slim_on(paths: ArbiterPaths, profile: str = "standard", *, dry_run: bool = False,
            env: ClientEnv | None = None) -> str:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose {', '.join(PROFILES)}")
    rec = load_record(paths)
    if rec and not dry_run:
        return f"slim mode is already on ({rec['profile']}); run `arbiter slim off` first to change profile"
    env = env or current_env()
    target, before, _after, added_env, added_deny = plan(env, profile)
    p = PROFILES[profile]
    lines = [f"Slim mode '{profile}' for Claude Code ({p['tokens']} on a trivial prompt).",
             f"Turns off: {p['off']}.",
             f"File: {target}",
             f"  adds env: {', '.join(added_env) or '(nothing new)'}",
             f"  adds permissions.deny: {', '.join(added_deny) or '(nothing new)'}"]
    if dry_run:
        return "\n".join(lines + ["(dry run: nothing written)"])
    backup = None
    if before is not None:
        paths.backups.mkdir(parents=True, exist_ok=True)
        backup = paths.backups / f"claude-settings.slim.{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(target, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    cm.write_with_recheck(target, lambda cur: cm.json_transform(cur, target, lambda d: _apply(d, profile)[0]))
    write_private(_record_path(paths), json.dumps({
        "profile": profile, "client": "claude_code", "settings": str(target), "added_env": added_env,
        "added_deny": added_deny, "backup": str(backup) if backup else None, "at": time.time()}, indent=2).encode())
    return "\n".join(lines + ["Done. Start a new Claude Code session for it to apply."])


def slim_off(paths: ArbiterPaths) -> str:
    rec = load_record(paths)
    if not rec:
        return "slim mode is off"
    target = Path(rec["settings"])
    added_env, added_deny = dict(rec.get("added_env") or {}), list(rec.get("added_deny") or [])

    def undo(d: dict[str, Any]) -> dict[str, Any]:
        env = dict(d.get("env") or {})
        for k, v in added_env.items():
            if env.get(k) == v:                      # only if the user hasn't changed it since
                env.pop(k)
        if env:
            d["env"] = env
        else:
            d.pop("env", None)
        perms = dict(d.get("permissions") or {})
        deny = [t for t in perms.get("deny") or [] if t not in added_deny]
        if deny:
            perms["deny"] = deny
        else:
            perms.pop("deny", None)
        if perms:
            d["permissions"] = perms
        else:
            d.pop("permissions", None)
        return d

    if target.exists():
        cm.write_with_recheck(target, lambda cur: cm.json_transform(cur, target, undo))
    _record_path(paths).unlink(missing_ok=True)
    return f"slim mode off: removed {len(added_env)} env switch(es) and {len(added_deny)} deny rule(s) from {target}"


def status(paths: ArbiterPaths) -> str:
    rec = load_record(paths)
    if not rec:
        return ("slim mode: off. `arbiter slim on` (standard) or `arbiter slim on --profile lean`; "
                "`--dry-run` shows what changes")
    p = PROFILES.get(rec["profile"], {})
    return (f"slim mode: on ({rec['profile']}; {p.get('tokens', '')}) in {rec['settings']}\n"
            f"turned off: {p.get('off', '')}")

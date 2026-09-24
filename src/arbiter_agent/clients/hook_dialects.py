"""Hook dialects for M5 clients (spec §4.4.2; decision 0027).

Each dialect knows, for one client:
- which native events Arbiter registers, and how to write/remove its handlers in the client's
  hook file (format differs per client);
- how to translate the client's payload into Arbiter's canonical (Claude-style) shape before
  ingest: ``hook_event_name``, ``session_id``, ``cwd``, ``prompt``, ``tool_*``,
  ``last_assistant_message``, ``stop_hook_active``;
- how to translate Arbiter's decision back into what that client accepts. A client that can't
  block a stop gets no block (annotate only), whatever the gate mode.

All three use the ``command`` transport: ``arbiter hook <client> <event>``. Only formats that are
documented are implemented; everything else stays T1 (MCP) only.
"""

from __future__ import annotations

import copy
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm

HOOK_TIMEOUT_S = 5


def hook_command(argv: list[str], client: str, event: str) -> str:
    """A shell command string for ``arbiter hook <client> <event>``. Paths with spaces are quoted;
    that works in cmd and POSIX shells (PowerShell needs a path without spaces)."""
    parts = [*argv[:-1], "hook", client, event] if argv[-1:] == ["mcp"] else [*argv, "hook", client, event]
    return " ".join(f'"{p}"' if (" " in p and not p.startswith('"')) else p for p in parts)


def _is_our_command(cmd: Any, client: str) -> bool:
    if not isinstance(cmd, str):
        return False
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        toks = cmd.split()
    toks = [t.strip('"') for t in toks]
    return "arbiter" in cmd.lower() and "hook" in toks and client in toks


@dataclass(frozen=True)
class Dialect:
    client: str
    hook_format: str
    events: tuple[str, ...]                                  # native event names Arbiter registers
    canonical: dict[str, str]                                # native event -> canonical event
    upsert: Callable[[bytes | None, Path, list[str]], bytes]
    remove: Callable[[bytes, Path], bytes]
    count: Callable[[bytes | None, Path], int]
    inbound: Callable[[dict[str, Any], str | None], dict[str, Any]]
    outbound: Callable[[str, dict[str, Any]], dict[str, Any]]
    can_block_stop: bool
    expected_handlers: int = 0


# ----------------------------------------------------------------------------- Cursor
CURSOR_EVENTS = ("sessionStart", "sessionEnd", "beforeSubmitPrompt", "preToolUse", "postToolUse",
                 "postToolUseFailure", "afterAgentResponse", "stop", "preCompact")
CURSOR_CANON = {"sessionStart": "SessionStart", "sessionEnd": "SessionEnd", "beforeSubmitPrompt": "UserPromptSubmit",
                "preToolUse": "PreToolUse", "postToolUse": "PostToolUse", "postToolUseFailure": "PostToolUseFailure",
                "afterAgentResponse": "AgentResponse", "stop": "Stop", "preCompact": "PreCompact"}


def _cursor_upsert(original: bytes | None, path: Path, argv: list[str]) -> bytes:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        d.setdefault("version", 1)
        hooks = d.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise cm.ConfigConflict("'hooks' isn't an object")
        for ev in CURSOR_EVENTS:
            lst = hooks.setdefault(ev, [])
            if not isinstance(lst, list):
                raise cm.ConfigConflict(f"'hooks.{ev}' isn't a list")
            lst[:] = [h for h in lst if not (isinstance(h, dict) and _is_our_command(h.get("command"), "cursor"))]
            lst.append({"command": hook_command(argv, "cursor", ev), "timeout": HOOK_TIMEOUT_S})
        return d

    return cm.json_transform(original, path, mutate)


def _cursor_remove(current: bytes, path: Path) -> bytes:
    def mutate(d: dict[str, Any]) -> dict[str, Any]:
        hooks = d.get("hooks")
        if isinstance(hooks, dict):
            for ev in list(hooks):
                lst = hooks[ev]
                if isinstance(lst, list):
                    lst[:] = [h for h in lst if not (isinstance(h, dict) and _is_our_command(h.get("command"),
                                                                                                  "cursor"))]
                    if not lst:
                        del hooks[ev]
            if not hooks:
                del d["hooks"]
                if d.get("version") == 1 and len(d) == 1:
                    del d["version"]
        return d

    return cm.json_transform(current, path, mutate)


def _cursor_count(raw: bytes | None, path: Path) -> int:
    try:
        d = cm.load_json(raw, path)
    except cm.ConfigParseError:
        return 0
    raw_hooks = d.get("hooks")
    hooks: dict[str, Any] = raw_hooks if isinstance(raw_hooks, dict) else {}
    n = 0
    for ev in CURSOR_EVENTS:
        if any(isinstance(h, dict) and _is_our_command(h.get("command"), "cursor") for h in (hooks.get(ev) or [])):
            n += 1
    return n


def _cursor_in(p: dict[str, Any], hint: str | None) -> dict[str, Any]:
    native = str(p.get("hook_event_name") or hint or "")
    out = dict(p)
    out["hook_event_name"] = CURSOR_CANON.get(native, native)
    out.setdefault("native_event", native)
    if p.get("conversation_id") and not p.get("session_id"):
        out["session_id"] = p["conversation_id"]
    if p.get("generation_id") and not p.get("turn_id"):
        out["turn_id"] = p["generation_id"]
    roots = p.get("workspace_roots")
    if not p.get("cwd") and isinstance(roots, list) and roots:
        out["cwd"] = str(roots[0])
    if "tool_output" in p and "tool_response" not in p:
        out["tool_response"] = p["tool_output"]
    if native == "afterAgentResponse" and "text" in p:
        out["last_assistant_message"] = p["text"]
    return out


def _cursor_out(event: str, resp: dict[str, Any]) -> dict[str, Any]:
    ctx = (resp.get("hookSpecificOutput") or {}).get("additionalContext")
    if event == "sessionStart" and ctx:
        return {"additional_context": ctx}
    if event in ("postToolUse", "postToolUseFailure") and ctx:
        return {"additional_context": ctx}
    return {}   # stop can't be blocked in Cursor (only a follow-up turn): annotate only


# ----------------------------------------------------------------------------- VS Code + Copilot
VSCODE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "SubagentStart",
                 "SubagentStop", "Stop")


def _vscode_doc(argv: list[str]) -> dict[str, Any]:
    return {"hooks": {ev: [{"type": "command", "command": hook_command(argv, "vscode", ev),
                            "timeout": HOOK_TIMEOUT_S}] for ev in VSCODE_EVENTS}}


def _vscode_all_ours(d: dict[str, Any]) -> bool:
    hooks = d.get("hooks")
    if not isinstance(hooks, dict):
        return not d
    return all(isinstance(h, dict) and _is_our_command(h.get("command"), "vscode")
               for lst in hooks.values() if isinstance(lst, list) for h in lst)


def _vscode_upsert(original: bytes | None, path: Path, argv: list[str]) -> bytes:
    """Arbiter owns its own file (``~/.copilot/hooks/arbiter.json``), so it's written whole."""
    if original and original.strip():
        if not _vscode_all_ours(cm.load_json(original, path)):
            raise cm.ConfigConflict(f"{path} has hooks that aren't Arbiter's")
    return cm.dump_json(_vscode_doc(argv), cm.JsonStyle(2, True))


def _vscode_remove(current: bytes, path: Path) -> bytes:
    d = cm.load_json(current, path)
    if _vscode_all_ours(d):
        return b"{}\n"
    hooks = d.get("hooks") or {}
    for ev in list(hooks):
        hooks[ev] = [h for h in hooks[ev] if not (isinstance(h, dict) and _is_our_command(h.get("command"),
                                                                                          "vscode"))]
        if not hooks[ev]:
            del hooks[ev]
    return cm.dump_json(d, cm.JsonStyle.detect(current))


def _vscode_count(raw: bytes | None, path: Path) -> int:
    try:
        hooks = cm.load_json(raw, path).get("hooks") or {}
    except cm.ConfigParseError:
        return 0
    return sum(1 for ev in VSCODE_EVENTS if any(isinstance(h, dict) and _is_our_command(h.get("command"), "vscode")
                                                for h in (hooks.get(ev) or [])))


def _vscode_in(p: dict[str, Any], hint: str | None) -> dict[str, Any]:
    out = dict(p)
    out.setdefault("hook_event_name", hint)
    return out


def _vscode_out(event: str, resp: dict[str, Any]) -> dict[str, Any]:
    if event == "Stop" and resp.get("decision") == "block":
        return {"hookSpecificOutput": {"hookEventName": "Stop", "decision": "block", "reason": resp.get("reason", "")}}
    ctx = (resp.get("hookSpecificOutput") or {}).get("additionalContext")
    if ctx and event in ("SessionStart", "SubagentStart", "PostToolUse"):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}
    return {}


# ----------------------------------------------------------------------------- Gemini CLI
GEMINI_EVENTS = ("SessionStart", "SessionEnd", "BeforeAgent", "AfterAgent", "BeforeTool", "AfterTool", "PreCompress")
GEMINI_CANON = {"SessionStart": "SessionStart", "SessionEnd": "SessionEnd", "BeforeAgent": "UserPromptSubmit",
                "AfterAgent": "Stop", "BeforeTool": "PreToolUse", "AfterTool": "PostToolUse",
                "PreCompress": "PreCompact"}


def _gemini_group(argv: list[str], ev: str) -> dict[str, Any]:
    handler = {"name": "arbiter", "type": "command", "command": hook_command(argv, "gemini_cli", ev),
               "timeout": HOOK_TIMEOUT_S * 1000}   # Gemini timeouts are milliseconds
    g: dict[str, Any] = {"hooks": [handler]}
    if ev in ("BeforeTool", "AfterTool"):
        g = {"matcher": "*", **g}
    return g


def _gemini_ours(h: dict[str, Any]) -> bool:
    return _is_our_command(h.get("command"), "gemini_cli")


def _gemini_upsert(original: bytes | None, path: Path, argv: list[str]) -> bytes:
    groups = {ev: _gemini_group(argv, ev) for ev in GEMINI_EVENTS}
    return cm.json_transform(original, path, cm.upsert_hook_groups(groups, _gemini_ours))


def _gemini_remove(current: bytes, path: Path) -> bytes:
    return cm.json_transform(current, path, cm.remove_hook_groups(_gemini_ours))


def _gemini_count(raw: bytes | None, path: Path) -> int:
    return cm.count_hook_groups(raw, path, _gemini_ours)


def _gemini_in(p: dict[str, Any], hint: str | None) -> dict[str, Any]:
    native = str(p.get("hook_event_name") or hint or "")
    out = dict(p)
    out["hook_event_name"] = GEMINI_CANON.get(native, native)
    out.setdefault("native_event", native)
    if native == "AfterAgent" and "prompt_response" in p:
        out["last_assistant_message"] = p["prompt_response"]
    if "tool_output" in p and "tool_response" not in p:
        out["tool_response"] = p["tool_output"]
    return out


def _gemini_out(event: str, resp: dict[str, Any]) -> dict[str, Any]:
    if event == "AfterAgent" and resp.get("decision") == "block":
        return {"decision": "deny", "reason": resp.get("reason", "")}   # Gemini retries the turn with the reason
    ctx = (resp.get("hookSpecificOutput") or {}).get("additionalContext")
    if ctx and event in ("BeforeAgent", "AfterTool", "SessionStart"):
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}
    return {}


DIALECTS: dict[str, Dialect] = {
    "cursor": Dialect("cursor", "cursor_hooks_v1", CURSOR_EVENTS, CURSOR_CANON, _cursor_upsert, _cursor_remove,
                      _cursor_count, _cursor_in, _cursor_out, can_block_stop=False,
                      expected_handlers=len(CURSOR_EVENTS)),
    "vscode": Dialect("vscode", "vscode_hooks_file", VSCODE_EVENTS, {e: e for e in VSCODE_EVENTS}, _vscode_upsert,
                      _vscode_remove, _vscode_count, _vscode_in, _vscode_out, can_block_stop=True,
                      expected_handlers=len(VSCODE_EVENTS)),
    "gemini_cli": Dialect("gemini_cli", "gemini_hook_groups", GEMINI_EVENTS, GEMINI_CANON, _gemini_upsert,
                          _gemini_remove, _gemini_count, _gemini_in, _gemini_out, can_block_stop=True,
                          expected_handlers=len(GEMINI_EVENTS)),
}
HOOK_FORMATS = {d.hook_format for d in DIALECTS.values()}


def adapt_inbound(client: str, payload: Any, hint: str | None) -> tuple[Any, str | None]:
    """(canonical payload, canonical event) for a client with a dialect; unchanged otherwise."""
    d = DIALECTS.get(client)
    if d is None or not isinstance(payload, dict):
        return payload, hint
    out = d.inbound(copy.deepcopy(payload), hint)
    return out, str(out.get("hook_event_name") or hint or "")


def adapt_outbound(client: str, native_event: str | None, response: dict[str, Any]) -> dict[str, Any]:
    d = DIALECTS.get(client)
    if d is None:
        return response
    if not response:
        return {}
    return d.outbound(str(native_event or ""), response)


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))

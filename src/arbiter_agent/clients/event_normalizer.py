"""Normalize client hook payloads and transcript records into one envelope (spec §6.1, §4.4.4).

Hook payloads from Codex and Claude Code share a shape (decisions 0015, 0017): an event name in
``hook_event_name`` plus ``session_id``, ``transcript_path``, ``cwd`` and event-specific fields.
Client specifics live in :data:`CLIENTS`; controller code only sees :class:`NormalizedEvent`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.clients import event_dedupe

# Hook event name (as sent by clients) -> normalized event type.
HOOK_EVENTS: dict[str, str] = {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "user_prompt",
    "PreToolUse": "pre_tool",
    "PostToolUse": "post_tool",
    "PostToolUseFailure": "post_tool_failure",
    "PermissionRequest": "permission_request",
    "Stop": "stop",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "Interrupt": "interrupt",
    "Notification": "notification",
}
_CAMEL = {k[0].lower() + k[1:]: k for k in HOOK_EVENTS}  # app-server style names (e.g. "stop")


@dataclass(frozen=True)
class ClientSpec:
    client_id: str
    profile_version: int
    # Field that carries the per-turn id in hook payloads.
    turn_field: str
    # Shell tool names this client uses (normalized to "shell").
    shell_tools: tuple[str, ...]


CLIENTS: dict[str, ClientSpec] = {
    "codex": ClientSpec("codex", 1, "turn_id", ("Bash", "exec_command", "exec", "shell")),
    "claude_code": ClientSpec("claude_code", 1, "prompt_id", ("Bash", "PowerShell")),
    "fake": ClientSpec("fake", 1, "turn_id", ("Bash",)),
    "generic": ClientSpec("generic", 1, "turn_id", ("Bash", "shell")),
}

# Fields kept in the non-sensitive attrs column (used by replay; never contains user text).
ATTR_FIELDS = ("tool_use_id", "tool_name", "stop_hook_active", "source", "reason", "trigger", "agent_type",
               "permission_mode", "model")


class NormalizationError(ValueError):
    pass


@dataclass
class NormalizedEvent:
    client_id: str
    profile_version: int
    native_session_id: str | None
    surface: str
    event_type: str
    raw_hash: str
    payload: dict[str, Any]
    turn_id: str | None = None
    upstream_id: str | None = None
    upstream_ts: str | None = None
    cwd: str | None = None
    transcript_path: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)
    ingest_channel: str | None = None
    idempotency_key: str = ""
    dedupe_key: str | None = None

    @property
    def session_id(self) -> str | None:
        if not self.native_session_id:
            return None
        return f"{self.client_id}:{self.native_session_id}"


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def resolve_client(client: str) -> ClientSpec:
    try:
        return CLIENTS[client]
    except KeyError as exc:
        raise NormalizationError(f"unknown client '{client}'") from exc


def hook_event_type(name: str | None) -> str:
    if not name:
        return "unknown"
    name = _CAMEL.get(name, name)
    return HOOK_EVENTS.get(name, "unknown")


def normalize_hook(client: str, payload: dict[str, Any], *, surface: str, keyer: Any,
                   event_hint: str | None = None, channel: str | None = None) -> NormalizedEvent:
    """Normalize a hook payload. ``keyer(bytes) -> hex`` provides keyed hashes (install key).
    ``payload`` must already be redacted; raw_hash is computed over the redacted form so it
    never fingerprints secret material."""
    spec = resolve_client(client)
    if not isinstance(payload, dict):
        raise NormalizationError("hook payload must be a JSON object")
    name = payload.get("hook_event_name") or event_hint
    etype = hook_event_type(name)
    attrs = {k: payload[k] for k in ATTR_FIELDS if k in payload and not isinstance(payload[k], (dict, list))}
    if "tool_name" in attrs:
        attrs["tool_kind"] = "shell" if attrs["tool_name"] in spec.shell_tools else "other"
    raw_hash = keyer(canonical_bytes({"event": name, "payload": payload}))
    ev = NormalizedEvent(
        client_id=spec.client_id, profile_version=spec.profile_version,
        native_session_id=_str(payload.get("session_id")), surface=surface, event_type=etype,
        raw_hash=raw_hash, payload=payload, turn_id=_str(payload.get(spec.turn_field) or payload.get("turn_id")),
        upstream_id=_str(payload.get("tool_use_id")), cwd=_str(payload.get("cwd")),
        transcript_path=_str(payload.get("transcript_path")), attrs=attrs, ingest_channel=channel,
    )
    if etype == "unknown":
        ev.attrs["native_event"] = str(name)[:64]
    ev.idempotency_key = event_dedupe.idempotency_key(ev)
    ev.dedupe_key = event_dedupe.dedupe_key(ev)
    return ev


def normalize_transcript_record(client: str, record: dict[str, Any], *, session_id: str | None, record_id: str,
                                record_type: str, keyer: Any, tool_use_id: str | None = None,
                                cwd: str | None = None, transcript_path: str | None = None,
                                extra_attrs: dict[str, Any] | None = None) -> NormalizedEvent:
    """Normalize one transcript record already parsed by a client parser (M2 adds real parsers)."""
    spec = resolve_client(client)
    etype = "tool_result" if record_type == "tool_result" else f"transcript.{record_type}"
    attrs: dict[str, Any] = {"record_id": record_id, **(extra_attrs or {})}
    if tool_use_id:
        attrs["tool_use_id"] = tool_use_id
    ev = NormalizedEvent(
        client_id=spec.client_id, profile_version=spec.profile_version, native_session_id=session_id,
        surface="transcript", event_type=etype, raw_hash=keyer(canonical_bytes(record)), payload=record,
        upstream_id=tool_use_id or record_id, cwd=cwd, transcript_path=transcript_path, attrs=attrs,
    )
    ev.idempotency_key = event_dedupe.idempotency_key(ev)
    ev.dedupe_key = event_dedupe.dedupe_key(ev)
    return ev


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s[:512] if s else None

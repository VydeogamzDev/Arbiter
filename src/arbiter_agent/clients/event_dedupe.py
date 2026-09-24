"""Idempotency and surface-independent dedupe keys (spec §6.1, decision 0020).

- ``idempotency_key``: the same raw event re-delivered on the same surface -> dropped.
- ``dedupe_key``: the same logical occurrence seen on different surfaces (e.g. a Codex
  PostToolUse hook and the rollout's function_call_output share ``tool_use_id``/``call_id``).
  The first row is primary; later rows are kept as evidence with ``duplicate_of`` set, so the
  reducer counts the occurrence once while richer data (exit codes) is still retained.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arbiter_agent.clients.event_normalizer import NormalizedEvent


# Hook events that clients legitimately repeat with identical payloads when they carry no turn
# id (Claude Code: a second "continue", or a second "Done." stop after a block). For these the
# idempotency window is REDELIVERY_WINDOW_S instead of forever (see append_event).
REPEATABLE = ("user_prompt", "stop")
HOOK_SURFACES = ("hook", "http", "mcp")
REDELIVERY_WINDOW_S = 2.0


def windowed(ev: NormalizedEvent) -> bool:
    return ev.surface in HOOK_SURFACES and ev.event_type in REPEATABLE and not ev.turn_id


def idempotency_key(ev: NormalizedEvent) -> str:
    base = f"{ev.surface}:{ev.client_id}:{ev.native_session_id or '-'}:{ev.raw_hash}"
    if windowed(ev):
        return f"{base}:w{int(ev.received_at // REDELIVERY_WINDOW_S)}"
    return base


def dedupe_key(ev: NormalizedEvent) -> str | None:
    sess = ev.session_id
    if not sess:
        return None
    t = ev.event_type
    tool = ev.attrs.get("tool_use_id") or ev.upstream_id
    if t in ("post_tool", "tool_result") and tool:
        return f"{sess}:tool_result:{tool}"
    if t == "pre_tool" and tool:
        return f"{sess}:pre_tool:{tool}"
    if t == "user_prompt" and ev.turn_id:
        return f"{sess}:prompt:{ev.turn_id}"
    if t == "stop" and ev.turn_id:
        return f"{sess}:stop:{ev.turn_id}:{int(bool(ev.attrs.get('stop_hook_active')))}"
    if t == "session_end":
        return f"{sess}:session_end"
    if t.startswith("transcript.") and ev.attrs.get("record_id") is not None:
        return f"{sess}:tr:{ev.attrs['record_id']}"
    return None

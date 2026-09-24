"""Transcript parsers (decision 0020). Each parser turns one JSONL line into zero or more
records, using a per-file context dict (session id, cwd) that persists across polls.

Payloads are trimmed: transcripts contain whole system prompts and tool outputs, but Arbiter
needs ids, types, exit codes, usage and short text excerpts. Everything still passes through
redaction at ingest.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TEXT_CAP = 8000
OUTPUT_CAP = 16000


@dataclass
class TranscriptRecord:
    record_id: str
    record_type: str
    session_id: str | None
    payload: dict[str, Any]
    tool_use_id: str | None = None
    cwd: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)


Parser = Callable[[str, dict[str, Any]], list[TranscriptRecord]]


def _cap(s: Any, n: int = TEXT_CAP) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f"…[{len(s) - n} more chars]"


# ---------------------------------------------------------------------------- fake (tests)
def parse_fake_v1(line: str, ctx: dict[str, Any]) -> list[TranscriptRecord]:
    obj = json.loads(line)
    if not isinstance(obj, dict) or "id" not in obj:
        return []
    attrs = {k: obj[k] for k in ("exit_code", "tool_name") if k in obj}
    return [TranscriptRecord(str(obj["id"]), str(obj.get("type", "record")), obj.get("session_id"), obj,
                             obj.get("tool_use_id"), obj.get("cwd"), attrs)]


# ---------------------------------------------------------------------------- Codex rollout
def _codex_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict) and c.get("text"))
    return str(content or "")


def parse_codex_rollout_v1(line: str, ctx: dict[str, Any]) -> list[TranscriptRecord]:
    d = json.loads(line)
    if not isinstance(d, dict):
        return []
    t, p = d.get("type"), d.get("payload") or {}
    rid = f"o{d.get('ordinal', ctx.get('_n', 0))}"
    ctx["_n"] = int(ctx.get("_n", 0)) + 1
    if t == "session_meta":
        ctx.update(session_id=p.get("id") or p.get("session_id"), cwd=p.get("cwd"),
                   originator=p.get("originator"), cli_version=p.get("cli_version"))
        small = {k: p.get(k) for k in ("id", "cwd", "originator", "cli_version", "model_provider", "source",
                                       "history_mode", "timestamp")}
        return [TranscriptRecord(rid, "session_meta", ctx.get("session_id"), small, cwd=ctx.get("cwd"),
                                 attrs={"originator": p.get("originator"), "cli_version": p.get("cli_version")})]
    sid, cwd = ctx.get("session_id"), ctx.get("cwd")
    if t == "response_item":
        pt = p.get("type")
        if pt == "message":
            role = p.get("role")
            text = _codex_text(p.get("content"))
            injected = role == "user" and text.lstrip().startswith("<")
            kind = "user_message" if role == "user" else ("assistant_message" if role == "assistant" else None)
            if kind is None:
                return []
            return [TranscriptRecord(rid, kind, sid, {"role": role, "text": _cap(text)}, cwd=cwd,
                                     attrs={"injected": injected} if injected else {})]
        if pt in ("function_call", "custom_tool_call"):
            return [TranscriptRecord(rid, "tool_call", sid, {"name": p.get("name"), "call_id": p.get("call_id"),
                                                             "arguments": _cap(p.get("arguments") or p.get("input"))},
                                     tool_use_id=None, cwd=cwd,
                                     attrs={"tool_name": p.get("name"), "call_id": p.get("call_id")})]
        if pt == "function_call_output":
            return [TranscriptRecord(rid, "tool_output", sid, {"call_id": p.get("call_id"),
                                                               "output": _cap(p.get("output"), OUTPUT_CAP)}, cwd=cwd,
                                     attrs={"call_id": p.get("call_id")})]
        if pt == "custom_tool_call_output":
            out = p.get("output")
            text = _codex_text(out) if isinstance(out, list) else str(out or "")
            return [TranscriptRecord(rid, "tool_result", sid, {"call_id": p.get("call_id"),
                                                               "output": _cap(text, OUTPUT_CAP)},
                                     tool_use_id=p.get("call_id"), cwd=cwd, attrs={"tool_name": "exec"})]
        return []
    if t == "event_msg":
        et = p.get("type")
        if et == "item_completed" and isinstance(p.get("item"), dict) and p["item"].get("type") == "CommandExecution":
            it: dict[str, Any] = p["item"]
            cmd = it.get("command")
            cmd_s = " ".join(cmd) if isinstance(cmd, list) else str(cmd or "")
            dur = it.get("duration") or {}
            secs = (dur.get("secs", 0) + dur.get("nanos", 0) / 1e9) if isinstance(dur, dict) else None
            payload = {"command": _cap(cmd_s, 2000), "exit_code": it.get("exit_code"), "status": it.get("status"),
                       "stdout": _cap(it.get("stdout"), OUTPUT_CAP), "stderr": _cap(it.get("stderr"), OUTPUT_CAP)}
            return [TranscriptRecord(rid, "tool_result", sid, payload, tool_use_id=it.get("id"), cwd=cwd,
                                     attrs={"exit_code": it.get("exit_code"), "tool_name": "Bash",
                                            "duration_s": round(secs, 3) if secs is not None else None,
                                            "turn_id": p.get("turn_id")})]
        if et == "token_count":
            info = p.get("info") or {}
            last = info.get("last_token_usage") or {}
            rl = (p.get("rate_limits") or {}).get("primary") or {}
            attrs = {"input_tokens": last.get("input_tokens"), "cached_input_tokens": last.get("cached_input_tokens"),
                     "output_tokens": last.get("output_tokens"),
                     "reasoning_output_tokens": last.get("reasoning_output_tokens"),
                     "context_window": info.get("model_context_window"),
                     "rate_limit_used_percent": rl.get("used_percent"), "rate_limit_window_minutes":
                         rl.get("window_minutes"), "rate_limit_resets_at": rl.get("resets_at")}
            return [TranscriptRecord(rid, "usage", sid, {k: v for k, v in attrs.items() if v is not None}, cwd=cwd,
                                     attrs={k: v for k, v in attrs.items() if v is not None})]
        if et == "thread_settings_applied":
            s = p.get("thread_settings") or {}
            attrs = {"model": s.get("model"), "reasoning_effort": s.get("reasoning_effort")}
            return [TranscriptRecord(rid, "settings", sid, attrs, cwd=cwd, attrs=attrs)]
        if et in ("task_started", "task_complete"):
            return [TranscriptRecord(rid, et, sid, {"turn_id": p.get("turn_id")}, cwd=cwd,
                                     attrs={"turn_id": p.get("turn_id")})]
    return []


# ---------------------------------------------------------------------------- Claude Code
def parse_claude_code_v1(line: str, ctx: dict[str, Any]) -> list[TranscriptRecord]:
    d = json.loads(line)
    if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
        return []
    sid, cwd, uuid = d.get("sessionId"), d.get("cwd"), d.get("uuid") or f"n{ctx.get('_n', 0)}"
    ctx["_n"] = int(ctx.get("_n", 0)) + 1
    if sid:
        ctx["session_id"], ctx["cwd"] = sid, cwd
    msg = d.get("message") or {}
    content = msg.get("content")
    out: list[TranscriptRecord] = []
    if d["type"] == "user":
        if isinstance(content, str):
            if not d.get("isMeta"):
                out.append(TranscriptRecord(f"{uuid}:0", "user_message", sid, {"text": _cap(content)}, cwd=cwd,
                                            attrs={"prompt_id": d.get("promptId")}))
        elif isinstance(content, list):
            raw_tur = d.get("toolUseResult")
            tur: dict[str, Any] = raw_tur if isinstance(raw_tur, dict) else {}
            for i, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    payload = {"is_error": bool(item.get("is_error")), "stdout": _cap(tur.get("stdout"), OUTPUT_CAP),
                               "stderr": _cap(tur.get("stderr"), OUTPUT_CAP), "interrupted": tur.get("interrupted")}
                    out.append(TranscriptRecord(f"{uuid}:{i}", "tool_result", sid, payload,
                                                tool_use_id=item.get("tool_use_id"), cwd=cwd,
                                                attrs={"is_error": bool(item.get("is_error"))}))
                elif isinstance(item, dict) and item.get("type") == "text" and not d.get("isMeta"):
                    out.append(TranscriptRecord(f"{uuid}:{i}", "user_message", sid, {"text": _cap(item.get("text"))},
                                                cwd=cwd, attrs={"prompt_id": d.get("promptId")}))
    else:
        if isinstance(content, list):
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            if texts:
                out.append(TranscriptRecord(f"{uuid}:t", "assistant_message", sid, {"text": _cap("\n".join(texts))},
                                            cwd=cwd))
            for i, c in enumerate(content):
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    out.append(TranscriptRecord(f"{uuid}:{i}", "tool_call", sid,
                                                {"name": c.get("name"), "input": _cap(json.dumps(c.get("input")))},
                                                cwd=cwd, attrs={"tool_name": c.get("name"), "call_id": c.get("id")}))
        usage = msg.get("usage") or {}
        if usage:
            attrs = {"input_tokens": usage.get("input_tokens"), "cache_read_input_tokens":
                     usage.get("cache_read_input_tokens"), "cache_creation_input_tokens":
                     usage.get("cache_creation_input_tokens"), "output_tokens": usage.get("output_tokens"),
                     "model": msg.get("model")}
            attrs = {k: v for k, v in attrs.items() if v is not None}
            out.append(TranscriptRecord(f"{uuid}:u", "usage", sid, attrs, cwd=cwd, attrs=attrs))
    return out


PARSERS: dict[str, tuple[int, Parser]] = {
    "fake_v1": (1, parse_fake_v1),
    "codex_rollout_v1": (1, parse_codex_rollout_v1),
    "claude_code_v1": (1, parse_claude_code_v1),
}

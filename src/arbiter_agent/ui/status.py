"""Compact session status (spec M4.4): shown by ``arbiter status``, the MCP status tool, and,
when enabled, injected through hooks as ``additionalContext`` only when it changes and only
within ``hooks.max_injected_tokens``.
"""

from __future__ import annotations

import hashlib
from typing import Any

WORDING = "[Arbiter] Session state (informational, current turn only):"


def approx_tokens(text: str) -> int:
    return (len(text) + 3) // 4


def summary_line(state: dict[str, Any]) -> str:
    parts = [f"goal epoch {state.get('goal_epoch', 0)}"]
    c = state.get("counts") or {}
    if c:
        parts.append(f"contracts {c.get('pass', 0)} PASS/{c.get('fail', 0)} FAIL/{c.get('unknown', 0)} UNKNOWN"
                     + (f"/{c['waived']} WAIVED" if c.get("waived") else ""))
    else:
        parts.append("no contracts recorded")
    if state.get("uncovered"):
        parts.append(f"{state['uncovered']} uncovered request(s)")
    t = state.get("tests")
    if t:
        parts.append(f"tests {t.get('tests_passed', 0)} pass/{t.get('tests_failed', 0)} fail")
    if state.get("integrity"):
        parts.append(f"test integrity {state['integrity']}")
    if state.get("loop_alerts"):
        parts.append(f"{state['loop_alerts']} loop alert(s)")
    if state.get("last_verdict"):
        parts.append(f"last finish check: {state['last_verdict']}")
    return "; ".join(parts)


def injection(state: dict[str, Any], *, last_hash: str | None, max_tokens: int,
              only_on_change: bool = True) -> tuple[str | None, str]:
    """(text to inject or None, content hash)."""
    text = f"{WORDING} {summary_line(state)}."
    if approx_tokens(text) > max_tokens:
        text = text[: max(0, max_tokens * 4 - 1)] + "…"
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    if only_on_change and h == last_hash:
        return None, h
    return text, h


def hook_context(event_name: str, text: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}

"""Token/cache usage roll-ups from transcript ``usage`` records (Codex token_count, Claude usage)."""

from __future__ import annotations

from typing import Any


def rollup(usages: list[dict[str, Any]]) -> dict[str, Any]:
    inp = cached = out = reasoning = cache_write = 0
    last_rl: Any = None
    window: Any = None
    for u in usages:
        inp += int(u.get("input_tokens") or 0)
        # Codex reports cached input inside input_tokens; Claude reports cache reads separately.
        cached += int(u.get("cached_input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        cache_write += int(u.get("cache_creation_input_tokens") or 0)
        out += int(u.get("output_tokens") or 0)
        reasoning += int(u.get("reasoning_output_tokens") or 0)
        if u.get("rate_limit_used_percent") is not None:
            last_rl = u.get("rate_limit_used_percent")
        if u.get("context_window"):
            window = u.get("context_window")
    claude_style = any("cache_read_input_tokens" in u for u in usages)
    total_in = inp + (cached + cache_write if claude_style else 0)
    return {
        "turns_with_usage": len(usages), "input_tokens": total_in, "cached_input_tokens": cached,
        "output_tokens": out, "reasoning_output_tokens": reasoning,
        "cache_hit_ratio": round(cached / total_in, 3) if total_in else None,
        "rate_limit_used_percent": last_rl, "context_window": window,
    }

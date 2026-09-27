"""Per-client hook budgets for retrieval work."""

from __future__ import annotations

from typing import Any


def prompt_budget(config: Any, client: str | None) -> float:
    """Seconds the prompt hook may spend building the context pack. Hosts that kill hooks after 5 s
    get ``auto_context_deadline_ms``; clients in ``auto_context_client_deadline_ms`` get their own.
    A pack that misses the window reaches the agent with its first tool result, after it has started
    searching on its own (sympy, 4 runs in parallel: a 2,000-file refresh plus ranking took over 2.5 s)."""
    per = config.get("retrieval.auto_context_client_deadline_ms") or {}
    if client and isinstance(per, dict) and per.get(client) is not None:
        return float(per[client]) / 1000.0
    return float(config.get("retrieval.auto_context_deadline_ms", 2500)) / 1000.0

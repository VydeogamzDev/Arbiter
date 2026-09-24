"""Verified tier sets and per-module requirements (spec §2, §4.4.3, decision 0008).

Tiers are additive sets, not a ladder. A module runs enforcing when the client's verified tier
set satisfies one of its enforcing alternatives, advisory when it satisfies an advisory
alternative, otherwise off.
"""

from __future__ import annotations

T1, T2, T3, T4 = "T1", "T2", "T3", "T4"
ALL_TIERS = (T1, T2, T3, T4)

# module -> (advisory alternatives, enforcing alternatives); each alternative is a tier set.
MODULES: dict[str, tuple[list[set[str]], list[set[str]]]] = {
    "tool_gateway": ([{T1}], [{T1}]),
    "retrieval": ([{T1}], [{T1}]),
    "task_state": ([{T3}, {T1}], [{T2}, {T4}]),
    "loop_detection": ([{T3}], [{T2}, {T4}]),
    "diff_risk": ([{T1}], [{T2}, {T4}]),
    "completion_gate": ([{T1}], [{T2}, {T4}]),
    "reasoning": ([{T1}, {T3}], [{T4}]),
    "context": ([{T3}], [{T4}]),
    "speculation": ([{T2}, {T4}], [{T2}, {T4}]),
    "branching": ([{T4}], [{T4}]),
}


def module_mode(module: str, tiers: set[str] | list[str]) -> str:
    have = set(tiers)
    advisory, enforcing = MODULES[module]
    if any(alt <= have for alt in enforcing):
        return "enforcing"
    if any(alt <= have for alt in advisory):
        return "advisory"
    return "off"


def fmt(tiers: set[str] | list[str]) -> str:
    return " ".join(t for t in ALL_TIERS if t in set(tiers)) or "none"

"""Risk and contract floors (spec §8.6, §8.8): the minimum logical level a call may run at.
Floors come first; cost optimization only chooses among options at or above them."""

from __future__ import annotations

from typing import Any

TASK_TIER = {"low": 0, "trivial": 0, "medium": 1, "normal": 1, "high": 2, "critical": 3}
DIFF_RISK = {"low": 0, "medium": 1, "high": 3, "critical": 3}


def floor(ctx: dict[str, Any]) -> tuple[int, list[str]]:
    why: list[str] = []
    lvl = 0
    tier = str(ctx.get("task_tier") or "").lower()
    if TASK_TIER.get(tier):
        lvl = max(lvl, TASK_TIER[tier])
        why.append(f"task tier {tier}")
    dr = str(ctx.get("diff_risk") or "").lower()
    if DIFF_RISK.get(dr, 0) >= 3:
        lvl = max(lvl, 3)
        why.append(f"{dr} diff risk")
    if ctx.get("integrity_alert"):
        lvl = max(lvl, 3)
        why.append("test-integrity alert")
    if ctx.get("high_consequence_contract"):
        lvl = max(lvl, 3)
        why.append("high-consequence contract")
    if ctx.get("irreversible"):
        lvl = max(lvl, 3)
        why.append("irreversible operation")
    return lvl, why

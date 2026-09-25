"""Capability lattice (spec §8.1, §8.4, §8.8).

Never hard-code which efforts or model tiers exist. The lattice is built from what the host (or a
client profile) says is available: each option is a (model, effort) pair with the model's strength
tier and price. Options are ordered by capability, and logical policy levels (0 = minimal ...
4 = maximum) are projected onto whatever is actually offered: two efforts or five, one model or
several.

Capability is **relative to the offered set**: the strongest option a host offers ranks at the top.
Absolute tier floors (for example "High-tier tasks never run on the smallest model") belong to the
host, which enforces them by what it puts in the allowed set (spec §4.6.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
LOGICAL = ("minimal", "low", "medium", "high", "max")


@dataclass(frozen=True)
class Option:
    model: str
    effort: str
    model_rank: int                  # 0 = weakest model tier offered
    effort_rank: int                 # position among this model's efforts
    price_in: float = 0.0            # $ per 1M uncached input tokens
    price_cached: float = 0.0        # $ per 1M cached input tokens
    price_out: float = 0.0           # $ per 1M output tokens
    effort_multiplier: float = 1.0   # expected output/reasoning token multiplier for this effort

    @property
    def key(self) -> str:
        return f"{self.model}@{self.effort}"


@dataclass
class Lattice:
    options: list[Option] = field(default_factory=list)

    def ordered(self) -> list[Option]:
        return sorted(self.options, key=lambda o: (o.model_rank, o.effort_rank))

    def capability(self, o: Option) -> float:
        """Capability in [0, 1]. Model tier dominates; effort refines within a tier (it never lifts a
        weaker model past the next tier's lowest effort)."""
        models = max((x.model_rank for x in self.options), default=0) + 1
        top = max((x.effort_rank for x in self.options if x.model == o.model), default=0)
        within = o.effort_rank / top if top else 0.5
        if models == 1:
            return round(0.2 + 0.8 * within, 4)
        return round((o.model_rank + 0.9 * within) / (models - 0.1), 4)

    def project(self, logical: int) -> float:
        """Logical level (0..4) -> required capability in [0, 1]."""
        return [0.0, 0.2, 0.45, 0.7, 0.95][max(0, min(4, logical))]


def effort_rank(effort: str, efforts: list[str]) -> int:
    ordered = sorted(efforts, key=lambda e: EFFORT_ORDER.index(e) if e in EFFORT_ORDER else len(EFFORT_ORDER))
    return ordered.index(effort)


def from_allowed_set(allowed: list[dict[str, Any]]) -> Lattice:
    """Host-supplied allowed set -> lattice.

    Each entry: ``{"model": str, "tier": int (strength rank), "efforts": [str], "price": {"input", "cached_input",
    "output"}, "effort_multipliers": {effort: float}}``. Unknown effort names keep the host's order.
    """
    tiers = sorted({int(a.get("tier", 0)) for a in allowed})
    rank_of = {t: i for i, t in enumerate(tiers)}
    opts = []
    for a in allowed:
        efforts = list(a.get("efforts") or ["default"])
        price = a.get("price") or {}
        mult = a.get("effort_multipliers") or {}
        for e in efforts:
            opts.append(Option(str(a["model"]), e, rank_of[int(a.get("tier", 0))], effort_rank(e, efforts),
                               float(price.get("input", 0)), float(price.get("cached_input", price.get("input", 0))),
                               float(price.get("output", 0)),
                               float(mult.get(e, 1.0 + 0.6 * effort_rank(e, efforts)))))
    return Lattice(opts)

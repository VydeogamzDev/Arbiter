"""Reasoning Scheduler: model x effort recommendations (spec §8, §8.8). Advisory / shadow in M9.

Steps, all deterministic and logged:
1. **Wanted level** (logical 0..4) from the call context: task tier and phase, failure history, loop
   and no-progress signals. A retrieval miss doesn't escalate reasoning: broaden retrieval first (§11.6).
2. **Floor** from risk and contracts (``risk_floors``). Nothing below it is ever proposed.
3. **Lease/hysteresis** smooths the level across calls for the same task (``leases``).
4. **Choice** within the host's allowed set: among options whose capability clears the floor, pick
   the lowest *expected cost per successful call* whose estimated success probability is within the
   non-inferiority margin of the best eligible option. Cost counts cached vs uncached input
   (switching model forfeits a warm cache: priced, §17) and the effort's output multiplier; the
   expected retries of a cheap attempt are included through 1/p(success).
5. **Abstain** when the allowed set is empty or nothing clears the floor. The host proceeds as it would
   without Arbiter.

The success estimate is a transparent prior (capability margin over the requirement), updated
per model with the host's reported outcomes for this task. It's a ranking device, not a calibrated
probability (§24).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from arbiter_agent.reasoning import risk_floors
from arbiter_agent.reasoning.capability_lattice import LOGICAL, Lattice, Option, from_allowed_set
from arbiter_agent.reasoning.leases import LeaseBook

NON_INFERIORITY = 0.05
PRIOR_WEIGHT = 4.0
PHASE_DELTA = {"plan": 1, "design": 1, "debug": 1, "review": 1, "implement": 0, "verify": -1, "format": -1,
               "summarize": -1}
BASE = {"low": 1, "trivial": 0, "medium": 2, "normal": 2, "high": 3, "critical": 3}


@dataclass
class Recommendation:
    model: str | None
    effort: str | None
    level: int
    floor: int
    abstain: bool
    reason: str
    reasons: list[str] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)       # e.g. "broaden retrieval before escalating"
    scores: list[dict[str, Any]] = field(default_factory=list)
    score_kind: str = "model_score"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["level_name"] = LOGICAL[self.level]
        return d


def wanted_level(ctx: dict[str, Any]) -> tuple[int, list[str], list[str]]:
    why: list[str] = []
    advice: list[str] = []
    tier = str(ctx.get("task_tier") or "medium").lower()
    lvl = BASE.get(tier, 2)
    why.append(f"task tier {tier} -> level {lvl}")
    phase = str(ctx.get("phase") or "").lower()
    if PHASE_DELTA.get(phase):
        lvl += PHASE_DELTA[phase]
        why.append(f"phase {phase} ({PHASE_DELTA[phase]:+d})")
    failures = int(ctx.get("failures") or 0)
    if ctx.get("retrieval_miss"):
        advice.append("retrieval miss: broaden retrieval before escalating reasoning")
    elif failures:
        lvl += min(failures, 2)
        why.append(f"{failures} failed attempt(s) (+{min(failures, 2)})")
    if ctx.get("loop"):
        lvl += 1
        why.append("loop detected (+1)")
        advice.append("loop: replan rather than retry the same approach")
    if ctx.get("no_progress"):
        lvl += 1
        why.append("no verified progress (+1)")
    return max(0, min(4, lvl)), why, advice


def p_success(capability: float, required: float, prior: tuple[int, int] = (0, 0)) -> float:
    """Transparent prior: logistic in the capability margin, steep enough that options comfortably
    above the requirement are equivalent (so cost decides among them); Beta-updated by the host's
    (successes, failures) for this task."""
    base = 1 / (1 + math.exp(-(12.0 * (capability - required) + 2.2)))
    s, f = prior
    return (base * PRIOR_WEIGHT + s) / (PRIOR_WEIGHT + s + f)


def call_cost(o: Option, ctx: dict[str, Any]) -> float:
    tin = float(ctx.get("expected_input_tokens") or 20_000)
    tout = float(ctx.get("expected_output_tokens") or 2_000) * o.effort_multiplier
    warm = ctx.get("cache_warm") or {}
    cached = float(warm.get(o.model, 0.0)) if isinstance(warm, dict) else 0.0
    cached = 1.0 if cached is True else max(0.0, min(1.0, cached))
    return (tin * (1 - cached) * o.price_in + tin * cached * o.price_cached + tout * o.price_out) / 1e6


def _priors(ctx: dict[str, Any]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for p in ctx.get("prior_outcomes") or []:
        s, f = out.get(str(p.get("model")), (0, 0))
        out[str(p.get("model"))] = (s + 1, f) if p.get("success") else (s, f + 1)
    return out


def recommend(ctx: dict[str, Any], allowed: list[dict[str, Any]], leases: LeaseBook | None = None,
              task_key: str | None = None) -> Recommendation:
    if not allowed:
        return Recommendation(None, None, 0, 0, True, "empty allowed set")
    try:
        lat: Lattice = from_allowed_set(allowed)
    except (KeyError, TypeError, ValueError) as exc:
        return Recommendation(None, None, 0, 0, True, f"malformed allowed set: {exc}"[:200])
    want, why, advice = wanted_level(ctx)
    flo, fwhy = risk_floors.floor(ctx)
    level = max(want, flo)
    if flo > want:
        why.append(f"raised to the floor {flo}: {', '.join(fwhy)}")
    if leases is not None and task_key:
        level, lease_note = leases.apply(task_key, level, bool(ctx.get("progress")))
        level = max(level, flo)
        why.append(lease_note)
    required = lat.project(level)
    floor_cap = lat.project(flo)
    priors = _priors(ctx)
    priced = any(o.price_in or o.price_out for o in lat.options)
    scores: list[dict[str, Any]] = []
    for o in lat.ordered():
        cap = lat.capability(o)
        p = p_success(cap, required, priors.get(o.model, (0, 0)))
        cost = call_cost(o, ctx) if priced else cap ** 2 + 0.01
        scores.append({"option": o.key, "model": o.model, "effort": o.effort, "capability": cap,
                       "p_success": round(p, 3), "cost": round(cost, 6), "cost_per_success": round(cost / p, 6),
                       "eligible": cap + 1e-9 >= floor_cap})
    eligible = [s for s in scores if s["eligible"]]
    if not eligible:
        return Recommendation(None, None, level, flo, True, "nothing in the allowed set meets the risk floor",
                              why, advice, scores)
    best_p = max(s["p_success"] for s in eligible)
    admissible = [s for s in eligible if s["p_success"] >= best_p - NON_INFERIORITY]
    pick = min(admissible, key=lambda s: (float(s["cost_per_success"]), float(s["capability"])))
    why.append(f"chose {pick['option']}: lowest expected cost per success within {NON_INFERIORITY:.2f} of the best "
               f"estimated success ({best_p:.2f})")
    return Recommendation(str(pick["model"]), str(pick["effort"]), level, flo, False, "ok", why, advice, scores)


def advise_hosted(signals: dict[str, Any]) -> dict[str, Any]:
    """Advice for hosted clients (T1), where Arbiter can't change effort itself (§8.5): a logical
    level plus what to do, surfaced in status and the MCP tools."""
    lvl, why, advice = wanted_level(signals)
    flo, fwhy = risk_floors.floor(signals)
    level = max(lvl, flo)
    if flo > lvl:
        why.append(f"floor: {', '.join(fwhy)}")
    return {"effort": LOGICAL[level], "level": level, "reasons": why, "advice": advice}

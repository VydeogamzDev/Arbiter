"""Decision cascade (spec §7.7): deterministic rule -> sensor -> abstain.

The first release uses rules, then the sensor, then a conservative fallback; a cheap learned
predictor slots in between later. The sensor may only *recommend*, and only when:

- the rule wasn't decisive;
- the family is routed to the sensor (``semif.route_families``, filled from benchmark results);
- the judgment is stable (not an abstention) and its breaker is closed;
- the decision isn't destructive or high-consequence (those abstain: §18.6).

Otherwise the cascade abstains and the rule's conservative default applies.
"""

from __future__ import annotations

from dataclasses import dataclass

from arbiter_agent.semif.types import Judgment


@dataclass(frozen=True)
class RuleOutcome:
    action: str                 # what the rule decided (or proposes)
    decisive: bool              # an obvious case / hard floor: no sensor needed
    default: str                # the conservative default if nothing else is confident


@dataclass(frozen=True)
class CascadeDecision:
    source: str                 # rule | semif | abstain
    action: str
    reason: str


def decide(family: str, rule: RuleOutcome, judgment: Judgment | None = None, *, routed: frozenset[str] = frozenset(),
           high_consequence: bool = False, breaker_open: bool = False,
           mapping: dict[str, str] | None = None) -> CascadeDecision:
    """``mapping`` translates the sensor's chosen option into an action (identity by default)."""
    if rule.decisive:
        return CascadeDecision("rule", rule.action, "deterministic rule")
    why = None
    if family not in routed:
        why = f"{family} isn't routed to the sensor (no benchmark-backed routing)"
    elif judgment is None:
        why = "no sensor judgment"
    elif breaker_open:
        why = f"sensor breaker open for {family}"
    elif judgment.abstain or judgment.choice is None:
        why = f"sensor abstained: {judgment.reason}"
    elif high_consequence:
        why = "high-consequence decision: the sensor can't decide it"
    if why is not None:
        return CascadeDecision("abstain", rule.default, why)
    assert judgment is not None and judgment.choice is not None
    action = (mapping or {}).get(judgment.choice, judgment.choice)
    return CascadeDecision("semif", action, f"sensor margin {judgment.margin:.2f}, spread {judgment.spread:.2f}")

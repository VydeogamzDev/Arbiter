"""Conflict resolution (spec §15.5).

For each key the highest-priority constraint wins; at equal level the later one wins. Two
invariants hold whatever the priorities say:

- Nothing at or below the user level can override upstream safety/permission policy.
- **Evidence floors.** A verdict or contract status can only be *lowered* by anything other
  than evidence: a user may finish despite missing verification, and the result is marked
  unverified, but UNKNOWN never becomes PASS by preference, override or sensor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.policy.constraints import Constraint, Level

# Ordered from strongest claim to weakest. Only evidence (INTEGRITY level) may move a value toward
# the front; a user's acceptance or waiver is a decision, not evidence, so it sits at the back.
EVIDENCE_SCALES: dict[str, list[str]] = {
    "verdict": ["verified", "unverified", "unverified_accepted"],
    "contract_status": ["pass", "unknown", "fail", "waived"],
}


@dataclass
class Resolution:
    values: dict[str, Constraint] = field(default_factory=dict)
    overridden: list[tuple[Constraint, Constraint]] = field(default_factory=list)   # (loser, winner)
    refused: list[tuple[Constraint, str]] = field(default_factory=list)

    def value(self, key: str, default: Any = None) -> Any:
        c = self.values.get(key)
        return default if c is None else c.value


def _rank(key: str, value: Any) -> int | None:
    scale = EVIDENCE_SCALES.get(key)
    return scale.index(value) if scale and value in scale else None


def resolve(constraints: list[Constraint]) -> Resolution:
    res = Resolution()
    evidence: dict[str, Constraint] = {}
    for c in constraints:
        if c.key in EVIDENCE_SCALES and c.level == Level.INTEGRITY:
            evidence[c.key] = c
    for c in constraints:
        if c.key in EVIDENCE_SCALES and c.level != Level.INTEGRITY:
            base = evidence.get(c.key)
            r_new, r_base = _rank(c.key, c.value), _rank(c.key, base.value) if base else None
            if r_new is None or (r_base is not None and r_new < r_base) or (base is None and r_new == 0):
                res.refused.append((c, "only evidence can raise a verdict or contract status"))
                continue
        cur = res.values.get(c.key)
        if cur is None:
            res.values[c.key] = c
            continue
        if cur.level == Level.UPSTREAM_SAFETY and c.level != Level.UPSTREAM_SAFETY:
            res.refused.append((c, "upstream safety/permission policy can't be overridden"))
            continue
        if c.level <= cur.level:
            res.overridden.append((cur, c))
            res.values[c.key] = c
        else:
            res.overridden.append((c, cur))
    return res

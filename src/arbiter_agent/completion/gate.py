"""Rules-only completion gate (spec §12.7, decisions 0003 and 0017).

- ``annotate`` (default): record the finish ledger; never block.
- ``block`` (opt-in): on a recognized completion claim with missing evidence, return the agent to
  work once with the specific missing items, at most ``max_stop_blocks_per_epoch`` times.
- If the gate can't run (breaker open, error, timeout) it never blocks and never reports PASS.

Block-reason wording (decision 0017): prefixed ``[Arbiter]``, scoped to the current turn, a
factual list of missing evidence, and no imperative instructions that could be saved as a
standing preference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.completion.claim_detection import ClaimDecision
from arbiter_agent.completion.evidence_ledger import Ledger

MAX_REASON_ITEMS = 6
MAX_REASON_CHARS = 1400
WORDING_PREFIX = "[Arbiter]"


@dataclass
class GateOutcome:
    verdict: str                      # verified | unverified | not_gated
    blocked: bool = False
    response: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    mode: str = "annotate"


def block_reason(ledger: Ledger) -> str:
    items = ledger.missing[:MAX_REASON_ITEMS]
    more = len(ledger.missing) - len(items)
    body = "; ".join(f"({i + 1}) {m}" for i, m in enumerate(items))
    if more > 0:
        body += f"; plus {more} more item(s) in the finish ledger"
    text = (f"{WORDING_PREFIX} Status for the current turn only: the completion claim in this turn is not yet backed "
            f"by the configured evidence (goal epoch {ledger.goal_epoch}). Missing evidence: {body}. "
            "This note describes this turn's state; it is not a standing preference or instruction.")
    return text[:MAX_REASON_CHARS]


def check_wording(text: str) -> list[str]:
    """Wording-rule violations (used by tests and the eval harness)."""
    problems = []
    if not text.startswith(WORDING_PREFIX):
        problems.append("missing [Arbiter] prefix")
    if "current turn" not in text and "this turn" not in text:
        problems.append("not scoped to the current turn")
    lowered = text.lower()
    for bad in ("always ", "never ", "from now on", "in the future", "remember to", "make sure to", "you must",
                "you should", "please "):
        if bad in lowered.replace("not a standing", ""):
            problems.append(f"imperative/standing wording: {bad.strip()!r}")
    return problems


def decide(*, mode: str, claim: ClaimDecision, ledger: Ledger | None, stop_blocks_used: int, max_blocks: int,
           unavailable_reason: str | None = None) -> GateOutcome:
    if not claim.gated:
        return GateOutcome("not_gated", mode=mode, reason=claim.reason)
    if ledger is None or unavailable_reason:
        return GateOutcome("unverified", mode=mode, reason=unavailable_reason or "gate unavailable")
    if ledger.verdict == "verified":
        return GateOutcome("verified", mode=mode, reason="all required evidence present")
    if mode != "block":
        return GateOutcome("unverified", mode=mode, reason="annotate mode: recorded, not blocked")
    if stop_blocks_used >= max_blocks:
        return GateOutcome("unverified", mode=mode,
                           reason=f"block limit reached ({max_blocks} per goal epoch); stop allowed, marked unverified")
    return GateOutcome("unverified", blocked=True, mode=mode, reason="blocked: missing evidence",
                       response={"decision": "block", "reason": block_reason(ledger)})

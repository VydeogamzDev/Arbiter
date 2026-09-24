"""Result validation (spec §7.3): anything malformed becomes an abstention, never an action."""

from __future__ import annotations

import math

from arbiter_agent.semif.types import TEMPLATE_VERSION, Question, ScoreResult

SUM_TOLERANCE = 1e-3


def validate(r: ScoreResult, q: Question, options: list[str], state_hash: str, expected_model: str | None = None,
             expected_revision: str | None = None) -> tuple[bool, str]:
    """``state_hash`` is the hash of the state text actually sent (after budgeting)."""
    if r.abstain:
        return False, r.reason or "backend abstained"
    if r.options != options:
        return False, "option list or order differs from the request"
    if len(r.probs) != len(options):
        return False, "wrong number of scores"
    if any(not isinstance(p, (int, float)) or math.isnan(p) or math.isinf(p) for p in r.probs):
        return False, "non-finite score"
    if any(p < -1e-9 or p > 1 + 1e-9 for p in r.probs):
        return False, "probability out of range"
    if abs(sum(r.probs) - 1.0) > SUM_TOLERANCE:
        return False, "probabilities don't sum to 1"
    if r.template_version != TEMPLATE_VERSION:
        return False, f"unexpected template version {r.template_version}"
    if not r.model or not r.revision:
        return False, "result doesn't name its model and revision"
    if expected_model and r.model != expected_model:
        return False, f"unexpected model {r.model}"
    if expected_revision and r.revision != expected_revision:
        return False, f"unexpected model revision {r.revision}"
    if r.state_hash != state_hash:
        return False, "state hash mismatch"
    return True, ""

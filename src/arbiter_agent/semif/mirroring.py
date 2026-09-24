"""Mirroring, paraphrase and uncertainty (spec §7.4).

Binary questions are scored in both option orders (and, for high-impact questions, once more
with an equivalent paraphrase). The variants are correlated measurements, not votes: their job
is to expose instability. The aggregate is orientation-corrected; a large spread or a thin
margin means abstain.
"""

from __future__ import annotations

import math

from arbiter_agent.semif.types import Judgment, Question, ScoreResult

MAX_SPREAD = 0.25       # abstain when variants disagree by more than this (on the positive option)
MIN_MARGIN = 0.20       # abstain when the top two options are closer than this


def variants(q: Question) -> list[tuple[list[str], str | None]]:
    """(option order, alternative criterion) pairs to score for ``q``."""
    out: list[tuple[list[str], str | None]] = [(list(q.options), None)]
    if len(q.options) == 2:
        out.append((list(reversed(q.options)), None))
        if q.impact == "high" and q.paraphrase:
            out.append((list(q.options), q.paraphrase))
    return out


def _entropy(ps: list[float]) -> float:
    return -sum(p * math.log(p, 2) for p in ps if p > 0)


def aggregate(q: Question, scored: list[tuple[list[str], ScoreResult]]) -> Judgment:
    usable = [(opts, r) for opts, r in scored if not r.abstain]
    if not usable:
        why = "; ".join(sorted({r.reason for _, r in scored if r.reason})) or "no usable scores"
        return Judgment(q.family, None, {}, 0.0, 0.0, 0.0, len(scored), True, why,
                        results=[r for _, r in scored])
    per_option: dict[str, list[float]] = {o: [] for o in q.options}
    for opts, r in usable:
        for o, p in zip(opts, r.probs, strict=True):
            per_option[o].append(p)
    means = {o: sum(v) / len(v) for o, v in per_option.items() if v}
    total = sum(means.values()) or 1.0
    probs = {o: p / total for o, p in means.items()}
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    margin = ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0.0)
    anchor = q.binary_positive or q.options[0]
    vals = per_option.get(anchor, [])
    spread = (max(vals) - min(vals)) if len(vals) > 1 else 0.0
    entropy = _entropy(list(probs.values()))
    reasons = []
    if any(r.hard_label for _, r in usable):
        reasons.append("backend returned hard labels (no calibrated margin)")
    if spread > MAX_SPREAD:
        reasons.append(f"variants disagree (spread {spread:.2f})")
    if margin < MIN_MARGIN:
        reasons.append(f"thin margin ({margin:.2f})")
    abstain = bool(reasons)
    return Judgment(q.family, None if abstain else ranked[0][0], probs, round(margin, 4), round(entropy, 4),
                    round(spread, 4), len(scored), abstain, "; ".join(reasons), results=[r for _, r in scored])

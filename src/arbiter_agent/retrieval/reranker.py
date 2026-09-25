"""Deterministic reranking and adaptive top-k (spec §11.3, §11.4).

Channels are fused with weighted reciprocal-rank fusion: robust to channels on different score
scales, and explainable (each file lists the channels that found it). Pins always come first and
never count against k.

Top-k adapts to:
- disagreement: a flat fused-score distribution (high normalized entropy) means broader retrieval;
- recent misses: each widens k (a miss should broaden retrieval before escalating reasoning, §11.6);
- phase: debugging is broader;
- the prompt budget caps it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.retrieval.candidates import Candidates, QueryContext

WEIGHTS = {"lexical": 1.0, "path": 1.2, "identifier": 0.9, "symbol": 0.9, "graph": 0.6, "tests": 0.5}
RRF_K = 10
TOKENS_PER_FILE = 600
MIN_K, MAX_K = 4, 16


@dataclass
class Ranked:
    pins: list[dict[str, Any]] = field(default_factory=list)
    ranked: list[dict[str, Any]] = field(default_factory=list)
    k: int = 0
    reasons: list[str] = field(default_factory=list)

    def selected(self) -> list[str]:
        return [p["path"] for p in self.pins] + [r["path"] for r in self.ranked[:self.k]]

    def to_dict(self) -> dict[str, Any]:
        return {"pins": self.pins, "ranked": self.ranked[:max(self.k, 1) + 5], "k": self.k, "reasons": self.reasons,
                "selected": self.selected()}


def fuse(c: Candidates) -> list[tuple[str, float, list[str]]]:
    score: dict[str, float] = {}
    found: dict[str, list[str]] = {}
    for ch, paths in c.channels.items():
        w = WEIGHTS.get(ch, 0.5)
        for rank, p in enumerate(paths):
            if p in c.pins:
                continue
            score[p] = score.get(p, 0.0) + w / (RRF_K + rank + 1)
            found.setdefault(p, []).append(ch)
    return sorted(((p, s, found[p]) for p, s in score.items()), key=lambda x: (-x[1], x[0]))


def entropy(scores: list[float]) -> float:
    """Normalized Shannon entropy of the top scores, in [0, 1] (1 = flat: no clear winner)."""
    top = scores[:12]
    total = sum(top)
    if len(top) < 2 or total <= 0:
        return 0.0
    ps = [s / total for s in top]
    return -sum(p * math.log(p) for p in ps if p > 0) / math.log(len(top))


def adaptive_k(fused: list[tuple[str, float, list[str]]], ctx: QueryContext, pins: int) -> tuple[int, list[str]]:
    why = []
    h = entropy([s for _, s, _ in fused])
    k = MIN_K + round(h * 6)
    why.append(f"score entropy {h:.2f}")
    if ctx.misses:
        k = round(k * (1 + 0.5 * min(ctx.misses, 3)))
        why.append(f"{ctx.misses} recent retrieval miss(es): broader")
    if ctx.phase in ("debug", "debugging"):
        k += 2
        why.append("debugging: broader")
    cap = max(MIN_K, ctx.budget_tokens // TOKENS_PER_FILE - pins)
    if k > cap:
        why.append(f"capped by the prompt budget ({ctx.budget_tokens} tokens)")
    return max(MIN_K, min(k, cap, MAX_K)), why


def rerank(c: Candidates, ctx: QueryContext) -> Ranked:
    fused = fuse(c)
    k, why = adaptive_k(fused, ctx, len(c.pins))
    return Ranked(pins=[{"path": p, "reason": r, "line": c.lines.get(p, 1)} for p, r in c.pins.items()],
                  ranked=[{"path": p, "score": round(s, 4), "channels": chs, "line": c.lines.get(p, 1)}
                          for p, s, chs in fused], k=k, reasons=why)

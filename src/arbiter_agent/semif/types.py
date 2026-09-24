"""Sensor request/result types (spec §7.3)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

TEMPLATE_VERSION = "q1"


def h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class StateSection:
    """One block of state. ``pinned`` sections (pending tool state, the user's request) are never
    dropped by budgeting; lower ``priority`` numbers are kept first."""
    name: str
    text: str
    priority: int = 50
    pinned: bool = False


@dataclass
class Question:
    family: str                          # completion_claim | scope_change | requirement | relevance | ...
    criterion: str                       # the question itself
    options: list[str]                   # e.g. ["yes", "no"]
    state: list[StateSection] = field(default_factory=list)
    impact: str = "normal"               # normal | high (high -> paraphrase variant too)
    paraphrase: str | None = None        # equivalent wording for high-impact binary questions
    binary_positive: str | None = None   # the option that means "yes" for binary mirroring

    def state_text(self) -> str:
        return "\n\n".join(f"## {s.name}\n{s.text}" for s in self.state if s.text)

    @property
    def state_hash(self) -> str:
        return h(self.state_text())

    @property
    def criterion_hash(self) -> str:
        return h(self.criterion + "\x00" + "\x00".join(self.options))


@dataclass
class ScoreResult:
    """One scored variant. ``probs`` are normalized over ``options`` in the order asked."""
    options: list[str]
    probs: list[float]
    model: str
    revision: str
    backend: str
    precision: str
    template_version: str
    tokenizer: str
    state_hash: str
    criterion_hash: str
    latency_ms: float
    cache_mode: str = "fresh"            # fresh | prefix_cached
    hard_label: bool = False             # backend returned a label, not scores (uninformative margin)
    abstain: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Judgment:
    """What callers get: an orientation-corrected aggregate over variants, or an abstention."""
    family: str
    choice: str | None
    probs: dict[str, float]
    margin: float
    entropy: float
    spread: float                        # disagreement across variants (mirrors/paraphrase)
    variants: int
    abstain: bool
    reason: str
    score_kind: str = "model_score"      # never "probability" until calibrated (§7.5)
    results: list[ScoreResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["results"] = [r.to_dict() for r in self.results]
        return d


def abstain_result(q: Question, backend: str, reason: str, latency_ms: float = 0.0) -> ScoreResult:
    return ScoreResult(list(q.options), [], "", "", backend, "", TEMPLATE_VERSION, "", q.state_hash, q.criterion_hash,
                       latency_ms, abstain=True, reason=reason)

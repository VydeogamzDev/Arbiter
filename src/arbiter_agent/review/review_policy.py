"""Review allocation (spec §13.4) and stratified low-risk sampling (§13.6).

The analysis assembles per-file risk, blast radius, test mapping and test order into one
explainable report. In M9 it's shadow: it's shown in status and logged as an advisory decision,
and no gate reads it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.review import blast_radius, diff_risk, test_mapper, test_scheduler
from arbiter_agent.review.diff_risk import LEVELS, FileChange, FileRisk, level_index

ALLOCATION = {
    "low": {"review": "static checks + sampled audit", "tests": "mapped tests", "skippable": True},
    "medium": {"review": "normal model review", "tests": "targeted tests", "skippable": False},
    "high": {"review": "explicit high-effort review", "tests": "broader relevant tests", "skippable": False},
    "critical": {"review": "mandatory configured verification", "tests": "full configured verification",
                 "skippable": False},
}


@dataclass
class Report:
    level: str
    files: list[dict[str, Any]] = field(default_factory=list)
    allocation: dict[str, Any] = field(default_factory=dict)
    test_order: list[str] = field(default_factory=list)
    sampled_for_audit: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "allocation": self.allocation, "files": self.files,
                "test_order": self.test_order[:30], "sampled_for_audit": self.sampled_for_audit}

    def summary(self) -> str:
        counts = {lv: sum(1 for f in self.files if f["level"] == lv) for lv in LEVELS}
        parts = [f"{counts[lv]} {lv}" for lv in reversed(LEVELS) if counts[lv]]
        return f"Diff: {' | '.join(parts) or 'no changes'} -> {self.allocation.get('review', '-')}"


def sample_low_risk(risks: list[FileRisk], rate: float = 0.2, salt: str = "") -> list[str]:
    """Stratified by (extension, top-level area): at least one file per stratum, then ``rate``.
    Deterministic for a given salt, so the audit is reproducible."""
    strata: dict[tuple[str, str], list[str]] = {}
    for r in risks:
        if r.level != "low":
            continue
        ext = r.path.rsplit(".", 1)[-1] if "." in r.path else ""
        area = r.path.split("/", 1)[0] if "/" in r.path else "."
        strata.setdefault((ext, area), []).append(r.path)
    out = []
    for paths in strata.values():
        ranked = sorted(paths, key=lambda p: hashlib.sha256((salt + p).encode()).hexdigest())
        out.extend(ranked[:max(1, round(rate * len(ranked)))])
    return sorted(out)


def analyze(changes: list[FileChange], *, files: list[str] | None = None, rev: dict[str, set[str]] | None = None,
            floors: dict[str, str] | None = None, recently_failed: list[str] | None = None,
            salt: str = "") -> Report:
    rev = rev or {}
    risks = [diff_risk.classify(ch, floors) for ch in changes]
    for r in risks:
        rad = blast_radius.radius(r.path, rev)
        if (rad.central or (rad.reaches_boundary and rad.direct_importers)) and r.level in ("low", "medium") \
                and "docs" not in r.tags and "comments_only" not in r.tags:
            r.level = LEVELS[level_index(r.level) + 1]
            r.reasons.append(f"blast radius: {rad.transitive_importers} dependent files"
                             + (", reaches a public boundary" if rad.reaches_boundary else ""))
    mapped = test_mapper.map_tests([r.path for r in risks], files or [], rev)
    lvl = diff_risk.overall(risks)
    rep = Report(lvl, [{**r.to_dict(), "tests": mapped.get(r.path, [])} for r in risks], dict(ALLOCATION[lvl]),
                 test_scheduler.order(mapped, risks, recently_failed), sample_low_risk(risks, salt=salt))
    return rep

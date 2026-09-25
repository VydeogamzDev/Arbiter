"""M9.3 exit gate: dangerous misses on the diff-risk corpus (spec §20.17)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.review import review_policy
from arbiter_agent.review.diff_risk import FileChange, level_index

CORPUS = Path(__file__).resolve().parent / "corpus" / "diffs.yaml"
MAX_DANGEROUS_MISS_RATE = 0.01


def run(path: Path | None = None, floors: dict[str, str] | None = None) -> dict[str, Any]:
    data = yaml.safe_load((path or CORPUS).read_text(encoding="utf-8"))
    floors = floors if floors is not None else {"security": "high", "persistence": "high", "destructive": "high",
                                                "public_api": "high", "test_harness": "high"}
    cases, misses, over, exact = [], [], [], 0
    for c in data["cases"]:
        changes = [FileChange(f["path"], f.get("status", "M"), list(f.get("added") or []),
                              list(f.get("removed") or [])) for f in c["files"]]
        rep = review_policy.analyze(changes, floors=floors)
        want, got = c["min_level"], rep.level
        exact += int(want == got)
        if level_index(want) >= 2 and level_index(got) < 2:
            misses.append({"id": c["id"], "want": want, "got": got})
        if want == "low" and level_index(got) >= 2:
            over.append({"id": c["id"], "got": got})
        cases.append({"id": c["id"], "want": want, "got": got})
    n = len(cases)
    rate = len(misses) / n if n else 0.0
    return {"cases": n, "dangerous_misses": misses, "dangerous_miss_rate": round(rate, 4),
            "over_flagged": over, "exact_level_rate": round(exact / n, 3) if n else 0.0,
            "passed": rate <= MAX_DANGEROUS_MISS_RATE, "detail": cases}

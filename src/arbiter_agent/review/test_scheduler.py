"""Inner-loop test ordering (spec §13.5).

A prioritization, never permission to skip: the final verification still runs the configured
command in full (``completion`` / ``arbiter verify``). Order:
1. tests that failed most recently in this session (fastest signal on a fix);
2. tests mapped to the highest-risk changed files;
3. other mapped tests;
the rest of the suite is left to the final run.
"""

from __future__ import annotations

from arbiter_agent.review.diff_risk import FileRisk, level_index


def order(mapped: dict[str, list[str]], risks: list[FileRisk], recently_failed: list[str] | None = None) -> list[str]:
    level = {r.path: level_index(r.level) for r in risks}
    score: dict[str, tuple[int, int]] = {}
    for path, tests in mapped.items():
        for t in tests:
            prev = score.get(t, (0, 0))
            score[t] = (max(prev[0], level.get(path, 0)), prev[1] + 1)
    failed = [t for t in (recently_failed or []) if t]
    ranked = sorted(score, key=lambda t: (-score[t][0], -score[t][1], t))
    return list(dict.fromkeys(failed + ranked))

"""Objective progress: failing tests trending down, contracts moving to PASS, new passing runs."""

from __future__ import annotations

from typing import Any


def test_progress(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Latest pass/fail counts per command and whether the most recent run improved."""
    latest: dict[str, dict[str, Any]] = {}
    improved_at: float | None = None
    for r in runs:
        fp = r.get("subject") or "?"
        prev = latest.get(fp)
        if prev is not None:
            if (r.get("failed", 0) + r.get("errors", 0)) < (prev.get("failed", 0) + prev.get("errors", 0)) or (
                    r.get("status") == "pass" and prev.get("status") != "pass"):
                improved_at = r.get("created_at")
        elif r.get("status") == "pass":
            improved_at = r.get("created_at")
        latest[fp] = r
    passed = sum(int(r.get("passed") or 0) for r in latest.values())
    failed = sum(int(r.get("failed") or 0) + int(r.get("errors") or 0) for r in latest.values())
    return {"tests_passed": passed, "tests_failed": failed, "commands": len(latest), "last_improved_at": improved_at}

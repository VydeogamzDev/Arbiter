"""Advisory loop detection (spec §8, M3.7). Deterministic signals over the fact stream:

- ``same_error``: one error fingerprint in >= 3 failing runs with no test progress in between.
- ``repeat_no_change``: the same failing command re-run >= 3 times with no file change between runs.
- ``edit_thrash``: one file edited >= 6 times since the last progress, with >= 2 failing runs.

Progress (a run that passes, or fewer failures than the previous run of the same command)
resets every counter. Alerts are advisory: they never block and never change contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SAME_ERROR_N = 3
REPEAT_N = 3
THRASH_EDITS = 6
THRASH_FAILS = 2


@dataclass
class LoopAlert:
    signal: str
    key: str
    count: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LoopDetector:
    def __init__(self) -> None:
        self.reset()
        self.alerted: set[str] = set()

    def reset(self) -> None:
        self.error_counts: dict[str, int] = {}
        self.error_text: dict[str, str] = {}
        self.repeat: dict[str, int] = {}
        self.edits: dict[str, int] = {}
        self.fails = 0
        self.changed_since_run = True
        self.last_fail: dict[str, int] = {}

    def _progress(self) -> None:
        self.reset()
        self.alerted.clear()

    def feed(self, fact: dict[str, Any]) -> list[LoopAlert]:
        kind = fact.get("kind")
        data = fact.get("data") or {}
        out: list[LoopAlert] = []
        if kind == "file_change":
            path = str(fact.get("subject") or "")
            self.edits[path] = self.edits.get(path, 0) + 1
            self.changed_since_run = True
            if self.edits[path] >= THRASH_EDITS and self.fails >= THRASH_FAILS:
                out += self._alert("edit_thrash", path, self.edits[path],
                                   f"{path} edited {self.edits[path]} times without test progress")
            return out
        if kind not in ("test_run", "command_run"):
            return out
        status = fact.get("status")
        subject = str(fact.get("subject") or "")
        failures = int(data.get("failed") or 0) + int(data.get("errors") or 0)
        if status == "pass" and kind == "test_run":
            self._progress()
            return out
        if status != "fail":
            self.changed_since_run = False
            return out
        prev = self.last_fail.get(subject)
        if kind == "test_run" and prev is not None and failures < prev:
            self._progress()
            self.last_fail[subject] = failures
            self.fails = 1
            return out
        self.last_fail[subject] = failures
        self.fails += 1
        if self.changed_since_run:
            self.repeat[subject] = 1
        else:
            self.repeat[subject] = self.repeat.get(subject, 0) + 1
            if self.repeat[subject] >= REPEAT_N:
                out += self._alert("repeat_no_change", subject, self.repeat[subject],
                                   f"`{subject}` failed {self.repeat[subject]} times with no file changes between runs")
        self.changed_since_run = False
        for fp, text in data.get("error_fps") or []:
            self.error_counts[fp] = self.error_counts.get(fp, 0) + 1
            self.error_text[fp] = text
            if self.error_counts[fp] >= SAME_ERROR_N:
                out += self._alert("same_error", fp, self.error_counts[fp],
                                   f"same error {self.error_counts[fp]} times: {text[:120]}")
        return out

    def _alert(self, signal: str, key: str, count: int, detail: str) -> list[LoopAlert]:
        k = f"{signal}:{key}"
        if k in self.alerted:
            return []
        self.alerted.add(k)
        return [LoopAlert(signal, key, count, detail)]


def detect(facts: list[dict[str, Any]]) -> list[LoopAlert]:
    d = LoopDetector()
    out: list[LoopAlert] = []
    for f in facts:
        out += d.feed(f)
    return out

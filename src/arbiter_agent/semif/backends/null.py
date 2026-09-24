"""Null backend (spec §7.8): abstains on everything. With it the cascade ends at rules or
conservative defaults, and shadow logs record that no semantic score was available."""

from __future__ import annotations

from typing import Any

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif.backends.base import ScoreRequest
from arbiter_agent.semif.types import TEMPLATE_VERSION, ScoreResult


class NullBackend:
    name = "null"
    model = ""
    revision = ""
    precision = ""
    tokenizer = ""
    max_tokens = 1 << 30

    def __init__(self, reason: str = "no sensor installed") -> None:
        self.reason = reason

    def count_tokens(self, text: str) -> int | None:
        return None

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]:
        return [ScoreResult(list(r.options), [], "", "", self.name, "", TEMPLATE_VERSION, "", r.state_hash,
                            r.criterion_hash, 0.0, abstain=True, reason=self.reason) for r in requests]

    def health(self) -> dict[str, Any]:
        return {"backend": self.name, "ok": True, "reason": self.reason}

    def close(self) -> None:
        pass

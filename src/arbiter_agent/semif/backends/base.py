"""Backend interface: every backend returns the same :class:`ScoreResult` shape (§7.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from arbiter_agent.concurrency import Deadline
from arbiter_agent.semif.types import ScoreResult, h


@dataclass
class ScoreRequest:
    family: str
    options: list[str]
    criterion: str
    state_text: str
    prompt: str                 # decoder prompt (rendered template); encoders use state_text + criterion
    adapter: str | None = None  # LoRA adapter / fine-tuned checkpoint for this decision family

    @property
    def state_hash(self) -> str:
        return h(self.state_text)

    @property
    def criterion_hash(self) -> str:
        return h(self.criterion + "\x00" + "\x00".join(self.options))


class Backend(Protocol):
    name: str
    model: str
    revision: str
    precision: str
    tokenizer: str
    max_tokens: int

    def count_tokens(self, text: str) -> int | None:
        """Exact token count with the deployed tokenizer, or None when unavailable."""
        ...

    def score(self, requests: list[ScoreRequest], deadline: Deadline) -> list[ScoreResult]: ...

    def health(self) -> dict[str, Any]: ...

    def close(self) -> None: ...

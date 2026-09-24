"""Exact request budgeting (spec §7.2).

- Token counts come from the deployed backend's own tokenizer when it offers one.
- Without one, the count is a *guaranteed upper bound*: UTF-8 bytes (every byte-level BPE token
  covers at least one byte). Never a character heuristic.
- The envelope (question, options, instructions) is reserved first; state is trimmed from the
  lowest-priority, unpinned sections; pinned sections (pending tool state, the user's request)
  are never dropped. An impossible budget is rejected before any model work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from arbiter_agent.semif import prompt
from arbiter_agent.semif.types import Question, StateSection

TokenCounter = Callable[[str], int | None]


class BudgetError(ValueError):
    pass


@dataclass
class Fitted:
    state_text: str
    tokens: int
    exact: bool
    dropped: list[str]


def count(text: str, counter: TokenCounter | None) -> tuple[int, bool]:
    if counter is not None:
        n = counter(text)
        if n is not None:
            return n, True
    return len(text.encode("utf-8")), False


def fit(q: Question, max_total: int, counter: TokenCounter | None = None, reserve: int = 16,
        max_state: int | None = None) -> Fitted:
    """Fit a question into ``max_total`` tokens (prompt + answer reserve)."""
    widest = max((q.options, list(reversed(q.options))), key=lambda o: len(prompt.envelope(q, o)))
    env_tokens, exact = count(prompt.render(q, "", widest, q.paraphrase if q.paraphrase and
                                            len(q.paraphrase) > len(q.criterion) else None), counter)
    budget = max_total - env_tokens - reserve
    if budget < 0:
        raise BudgetError(f"the question alone needs {env_tokens + reserve} tokens, over the {max_total} limit")
    if max_state is not None:
        budget = min(budget, max_state)
    sections = sorted(q.state, key=lambda s: (not s.pinned, s.priority))
    kept: list[StateSection] = []
    dropped: list[str] = []
    for s in sections:
        trial = Question(q.family, q.criterion, q.options, kept + [s])
        n, ex = count(trial.state_text(), counter)
        exact = exact and ex
        if n <= budget:
            kept.append(s)
        elif s.pinned:
            raise BudgetError(f"pinned state '{s.name}' doesn't fit in {budget} tokens")
        else:
            dropped.append(s.name)
    order = {id(s): i for i, s in enumerate(q.state)}
    kept.sort(key=lambda s: order[id(s)])
    text = Question(q.family, q.criterion, q.options, kept).state_text()
    n, ex = count(text, counter)
    return Fitted(text, n + env_tokens, exact and ex, dropped)

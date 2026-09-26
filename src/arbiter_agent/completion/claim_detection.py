"""Completion-claim detection (spec §12.7). A turn ending is not a completion claim.

Order of signals: an ``arbiter_finish_check`` call this turn (strongest), then deterministic
rules on the final assistant message. A message that ends by asking the user something, or
that the rules can't classify, is not gated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMPLETION = re.compile(
    r"^\s*(?:all\s+)?(?:done|finished|complete)\b|"
    r"\b(?:all\s+)?(?:done|finished|completed?)\s*[.!]|"
    r"\b(?:the\s+)?tests?\s+(?:now\s+)?(?:pass|passes|passed)\b|"
    r"\b(?:i'?ve|i have|we'?ve|we have)\s+(?:now\s+)?(?:fixed|implemented|added|completed|resolved|updated|made|"
    r"finished|refactored|created|written|removed|renamed|migrated|wired|addressed)\b|"
    r"\b(?:the\s+)?(?:task|fix|feature|implementation|change|changes|work|refactor|bug|issue|migration)\s+"
    r"(?:is|are|has been|have been)\s+(?:now\s+)?(?:complete|completed|done|finished|ready|in place|fixed|resolved|"
    r"implemented)\b|"
    r"\ball (?:the )?(?:tests?|checks?)\s+(?:now\s+)?(?:pass|passing|passed|green)\b|"
    r"\b(?:tests?|checks?|build) (?:are|is) (?:now )?(?:passing|green)\b|"
    r"\beverything (?:is |now )*(?:working|passing|green|in place)\b|"
    r"\bsuccessfully (?:implemented|fixed|added|completed|updated|resolved|created|refactored)\b|"
    r"\b(?:summary of (?:the |my )?changes|"
    r"here'?s (?:a |the )?summary of (?:the |my )?(?:changes|what i (?:changed|did))|"
    r"changes made:|what (?:i|we) changed)\b|"
    r"\bready for (?:review|merge|testing)\b|\bshould now (?:work|pass|be fixed)\b|"
    r"\b(?:is|are) now (?:fixed|working|resolved|passing)\b|\bhas been (?:fixed|implemented|resolved|added)\b|"
    # Seen in a real Claude Code run (benchmark smoke test, 2026-09-25): "The suite passes now (2 passed)."
    r"\b(?:test\s+)?suite\s+(?:now\s+)?(?:passes|passed|is (?:now )?(?:green|passing))\b|"
    r"\b(?:passes|passing|green) now\b|\b\d+ passed\b|"
    r"\bi (?:fixed|changed|implemented|corrected|replaced|renamed|resolved)\b",
    re.I)
PARTIAL = re.compile(
    r"\bnot (?:yet |fully )?(?:done|finished|complete|working|fixed)\b|\bstill (?:need|needs|fail|failing|fails|broken|"
    r"working on|investigating)\b|\bnext steps?\b|\bremaining (?:work|items|issues|tasks)\b|\bblocked\b|"
    r"\b(?:couldn'?t|could not|unable to|wasn'?t able to|failed to|can'?t)\b|\bpartial(?:ly)?\b|"
    r"\bwork in progress\b|\bwip\b|\bi'?ll (?:now |next )?(?:continue|start|look|investigate)\b|"
    r"\bin progress\b|\bhalfway\b|\bfirst (?:part|step|half)\b",
    re.I)
QUESTION_END = re.compile(r"\?\s*(?:[)\]*_`\"']\s*)*$")


@dataclass(frozen=True)
class ClaimDecision:
    claim: str        # claim | not_claim | uncertain
    reason: str

    @property
    def gated(self) -> bool:
        return self.claim == "claim"


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def classify(message: str | None, *, finish_check_called: bool = False) -> ClaimDecision:
    if finish_check_called:
        return ClaimDecision("claim", "arbiter_finish_check was called this turn")
    text = (message or "").strip()
    if not text:
        return ClaimDecision("not_claim", "no final message")
    if QUESTION_END.search(text) or QUESTION_END.search(_last_line(text)):
        return ClaimDecision("not_claim", "ends with a question to the user")
    has_claim = bool(COMPLETION.search(text))
    if not has_claim:
        return ClaimDecision("not_claim", "no completion statement")
    if PARTIAL.search(text):
        return ClaimDecision("uncertain", "completion wording mixed with partial-progress wording")
    return ClaimDecision("claim", "final message states completion")

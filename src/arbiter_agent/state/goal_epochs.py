"""Goal epochs without a semantic model (spec §6.3.1, decision 0023).

Every prompt is logged as intent. Deterministic rules decide what it does to the epoch:

- ``confirm_new``: the first prompt of a session, an explicit new-task marker, a user phrase
  rule, or the first non-continuation prompt after a resume boundary. The epoch advances now.
- ``join``: a short continuation reply ("yes, continue"). Never touches contracts.
- ``candidate``: everything else. The prompt joins the current epoch as additional intent that
  coverage must account for, and opens a candidate epoch that only ``arbiter_scope_change`` or a
  contract proposal declaring ``supersedes`` can confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NEW_TASK = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|alright|great|thanks|now|so)[,.!]?\s+)?(?:"
    r"new task|next task|new request|another task|different task|separate task|unrelated(?: question| task)?[:,]|"
    r"switching gears|change of plans?|scratch that|forget (?:that|about that|the previous|what i said|everything)|"
    r"ignore (?:the |my )?previous|start over|let'?s start over|moving on[,:]|let'?s move on to|"
    r"now let'?s (?:work on|do|switch to)|now (?:i want|i need) you to (?:work on|switch)|"
    r"stop (?:that|what you'?re doing)|instead,? (?:let'?s|please|can you|could you))(?=\W|$)",
    re.I)
CONTINUATION = re.compile(
    r"^\s*(?:(?:yes|yep|yeah|yup|ok(?:ay)?|sure|sounds good|great|perfect|thanks|thank you|lgtm|cool|nice|good)"
    r"[\s,.!]*)*(?:please\s+)?(?:continue|go on|go ahead|keep going|proceed|carry on|resume|do it|"
    r"yes|yep|yeah|ok(?:ay)?|sure|sounds good|lgtm|approved|go for it|that works|looks good)?"
    r"(?:\s+(?:please|then|with (?:that|it|the plan)|as planned))?[\s.!]*$",
    re.I)


@dataclass(frozen=True)
class EpochDecision:
    action: str      # confirm_new | join | candidate
    reason: str


def is_continuation(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and len(t.split()) <= 8 and bool(CONTINUATION.match(t))


def decide(text: str, *, first_in_session: bool, after_resume: bool = False,
           phrase_rules: list[str] | None = None) -> EpochDecision:
    t = (text or "").strip()
    if first_in_session:
        return EpochDecision("confirm_new", "first prompt of the session")
    if is_continuation(t):
        return EpochDecision("join", "continuation reply")
    if NEW_TASK.match(t):
        return EpochDecision("confirm_new", "explicit new-task marker")
    for rule in phrase_rules or []:
        try:
            if re.search(rule, t, re.I):
                return EpochDecision("confirm_new", "user phrase rule")
        except re.error:
            continue
    if after_resume:
        return EpochDecision("confirm_new", "first instruction after a resume boundary")
    return EpochDecision("candidate", "may change scope; kept as added intent until confirmed")

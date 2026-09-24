"""Shadow harness (M7.7): ask the sensor about decisions the rules already made, asynchronously,
and log both side by side. Shadow judgments never change a decision; they're the calibration
data that later stages (M8 cascade, R6 fine-tuning) are allowed to learn from.

Called from the session engine's decision points. Listeners only enqueue work: they never wait
for a score, so a hook's latency doesn't depend on the sensor at all (§4.4.5).
"""

from __future__ import annotations

import json
import time
from typing import Any

from arbiter_agent.semif.service import SemIfService
from arbiter_agent.semif.types import Judgment, Question, StateSection, h

QUESTIONS = {
    "scope_change": ("Does the latest user message start a new, different task, rather than continuing or "
                     "refining the current one?", ["new_task", "continue"], "new_task"),
    "completion_claim": ("Does this final assistant message claim that the requested work is complete?",
                         ["yes", "no"], "yes"),
    "requirement_detection": ("Is this sentence something the user wants done or kept true when the work is finished?",
                    ["yes", "no"], "yes"),
}


def question(family: str, sections: list[StateSection]) -> Question:
    criterion, options, positive = QUESTIONS[family]
    return Question(family, criterion, list(options), sections, binary_positive=positive)


class ShadowHarness:
    def __init__(self, service: SemIfService, writer: Any, *, flags: Any = None, shedder: Any = None,
                 budgets: Any = None) -> None:
        self.service = service
        self.writer = writer
        self.flags = flags
        self.shedder = shedder
        self.budgets = budgets
        self.stats = {"queued": 0, "shed": 0, "logged": 0, "skipped": 0}

    def on_decision(self, sid: str, kind: str, info: dict[str, Any]) -> None:
        """Engine listener: kind is ``prompt`` or ``stop``. Enqueue only."""
        if kind == "prompt" and info.get("previous"):     # a session's first prompt is always a new task
            sections = [StateSection("previous user message", str(info["previous"])[:4000], 20),
                        StateSection("latest user message", str(info.get("text") or "")[:4000], 0, pinned=True)]
            self._ask(sid, question("scope_change", sections), str(info.get("decision")))
        elif kind == "stop" and info.get("message"):
            sections = [StateSection("final assistant message", str(info["message"])[:4000], 0, pinned=True)]
            self._ask(sid, question("completion_claim", sections), str(info.get("claim")))

    def _ask(self, sid: str, q: Question, rule: str) -> None:
        if (self.flags is not None and not self.flags.enabled("semif")) or \
                (self.shedder is not None and not self.shedder.allow("background")) or \
                (self.budgets is not None and not self.budgets.charge(sid, "sensor_calls_per_turn")):
            self.stats["skipped"] += 1
            return
        qhash = h(q.state_text() + q.criterion)
        fut = self.service.submit(q, callback=lambda j: self._log(sid, q, qhash, rule, j))
        if fut is None:
            self.stats["shed"] += 1
        else:
            self.stats["queued"] += 1

    def _log(self, sid: str, q: Question, qhash: str, rule: str, j: Judgment) -> None:
        first = next((r for r in j.results if not r.abstain), j.results[0] if j.results else None)
        row = (time.time(), sid, q.family, qhash, rule, j.choice, int(j.abstain), j.reason[:300], j.margin, j.spread,
               first.backend if first else None, first.model if first else None, first.revision if first else None,
               sum(r.latency_ms for r in j.results), json.dumps({"probs": j.probs, "variants": j.variants,
                                                               "entropy": j.entropy, "score_kind": j.score_kind}))
        try:
            self.writer.run(lambda c: c.execute(
                "INSERT INTO sensor_log(created_at, session_id, family, question_hash, rule_decision, choice, abstain, "
                "reason, margin, spread, backend, model, revision, latency_ms, judgment_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row), timeout=5)
            self.stats["logged"] += 1
        except Exception:
            pass

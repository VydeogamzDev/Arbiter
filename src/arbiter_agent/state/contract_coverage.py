"""Coverage check and deterministic extraction (spec §6.8.1 steps 4-5).

Rules first: requirement-like sentences in the active epoch's intent that no contract quotes
are flagged as uncovered. Some constraints are extracted by rules alone (``don't change X``,
requested test commands, "create file X") and become contracts proposed by ``rules``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from arbiter_agent.state.goal_epochs import is_continuation
from arbiter_agent.state.intent import Intent
from arbiter_agent.state.schema import Contract
from arbiter_agent.state.text_norm import norm, sentences, words

IMPERATIVE = re.compile(
    r"^\s*(?:please\s+|pls\s+|also\s+|and\s+|then\s+|now\s+|just\s+|first,?\s+|finally,?\s+|next,?\s+)*"
    r"(?:add|fix|implement|create|make|update|change|remove|delete|rename|refactor|write|build|run|test|ensure|"
    r"use|move|replace|convert|support|handle|return|raise|throw|log|document|migrate|upgrade|bump|set|keep|"
    r"avoid|don'?t|do not|never|stop|preserve|validate|check|verify|split|merge|extract|expose|hide|allow|"
    r"prevent|reject|accept|limit|cap|increase|decrease|reduce|optimi[sz]e|improve|clean ?up|restore|revert|"
    r"install|configure|enable|disable|wire|hook|port|generate|format|lint|sort|drop|include|exclude|"
    r"parse|print|show|display|emit|send|store|save|load|cache|retry|guard|wrap|mock|stub|cover|fill|"
    r"speed up|simplify|rewrite|redesign|rework|introduce|adjust|tweak|modify|edit|correct|patch|investigate|"
    r"debug|profile|benchmark|explain|describe|list|find|review|audit|translate|deploy|release|publish|ship|"
    r"notify|track|measure|compute|calculate|count|filter|group|paginate|index|link|connect|register|"
    r"return|reuse|share|split|combine|align|pin|unpin|freeze|lock|unlock|close|open|resolve|address)\b",
    re.I)
MODAL = re.compile(r"\b(must|should|shall|needs? to|have to|has to|make sure|ensure|don'?t|do not|never|without|"
                   r"avoid|only|always|required?|requirement|constraint|can'?t|cannot|mustn'?t|shouldn'?t|"
                   r"at least|at most|no more than|instead of|rather than|so that|as long as)\b", re.I)
REQUEST = re.compile(r"\b(can|could|would|will) you\b|\bi (?:want|need|would like|'d like)\b|\bwe (?:need|want)\b|"
                     r"\bplease\b|\blet'?s\b", re.I)
NAMED = re.compile(r"`[^`]+`|\b[\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|cs|rb|php|json|ya?ml|toml|md|sql|css|html|"
                   r"sh|ps1|c|h|cpp|swift)\b|\b\w+\(\)")
PURE_QUESTION = re.compile(r"^\s*(?:why|what|how|when|where|who|which|is|are|does|do|did|was|were|has|have)\b.*\?\s*$",
                           re.I)
FILLER = re.compile(r"^\s*(?:thanks|thank you|great|nice|cool|ok(?:ay)?|good|perfect|awesome|hi|hello|hey)\b[\s!.,]*$",
                    re.I)

DONT_CHANGE = re.compile(r"\b(?:don'?t|do not|never|without)\s+(?:change|changing|modify|modifying|touch|touching|edit|"
                         r"editing|alter|altering|break|breaking|delete|deleting|remove|removing)\s+(?:the\s+|any\s+)?"
                         r"(?P<what>`[^`]+`|[\w./\\*-]+\.[\w*]+|[\w.-]+/[\w./*-]*)", re.I)
RUN_CMD = re.compile(r"\b(?:run|make sure|ensure|verify(?: that)?|check(?: that)?|confirm(?: that)?)\s+"
                     r"`(?P<cmd>[^`]{2,200})`"
                     r"|`(?P<cmd2>[^`]{2,200})`\s+(?:passes|pass|succeeds|is green|should pass|must pass|still passes)",
                     re.I)
CREATE_FILE = re.compile(r"\b(?:create|add|write)\s+(?:a\s+)?(?:new\s+)?(?:file\s+)?(?:called\s+|named\s+)?"
                         r"(?P<path>`[^`]+\.[\w]+`|[\w./-]+\.(?:py|ts|tsx|js|go|rs|md|json|ya?ml|toml|sql|sh))\b", re.I)

STOP = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with", "that", "this", "it", "is", "be", "so",
        "please", "also", "then", "just", "can", "you", "i", "we", "my", "our", "your", "should", "must", "make",
        "sure"}


@dataclass
class Requirement:
    intent_id: str
    text: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def requirement_reason(s: str) -> str | None:
    t = s.strip()
    if len(t) < 6 or FILLER.match(t) or is_continuation(t):
        return None
    if PURE_QUESTION.match(t) and not REQUEST.search(t) and not MODAL.search(t):
        return None
    if IMPERATIVE.match(t):
        return "imperative"
    if MODAL.search(t):
        return "constraint"
    if REQUEST.search(t):
        return "request"
    if NAMED.search(t):
        return "named file/symbol"
    return None


def requirements(intents: list[Intent]) -> list[Requirement]:
    out: list[Requirement] = []
    for it in intents:
        if it.source == "rules":
            continue
        for s in sentences(it.text):
            why = requirement_reason(s)
            if why:
                out.append(Requirement(it.short_id, s.strip()[:500], why))
    return out


def _content(ws: list[str]) -> set[str]:
    return {w for w in ws if w not in STOP and len(w) > 1}


def covered_by(req: Requirement, contracts: list[Contract]) -> str | None:
    s = norm(req.text)
    sw = _content(words(req.text))
    for c in contracts:
        for q in c.quotes:
            nq = norm(q).strip(" .,;:!?\"'")
            if not nq:
                continue
            if nq in s or s.strip(" .,;:!?\"'") in nq:
                return c.id
            qw = _content(words(q))
            if sw and len(sw & qw) / len(sw) >= 0.6:
                return c.id
    return None


def uncovered(intents: list[Intent], contracts: list[Contract]) -> list[Requirement]:
    return [r for r in requirements(intents) if covered_by(r, contracts) is None]


def _strip_ticks(s: str) -> str:
    return s.strip().strip("`").strip()


def extract(it: Intent) -> list[dict[str, Any]]:
    """Rule-extracted obligations (quotes are exact substrings of the intent)."""
    out: list[dict[str, Any]] = []
    for s in sentences(it.text):
        for m in DONT_CHANGE.finditer(s):
            what = _strip_ticks(m.group("what"))
            if what and not what.startswith("-"):
                out.append({"text": f"Leave {what} unchanged", "quotes": [s.strip()],
                            "recipe": {"type": "paths_unchanged", "paths": [what]}})
        for m in RUN_CMD.finditer(s):
            cmd = _strip_ticks(m.group("cmd") or m.group("cmd2") or "")
            from arbiter_agent.state.contract_compiler import is_runner_command

            if cmd and is_runner_command(cmd):
                out.append({"text": f"`{cmd}` passes", "quotes": [s.strip()],
                            "recipe": {"type": "test_command", "command": cmd}})
        for m in CREATE_FILE.finditer(s):
            path = _strip_ticks(m.group("path"))
            out.append({"text": f"{path} exists", "quotes": [s.strip()], "recipe": {"type": "file_exists",
                                                                                    "path": path}})
    return out

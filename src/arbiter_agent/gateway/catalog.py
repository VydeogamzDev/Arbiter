"""The gateway catalog (spec §10.2, §10.7).

Entries come from trusted sources only:
- Arbiter's own tools, with classifications in code;
- servers the user explicitly adopted, whose tools (names, descriptions, schemas, annotations) come
  from the server's own ``tools/list``: the same text the client would show, never repository
  files or model output.

**Read-only** means the server annotates the tool ``readOnlyHint: true`` and not ``destructiveHint:
true``, *and* the user confirmed that classification when adopting (the confirmation snapshot
records the schema hash; a changed tool loses its confirmation until it's re-adopted).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

FAMILIES = [
    ("code", r"\b(code|file|repo|search|symbol|grep|read|write|edit|diff|commit|branch|git)\w*"),
    ("issues", r"\b(issue|pull|pr|review|comment|label|milestone|ticket)\w*"),
    ("data", r"\b(sql|query|database|table|row|record|schema|db)\w*"),
    ("web", r"\b(browser|page|url|http|fetch|navigate|click|screenshot|web)\w*"),
    ("messaging", r"\b(message|channel|slack|email|mail|chat|notify|post)\w*"),
    ("tasks", r"\b(task|contract|verify|finish|scope|session|status|ledger)\w*"),
    ("files", r"\b(directory|folder|path|upload|download|document|drive)\w*"),
]


def tokens_estimate(obj: Any) -> int:
    """Rough token count of a JSON payload (bytes / 4): the unit schema costs are compared in."""
    return max(1, len(json.dumps(obj, separators=(",", ":"))) // 4)


def schema_hash(tool: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"d": tool.get("description"), "s": tool.get("inputSchema"),
                                      "a": tool.get("annotations")}, sort_keys=True).encode()).hexdigest()[:16]


def family_of(name: str, description: str) -> str:
    text = f"{name.replace('_', ' ')} {description}".lower()
    best, score = "other", 0
    for fam, rx in FAMILIES:
        n = len(re.findall(rx, text))
        if n > score:
            best, score = fam, n
    return best


@dataclass
class Tool:
    tool_id: str                         # "<server>.<tool>"
    server: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False              # confirmed read-only (see module docstring)
    family: str = "other"
    confirmed_hash: str | None = None    # schema hash the user confirmed at adoption
    local: bool = False                  # Arbiter's own tool (dispatched in-process)

    @property
    def schema_tokens(self) -> int:
        return tokens_estimate({"name": self.name, "description": self.description, "inputSchema": self.input_schema})

    def summary(self) -> dict[str, Any]:
        return {"tool_id": self.tool_id, "description": self.description[:200], "family": self.family,
                "read_only": self.read_only}

    def canonical(self) -> dict[str, Any]:
        return {"tool_id": self.tool_id, "name": self.name, "description": self.description,
                "inputSchema": self.input_schema, "annotations": self.annotations, "read_only": self.read_only}


def annotated_read_only(tool: dict[str, Any]) -> bool:
    ann = tool.get("annotations") or {}
    return ann.get("readOnlyHint") is True and ann.get("destructiveHint") is not True


def from_server(server: str, tools: list[dict[str, Any]], confirmations: dict[str, str] | None = None) -> list[Tool]:
    """Catalog entries for an adopted server. ``confirmations``: tool name -> schema hash the user
    confirmed as read-only at adoption."""
    out = []
    for t in tools:
        name = str(t.get("name") or "")
        if not name:
            continue
        h = schema_hash(t)
        confirmed = (confirmations or {}).get(name)
        ro = annotated_read_only(t) and confirmed == h
        out.append(Tool(f"{server}.{name}", server, name, str(t.get("description") or ""),
                        dict(t.get("inputSchema") or {"type": "object"}), dict(t.get("annotations") or {}), ro,
                        family_of(name, str(t.get("description") or "")), confirmed))
    return out


# Arbiter's own tools: trusted classification in code. The hook tool and the core task tools stay
# directly visible (always-visible core, §10.3); the rest can sit behind the gateway.
CORE_TOOLS = {"arbiter_hook", "arbiter_status", "arbiter_contract_propose", "arbiter_finish_check",
              "tool_search", "tool_describe", "tool_call"}
OWN_READ_ONLY = {"arbiter_status", "arbiter_ping", "arbiter_contracts", "arbiter_search", "arbiter_symbol",
                 "arbiter_related", "arbiter_context", "arbiter_advice", "arbiter_controls"}


def own_tools(tool_defs: list[dict[str, Any]]) -> list[Tool]:
    return [Tool(f"arbiter.{t['name']}", "arbiter", t["name"], str(t.get("description") or ""),
                 dict(t.get("inputSchema") or {}), {"readOnlyHint": t["name"] in OWN_READ_ONLY},
                 t["name"] in OWN_READ_ONLY, family_of(t["name"], str(t.get("description") or "")), local=True)
            for t in tool_defs if t["name"] not in CORE_TOOLS]


class Catalog:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {t.tool_id: t for t in tools or []}

    def add(self, tools: list[Tool]) -> None:
        for t in tools:
            self._tools[t.tool_id] = t

    def get(self, tool_id: str) -> Tool | None:
        return self._tools.get(tool_id)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.tool_id)

    def __len__(self) -> int:
        return len(self._tools)

    def schema_tokens(self) -> int:
        return sum(t.schema_tokens for t in self._tools.values())

"""Typed operational task-state (spec §6.4, §6.7, §6.8).

Recipes are a closed set Arbiter can evaluate itself. Anything else is stored as ``manual``
with status UNKNOWN, and only the user can confirm it (§6.8.1 step 3).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

ORIGINS = ("agent_asserted", "host_reported", "arbiter_observed")   # weakest -> strongest
ORIGIN_RANK = {o: i for i, o in enumerate(ORIGINS)}
STATUSES = ("pass", "fail", "unknown", "waived")
GRADES = ("direct", "indirect", "unavailable", "conflicted")

RECIPE_TYPES: dict[str, dict[str, Any]] = {
    # recipe type -> required fields, optional fields, minimum evidence origin
    "test_command": {"required": ["command"], "optional": ["expect"], "min_origin": "host_reported"},
    "command_exit": {"required": ["command"], "optional": ["exit_code"], "min_origin": "host_reported"},
    "file_exists": {"required": ["path"], "optional": ["absent"], "min_origin": "arbiter_observed"},
    "file_contains": {"required": ["path"], "optional": ["text", "regex", "absent"], "min_origin": "arbiter_observed"},
    "paths_unchanged": {"required": ["paths"], "optional": [], "min_origin": "arbiter_observed"},
    "public_surface_unchanged": {"required": ["paths"], "optional": [], "min_origin": "arbiter_observed"},
    "manual": {"required": [], "optional": ["description"], "min_origin": "user"},
}


class RecipeError(ValueError):
    pass


@dataclass
class Recipe:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any) -> Recipe:
        """Accept a typed dict, or free text (stored as manual). Raises on malformed typed recipes."""
        if raw is None:
            return cls("manual", {"description": ""})
        if isinstance(raw, str):
            return cls("manual", {"description": raw[:500]})
        if not isinstance(raw, dict):
            raise RecipeError("recipe must be an object or text")
        rtype = str(raw.get("type") or "")
        if rtype not in RECIPE_TYPES:
            return cls("manual", {"description": json.dumps(raw)[:500], "unrecognized_type": rtype[:40]})
        spec = RECIPE_TYPES[rtype]
        params = {k: raw[k] for k in spec["required"] + spec["optional"] if k in raw}
        missing = [k for k in spec["required"] if not params.get(k)]
        if missing:
            raise RecipeError(f"recipe '{rtype}' needs {', '.join(missing)}")
        if rtype in ("paths_unchanged", "public_surface_unchanged"):
            paths = params["paths"]
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
                raise RecipeError(f"recipe '{rtype}' needs a list of paths")
            params["paths"] = [p[:300] for p in paths[:50]]
        for k in ("command", "path", "text", "regex"):
            if k in params:
                params[k] = str(params[k])[:1000]
        if rtype == "command_exit":
            params["exit_code"] = int(params.get("exit_code", 0))
        if rtype == "test_command":
            params["expect"] = "pass"
        return cls(rtype, params)

    @property
    def min_origin(self) -> str:
        return str(RECIPE_TYPES[self.type]["min_origin"])

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.params}

    def describe(self) -> str:
        p = self.params
        if self.type == "test_command":
            return f"`{p['command']}` passes"
        if self.type == "command_exit":
            return f"`{p['command']}` exits {p.get('exit_code', 0)}"
        if self.type == "file_exists":
            return f"{p['path']} {'is absent' if p.get('absent') else 'exists'}"
        if self.type == "file_contains":
            what = p.get("text") or p.get("regex") or ""
            return f"{p['path']} {'does not contain' if p.get('absent') else 'contains'} {what!r}"
        if self.type == "paths_unchanged":
            return f"unchanged: {', '.join(p['paths'])}"
        if self.type == "public_surface_unchanged":
            return f"public surface unchanged: {', '.join(p['paths'])}"
        return f"manual: {p.get('description', '')}"[:200]


@dataclass
class Contract:
    id: str                      # display id, e.g. "C3"
    session_id: str
    goal_epoch: int
    text: str
    quotes: list[str]
    source_intent_ids: list[str]
    recipe: Recipe
    proposed_by: str = "host_agent"        # host_agent | rules | user
    scope: str = ""
    version: int = 1
    status: str = "unknown"
    strength_flag: str = "ok"              # ok | low | manual
    strength_reason: str = ""
    mapping_confidence: float = 1.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    superseded_by: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def row_id(self) -> str:
        return f"{self.session_id}#{self.id}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["recipe"] = self.recipe.to_dict()
        d["recipe_text"] = self.recipe.describe()
        return d


@dataclass
class Fact:
    kind: str
    origin: str
    subject: str | None
    status: str | None
    data: dict[str, Any]
    session_id: str
    goal_epoch: int = 0
    tool_use_id: str | None = None
    source_seq: int | None = None
    created_at: float = 0.0
    id: int | None = None


def contract_from_row(row: Any) -> Contract:
    """Build a Contract from a ``contract`` table row (sqlite3.Row)."""
    extra = json.loads(row["evidence_json"] or "{}") if row["evidence_json"] else {}
    if isinstance(extra, list):
        extra = {"evidence": extra}
    recipe_raw = json.loads(row["verification_recipe_json"] or "{}")
    rtype = recipe_raw.pop("type", "manual")
    rid = str(row["id"])
    session_id, _, short = rid.rpartition("#")
    return Contract(
        id=short, session_id=session_id, goal_epoch=int(row["goal_epoch"] or 0), text=row["normalized_text"] or "",
        quotes=json.loads(row["quotes_json"] or "[]"),
        source_intent_ids=json.loads(row["source_intent_ids_json"] or "[]"),
        recipe=Recipe(rtype, recipe_raw), proposed_by=row["proposed_by"] or "", scope=row["scope"] or "",
        version=int(row["version"]), status=row["status"], strength_flag=row["strength_flag"] or "ok",
        strength_reason=str(extra.get("strength_reason", "")), mapping_confidence=float(row["mapping_confidence"] or 0),
        evidence=list(extra.get("evidence", [])), superseded_by=row["superseded_by"],
        snapshot=dict(extra.get("snapshot", {})),
    )

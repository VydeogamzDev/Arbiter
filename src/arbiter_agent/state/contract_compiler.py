"""Contract compiler (spec §6.8, §6.8.1): the host agent proposes, Arbiter checks integrity.

- Provenance: every quote must appear verbatim in this session's immutable intent log.
- Recipes: typed set only; free text becomes ``manual`` (UNKNOWN until the user confirms).
- Status authority: proposals can't set status. New contracts start UNKNOWN.
- Weak-contract defense: a deterministic strength heuristic flags recipes weaker than the quote.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.state import intent as intent_log
from arbiter_agent.state.schema import Contract, Recipe, RecipeError, contract_from_row
from arbiter_agent.state.text_norm import norm
from arbiter_agent.telemetry.runner_parsers import PARSERS, fingerprint
from arbiter_agent.telemetry.runner_parsers.base import tokens
from arbiter_agent.telemetry.runner_parsers.registry import GENERIC_TEST

MAX_OBLIGATIONS = 30
BEHAVIOR_WORDS = re.compile(r"\b(fix|bug|implement|add (?:support|a feature|handling|validation|retries)|support|"
                            r"refactor|handle|resolve|optimi[sz]e|improve|make .{0,40}work|speed up|"
                            r"should (?:return|work|pass|fail|raise|accept|reject)|feature|behaviou?r|crash|error)",
                            re.I)
ALL_TESTS = re.compile(r"\b(all (?:the )?tests|full (?:test )?suite|entire (?:test )?suite|every test|whole (?:test )?"
                       r"suite|all (?:unit|integration) tests)\b", re.I)
TRIVIAL_CMD = re.compile(r"^(?:echo|true|:|exit 0|ls|dir|pwd|cat|type|get-content|get-childitem|whoami|date|"
                         r"write-output|write-host|printf)\b", re.I)


@dataclass
class ProposalResult:
    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "rejected": self.rejected, "superseded": self.superseded}


def is_runner_command(command: str) -> bool:
    fp = fingerprint(command)
    return any(p.command.search(fp) for p in PARSERS) or bool(GENERIC_TEST.search(fp))


def strength(quotes: list[str], recipe: Recipe) -> tuple[str, str]:
    if recipe.type == "manual":
        return "manual", "needs your confirmation; Arbiter can't check it"
    q = " ".join(quotes)
    if recipe.type in ("file_exists", "file_contains") and BEHAVIOR_WORDS.search(q):
        return "low", "a file check can't show the requested behavior"
    cmd = str(recipe.params.get("command", ""))
    if cmd and TRIVIAL_CMD.match(fingerprint(cmd)):
        return "low", "the command can't fail in a meaningful way"
    if recipe.type == "test_command":
        if not is_runner_command(cmd):
            return "low", "test output from this command can't be parsed, so it stays UNKNOWN"
        positional = [t for t in tokens(cmd)[1:] if not t.startswith("-") and t not in ("run", "test", "exec")]
        if ALL_TESTS.search(q) and positional:
            return "low", "narrower than the requested test scope"
    if recipe.type == "command_exit" and BEHAVIOR_WORDS.search(q) and not is_runner_command(cmd):
        return "low", "an exit status alone may not show the requested behavior"
    return "ok", ""


def _file_sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def expand_paths(root: str, patterns: list[str], limit: int = 500) -> list[Path]:
    base = Path(root)
    out: list[Path] = []
    for pat in patterns:
        pat = pat.replace("\\", "/").lstrip("./") if not Path(pat).is_absolute() else pat
        p = Path(pat) if Path(pat).is_absolute() else base / pat
        if any(ch in pat for ch in "*?["):
            out += [m for m in base.glob(pat) if m.is_file()][:limit]
        elif p.is_dir():
            out += [m for m in p.rglob("*") if m.is_file() and ".git" not in m.parts][:limit]
        else:
            out.append(p)
    return out[:limit]


PY_SURFACE = re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)\s*\(|^class\s+([A-Za-z]\w*)\b|"
                        r"^([A-Z][A-Z0-9_]*)\s*[:=]", re.M)
PY_SIG = re.compile(r"^(?:async\s+)?def\s+[A-Za-z]\w*\s*\([^)]*\)", re.M)
JS_SURFACE = re.compile(r"^export\s+(?:default\s+)?(?:async\s+)?(?:function\*?|class|const|let|var|interface|type|enum)"
                        r"\s+([A-Za-z_$][\w$]*)|^export\s*\{([^}]*)\}", re.M)


def public_surface(path: Path) -> list[str] | None:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return None
    names: set[str] = set()
    if path.suffix == ".py":
        allm = re.search(r"__all__\s*=\s*[\[(]([^\])]*)[\])]", text)
        if allm:
            names |= {n.strip().strip("'\"") for n in allm.group(1).split(",") if n.strip()}
        else:
            for m in PY_SURFACE.finditer(text):
                n = next(g for g in m.groups() if g)
                if not n.startswith("_"):
                    names.add(n)
        # signatures matter too: record top-level def lines
        names |= {f"sig:{m.group(0).strip()}" for m in PY_SIG.finditer(text)}
    elif path.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts"):
        for m in JS_SURFACE.finditer(text):
            if m.group(1):
                names.add(m.group(1))
            elif m.group(2):
                names |= {n.strip().split(" as ")[-1].strip() for n in m.group(2).split(",") if n.strip()}
    else:
        return None
    return sorted(names)


def snapshot(recipe: Recipe, root: str | None) -> dict[str, Any]:
    if not root or recipe.type not in ("paths_unchanged", "public_surface_unchanged"):
        return {}
    files = expand_paths(root, recipe.params["paths"])
    snap: dict[str, Any] = {}
    for f in files:
        key = str(f.relative_to(root)).replace("\\", "/") if str(f).startswith(str(root)) else str(f)
        snap[key] = _file_sha(f) if recipe.type == "paths_unchanged" else public_surface(f)
    return {"files": snap}


def next_id(conn: sqlite3.Connection, session_id: str) -> str:
    n = conn.execute("SELECT COUNT(DISTINCT id) FROM contract WHERE thread_id = ?", (session_id,)).fetchone()[0]
    return f"C{int(n) + 1}"


def insert(conn: sqlite3.Connection, c: Contract) -> None:
    extra = {"strength_reason": c.strength_reason, "evidence": c.evidence, "snapshot": c.snapshot}
    conn.execute(
        "INSERT INTO contract(id, thread_id, goal_epoch, version, normalized_text, source_intent_ids_json, "
        "quotes_json, proposed_by, scope, status, mapping_confidence, strength_flag, verification_recipe_json, "
        "min_evidence_origin, "
        "evidence_json, superseded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c.row_id, c.session_id, c.goal_epoch, c.version, c.text, json.dumps(c.source_intent_ids),
         json.dumps(c.quotes), c.proposed_by, c.scope, c.status, c.mapping_confidence, c.strength_flag,
         json.dumps(c.recipe.to_dict()), c.recipe.min_origin, json.dumps(extra), c.superseded_by))


def load(conn: sqlite3.Connection, session_id: str, *, active_only: bool = True,
         epoch: int | None = None) -> list[Contract]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute("SELECT * FROM contract c WHERE thread_id = ? AND version = (SELECT MAX(version) FROM contract "
                       "c2 WHERE c2.id = c.id) ORDER BY rowid", (session_id,)).fetchall()
    out = [contract_from_row(r) for r in rows]
    if epoch is not None:
        out = [c for c in out if c.goal_epoch == epoch]
    if active_only:
        out = [c for c in out if not c.superseded_by]
    return out


def set_status(conn: sqlite3.Connection, c: Contract, status: str, evidence: list[dict[str, Any]]) -> None:
    extra = {"strength_reason": c.strength_reason, "evidence": evidence[-10:], "snapshot": c.snapshot}
    conn.execute("UPDATE contract SET status = ?, evidence_json = ? WHERE id = ? AND version = ?",
                 (status, json.dumps(extra, default=str), c.row_id, c.version))


def supersede(conn: sqlite3.Connection, row_id: str, by: str) -> None:
    conn.execute("UPDATE contract SET superseded_by = ? WHERE id = ? AND superseded_by IS NULL", (by, row_id))


def propose(conn: sqlite3.Connection, session_id: str, epoch: int, obligations: Any, *, root: str | None,
            proposed_by: str = "host_agent", supersedes: list[str] | None = None,
            user_source: str | None = None) -> ProposalResult:
    """``user_source`` (an intent id) marks contracts the user created directly (CLI): the user is
    the source, so quote provenance isn't required."""
    res = ProposalResult()
    if not isinstance(obligations, list):
        res.rejected.append({"reason": "contracts must be a list"})
        return res
    intents = intent_log.load_intents(conn, session_id)
    existing = load(conn, session_id, epoch=epoch)
    for ob in obligations[:MAX_OBLIGATIONS]:
        if not isinstance(ob, dict):
            res.rejected.append({"reason": "each contract must be an object"})
            continue
        text = str(ob.get("text") or ob.get("obligation") or "").strip()[:500]
        quotes = ob.get("quotes") or ob.get("quote") or []
        if isinstance(quotes, str):
            quotes = [quotes]
        quotes = [str(q)[:1000] for q in quotes if str(q).strip()][:10]
        if not text:
            res.rejected.append({"text": text, "reason": "missing obligation text"})
            continue
        if not quotes:
            res.rejected.append({"text": text, "reason": "needs at least one verbatim quote from the user's messages"})
            continue
        sources, bad = [], []
        for q in quotes:
            hit = intent_log.find_quote(q, intents)
            if hit is None:
                bad.append(q[:120])
            elif hit.short_id not in sources:
                sources.append(hit.short_id)
        if bad and user_source:
            bad, sources = [], sources or [user_source]
        if bad:
            res.rejected.append({"text": text, "reason": "quote not found in the user's messages", "quotes": bad})
            continue
        try:
            recipe = Recipe.parse(ob.get("recipe") if "recipe" in ob else ob.get("verification"))
        except RecipeError as exc:
            res.rejected.append({"text": text, "reason": str(exc)})
            continue
        dup = next((c for c in existing if norm(c.text) == norm(text) and c.recipe.to_dict() == recipe.to_dict()), None)
        if dup is not None:
            res.accepted.append({"id": dup.id, "duplicate": True, "text": dup.text})
            continue
        flag, why = strength(quotes, recipe)
        c = Contract(id=next_id(conn, session_id), session_id=session_id, goal_epoch=epoch, text=text, quotes=quotes,
                     source_intent_ids=sources, recipe=recipe, proposed_by=proposed_by,
                     scope=str(ob.get("scope") or "")[:200], strength_flag=flag, strength_reason=why,
                     snapshot=snapshot(recipe, root))
        insert(conn, c)
        existing.append(c)
        res.accepted.append({"id": c.id, "text": c.text, "recipe": recipe.describe(), "strength": flag,
                             **({"strength_note": why} if why else {})})
    for sid in supersedes or []:
        short = str(sid).rpartition("#")[2]
        rid = f"{session_id}#{short}"
        row = conn.execute("SELECT 1 FROM contract WHERE id = ? AND superseded_by IS NULL", (rid,)).fetchone()
        if row:
            supersede(conn, rid, ",".join(a["id"] for a in res.accepted) or "scope_change")
            res.superseded.append(short)
    return res

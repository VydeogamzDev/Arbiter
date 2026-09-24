"""Manual controls (spec §24), persisted and authority-checked (spec §15.5).

Controls:
- ``module:<flag>`` on/off, global: disable or re-enable an individual module;
- ``controller`` on/off, global or per session: off returns clients to baseline behavior while
  events are still recorded;
- ``next_turn:<control>``, per session: one-shot optimization bypasses (the only controls an
  agent may set for itself), consumed by the module they bypass;
- breaker resets go through :meth:`reset_breaker`.

The completion gate mode keeps its own control (``arbiter gate``, stored with the session).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from arbiter_agent.flags import REGISTRY
from arbiter_agent.policy import authority
from arbiter_agent.policy.authority import Actor
from arbiter_agent.policy.circuit_breaker import SPECS, BreakerBoard
from arbiter_agent.state.store import connect

AuditFn = Callable[[str, dict[str, Any]], None]


class Overrides:
    def __init__(self, db: Any, writer: Any, flags: Any, board: BreakerBoard | None = None,
                 audit: AuditFn | None = None) -> None:
        self.db = db
        self.writer = writer
        self.flags = flags
        self.board = board
        self.audit = audit
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}

    # ------------------------------------------------------------------ load / apply
    def load(self) -> Overrides:
        rc = connect(self.db, readonly=True)
        try:
            rows = rc.execute("SELECT scope, key, value_json, actor, reason, set_at FROM control_override").fetchall()
        finally:
            rc.close()
        self._rows = {(r[0], r[1]): {"scope": r[0], "key": r[1], "value": json.loads(r[2]), "actor": r[3],
                                     "reason": r[4], "set_at": r[5]} for r in rows}
        for (scope, key), row in self._rows.items():
            self._apply(scope, key, row["value"])
        return self

    def _apply(self, scope: str, key: str, value: Any) -> None:
        if scope != "global" or self.flags is None:
            return
        if key.startswith("module:"):
            self.flags.set_user(key.split(":", 1)[1], None if value is None else bool(value))
        elif key == "controller":
            self.flags.set_user("controller", None if value is None else bool(value))

    @staticmethod
    def _normalize(key: str, value: Any) -> Any:
        if isinstance(value, str):
            v = value.lower()
            if v in ("on", "true", "1", "yes"):
                return True
            if v in ("off", "false", "0", "no"):
                return False
        return value

    # ------------------------------------------------------------------ changes
    def set(self, key: str, value: Any, *, actor: Actor, scope: str = "global", reason: str = "") -> dict[str, Any]:
        value = self._normalize(key, value)
        if key.startswith("module:"):
            flag = key.split(":", 1)[1]
            if flag not in REGISTRY:
                raise KeyError(f"unknown module {flag!r}; see `arbiter control`")
            if scope != "global":
                raise ValueError("modules are switched globally")
            if not isinstance(value, bool):
                raise ValueError("module controls take on/off")
        elif key == "controller":
            if not isinstance(value, bool):
                raise ValueError("controller takes on/off")
        elif key in authority.AGENT_CONTROLS:
            if scope == "global":
                raise ValueError("next-turn controls apply to one session")
            value = True
        else:
            raise KeyError(f"unknown control {key!r}")
        why = authority.check(actor, key, value)
        if why:
            raise PermissionError(why)
        row = {"scope": scope, "key": key, "value": value, "actor": actor.name.lower(), "reason": reason[:300],
               "set_at": time.time()}
        self.writer.run(lambda c: c.execute(
            "INSERT INTO control_override(scope, key, value_json, actor, reason, set_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(scope, key) DO UPDATE SET value_json = excluded.value_json, actor = excluded.actor, "
            "reason = excluded.reason, set_at = excluded.set_at",
            (scope, key, json.dumps(value), row["actor"], row["reason"], row["set_at"])), timeout=5)
        self._rows[(scope, key)] = row
        self._apply(scope, key, value)
        self._audit("internal.control", row)
        return row

    def clear(self, key: str, *, actor: Actor, scope: str = "global") -> bool:
        row = self._rows.get((scope, key))
        if row is None:
            return False
        # clearing restores the default; clearing a user's integrity-strengthening choice is fine,
        # but an agent can only clear its own next-turn controls
        if actor == Actor.AGENT and key not in authority.AGENT_CONTROLS:
            raise PermissionError(f"{key} can only be changed by the user with `arbiter control ...`")
        self.writer.run(lambda c: c.execute("DELETE FROM control_override WHERE scope = ? AND key = ?",
                                            (scope, key)), timeout=5)
        del self._rows[(scope, key)]
        self._apply(scope, key, None)
        self._audit("internal.control", {"scope": scope, "key": key, "value": None, "actor": actor.name.lower()})
        return True

    def reset_breaker(self, name: str, *, actor: Actor, reason: str = "") -> dict[str, Any]:
        if self.board is None:
            raise RuntimeError("circuit breakers are disabled")
        kind = name.split(":", 1)[0]
        spec = SPECS.get(kind)
        if spec is None:
            raise KeyError(f"no breaker kind {kind!r}")
        why = authority.check(actor, f"breaker_reset:{'integrity' if spec.integrity else 'optimization'}")
        if why:
            raise PermissionError(why)
        was_open = self.board.reset(name, f"manual reset by {actor.name.lower()}: {reason}"[:200])
        return {"breaker": name, "was_open": was_open}

    # ------------------------------------------------------------------ queries
    def controller_on(self, session: str | None = None) -> bool:
        if session is not None:
            row = self._rows.get((session, "controller"))
            if row is not None:
                return bool(row["value"])
        return True if self.flags is None else bool(self.flags.enabled("controller"))

    def take_next_turn(self, session: str, key: str) -> bool:
        """Consume a one-shot control. True if it was set."""
        if (session, key) not in self._rows:
            return False
        self.clear(key, actor=Actor.AGENT, scope=session)
        return True

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._rows.values(), key=lambda r: (r["scope"], r["key"]))

    def _audit(self, etype: str, payload: dict[str, Any]) -> None:
        if self.audit is not None:
            try:
                self.audit(etype, payload)
            except Exception:
                pass


def persist_breakers(conn: sqlite3.Connection, board: BreakerBoard) -> None:
    now = time.time()
    live = {r["name"]: r for r in board.persisted()}
    marks = ",".join("?" * len(live))
    conn.execute(f"DELETE FROM breaker_state WHERE name NOT IN ({marks})" if live
                 else "DELETE FROM breaker_state", tuple(live))
    for r in live.values():
        conn.execute("INSERT INTO breaker_state(name, opened_at, trips, last_reason, updated_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(name) DO UPDATE SET opened_at = excluded.opened_at, trips = excluded.trips, "
                     "last_reason = excluded.last_reason, updated_at = excluded.updated_at",
                     (r["name"], r["opened_at"], r["trips"], r["last_reason"], now))


def load_breakers(db: Any) -> list[dict[str, Any]]:
    rc = connect(db, readonly=True)
    try:
        return [{"name": r[0], "opened_at": r[1], "trips": r[2], "last_reason": r[3]}
                for r in rc.execute("SELECT name, opened_at, trips, last_reason FROM breaker_state "
                                    "WHERE opened_at IS NOT NULL")]
    finally:
        rc.close()

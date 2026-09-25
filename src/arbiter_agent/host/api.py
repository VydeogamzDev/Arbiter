"""Host Advisory API v0 (spec §4.6.2; M9 build step 24). Shadow mode.

Operations over the daemon's authenticated IPC (method names prefixed ``host.``):

- ``capabilities()`` -> api version, mode, operations, sensor state, open breakers;
- ``recommend_call(call_context, allowed_set, deadline_ms)`` -> model x effort within the allowed set,
  scores, abstain, decision_id;
- ``rerank_candidates(query_context)`` -> pins + ranked files for a task (retrieval reranker);
- ``session_signals(session_ref)`` -> loop / no_progress / retrieval_miss / evidence_gaps;
- ``report_outcome(decision_id, outcome)`` -> ack (usage incl. cached input, success, rework, latency).

Rules enforced here:
- **Advisory only, never wider than the allowed set.** The response is validated against the
  host's set; anything else abstains. v0 runs in **shadow**: responses say ``mode: shadow``, and the
  host logs them without applying them.
- **Deadlines.** Answers come from rules and precomputed state within the host's deadline; the
  sensor is never awaited. A missed deadline or an error abstains, and the host proceeds unchanged.
- **Project confinement** (§4.6.3). Every decision and outcome is stored under a per-project partition
  (normalized repository identity, or the host's project id), and outcomes from one partition never
  feed another's recommendations.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from arbiter_agent.reasoning import scheduler
from arbiter_agent.reasoning.leases import LeaseBook

API_VERSION = "0.1"
MODE = "shadow"
OPERATIONS = ["capabilities", "recommend_call", "rerank_candidates", "session_signals", "report_outcome"]


def partition_key(project: dict[str, Any] | None) -> str:
    """Normalized repository identity when the host gives a path, else a hash of its project id."""
    project = project or {}
    if project.get("path"):
        from arbiter_agent.state.repo_identity import identify

        try:
            return "repo:" + identify(str(project["path"])).key
        except Exception:
            pass
    ident = str(project.get("id") or "unknown")
    return "host-project:" + hashlib.sha256(ident.encode()).hexdigest()[:16]


def log_decision(writer: Any, kind: str, host: str | None, pkey: str, sid: str | None, request: Any, response: Any,
                 summary: str, mode: str = MODE) -> str:
    """Append an advisory decision to the audit trail; returns its id. Never raises."""
    did = f"d_{secrets.token_hex(8)}"
    row = (did, time.time(), kind, mode, host, pkey, sid, json.dumps(request, default=str)[:65536],
           json.dumps(response, default=str)[:65536], summary[:300])
    try:
        writer.submit(lambda c: c.execute(
            "INSERT INTO advisory_decision(decision_id, created_at, kind, mode, host, partition_key, session_id, "
            "request_json, response_json, summary) VALUES (?,?,?,?,?,?,?,?,?,?)", row))
    except Exception:
        pass
    return did


def list_decisions(db: Any, *, limit: int = 20, session_id: str | None = None, kind: str | None = None) -> list[dict]:
    from arbiter_agent.state.store import connect

    rc = connect(db, readonly=True)
    try:
        where, args = [], []
        if session_id:
            where.append("session_id = ?")
            args.append(session_id)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        sql = ("SELECT decision_id, created_at, kind, mode, host, partition_key, session_id, summary, response_json, "
               "outcome_json FROM advisory_decision" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY created_at DESC LIMIT ?")
        rows = rc.execute(sql, (*args, limit)).fetchall()
    finally:
        rc.close()
    out = []
    for r in rows:
        resp = json.loads(r[8]) if r[8] else {}
        out.append({"decision_id": r[0], "created_at": r[1], "kind": r[2], "mode": r[3], "host": r[4],
                    "partition": r[5], "session_id": r[6], "summary": r[7],
                    "reasons": (resp.get("reasons") or resp.get("reasoning", {}).get("reasons") or [])[:8],
                    "outcome": json.loads(r[9]) if r[9] else None})
    return out


class HostAPI:
    def __init__(self, writer: Any, db: Any, *, config: Any = None, engine: Any = None, retrieval: Any = None,
                 sensor: Any = None, board: Any = None) -> None:
        self.writer, self.db, self.config = writer, db, config
        self.engine, self.retrieval, self.sensor, self.board = engine, retrieval, sensor, board
        cfg = (lambda k, d: config.get(k, d)) if config is not None else (lambda k, d: d)
        self.leases = LeaseBook(int(cfg("reasoning.high_lease_calls", 2)),
                                int(cfg("reasoning.expensive_tier_lease_calls", 1)),
                                int(cfg("reasoning.deescalate_progress_checkpoints", 2)))
        self.default_deadline_ms = float(cfg("hosts.default_deadline_ms", 150))
        self.stats = {"recommend": 0, "abstain": 0, "outcomes": 0, "deadline_missed": 0}

    # ------------------------------------------------------------------ operations
    def capabilities(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, "mode": MODE, "operations": OPERATIONS,
                "semif_state": self.sensor.health() if self.sensor is not None else None,
                "breakers": self.board.open_names() if self.board is not None else []}

    def recommend_call(self, params: dict[str, Any], host: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        deadline_ms = float(params.get("deadline_ms") or self.default_deadline_ms)
        ctx = dict(params.get("call_context") or {})
        allowed = list(params.get("allowed_set") or [])
        pkey = partition_key(params.get("project"))
        ctx["prior_outcomes"] = (self._priors(pkey, str(ctx.get("task_id") or ""))
                                 + list(ctx.get("prior_outcomes") or []))
        task_key = f"{pkey}:{ctx.get('task_id')}" if ctx.get("task_id") else None
        try:
            rec = scheduler.recommend(ctx, allowed, self.leases, task_key)
        except Exception as exc:
            rec = scheduler.Recommendation(None, None, 0, 0, True, f"error: {type(exc).__name__}: {exc}"[:200])
        elapsed = (time.perf_counter() - t0) * 1000
        out = rec.to_dict()
        if not rec.abstain and not _in_allowed(rec.model, rec.effort, allowed):
            out.update(model=None, effort=None, abstain=True, reason="recommendation outside the allowed set")
        if elapsed > deadline_ms:
            self.stats["deadline_missed"] += 1
            out.update(model=None, effort=None, abstain=True, reason=f"deadline missed ({elapsed:.0f} ms)")
        self.stats["recommend"] += 1
        self.stats["abstain"] += int(bool(out["abstain"]))
        out.update(mode=MODE, api_version=API_VERSION, elapsed_ms=round(elapsed, 2))
        out["decision_id"] = self._log("recommend_call", host, pkey, None,
                                       {"call_context": ctx, "allowed_set": allowed}, out, _summary(out))
        return out

    def rerank_candidates(self, params: dict[str, Any], host: str) -> dict[str, Any]:
        if self.retrieval is None:
            return {"abstain": True, "reason": "repository index disabled", "mode": MODE}
        project = params.get("project") or {}
        cwd = project.get("path")
        if not cwd:
            return {"abstain": True, "reason": "rerank needs project.path", "mode": MODE}
        out = self.retrieval.context(str(cwd), dict(params.get("query_context") or {}))
        out.update(mode=MODE, abstain=False)
        out["decision_id"] = self._log("rerank", host, partition_key(project), None, params,
                                       {k: out[k] for k in ("pins", "ranked", "k", "reasons", "selected") if k in out},
                                       f"{len(out.get('selected', []))} files (k={out.get('k')})")
        return out

    def session_signals(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.engine is None:
            return {"abstain": True, "reason": "task state disabled"}
        sid = self.engine.resolve_session(params)
        if sid is None:
            return {"abstain": True, "reason": "unknown session"}
        return {"session_id": sid, **self.engine.signals(sid)}

    def report_outcome(self, params: dict[str, Any], host: str) -> dict[str, Any]:
        did = str(params.get("decision_id") or "")
        outcome = dict(params.get("outcome") or {})
        known = {"success", "rework", "latency_ms", "usage", "applied", "status", "cached_input_tokens",
                 "input_tokens", "output_tokens", "cost", "note"}
        outcome = {k: v for k, v in outcome.items() if k in known}

        def job(conn: Any) -> int:
            cur = conn.execute("UPDATE advisory_decision SET outcome_json = ?, outcome_at = ? "
                               "WHERE decision_id = ? AND (host IS ? OR host = ?)",
                               (json.dumps(outcome), time.time(), did, host, host))
            return int(cur.rowcount)

        n = self.writer.run(job, timeout=5)
        if not n:
            raise LookupError(f"unknown decision {did!r} for this host")
        self.stats["outcomes"] += 1
        return {"ack": True, "decision_id": did}

    # ------------------------------------------------------------------ storage
    def _log(self, kind: str, host: str | None, pkey: str, sid: str | None, request: Any, response: Any,
             summary: str) -> str:
        return log_decision(self.writer, kind, host, pkey, sid, request, response, summary)

    def _priors(self, pkey: str, task_id: str) -> list[dict[str, Any]]:
        """Outcomes for this task in this partition only (project confinement)."""
        if not task_id:
            return []
        from arbiter_agent.state.store import connect

        rc = connect(self.db, readonly=True)
        try:
            rows = rc.execute("SELECT request_json, response_json, outcome_json FROM advisory_decision "
                              "WHERE partition_key = ? AND kind = 'recommend_call' AND outcome_json IS NOT NULL "
                              "ORDER BY created_at DESC LIMIT 50", (pkey,)).fetchall()
        finally:
            rc.close()
        out = []
        for req, resp, oc in rows:
            try:
                if (json.loads(req).get("call_context") or {}).get("task_id") != task_id:
                    continue
                r, o = json.loads(resp), json.loads(oc)
            except ValueError:
                continue
            if r.get("model") and "success" in o:
                out.append({"model": r["model"], "success": bool(o["success"])})
        return out


def _in_allowed(model: str | None, effort: str | None, allowed: list[dict[str, Any]]) -> bool:
    return any(a.get("model") == model and effort in (a.get("efforts") or ["default"]) for a in allowed)


def _summary(out: dict[str, Any]) -> str:
    if out.get("abstain"):
        return f"abstain: {out.get('reason')}"
    return f"{out.get('model')} @ {out.get('effort')} (level {out.get('level_name')}, floor {out.get('floor')})"

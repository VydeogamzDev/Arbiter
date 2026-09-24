"""Session engine (M3): turns the event log into operational task state.

Design:
- It consumes ``event_log`` in seq order from a persisted cursor, so events whose hook caller
  stopped waiting (``pending``) are still processed and restarts resume where they left off.
- Tool actions (contract proposals, scope changes, user decisions, verify results) and epoch
  changes are appended to the event log as ``internal.*`` audit events. The task-state tables
  are the operational state; v0.1 does not rebuild them from the log.
- All state mutation for an event happens in one writer job (single-writer rule).
- Heavy work (baseline capture, integrity scans, git) runs on a background queue and only
  submits small writes. The gate path calls :meth:`catch_up` with its deadline first, so gate
  decisions see every event logged before the stop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from arbiter_agent.clients import event_dedupe
from arbiter_agent.clients.event_normalizer import NormalizedEvent, canonical_bytes, hook_event_type
from arbiter_agent.completion import claim_detection, evidence_ledger, gate
from arbiter_agent.completion.breakers import Breakers
from arbiter_agent.completion.evidence_grades import ContractEval, EvalContext, evaluate
from arbiter_agent.concurrency import Deadline, EpochCancel, Priority, WorkQueue
from arbiter_agent.reasoning.loop_detector import LoopDetector
from arbiter_agent.state import contract_compiler, contract_coverage, facts, goal_epochs
from arbiter_agent.state import intent as intent_log
from arbiter_agent.state.repo_identity import RepoIdentity, identify
from arbiter_agent.state.schema import Contract
from arbiter_agent.state.store import connect
from arbiter_agent.telemetry import baseline as baseline_mod
from arbiter_agent.telemetry import cache_metrics, progress, test_integrity
from arbiter_agent.telemetry.runner_parsers import PARSERS, fingerprint
from arbiter_agent.ui import status as ui_status

log = logging.getLogger("arbiter.engine")
BATCH = 200
SESSION_COLS = ("session_id", "client_id", "goal_epoch", "intent_count", "epoch_start_ordinal", "candidate_ordinal",
                "epoch_confirmed_at", "epoch_reason", "processed_seq", "hook_intents", "cwd", "repo_json",
                "baseline_id", "stop_blocks", "flags_json", "last_ledger_hash", "last_injected_hash", "updated_at")


def _runner_for(command: str) -> str | None:
    fp = fingerprint(command)
    for p in PARSERS:
        if p.command.search(fp):
            return p.name
    return None


class SessionEngine:
    def __init__(self, *, db: Path, writer: Any, keyer: Callable[[bytes], str], config: Any, reducer: Any = None,
                 breakers: Breakers | None = None, background: bool = True, flags: Any = None) -> None:
        self.db = db
        self.flags = flags
        self.writer = writer
        self.keyer = keyer
        self.config = config
        self.reducer = reducer
        self.breakers = breakers or Breakers(float(config.get("hooks.gating_p95_budget_ms", 300)))
        self.bg = WorkQueue("engine-bg", capacity=256)
        self.cancel = EpochCancel()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._background = background
        self._detectors: dict[str, LoopDetector] = {}
        self._calls: dict[tuple[str, str], tuple[str, Any]] = {}       # (session, call_id) -> (tool, input)
        self._cwd_hint: dict[str, str] = {}
        self._integrity_cache: dict[str, tuple[int, dict[str, Any]]] = {}   # session -> (change seq, report)
        self._baseline_pending: set[str] = set()
        self.stats: dict[str, int] = {"events": 0, "errors": 0, "intents": 0, "facts": 0, "epochs": 0}

    def enabled(self, name: str) -> bool:
        return True if self.flags is None else bool(self.flags.enabled(name))

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> SessionEngine:
        self.bg.start()
        self._thread = threading.Thread(target=self._loop, name="arbiter-engine", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(5)
        self.bg.stop()

    def wake(self, ev: NormalizedEvent, res: Any = None) -> None:
        """Ingest observer: remember the cwd (not stored in the log) and wake the loop."""
        if ev.session_id and ev.cwd:
            self._cwd_hint[ev.session_id] = ev.cwd
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.catch_up(Deadline(5.0))
            except Exception:
                self.stats["errors"] += 1
                log.exception("engine catch-up failed")

    def drain(self, timeout: float = 10.0) -> None:
        """Process everything logged so far, including background work (tests, CLI barriers)."""
        self.catch_up(Deadline(timeout))
        self.bg.drain(timeout)
        self.catch_up(Deadline(timeout))

    # ------------------------------------------------------------------ catch-up
    def catch_up(self, deadline: Deadline | None = None) -> bool:
        deadline = deadline or Deadline(5.0)
        while not deadline.expired():
            try:
                fut = self.writer.submit(self._batch_job, timeout=min(0.5, max(0.01, deadline.remaining())))
                n, bg = fut.result(timeout=max(0.05, deadline.remaining()))
            except Exception as exc:
                if type(exc).__name__ in ("TimeoutError", "WriterBusy", "ReadOnlyDegraded"):
                    return False
                raise
            for job in bg:
                self._schedule(*job)
            if n < BATCH:
                return True
        return False

    def _batch_job(self, conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str, int]]]:
        cursor = int(conn.execute("SELECT seq FROM engine_cursor WHERE id = 1").fetchone()[0])
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row
        rows = cur.execute(
            "SELECT e.seq, e.client_id, e.session_id, e.surface, e.event_type, e.duplicate_of, e.attrs_json, "
            "e.received_at, b.data FROM event_log e LEFT JOIN blob b ON b.hash = e.payload_pointer "
            "WHERE e.seq > ? ORDER BY e.seq LIMIT ?", (cursor, BATCH)).fetchall()
        bg: list[tuple[str, str, int]] = []
        for r in rows:
            cursor = int(r["seq"])
            if not r["session_id"] or r["surface"] == "internal":
                continue
            # Cross-surface duplicates are skipped, except transcript tool results: they are the same
            # occurrence as the hook event but carry the exit status the hook lacks (decision 0020).
            if r["duplicate_of"] is not None and not (r["surface"] == "transcript"
                                                      and r["event_type"] == "tool_result"):
                continue
            try:
                conn.execute("SAVEPOINT ev")
                bg += self._process(conn, r)
                conn.execute("RELEASE ev")
                self.stats["events"] += 1
            except Exception:
                conn.execute("ROLLBACK TO ev")
                conn.execute("RELEASE ev")
                self.stats["errors"] += 1
                log.exception("engine failed on event", extra={"fields": {"seq": cursor}})
        conn.execute("UPDATE engine_cursor SET seq = ? WHERE id = 1", (cursor,))
        return len(rows), bg

    # ------------------------------------------------------------------ session state
    def _state(self, conn: sqlite3.Connection, session_id: str, client_id: str | None = None,
               create: bool = True) -> dict[str, Any] | None:
        row = conn.execute(f"SELECT {', '.join(SESSION_COLS)} FROM session_state WHERE session_id = ?",
                           (session_id,)).fetchone()
        if row is None:
            if not create:
                return None
            now = time.time()
            conn.execute("INSERT INTO session_state(session_id, client_id, updated_at) VALUES (?,?,?)",
                         (session_id, client_id or session_id.split(":", 1)[0], now))
            row = conn.execute(f"SELECT {', '.join(SESSION_COLS)} FROM session_state WHERE session_id = ?",
                               (session_id,)).fetchone()
        st = dict(zip(SESSION_COLS, row, strict=True))
        st["flags"] = json.loads(st.get("flags_json") or "{}")
        return st

    def _save(self, conn: sqlite3.Connection, st: dict[str, Any], **changes: Any) -> None:
        st.update(changes)
        if "flags" in changes:
            st["flags_json"] = json.dumps(changes.pop("flags"))
            changes["flags_json"] = st["flags_json"]
        if not changes:
            return
        changes["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE session_state SET {sets} WHERE session_id = ?", (*changes.values(), st["session_id"]))

    def _internal_event(self, conn: sqlite3.Connection, st: dict[str, Any], etype: str, payload: dict[str, Any],
                        key: str) -> None:
        from arbiter_agent.audit.event_log import append_event

        client, _, native = st["session_id"].partition(":")
        ev = NormalizedEvent(client_id=client, profile_version=1, native_session_id=native or None, surface="internal",
                             event_type=etype, raw_hash=self.keyer(canonical_bytes(payload)), payload=payload,
                             attrs={k: v for k, v in payload.items() if isinstance(v, (int, float, str, bool))
                                    and k != "text"})
        ev.idempotency_key = f"internal:{etype}:{key}"
        ev.dedupe_key = None
        append_event(conn, ev, redactions=0, max_payload_bytes=64 * 1024, keyer=self.keyer, reducer=self.reducer,
                     goal_epoch=int(st["goal_epoch"]))
        _ = event_dedupe  # the module keys hook events; internal events use explicit keys

    # ------------------------------------------------------------------ event processing
    def _process(self, conn: sqlite3.Connection, r: sqlite3.Row) -> list[tuple[str, str, int]]:
        sid, etype = str(r["session_id"]), str(r["event_type"])
        payload: dict[str, Any] = {}
        if r["data"] is not None:
            try:
                obj = json.loads(bytes(r["data"]).decode("utf-8"))
                payload = obj if isinstance(obj, dict) else {}
            except ValueError:
                payload = {}
        attrs = json.loads(r["attrs_json"] or "{}")
        st = self._state(conn, sid, r["client_id"])
        assert st is not None
        bg: list[tuple[str, str, int]] = []
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else self._cwd_hint.get(sid)
        if cwd and cwd != st.get("cwd"):
            self._save(conn, st, cwd=cwd)
        seq = int(r["seq"])
        if st.get("cwd") and not st.get("baseline_id") and sid not in self._baseline_pending:
            self._baseline_pending.add(sid)
            bg.append(("baseline", sid, seq))
        if etype == "session_start":
            src = str(attrs.get("source") or payload.get("source") or "")
            if src == "resume" and int(st["goal_epoch"]) > 0:
                flags = dict(st["flags"])
                flags["resume_pending"] = True
                self._save(conn, st, flags=flags)
        elif etype == "user_prompt":
            text = payload.get("prompt")
            if isinstance(text, str) and text.strip():
                self._save(conn, st, hook_intents=int(st["hook_intents"]) + 1)
                self._apply_prompt(conn, st, text, "hook")
        elif etype == "transcript.user_message":
            text = str(payload.get("text") or "")
            if int(st["hook_intents"]) == 0 and text.strip() and not attrs.get("injected") \
                    and not text.lstrip().startswith("<"):
                self._apply_prompt(conn, st, text, "transcript")
        elif etype in ("post_tool", "post_tool_failure"):
            flags = dict(st["flags"])
            if not flags.get("hook_tools"):
                flags["hook_tools"] = True
                self._save(conn, st, flags=flags)
            if etype == "post_tool_failure":
                resp = payload.get("tool_response")
                payload = {**payload, "tool_response": {**(resp if isinstance(resp, dict) else {"output": resp}),
                                                        "is_error": True}}
            drafts = facts.from_hook("post_tool", payload)
            bg += self._store_facts(conn, st, drafts, seq)
        elif etype == "transcript.tool_call":
            name, call_id, tin = facts.from_transcript_call(payload)
            if call_id:
                self._calls[(sid, call_id)] = (name, tin)
                if len(self._calls) > 5000:
                    self._calls.pop(next(iter(self._calls)))
            if not st["flags"].get("hook_tools") and name.lower() in facts.EDIT_TOOLS:
                drafts = [facts.FactDraft("file_change", "host_reported", p, "n/a", {"tool": name[:60]},
                                          f"{call_id}:{i}" if call_id else None)
                          for i, p in enumerate(facts.edit_paths(name, tin))]
                bg += self._store_facts(conn, st, drafts, seq)
        elif etype == "tool_result":
            bg += self._transcript_result(conn, st, payload, attrs, seq, float(r["received_at"]))
        elif etype == "transcript.usage":
            facts.insert(conn, sid, int(st["goal_epoch"]),
                         facts.FactDraft("usage", "host_reported", None, "n/a", attrs,
                                         f"usage:{attrs.get('record_id') or seq}"), seq)
        elif etype in ("pre_compact", "post_compact"):
            flags = dict(st["flags"])
            flags["compacted_at_seq"] = seq
            self._save(conn, st, flags=flags)
        self._save(conn, st, processed_seq=seq)
        return bg

    def _apply_prompt(self, conn: sqlite3.Connection, st: dict[str, Any], text: str, source: str) -> None:
        ordinal = int(st["intent_count"]) + 1
        dec = goal_epochs.decide(text, first_in_session=int(st["goal_epoch"]) == 0,
                                 after_resume=bool(st["flags"].get("resume_pending")),
                                 phrase_rules=list(self.config.get("state.new_task_phrases") or []))
        flags = dict(st["flags"])
        flags.pop("resume_pending", None)
        self._save(conn, st, intent_count=ordinal, flags=flags)
        if dec.action == "confirm_new":
            self._confirm_epoch(conn, st, ordinal, dec.reason)
        elif dec.action == "candidate":
            self._save(conn, st, candidate_ordinal=ordinal)
        it = intent_log.append_intent(conn, st["session_id"], ordinal, int(st["goal_epoch"]), text, source,
                                      self.keyer)
        self.stats["intents"] += 1
        extracted = contract_coverage.extract(it) if self.enabled("contracts") else []
        if extracted:
            contract_compiler.propose(conn, st["session_id"], int(st["goal_epoch"]), extracted,
                                      root=self._root(st), proposed_by="rules")

    def _confirm_epoch(self, conn: sqlite3.Connection, st: dict[str, Any], start_ordinal: int, reason: str,
                       carry: list[str] | None = None) -> int:
        new = int(st["goal_epoch"]) + 1
        carry_ids = {c.rpartition("#")[2] for c in (carry or [])}
        for c in contract_compiler.load(conn, st["session_id"]):
            if c.goal_epoch < new:
                ordinals = [int(i[1:]) for i in c.source_intent_ids if i[1:].isdigit()]
                if c.id in carry_ids or (ordinals and min(ordinals) >= start_ordinal):
                    c.version += 1
                    c.goal_epoch = new
                    c.status = "unknown"
                    contract_compiler.insert(conn, c)
                else:
                    contract_compiler.supersede(conn, c.row_id, f"epoch:{new}")
        self._save(conn, st, goal_epoch=new, epoch_start_ordinal=start_ordinal, candidate_ordinal=None,
                   epoch_confirmed_at=time.time(), epoch_reason=reason[:200], stop_blocks=0)
        self.cancel.bump(st["session_id"], new)
        self.stats["epochs"] += 1
        self._internal_event(conn, st, "internal.epoch", {"epoch": new, "reason": reason[:200],
                                                          "start_ordinal": start_ordinal},
                             f"{st['session_id']}:epoch:{new}")
        return new

    def _store_facts(self, conn: sqlite3.Connection, st: dict[str, Any], drafts: list[facts.FactDraft],
                     seq: int) -> list[tuple[str, str, int]]:
        bg: list[tuple[str, str, int]] = []
        sid, epoch = st["session_id"], int(st["goal_epoch"])
        for d in drafts:
            self._parser_health(d)
            fid = facts.insert(conn, sid, epoch, d, seq)
            if fid is None:
                continue
            self.stats["facts"] += 1
            if d.kind == "file_change":
                bg.append(("integrity", sid, seq))
            self._feed_loop(conn, st, {"id": fid, "kind": d.kind, "subject": d.subject, "status": d.status,
                                       "data": d.data, "source_seq": seq}, seq)
        return bg

    def _parser_health(self, d: facts.FactDraft) -> None:
        cmd = str(d.data.get("command") or "")
        if d.kind == "command_run":
            runner = _runner_for(cmd)
            if runner and not d.data.get("unavailable"):
                self.breakers.parser(runner).record_failure("recognized command, unrecognized output")
        elif d.kind == "test_run":
            runner = str(d.data.get("runner") or "")
            if "contradicts" in str(d.data.get("note") or ""):
                self.breakers.parser(runner).record_failure("parsed pass contradicted by exit status")
            else:
                self.breakers.parser(runner).record_success()
            if self.breakers.parser_open(runner) and d.status == "pass":
                d.status = "unknown"
                d.data["note"] = f"parser breaker open for {runner}"

    def _feed_loop(self, conn: sqlite3.Connection, st: dict[str, Any], fact: dict[str, Any], seq: int) -> None:
        if not self.enabled("loop_alerts"):
            return
        sid = st["session_id"]
        det = self._detectors.get(sid)
        if det is None:
            det = self._detectors[sid] = LoopDetector()
            for f in facts.load(conn, sid, ("test_run", "command_run", "file_change")):
                if f["id"] != fact["id"]:
                    det.feed(f)
        for a in det.feed(fact):
            facts.insert(conn, sid, int(st["goal_epoch"]),
                         facts.FactDraft("loop_alert", "arbiter_observed", a.signal, "n/a", a.to_dict(),
                                         f"loop:{a.signal}:{a.key}:{seq}"), seq)

    def _transcript_result(self, conn: sqlite3.Connection, st: dict[str, Any], payload: dict[str, Any],
                           attrs: dict[str, Any], seq: int, now: float) -> list[tuple[str, str, int]]:
        sid = st["session_id"]
        tid = attrs.get("tool_use_id") or payload.get("call_id")
        command = payload.get("command")
        if not command and tid:
            name, tin = self._calls.get((sid, str(tid)), ("", None))
            if name.lower() in facts.SHELL_TOOLS:
                command = facts._shell_command(tin)
        if not command and tid:
            # Claude Code results carry no command; the hook fact for the same tool id has it.
            hook_row = facts.find_joinable(conn, sid, str(tid), None, now)
            if hook_row is not None:
                command = json.loads(hook_row["data_json"] or "{}").get("command")
        if not command:
            return []
        output = "\n".join(str(payload.get(k) or "") for k in ("stdout", "stderr", "output") if payload.get(k))
        exit_code = payload.get("exit_code") if isinstance(payload.get("exit_code"), int) else None
        is_err = payload.get("is_error") if isinstance(payload.get("is_error"), bool) else None
        drafts = facts.shell_result(str(command), output, exit_code=exit_code, is_error=is_err,
                                    interrupted=bool(payload.get("interrupted")), origin="host_reported",
                                    tool_use_id=str(tid) if tid else None)
        main = next((d for d in drafts if d.kind in ("test_run", "command_run")), None)
        if main is None:
            return []
        runner = str(main.data.get("runner") or "")
        if main.status == "pass" and self.breakers.parser_open(runner):
            main.status = "unknown"
            main.data["note"] = f"parser breaker open for {runner}"
        row = facts.find_joinable(conn, sid, main.tool_use_id, main.subject, now)
        if row is not None:
            facts.apply_join(conn, row, main, now)
            return []
        if st["flags"].get("hook_tools"):
            # Hooks are active but this result has no hook counterpart (e.g. a subagent): keep it,
            # without a file_change side fact whose late seq could make earlier runs look stale.
            drafts = [main]
        return self._store_facts(conn, st, drafts, seq)

    # ------------------------------------------------------------------ background work
    def _schedule(self, kind: str, sid: str, seq: int) -> None:
        if not self._background:
            return
        if kind == "baseline":
            self.bg.submit(lambda: self._capture_baseline(sid), Priority.BACKGROUND, key=f"baseline:{sid}")
        elif kind == "integrity":
            self.bg.submit(lambda: self._refresh_integrity(sid), Priority.TELEMETRY, key=f"integrity:{sid}")

    def _read(self) -> sqlite3.Connection:
        return connect(self.db, readonly=True)

    def _root(self, st: dict[str, Any]) -> str | None:
        repo = json.loads(st.get("repo_json") or "{}")
        if repo.get("root"):
            return str(repo["root"])
        return st.get("cwd")

    def _capture_baseline(self, sid: str) -> None:
        rc = self._read()
        try:
            st = self._state(rc, sid, create=False)
        finally:
            rc.close()
        if not st or not st.get("cwd") or st.get("baseline_id"):
            return
        b = baseline_mod.capture(sid, st["cwd"])

        def job(conn: sqlite3.Connection) -> None:
            if conn.execute("SELECT baseline_id FROM session_state WHERE session_id = ?", (sid,)).fetchone()[0]:
                return
            baseline_mod.store(conn, b)
            conn.execute("UPDATE session_state SET repo_json = ? WHERE session_id = ?", (json.dumps(b.repo), sid))

        self.writer.run(job, timeout=10)
        self._baseline_pending.discard(sid)

    def capture_baseline_now(self, sid: str, cwd: str | None = None, *, replace: bool = False) -> dict[str, Any]:
        rc = self._read()
        try:
            st = self._state(rc, sid, create=False) or {}
        finally:
            rc.close()
        where = cwd or st.get("cwd")
        if not where:
            raise ValueError("no working directory known for this session")
        b = baseline_mod.capture(sid, where)

        def job(conn: sqlite3.Connection) -> None:
            self._state(conn, sid)
            if not replace and conn.execute("SELECT baseline_id FROM session_state WHERE session_id = ?",
                                            (sid,)).fetchone()[0]:
                return
            baseline_mod.store(conn, b)
            conn.execute("UPDATE session_state SET repo_json = ?, cwd = COALESCE(cwd, ?) WHERE session_id = ?",
                         (json.dumps(b.repo), where, sid))

        self.writer.run(job, timeout=10)
        return {"baseline": b.id, "files": len(b.files), "head": b.head, "dirty": b.dirty, "truncated": b.truncated}

    def _integrity(self, rc: sqlite3.Connection, st: dict[str, Any], budget_s: float | None = None
                   ) -> test_integrity.IntegrityReport:
        sid = st["session_id"]
        if not self.enabled("test_integrity"):
            return test_integrity.IntegrityReport("unknown", reason="test-integrity checks are disabled")
        b = baseline_mod.load(rc, sid)
        if b is None:
            return test_integrity.IntegrityReport("unknown", reason="no session baseline")
        fs = facts.load(rc, sid, ("file_change", "test_run"))
        change_seq = max([f["source_seq"] for f in fs if f["kind"] == "file_change"] or [0])
        runs = [f for f in fs if f["kind"] == "test_run"]
        cached = self._integrity_cache.get(sid)
        acks = set(st["flags"].get("integrity_acks") or [])
        if cached and cached[0] >= change_seq and cached[1].get("acks") == sorted(acks) \
                and cached[1].get("runs") == len(runs):
            rep = cached[1]["report"]
            return test_integrity.IntegrityReport(rep["status"], [test_integrity.Finding(**f) for f in rep["findings"]],
                                                  rep.get("reason", ""), rep.get("files_checked", 0))
        repo = b.repo or {}
        ident = RepoIdentity(root=repo.get("root") or st.get("cwd") or "", in_repo=bool(repo.get("in_repo")),
                             case_insensitive=bool(repo.get("case_insensitive")))
        if not ident.root:
            return test_integrity.IntegrityReport("unknown", reason="repository root unknown")
        rep_obj = test_integrity.check(b, ident, runs, acks, max_s=budget_s)
        if rep_obj.status == "unknown":
            return rep_obj   # timed-out scans aren't cached
        self._integrity_cache[sid] = (change_seq, {"report": rep_obj.to_dict(), "acks": sorted(acks),
                                                   "runs": len(runs)})
        return rep_obj

    def _refresh_integrity(self, sid: str) -> None:
        rc = self._read()
        try:
            st = self._state(rc, sid, create=False)
            if st:
                self._integrity(rc, st)
        finally:
            rc.close()

    # ------------------------------------------------------------------ evaluation
    def _decisions(self, rc: sqlite3.Connection, sid: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for cid, dec in rc.execute("SELECT contract_id, decision FROM contract_decision WHERE contract_id LIKE ? "
                                   "ORDER BY id", (f"{sid}#%",)):
            out[str(cid).rpartition("#")[2]] = str(dec)
        return out

    def evaluate(self, sid: str, *, integrity_budget_s: float | None = None) -> tuple[dict[str, Any], Any]:
        """(session state, Ledger) for the session's active epoch. Read-only."""
        rc = self._read()
        try:
            st = self._state(rc, sid, create=False)
            if st is None:
                raise KeyError(sid)
            epoch = int(st["goal_epoch"])
            contracts = [c for c in contract_compiler.load(rc, sid) if c.goal_epoch == epoch]
            decisions = self._decisions(rc, sid)
            contracts = [c for c in contracts if decisions.get(c.id) != "reject"]
            fs = facts.load(rc, sid)
            b = baseline_mod.load(rc, sid)
            ctx = EvalContext(facts=[f for f in fs if f["kind"] in ("test_run", "command_run", "file_change")],
                              root=self._root(st), baseline_head=b.head if b else None,
                              baseline_dirty=b.dirty if b else None, decisions=decisions)
            evals: list[tuple[Contract, ContractEval]] = [(c, evaluate(c, ctx)) for c in contracts]
            intents = intent_log.load_intents(rc, sid, int(st["epoch_start_ordinal"] or 0))
            uncovered = contract_coverage.uncovered(intents, contracts)
            integrity = self._integrity(rc, st, integrity_budget_s)
            alerts = [f["data"] for f in fs if f["kind"] == "loop_alert" and f["goal_epoch"] == epoch]
            flag_uncovered = bool(self.config.get("completion.flag_uncovered_intent", True))
            ledger = evidence_ledger.build(sid, epoch, evals, uncovered, integrity,
                                           [f for f in fs if f["goal_epoch"] == epoch or f["kind"] != "loop_alert"],
                                           alerts, flag_uncovered=flag_uncovered)
            return st, ledger
        finally:
            rc.close()

    def record_ledger(self, sid: str, ledger: Any, *, trigger: str, claim: str, verdict: str, mode: str,
                      blocked: bool, note: str = "") -> None:
        text = ledger.text() if ledger is not None else (note or "gate unavailable")
        missing = json.dumps(ledger.missing if ledger is not None else [])
        contracts_json = json.dumps([{"id": c.id, "status": ev.status, "grade": ev.grade}
                                     for c, ev in (ledger.contracts if ledger is not None else [])])
        epoch = ledger.goal_epoch if ledger is not None else 0
        updates = [(c, ev) for c, ev in (ledger.contracts if ledger is not None else [])]

        def job(conn: sqlite3.Connection) -> None:
            conn.execute("INSERT INTO finish_ledger(session_id, goal_epoch, created_at, trigger, claim, verdict, mode, "
                         "blocked, missing_json, contracts_json, ledger_text) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (sid, epoch, time.time(), trigger, claim, verdict, mode, int(blocked), missing,
                          contracts_json, text))
            for c, ev in updates:
                if ev.status != c.status or ev.evidence:
                    contract_compiler.set_status(conn, c, ev.status, ev.evidence)
            if blocked:
                conn.execute("UPDATE session_state SET stop_blocks = stop_blocks + 1 WHERE session_id = ?", (sid,))

        self.writer.run(job, timeout=5)

    # ------------------------------------------------------------------ gate (M4)
    def after_hook(self, client: str, payload: Any, event_hint: str | None, deadline: Deadline,
                   can_block: bool = True) -> dict[str, Any]:
        """Decision for a just-logged hook: the gate on Stop, optional status on prompt/start."""
        if not isinstance(payload, dict):
            return {}
        name = payload.get("hook_event_name") or event_hint
        etype = hook_event_type(str(name) if name else None)
        native = payload.get("session_id")
        if not native or etype not in ("stop", "user_prompt", "session_start"):
            return {}
        sid = f"{client}:{native}"
        if etype == "stop":
            return self.on_stop(sid, payload, deadline, can_block=can_block)
        return self.on_prompt_hook(sid, str(name), deadline)

    def on_stop(self, sid: str, payload: dict[str, Any], deadline: Deadline,
                can_block: bool = True) -> dict[str, Any]:
        """Stop-hook gate. Returns the client response ({} unless block mode blocks)."""
        if not self.enabled("completion_gate"):
            return {}
        mode = str(self.config.get("completion.gate_mode", "annotate"))
        max_blocks = int(self.config.get("completion.max_stop_blocks_per_epoch", 3))
        t0 = time.monotonic()
        try:
            self.catch_up(deadline.sub(0.4))
            rc = self._read()
            try:
                st = self._state(rc, sid, create=False)
            finally:
                rc.close()
            if st is None:
                return {}
            mode = str(st["flags"].get("gate_mode") or mode)
            if not can_block:
                mode = "annotate"       # this client can't hold back a stop (e.g. Cursor): record only
            fc = st["flags"].get("finish_check_ordinal")
            message = payload.get("last_assistant_message") or self._last_agent_response(sid)
            claim = claim_detection.classify(message,
                                             finish_check_called=fc is not None and fc == st["intent_count"])
            if not claim.gated and not self.config.get("completion.record_non_claims", False):
                self._note_verdict(sid, claim.claim, "not_gated")
                return {}
            breaker = self.breakers.gate_open()
            ledger = None
            if breaker is None and claim.gated:
                _, ledger = self.evaluate(sid, integrity_budget_s=max(0.05, deadline.remaining() * 0.3))
            out = gate.decide(mode=mode, claim=claim, ledger=ledger, stop_blocks_used=int(st["stop_blocks"]),
                              max_blocks=max_blocks, unavailable_reason=breaker)
            self.record_ledger(sid, ledger, trigger="stop_hook", claim=claim.claim, verdict=out.verdict, mode=mode,
                               blocked=out.blocked, note=out.reason)
            self._note_verdict(sid, claim.claim, out.verdict)
            self.breakers.gate_errors.record_success()
            return out.response
        except Exception as exc:
            self.breakers.gate_errors.record_failure(f"{type(exc).__name__}: {exc}")
            log.exception("gate failed (fail open, session unverified)")
            return {}
        finally:
            self.breakers.hook_latency.record_latency((time.monotonic() - t0) * 1000.0)

    def _last_agent_response(self, sid: str) -> str | None:
        """Final assistant text for clients whose stop payload lacks it (Cursor sends it on a separate
        afterAgentResponse event, normalized to ``agent_response``)."""
        rc = self._read()
        try:
            row = rc.execute("SELECT b.data FROM event_log e JOIN blob b ON b.hash = e.payload_pointer "
                             "WHERE e.session_id = ? AND e.event_type = 'agent_response' ORDER BY e.seq DESC LIMIT 1",
                             (sid,)).fetchone()
        finally:
            rc.close()
        if not row:
            return None
        try:
            data = json.loads(bytes(row[0]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        text = data.get("last_assistant_message") or data.get("text") if isinstance(data, dict) else None
        return str(text) if text else None

    def _note_verdict(self, sid: str, claim: str, verdict: str) -> None:
        def job(conn: sqlite3.Connection) -> None:
            st = self._state(conn, sid, create=False)
            if st is None:
                return
            flags = dict(st["flags"])
            flags["last_verdict"] = verdict
            flags["last_claim"] = claim
            self._save(conn, st, flags=flags)

        try:
            self.writer.run(job, timeout=2)
        except Exception:
            pass

    def on_prompt_hook(self, sid: str, event_name: str, deadline: Deadline) -> dict[str, Any]:
        """Optional status injection (``ui.inject_status``; default off)."""
        if not self.config.get("ui.inject_status", False) or not self.enabled("status_injection"):
            return {}
        self.catch_up(deadline.sub(0.5))
        try:
            state = self.session_status(sid, light=True)
        except KeyError:
            return {}
        text, h = ui_status.injection(state, last_hash=state.get("last_injected_hash"),
                                      max_tokens=int(self.config.get("hooks.max_injected_tokens", 300)),
                                      only_on_change=bool(self.config.get("hooks.inject_only_on_change", True)))
        if text is None:
            return {}
        self.writer.run(lambda c: c.execute("UPDATE session_state SET last_injected_hash = ? WHERE session_id = ?",
                                            (h, sid)), timeout=2)
        return ui_status.hook_context(event_name, text)

    # ------------------------------------------------------------------ tool API (MCP + CLI)
    def resolve_session(self, params: dict[str, Any]) -> str | None:
        client = params.get("client")
        explicit = params.get("session_id")
        rc = self._read()
        try:
            if explicit:
                s = str(explicit)
                cands = [s] if ":" in s else ([f"{client}:{s}"] if client else [])
                for c in cands + [s]:
                    if rc.execute("SELECT 1 FROM client_session WHERE id = ?", (c,)).fetchone():
                        return c
                row = rc.execute("SELECT id FROM client_session WHERE native_session_id = ? ORDER BY last_event_at "
                                 "DESC LIMIT 1", (s,)).fetchone()
                if row:
                    return str(row[0])
            hint = params.get("session_hint")
            if hint and rc.execute("SELECT 1 FROM client_session WHERE id = ?", (hint,)).fetchone():
                return str(hint)
            cwd = params.get("cwd")
            q = "SELECT id FROM client_session WHERE ended_at IS NULL"
            args: list[Any] = []
            if client and client != "generic":
                q += " AND client_id = ?"
                args.append(client)
            if cwd:
                variants = {cwd, cwd.rstrip("\\/"), cwd[:1].upper() + cwd[1:], cwd[:1].lower() + cwd[1:]}
                hmacs = [self.keyer(v.encode("utf-8")) for v in variants]
                row = rc.execute(q + f" AND cwd_hmac IN ({','.join('?' * len(hmacs))}) ORDER BY last_event_at DESC "
                                 "LIMIT 1", (*args, *hmacs)).fetchone()
                if row:
                    return str(row[0])
            row = rc.execute(q + " AND last_event_at > ? ORDER BY last_event_at DESC LIMIT 1",
                             (*args, time.time() - 7200)).fetchone()
            return str(row[0]) if row else None
        finally:
            rc.close()

    def propose(self, sid: str, obligations: Any, *, supersedes: list[str] | None = None, new_task: bool = False,
                proposed_by: str = "host_agent") -> dict[str, Any]:
        if not self.enabled("contracts"):
            return {"accepted": [], "rejected": [{"reason": "contracts are disabled in Arbiter's config"}]}
        self.catch_up(Deadline(2.0))

        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            st = self._state(conn, sid)
            assert st is not None
            confirmed = None
            if (supersedes or new_task) and st.get("candidate_ordinal"):
                confirmed = self._confirm_epoch(conn, st, int(st["candidate_ordinal"]),
                                                "contract proposal declared what it supersedes")
            res = contract_compiler.propose(conn, sid, int(st["goal_epoch"]), obligations, root=self._root(st),
                                            proposed_by=proposed_by, supersedes=supersedes)
            out = res.to_dict()
            out["goal_epoch"] = int(st["goal_epoch"])
            if confirmed:
                out["epoch_confirmed"] = confirmed
            self._internal_event(conn, st, "internal.contracts_proposed",
                                 {"by": proposed_by, "accepted": [a["id"] for a in res.accepted],
                                  "rejected": len(res.rejected), "superseded": res.superseded,
                                  "epoch": int(st["goal_epoch"])}, f"{sid}:propose:{time.time_ns()}")
            return out

        return dict(self.writer.run(job, timeout=10))

    def scope_change(self, sid: str, summary: str = "", carry: list[str] | None = None) -> dict[str, Any]:
        self.catch_up(Deadline(2.0))

        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            st = self._state(conn, sid)
            assert st is not None
            start = int(st.get("candidate_ordinal") or st["intent_count"] or 0)
            new = self._confirm_epoch(conn, st, start, f"arbiter_scope_change: {summary}"[:200], carry)
            self._internal_event(conn, st, "internal.scope_change", {"epoch": new, "carry": carry or []},
                                 f"{sid}:scope:{time.time_ns()}")
            return {"goal_epoch": new, "starts_at_intent": f"U{start}", "carried": carry or []}

        return dict(self.writer.run(job, timeout=10))

    def decide_contract(self, sid: str, contract_id: str, decision: str, note: str = "") -> dict[str, Any]:
        if decision not in ("waive", "confirm", "reject"):
            raise ValueError("decision must be waive, confirm or reject")
        short = contract_id.rpartition("#")[2].upper()

        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute("SELECT verification_recipe_json FROM contract WHERE id = ?",
                               (f"{sid}#{short}",)).fetchone()
            if row is None:
                raise KeyError(f"no contract {short} in session {sid}")
            if decision == "confirm" and json.loads(row[0]).get("type") != "manual":
                raise ValueError("only manual contracts can be confirmed; evidence decides the others")
            conn.execute("INSERT INTO contract_decision(contract_id, decision, by_whom, note, created_at) "
                         "VALUES (?,?,?,?,?)", (f"{sid}#{short}", decision, "user", note[:300], time.time()))
            st = self._state(conn, sid)
            assert st is not None
            self._internal_event(conn, st, "internal.contract_decision", {"contract": short, "decision": decision},
                                 f"{sid}:decide:{time.time_ns()}")
            return {"contract": short, "decision": decision}

        return dict(self.writer.run(job, timeout=5))

    def add_contract(self, sid: str, text: str, recipe: Any, quote: str | None = None) -> dict[str, Any]:
        """CLI path (user-created): the user is the source, so a quote is optional."""
        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            st = self._state(conn, sid)
            assert st is not None
            q = quote
            source = None
            if not q:
                ordinal = int(st["intent_count"]) + 1
                intent_log.append_intent(conn, sid, ordinal, int(st["goal_epoch"]), text, "cli", self.keyer)
                self._save(conn, st, intent_count=ordinal)
                q, source = text, f"U{ordinal}"
            res = contract_compiler.propose(conn, sid, int(st["goal_epoch"]), [{"text": text, "quotes": [q],
                                                                                "recipe": recipe}],
                                            root=self._root(st), proposed_by="user", user_source=source)
            return res.to_dict()

        return dict(self.writer.run(job, timeout=10))

    def ack_integrity(self, sid: str, keys: list[str]) -> dict[str, Any]:
        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            st = self._state(conn, sid, create=False)
            if st is None:
                raise KeyError(sid)
            flags = dict(st["flags"])
            acks = sorted(set(flags.get("integrity_acks") or []) | set(keys))
            flags["integrity_acks"] = acks
            self._save(conn, st, flags=flags)
            return {"acknowledged": acks}

        return dict(self.writer.run(job, timeout=5))

    def finish_check(self, sid: str, summary: str = "", claims: list[str] | None = None) -> dict[str, Any]:
        self.catch_up(Deadline(3.0))
        seq_row: Any = None

        def mark(conn: sqlite3.Connection) -> None:
            nonlocal seq_row
            st = self._state(conn, sid)
            assert st is not None
            flags = dict(st["flags"])
            flags["finish_check_ordinal"] = int(st["intent_count"])
            self._save(conn, st, flags=flags)
            seq_row = conn.execute("SELECT MAX(seq) FROM event_log").fetchone()[0]
            for i, c in enumerate((claims or [])[:10]):
                facts.insert(conn, sid, int(st["goal_epoch"]),
                             facts.FactDraft("assertion", "agent_asserted", str(c)[:200], "n/a",
                                             {"summary": summary[:500]}, f"assert:{seq_row}:{i}"), seq_row)

        self.writer.run(mark, timeout=5)
        _, ledger = self.evaluate(sid)
        verdict = ledger.verdict
        self.record_ledger(sid, ledger, trigger="finish_check", claim="claim", verdict=verdict,
                           mode=str(self.config.get("completion.gate_mode", "annotate")), blocked=False)
        self._note_verdict(sid, "claim", verdict)
        out = ledger.to_dict()
        out["ledger_text"] = ledger.text()
        if ledger.missing:
            out["note"] = ("[Arbiter] Status for the current turn only. Missing evidence is listed above; "
                           "agent statements alone can't mark a contract PASS.")
        return out

    def verify_plan(self, sid: str | None, cwd: str | None, config_dir: Path) -> dict[str, Any]:
        from arbiter_agent.completion import verify_runner as vr

        root = None
        if sid:
            rc = self._read()
            try:
                st = self._state(rc, sid, create=False)
            finally:
                rc.close()
            root = self._root(st) if st else None
        root = root or (identify(cwd).root if cwd else None)
        if not root:
            return {"ok": False, "error": "no repository root known"}
        path = vr.config_path(root, config_dir, str(self.config.get("completion.verify_config_file",
                                                                       ".arbiter/verify.yaml")))
        if path is None:
            return {"ok": False, "root": root, "error": "no .arbiter/verify.yaml (see `arbiter verify --init`)"}
        sha = vr.file_sha(path)
        rc = self._read()
        try:
            row = rc.execute("SELECT sha256 FROM verify_trust WHERE path = ?", (str(path),)).fetchone()
        finally:
            rc.close()
        if not row or row[0] != sha:
            return {"ok": False, "root": root, "path": str(path), "sha256": sha, "untrusted": True,
                    "error": "verify config isn't trusted yet (run `arbiter verify --trust` in a terminal)"}
        try:
            cmds = vr.load(path)
        except vr.VerifyConfigError as exc:
            return {"ok": False, "root": root, "path": str(path), "error": str(exc)}
        return {"ok": True, "root": root, "path": str(path), "sha256": sha,
                "commands": [c.__dict__ for c in cmds]}

    def set_gate_mode(self, sid: str, mode: str) -> dict[str, Any]:
        if mode not in ("annotate", "block", "default"):
            raise ValueError("mode must be annotate, block or default")

        def job(conn: sqlite3.Connection) -> None:
            st = self._state(conn, sid)
            assert st is not None
            flags = dict(st["flags"])
            if mode == "default":
                flags.pop("gate_mode", None)
            else:
                flags["gate_mode"] = mode
            self._save(conn, st, flags=flags)

        self.writer.run(job, timeout=5)
        return {"session": sid, "gate_mode": mode}

    def trust_verify(self, path: str, sha: str) -> dict[str, Any]:
        self.writer.run(lambda c: c.execute("INSERT OR REPLACE INTO verify_trust(path, sha256, trusted_at) "
                                            "VALUES (?,?,?)", (path, sha, time.time())), timeout=5)
        return {"trusted": path, "sha256": sha}

    def submit_verification(self, sid: str, results: list[dict[str, Any]], channel: str) -> dict[str, Any]:
        def job(conn: sqlite3.Connection) -> dict[str, Any]:
            st = self._state(conn, sid)
            assert st is not None
            seq = int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM event_log").fetchone()[0])
            ids = []
            for i, r in enumerate(results[:50]):
                data = dict(r.get("data") or {})
                data["channel"] = channel
                d = facts.FactDraft(str(r.get("kind") or "test_run"), "arbiter_observed", r.get("subject"),
                                    r.get("status"), data, f"verify:{time.time_ns()}:{i}")
                fid = facts.insert(conn, sid, int(st["goal_epoch"]), d, seq)
                ids.append(fid)
                if fid is not None:
                    self._feed_loop(conn, st, {"id": fid, "kind": d.kind, "subject": d.subject, "status": d.status,
                                               "data": d.data, "source_seq": seq}, seq)
            self._internal_event(conn, st, "internal.verify_results",
                                 {"channel": channel, "results": [{"subject": r.get("subject"),
                                                                   "status": r.get("status")} for r in results[:50]]},
                                 f"{sid}:verify:{time.time_ns()}")
            return {"facts": ids}

        return dict(self.writer.run(job, timeout=10))

    def session_status(self, sid: str, *, light: bool = False) -> dict[str, Any]:
        st, ledger = self.evaluate(sid, integrity_budget_s=0.5 if light else None)
        rc = self._read()
        try:
            fs = facts.load(rc, sid, ("test_run", "usage", "loop_alert"))
            last = rc.execute("SELECT verdict, trigger, created_at FROM finish_ledger WHERE session_id = ? "
                              "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        finally:
            rc.close()
        epoch = int(st["goal_epoch"])
        runs = [f for f in fs if f["kind"] == "test_run"]
        return {
            "session_id": sid, "goal_epoch": epoch, "epoch_reason": st.get("epoch_reason"),
            "candidate_pending": st.get("candidate_ordinal") is not None, "intents": int(st["intent_count"]),
            "counts": ledger.counts() if ledger.contracts else {}, "uncovered": len(ledger.uncovered),
            "tests": progress.test_progress([{**f, **{k: f["data"].get(k) for k in ("failed", "errors", "passed")}}
                                             for f in runs]),
            "integrity": ledger.integrity.summary() if ledger.integrity else None,
            "loop_alerts": len([f for f in fs if f["kind"] == "loop_alert" and f["goal_epoch"] == epoch]),
            "usage": cache_metrics.rollup([f["data"] for f in fs if f["kind"] == "usage"]),
            "last_verdict": (last[0] if last else st["flags"].get("last_verdict")),
            "last_injected_hash": st.get("last_injected_hash"), "stop_blocks": int(st["stop_blocks"]),
            "verdict_now": ledger.verdict,
            **({} if light else {"ledger": ledger.to_dict(), "ledger_text": ledger.text()}),
        }

    def sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rc = self._read()
        try:
            rows = rc.execute("SELECT s.session_id, s.client_id, s.goal_epoch, s.intent_count, s.updated_at, "
                              "(SELECT COUNT(*) FROM contract c WHERE c.thread_id = s.session_id AND c.superseded_by "
                              "IS NULL) FROM session_state s ORDER BY s.updated_at DESC LIMIT ?", (limit,)).fetchall()
        finally:
            rc.close()
        return [{"session_id": r[0], "client": r[1], "goal_epoch": r[2], "intents": r[3], "updated_at": r[4],
                 "contracts": r[5]} for r in rows]

    def summary(self) -> dict[str, Any]:
        return {"stats": dict(self.stats), "background": dict(self.bg.stats), "backlog": self.bg.backlog,
                "breakers": self.breakers.snapshot()}

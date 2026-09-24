"""The Arbiter daemon (spec §4.4.1): one per user; owns the writer, event log and reducer.

Startup order: lock -> logging -> keys -> migrate (backup first) -> writer -> replay reducer ->
rotate IPC token -> IPC endpoint -> HTTP hook endpoint -> watchers -> retention -> daemon.json.
Shutdown drains queued writes before exiting (spec §16.5).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any

from arbiter_agent import PROTOCOL_MAJOR, PROTOCOL_MINOR, __version__
from arbiter_agent.audit.replay import replay
from arbiter_agent.clients.ingest import Ingestor
from arbiter_agent.clients.watchers.transcript_tail import TranscriptTailer, WatcherManager
from arbiter_agent.concurrency import Deadline
from arbiter_agent.config import Config, load_config
from arbiter_agent.daemon import diagnostics, ipc, protocol
from arbiter_agent.daemon.auth import ensure_token, rotate_token
from arbiter_agent.daemon.http_hooks import HookHTTPServer
from arbiter_agent.daemon.single_instance import InstanceLock
from arbiter_agent.flags import FeatureFlags
from arbiter_agent.paths import ArbiterPaths, get_paths, write_private
from arbiter_agent.privacy.redaction import Redactor, load_install_key
from arbiter_agent.privacy.retention import policy_from_config, run_retention
from arbiter_agent.privacy.scope import ProjectScope
from arbiter_agent.state.store import connect, migrate
from arbiter_agent.state.writer import Writer

MAX_CONNECTIONS = 64
ENGINE_METHODS = {"engine_drain", "sessions", "session_status", "contract_propose", "contract_add", "contract_decide",
                  "scope_change", "finish_check", "integrity_ack", "baseline", "verify_plan", "verify_trust",
                  "verify_submit", "gate_mode"}
RETENTION_INTERVAL_S = 6 * 3600

log = logging.getLogger("arbiter.daemon")


def _pick_port(preferred: int | None) -> int:
    if preferred:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Daemon:
    def __init__(self, paths: ArbiterPaths | None = None, config: Config | None = None) -> None:
        self.paths = (paths or get_paths()).ensure()
        self.config = config or load_config(self.paths)
        self.flags = FeatureFlags(self.config.get("features") or {})
        self.started_at = time.time()
        self.lock = InstanceLock(self.paths.lock_file)
        self._stop = threading.Event()
        self._conn_sem = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.endpoint: ipc.Endpoint | None = None
        self.http: HookHTTPServer | None = None
        self.watchers = WatcherManager()
        self.degraded = False
        self.migration_error: str | None = None
        self.connections = 0
        self.auth_failures = 0
        self.passthrough_hellos = 0

    # ------------------------------------------------------------------ startup
    def start(self) -> Daemon:
        self.lock.acquire()
        self.key = load_install_key(self.paths.install_key_file)
        self.redactor = Redactor(self.key, enabled=self.flags.enabled("redaction"))
        diagnostics.setup_logging(self.paths, redactor=self.redactor,
                                  rotation_mb=float(self.config.get("diagnostics.log_rotation_mb", 20)))
        res = migrate(self.paths.db, self.paths.backups)
        self.degraded, self.migration_error = res.degraded, res.error
        if res.degraded:
            log.error("migration failed; store is read-only", extra={"fields": {"error": res.error}})
        self.writer = Writer(self.paths.db, degraded=self.degraded).start()
        rconn = connect(self.paths.db, readonly=True)
        try:
            self.reducer = replay(rconn)
        finally:
            rconn.close()
        self.scope = ProjectScope(self.paths.scope_file, mode=self.config.get("privacy.project_scope"),
                                  config_excludes=self.config.get("privacy.project_exclude") or [],
                                  respect_arbiterignore=bool(self.config.get("privacy.respect_arbiterignore", True)))
        self.ingestor = Ingestor(key=self.key, writer=self.writer, reducer=self.reducer, scope=self.scope,
                                 max_payload_bytes=int(self.config.get("storage.max_payload_kb", 512)) * 1024,
                                 redaction_enabled=self.flags.enabled("redaction"))
        from arbiter_agent.daemon.clients_runtime import ClientRuntime

        self.clients = ClientRuntime(self)
        self.ingestor.observers.append(self.clients.on_event)
        from arbiter_agent.completion.breakers import Breakers
        from arbiter_agent.daemon.session_engine import SessionEngine

        self.breakers = Breakers(float(self.config.get("hooks.gating_p95_budget_ms", 300)),
                                 enabled=self.flags.enabled("circuit_breakers"))
        self.engine = SessionEngine(db=self.paths.db, writer=self.writer, keyer=self.ingestor.keyer,
                                    config=self.config, reducer=self.reducer, breakers=self.breakers,
                                    flags=self.flags)
        self.ingestor.observers.append(self.engine.wake)
        self.retrieval = None
        if self.flags.enabled("repo_index"):
            from arbiter_agent.retrieval.service import RetrievalService

            self.retrieval = RetrievalService(self.paths.data, self.key, self.config,
                                              redaction_enabled=self.flags.enabled("redaction"))
            self.engine.cwd_listeners.append(lambda sid, cwd: self.retrieval.warm(cwd) if self.retrieval else None)
        self.ipc_token = rotate_token(self.paths.token_file)  # shim tokens rotate on every start
        self.endpoint = ipc.listen(self.paths.pipe_address, prefer=str(self.config.get("daemon.ipc", "auto")))
        if self.flags.enabled("http_hooks"):
            self.hook_token = ensure_token(self.paths.hook_token_file)  # stable across restarts
            self.http = HookHTTPServer(self._http_port(), self.hook_token, self._on_http_hook).start()
        self.clients.start()
        if not self.degraded and self.flags.enabled("task_state"):
            self.engine.start()
        if self.retrieval is not None:
            self.retrieval.start()
        if self.flags.enabled("transcript_watchers"):
            self.watchers.start()
        if self.flags.enabled("retention") and not self.degraded:
            threading.Thread(target=self._retention_loop, name="arbiter-retention", daemon=True).start()
        self._write_record()
        # Accept only after daemon.json is published, so a client that can reach us can also
        # discover the HTTP hook port. Early connections queue on the listening endpoint.
        threading.Thread(target=self._accept_loop, name="arbiter-accept", daemon=True).start()
        log.info("daemon started", extra={"fields": {"version": __version__, "endpoint": self.endpoint.describe(),
                                                     "http_port": self.http.port if self.http else None,
                                                     "degraded": self.degraded}})
        return self

    def _http_port(self) -> int:
        f = self.paths.state / "http_port"
        preferred = None
        try:
            preferred = int(f.read_text().strip())
        except (OSError, ValueError):
            pass
        port = _pick_port(preferred)
        if port != preferred:
            write_private(f, str(port).encode())
        return port

    def _write_record(self) -> None:
        rec = {"pid": os.getpid(), "started_at": self.started_at, "version": __version__,
               "protocol": [PROTOCOL_MAJOR, PROTOCOL_MINOR], "instance_id": self.paths.instance_id,
               "endpoint": self.endpoint.describe() if self.endpoint else None,
               "http": {"host": "127.0.0.1", "port": self.http.port} if self.http else None,
               "degraded": self.degraded}
        write_private(self.paths.daemon_record, json.dumps(rec, indent=1).encode())

    # ------------------------------------------------------------------ IPC
    def _accept_loop(self) -> None:
        assert self.endpoint is not None
        while not self._stop.is_set():
            try:
                conn = self.endpoint.accept()
            except (OSError, EOFError):
                if self._stop.is_set():
                    break
                time.sleep(0.05)
                continue
            if self._stop.is_set():
                conn.close()
                break
            if not self._conn_sem.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn: Any) -> None:
        try:
            try:
                ipc.server_handshake(conn, self.ipc_token)
            except ipc.IPCAuthError:
                self.auth_failures += 1
                return
            self.connections += 1
            hello_done = False
            while not self._stop.is_set():
                try:
                    raw = conn.recv_bytes(protocol.MAX_FRAME)
                except (EOFError, OSError):
                    break
                try:
                    msg = protocol.decode(raw)
                except protocol.ProtocolError as exc:
                    conn.send_bytes(protocol.encode({"id": None, "ok": False, "error": str(exc)}))
                    continue
                mid, method = msg.get("id"), msg.get("method")
                if not hello_done:
                    if method != "hello":
                        conn.send_bytes(protocol.encode({"id": mid, "ok": False, "error": "hello required"}))
                        break
                    params = msg.get("params") or {}
                    result = protocol.negotiate(params.get("protocol"))
                    conn.send_bytes(protocol.encode({"id": mid, "ok": True, "result": result}))
                    if result["action"] == "drain_restart":
                        log.info("newer shim requested drain-and-restart")
                        threading.Thread(target=self.shutdown, daemon=True).start()
                        break
                    if not result["compatible"]:
                        self.passthrough_hellos += 1
                        break
                    hello_done = True
                    continue
                try:
                    result = self.dispatch(str(method), msg.get("params") or {})
                    reply = {"id": mid, "ok": True, "result": result}
                except Exception as exc:
                    log.exception("request failed", extra={"fields": {"method": method}})
                    reply = {"id": mid, "ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
                try:
                    conn.send_bytes(protocol.encode(reply))
                except (OSError, EOFError):
                    break
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._conn_sem.release()

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping":
            return {"pong": True, "t": time.time()}
        if method == "status":
            return self.status()
        if method == "ingest_hook":
            return self.ingest_hook(str(params.get("client", "generic")), params.get("payload"),
                                    surface=str(params.get("surface", "hook")), event_hint=params.get("event"),
                                    channel=params.get("channel"))
        if method == "client_seen":
            return self.clients.client_seen(params)
        if method == "probe":
            return {"clients": self.clients.run_probe()}
        if method == "watch_add":
            return self.watch_add(params)
        if method == "watch_poll":
            if self.watchers.discovery is not None:
                self.watchers.discovery.scan(self.watchers, force=True)
            return {"records": self.watchers.poll_all()}
        if method == "replay_check":
            return self.replay_check()
        if method == "retention_run":
            return self.run_retention_now()
        if method == "scope_reload":
            return {"lists": self.scope.lists()}
        if method == "debug_refresh":
            diagnostics.refresh_level(self.paths)
            return {"debug": diagnostics.debug_active(self.paths)}
        if method == "shutdown":
            threading.Thread(target=self.shutdown, daemon=True).start()
            return {"stopping": True}
        if method in ENGINE_METHODS:
            return self.engine_call(method, params)
        if method == "retrieve":
            return self.retrieve(params)
        raise ValueError(f"unknown method '{method}'")

    # ------------------------------------------------------------------ retrieval (M6)
    def retrieve(self, params: dict[str, Any]) -> Any:
        if self.retrieval is None:
            raise RuntimeError("repository indexes are disabled (feature flag repo_index)")
        sid = self.engine.resolve_session(params)
        cwd = (self.engine.session_cwd(sid) if sid else None) or params.get("cwd")
        if not cwd:
            raise LookupError("no working directory: run this from the repository, or pass session_id")
        op = str(params.get("op") or "search")
        r = self.retrieval
        if op == "search":
            out = r.search(cwd, str(params.get("query") or ""), int(params.get("limit") or 20), params.get("path_glob"))
            n = len(out.get("hits", []))
        elif op == "symbol":
            out = r.symbol(cwd, str(params.get("name") or ""), int(params.get("limit") or 30))
            n = len(out.get("definitions", [])) + len(out.get("references", []))
        elif op == "related":
            out = r.related(cwd, str(params.get("path") or ""))
            n = len(out.get("imports", [])) + len(out.get("importers", [])) + len(out.get("tests", []))
        elif op == "status":
            return r.status(cwd)
        elif op == "gc":
            return r.gc(cwd, float(params.get("keep_days", 7)))
        else:
            raise ValueError(f"unknown retrieval op '{op}'")
        if sid:
            self.engine.record_retrieval(sid, {"op": op, "results": n, "index_version": out["index"]["version"],
                                               "index_generation": out["index"]["generation"],
                                               "index_head": out["index"]["head"] or ""})
        return out

    # ------------------------------------------------------------------ task state (M3/M4)
    def _session(self, params: dict[str, Any], required: bool = True) -> str | None:
        sid = self.engine.resolve_session(params)
        if sid is None and required:
            raise LookupError("no active session found; pass session_id, or run this from the session's directory")
        return sid

    def engine_call(self, method: str, params: dict[str, Any]) -> Any:
        e = self.engine
        if method == "engine_drain":
            e.drain(float(params.get("timeout", 10)))
            return {"drained": True, **e.summary()}
        if method == "sessions":
            return {"sessions": e.sessions(int(params.get("limit", 20)))}
        if method == "session_status":
            e.catch_up()
            return e.session_status(str(self._session(params)), light=bool(params.get("light")))
        if method == "contract_propose":
            return e.propose(str(self._session(params)), params.get("contracts"),
                             supersedes=list(params.get("supersedes") or []), new_task=bool(params.get("new_task")))
        if method == "contract_add":
            return e.add_contract(str(self._session(params)), str(params["text"]), params.get("recipe"),
                                  params.get("quote"))
        if method == "contract_decide":
            return e.decide_contract(str(self._session(params)), str(params["contract"]), str(params["decision"]),
                                     str(params.get("note") or ""))
        if method == "scope_change":
            return e.scope_change(str(self._session(params)), str(params.get("summary") or ""),
                                  list(params.get("carry") or []))
        if method == "finish_check":
            return e.finish_check(str(self._session(params)), str(params.get("summary") or ""),
                                  list(params.get("claims") or []))
        if method == "integrity_ack":
            return e.ack_integrity(str(self._session(params)), list(params.get("keys") or []))
        if method == "baseline":
            sid = str(self._session(params))
            return e.capture_baseline_now(sid, params.get("cwd"), replace=bool(params.get("replace")))
        if method == "verify_plan":
            return e.verify_plan(self._session(params, required=False), params.get("cwd"), self.paths.config)
        if method == "verify_trust":
            if params.get("channel") != "cli_tty":
                raise PermissionError("verify configs can only be trusted from an interactive `arbiter verify --trust`")
            return e.trust_verify(str(params["path"]), str(params["sha256"]))
        if method == "verify_submit":
            return e.submit_verification(str(self._session(params)), list(params.get("results") or []),
                                         str(params.get("channel") or "cli"))
        if method == "gate_mode":
            return e.set_gate_mode(str(self._session(params)), str(params.get("mode") or ""))
        raise ValueError(f"unknown method '{method}'")

    # ------------------------------------------------------------------ hooks
    def _gating_wait(self) -> float:
        return max(0.05, float(self.config.get("hooks.gating_deadline_ms", 1500)) / 1000.0 * 0.8)

    def ingest_hook(self, client: str, payload: Any, *, surface: str, event_hint: str | None = None,
                    channel: str | None = None) -> dict[str, Any]:
        if not self.flags.enabled("event_log"):
            return {"status": "disabled", "response": {}}
        deadline = Deadline(max(0.1, float(self.config.get("hooks.gating_deadline_ms", 1500)) / 1000.0 * 0.8))
        from arbiter_agent.clients.hook_dialects import DIALECTS, adapt_inbound, adapt_outbound

        native_event = (payload.get("hook_event_name") if isinstance(payload, dict) else None) or event_hint
        payload, event_hint = adapt_inbound(client, payload, event_hint)   # M5 dialects -> canonical shape
        res = self.ingestor.ingest_hook(client, payload, surface=surface, event_hint=event_hint, channel=channel,
                                        wait=self._gating_wait())
        response: dict[str, Any] = {}
        if res.status in ("stored", "duplicate", "pending") and not self.degraded:
            dialect = DIALECTS.get(client)
            response = self.engine.after_hook(client, payload, event_hint, deadline,
                                              can_block=dialect.can_block_stop if dialect else True)
            response = adapt_outbound(client, native_event, response)
        return {**res.to_dict(), "response": response}

    def _on_http_hook(self, client: str, event: str, body: dict[str, Any]) -> dict[str, Any]:
        out = self.ingest_hook(client, body, surface="http", event_hint=event, channel="http")
        return dict(out.get("response") or {})

    def watch_add(self, params: dict[str, Any]) -> dict[str, Any]:
        t = TranscriptTailer(client=str(params["client"]), path=Path(params["path"]),
                             parser=str(params.get("parser", "fake_v1")), ingestor=self.ingestor, writer=self.writer,
                             cwd_hint=params.get("cwd"))
        self.watchers.add(t)
        return {"source": t.source_id, "offset": t.offset}

    # ------------------------------------------------------------------ state
    def replay_check(self) -> dict[str, Any]:
        self.writer.run(lambda c: None)  # barrier: all queued writes applied
        rconn = connect(self.paths.db, readonly=True)
        try:
            rebuilt = replay(rconn).snapshot()
        finally:
            rconn.close()
        live = self.reducer.snapshot()
        return {"equal": rebuilt == live, "sessions": len(live)}

    def run_retention_now(self) -> dict[str, Any]:
        policy = policy_from_config(self.config)
        rep = self.writer.run(lambda c: run_retention(c, self.paths.db, policy), timeout=120)
        try:
            self.writer.run(lambda c: c.execute("PRAGMA incremental_vacuum"), timeout=120)
        except Exception:
            pass
        return rep.__dict__

    def _retention_loop(self) -> None:
        if self._stop.wait(30):
            return
        while not self._stop.is_set():
            try:
                self.run_retention_now()
            except Exception:
                log.exception("retention run failed")
            if self._stop.wait(RETENTION_INTERVAL_S):
                return

    def status(self) -> dict[str, Any]:
        rconn = connect(self.paths.db, readonly=True)
        try:
            events, internal = rconn.execute("SELECT COALESCE(SUM(surface != 'internal'), 0), "
                                             "COALESCE(SUM(surface = 'internal'), 0) FROM event_log").fetchone()
            sessions = rconn.execute("SELECT COUNT(*) FROM client_session").fetchone()[0]
            skips = {r[0]: r[1] for r in rconn.execute("SELECT reason, count FROM scope_skip")}
        finally:
            rconn.close()
        return {
            "version": __version__, "pid": os.getpid(), "uptime_s": round(time.time() - self.started_at, 1),
            "protocol": [PROTOCOL_MAJOR, PROTOCOL_MINOR],
            "endpoint": self.endpoint.describe() if self.endpoint else None,
            "http_port": self.http.port if self.http else None, "degraded": self.degraded,
            "migration_error": self.migration_error, "events": events, "internal_events": internal,
            "sessions": sessions,
            "scope_skips": skips, "ingest": dict(self.ingestor.stats), "writer_backlog": self.writer.backlog,
            "auth_failures": self.auth_failures, "debug": diagnostics.debug_active(self.paths),
            "watchers": self.watchers.describe(), "flags": {k: v["enabled"] for k, v in self.flags.snapshot().items()},
            "http_stats": self.http.stats if self.http else None,
            "clients": self.clients.summary(), "watching": self.clients.watch_clients,
            "engine": self.engine.summary(), "gate_mode": self.config.get("completion.gate_mode", "annotate"),
            "retrieval": self.retrieval.summary() if self.retrieval else None,
            "client_homes": {k: os.environ.get(k) for k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "ARBITER_CLIENT_HOME")},
        }

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        log.info("daemon draining")
        self.watchers.stop()
        self.engine.stop()
        if self.retrieval is not None:
            self.retrieval.stop()
        if self.http:
            self.http.stop()
        self._unblock_accept()
        if self.endpoint:
            self.endpoint.close()
        self.writer.stop()
        self.lock.release()
        log.info("daemon stopped")

    def _unblock_accept(self) -> None:
        """Wake a blocked accept() by connecting once (the handshake then fails harmlessly)."""
        try:
            conn = ipc.connect(self.endpoint.describe() if self.endpoint else None, self.paths.pipe_address,
                               b"x", timeout=0.3)
            conn.close()
        except Exception:
            pass

    def wait(self) -> None:
        while not self._stop.wait(0.5):
            pass


def run_forever(paths: ArbiterPaths | None = None) -> int:
    d = Daemon(paths).start()
    try:
        d.wait()
    except KeyboardInterrupt:
        d.shutdown()
    return 0

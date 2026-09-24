"""Client-facing runtime inside the daemon (M2): observations, transcript discovery, probe."""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from arbiter_agent.clients.client_env import ClientEnv, current_env
from arbiter_agent.clients.event_normalizer import NormalizedEvent
from arbiter_agent.clients.registry import load_registry
from arbiter_agent.clients.watchers.discovery import Discovery
from arbiter_agent.integration import capability_probe
from arbiter_agent.setup.manifest import Manifest
from arbiter_agent.state.store import connect

if TYPE_CHECKING:
    from arbiter_agent.clients.ingest import IngestResult
    from arbiter_agent.daemon.server import Daemon

log = logging.getLogger("arbiter.clients")
OBS_THROTTLE_S = 60.0
PROBE_INTERVAL_S = 600.0


@functools.lru_cache(maxsize=1)
def _profiles() -> tuple[Any, ...]:
    from arbiter_agent.clients.registry import load_registry

    return tuple(load_registry().profiles.values())


def client_from_info(name: str | None) -> str:
    """Map an MCP ``clientInfo.name`` to a profile id (profiles declare their names)."""
    n = (name or "").lower().strip()
    if not n:
        return "generic"
    for p in _profiles():
        if p.matches_client_name(n):
            return str(p.id)
    if "codex" in n:           # fallbacks for unknown variants of the two primary clients
        return "codex"
    if "claude-code" in n or "claude_code" in n or n == "claude code":
        return "claude_code"
    return "generic"


class ClientRuntime:
    def __init__(self, daemon: Daemon, env: ClientEnv | None = None) -> None:
        self.d = daemon
        self.env = env or current_env()
        self._last_obs: dict[tuple[str, str, str], float] = {}
        self._lock = threading.Lock()
        self.last_probe: dict[str, dict[str, Any]] = {}
        manifest_clients = Manifest.load(daemon.paths).installed_clients()
        extra = [c for c in os.environ.get("ARBITER_WATCH_CLIENTS", "").split(",") if c]
        self.watch_clients = sorted(set(manifest_clients) | set(extra))

    def start(self) -> None:
        if self.watch_clients and self.d.flags.enabled("transcript_watchers"):
            self.d.watchers.discovery = Discovery(self.env, self.watch_clients, self.d.ingestor, self.d.writer)
        threading.Thread(target=self._probe_loop, name="arbiter-probe", daemon=True).start()

    # -------------------------------------------------------------- observations
    def observe(self, client: str, kind: str, detail: str, info: dict[str, Any] | None = None,
                force: bool = False) -> None:
        key = (client, kind, detail)
        now = time.time()
        with self._lock:
            if not force and now - self._last_obs.get(key, 0) < OBS_THROTTLE_S:
                return
            self._last_obs[key] = now
        payload = json.dumps(info or {})
        try:
            self.d.writer.submit(lambda c: c.execute(
                "INSERT INTO client_observation(client_id, kind, detail, first_at, last_at, count, info_json) "
                "VALUES (?, ?, ?, ?, ?, 1, ?) ON CONFLICT(client_id, kind, detail) DO UPDATE SET "
                "last_at = excluded.last_at, count = count + 1, info_json = excluded.info_json",
                (client, kind, detail, now, now, payload)))
        except Exception:
            pass

    def on_event(self, ev: NormalizedEvent, res: IngestResult) -> None:
        if ev.surface in ("mcp", "http", "hook"):
            self.observe(ev.client_id, "hook", ev.surface, {"last_event": ev.event_type})
        elif ev.surface == "transcript":
            self.observe(ev.client_id, "transcript", "parsed")

    def client_seen(self, params: dict[str, Any]) -> dict[str, Any]:
        client = client_from_info(params.get("name"))
        self.observe(client, "mcp_session", str(params.get("name") or "unknown")[:80],
                     {"version": params.get("version"), "cwd_known": bool(params.get("cwd"))}, force=True)
        return {"client": client}

    # -------------------------------------------------------------- probe
    def _desktop_exec_capability(self, conn: Any) -> str:
        exec_calls = conn.execute(
            "SELECT json_extract(attrs_json, '$.call_id') FROM event_log WHERE client_id = 'codex' AND "
            "event_type = 'transcript.tool_call' AND json_extract(attrs_json, '$.tool_name') = 'exec' "
            "ORDER BY seq DESC LIMIT 200").fetchall()
        ids = [r[0] for r in exec_calls if r[0]]
        if not ids:
            return "unknown"
        marks = ",".join("?" * len(ids))
        hit = conn.execute(f"SELECT COUNT(*) FROM event_log WHERE client_id = 'codex' AND surface IN "
                           f"('mcp','hook') AND event_type IN ('pre_tool','post_tool') AND upstream_id IN ({marks})",
                           ids).fetchone()[0]
        hooks_active = conn.execute("SELECT COUNT(*) FROM event_log WHERE client_id = 'codex' AND surface IN "
                                    "('mcp','hook')").fetchone()[0]
        if hit:
            return "observed"
        return "not_observed" if hooks_active and len(ids) >= 3 else "unknown"

    def run_probe(self) -> list[dict[str, Any]]:
        reg = load_registry(self.d.paths.config / "profiles")
        manifest = Manifest.load(self.d.paths)
        installed = set(manifest.installed_clients())
        results = []
        conn = connect(self.d.paths.db, readonly=True)
        try:
            exec_cap = self._desktop_exec_capability(conn)
            if exec_cap != "unknown":
                self.observe("codex", "capability", "desktop_exec_tool_hooks", {"value": exec_cap}, force=True)
            for p in reg.installable():
                found, _ = p.detected(self.env)
                if not found and p.id not in installed:
                    continue
                prev = set((self.last_probe.get(p.id) or {}).get("verified", []))
                installed_at = min((r.installed_at for r in manifest.active(p.id)), default=None)
                cap = capability_probe.probe(p, self.env, conn, installed_at=installed_at, previous=prev)
                if p.id == "codex":
                    cap.capabilities["desktop_exec_tool_hooks"] = exec_cap
                def _store(c: Any, p: Any = p, cap: Any = cap) -> None:
                    capability_probe.store(c, p, cap, None)

                self.d.writer.submit(_store)
                self.last_probe[p.id] = cap.to_dict()
                results.append(cap.to_dict())
        finally:
            conn.close()
        return results

    def _probe_loop(self) -> None:
        if self.d._stop.wait(20):
            return
        while True:
            try:
                self.run_probe()
            except Exception:
                log.exception("probe failed")
            if self.d._stop.wait(PROBE_INTERVAL_S):
                return

    def summary(self) -> dict[str, Any]:
        return {cid: {"verified": v.get("verified"), "notes": v.get("notes"), "drift": v.get("drift")}
                for cid, v in self.last_probe.items()}

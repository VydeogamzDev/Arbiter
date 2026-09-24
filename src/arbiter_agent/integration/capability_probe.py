"""Per-client capability probe (spec §4.2): configured checks + observed evidence -> verified
tier set. A tier that was verified but now fails its conformance check is dropped and recorded
as drift; the other tiers are kept (decision 0008)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from arbiter_agent.clients.client_env import ClientEnv
from arbiter_agent.clients.profile_schema import Profile
from arbiter_agent.integration import conformance as conf
from arbiter_agent.integration.tiers import T1, T2, T3, fmt

OBS_WINDOW_S = 30 * 86400


@dataclass
class Capability:
    client: str
    configured: set[str] = field(default_factory=set)
    verified: set[str] = field(default_factory=set)
    checks: list[conf.Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"client": self.client, "configured": sorted(self.configured), "verified": sorted(self.verified),
                "verified_fmt": fmt(self.verified), "notes": self.notes, "drift": self.drift,
                "capabilities": self.capabilities,
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks]}


def observations(conn: sqlite3.Connection | None, client: str, since: float) -> dict[tuple[str, str], dict[str, Any]]:
    if conn is None:
        return {}
    rows = conn.execute("SELECT kind, detail, first_at, last_at, count, info_json FROM client_observation "
                        "WHERE client_id = ? AND last_at >= ?", (client, since)).fetchall()
    return {(r["kind"], r["detail"]): {"first_at": r["first_at"], "last_at": r["last_at"], "count": r["count"],
                                       "info": json.loads(r["info_json"] or "{}")} for r in rows}


def probe(profile: Profile, env: ClientEnv, conn: sqlite3.Connection | None, *, installed_at: float | None = None,
          previous: set[str] | None = None, now: float | None = None) -> Capability:
    now = now or time.time()
    since = max(installed_at or 0, now - OBS_WINDOW_S)
    cap = Capability(profile.id)
    obs = observations(conn, profile.id, since)

    mcp = conf.check_mcp(profile, env)
    cap.checks.append(mcp)
    if mcp.ok:
        cap.configured.add(T1)
        if any(k == "mcp_session" for k, _ in obs):
            cap.verified.add(T1)
        else:
            cap.notes.append("T1 configured; not yet observed (the client hasn't launched Arbiter's MCP server)")

    if profile.hooks:
        hk = conf.check_hooks(profile, env)
        cap.checks.append(hk)
        trusted_ok = True
        if profile.hooks.get("trust_required") and profile.id == "codex":
            tr = conf.codex_trust_state(env)
            cap.checks.append(tr)
            trusted_ok = tr.ok
            if not tr.ok:
                cap.notes.append(tr.detail)
        if hk.ok:
            cap.configured.add(T2)
            hook_obs = [v for (k, _), v in obs.items() if k == "hook"]
            if hook_obs:
                cap.verified.add(T2)
            elif trusted_ok:
                cap.notes.append("T2 configured; no hook events observed yet")

    if profile.transcripts:
        tc = conf.check_transcripts(profile, env)
        cap.checks.append(tc)
        if tc.ok:
            cap.configured.add(T3)
            cap.verified.add(T3)
            cap.capabilities["transcript_parser_version"] = tc.data.get("parser_version")

    # Carried over from M0: do tool hooks fire for Codex desktop code-mode `exec` calls?
    if profile.id == "codex":
        exec_hook = obs.get(("capability", "desktop_exec_tool_hooks"))
        cap.capabilities["desktop_exec_tool_hooks"] = (exec_hook or {}).get("info", {}).get("value", "unknown")

    if previous:
        for tier in sorted(previous - cap.verified):
            if tier not in cap.configured:
                cap.drift.append(f"{tier} dropped: conformance check failed")
    return cap


def store(conn: sqlite3.Connection, profile: Profile, cap: Capability, client_version: str | None) -> None:
    now = time.time()
    conn.execute(
        "INSERT INTO client(id, profile_id, profile_version, client_version, verified_tiers_json, capabilities_json, "
        "detected_at, last_probe_at, drift_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
        "profile_version = excluded.profile_version, client_version = COALESCE(excluded.client_version, "
        "client.client_version), verified_tiers_json = excluded.verified_tiers_json, "
        "capabilities_json = excluded.capabilities_json, last_probe_at = excluded.last_probe_at, "
        "drift_json = excluded.drift_json",
        (profile.id, profile.id, profile.profile_version, client_version, json.dumps(sorted(cap.verified)),
         json.dumps(cap.capabilities), now, now, json.dumps(cap.drift)))

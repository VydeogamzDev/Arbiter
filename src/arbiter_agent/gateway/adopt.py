"""Explicit adoption and release of existing MCP servers (spec §10.7, §4.5.2; M10.2).

``arbiter gateway adopt <server>`` moves one server from the client's config behind the gateway:

1. Look up the server's entry in the client's own config (Codex ``[mcp_servers.<name>]``, Claude
   Code ``mcpServers``, or the profile's container for other clients). Only stdio servers qualify.
2. Start it once with its exact launch spec and read ``tools/list``. Classify each tool: read-only
   only when annotated ``readOnlyHint`` and not ``destructiveHint``.
3. **Refuse** when the server has tools that may change things and the client can't mirror
   approvals (no MCP elicitation): those tools must keep the client's native per-tool approval, so the
   server stays registered directly (spec §10.7 rule 2).
4. Otherwise remove the entry through the setup machinery (diff, backup, manifest record
   ``gateway_adopt``, validation, restore on failure) and record the launch spec plus the confirmed
   read-only classification (schema hashes) in the registry.

``arbiter gateway release <server>`` puts the original entry back (verbatim text for TOML) and
drops it from the registry; ``arbiter uninstall`` does the same for every adopted server.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm
from arbiter_agent.clients import mcp_entry
from arbiter_agent.clients.client_env import ClientEnv
from arbiter_agent.clients.profile_schema import Profile
from arbiter_agent.gateway import toml_tables
from arbiter_agent.gateway.catalog import annotated_read_only, schema_hash
from arbiter_agent.gateway.registry import Adopted, Registry
from arbiter_agent.gateway.upstream import LaunchSpec, Upstream
from arbiter_agent.paths import ArbiterPaths
from arbiter_agent.setup.manifest import Manifest

Probe = Callable[[LaunchSpec], list[dict[str, Any]]]


@dataclass
class Location:
    path: Path
    fmt: str                         # toml_table | json_named_entry | jsonc_named_entry | yaml_named_entry
    container: list[str]
    live: bool = False


@dataclass
class AdoptPlan:
    client: str
    server: str
    location: Location | None
    entry: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    read_only: list[str] = field(default_factory=list)
    mutating: list[str] = field(default_factory=list)
    allowed: bool = False
    reason: str = ""


def location(profile: Profile, env: ClientEnv) -> Location | None:
    spec = profile.mcp
    path = profile.expand(spec.get("file"), env)
    if path is None:
        return None
    if profile.id == "codex":
        table = str(spec.get("table", "mcp_servers.arbiter"))
        return Location(path, "toml_table", table.split(".")[:-1])
    if profile.id == "claude_code":
        return Location(path, "json_named_entry", ["mcpServers"], live=True)
    fmt = str(spec.get("format"))
    if fmt in mcp_entry.ENTRY_FORMATS:
        return Location(path, fmt, mcp_entry.container_path(spec), live=bool(spec.get("live_file")))
    return None


def servers(loc: Location) -> dict[str, dict[str, Any]]:
    """MCP servers in the client's config, excluding Arbiter itself."""
    raw = cm.read_bytes(loc.path)
    if not raw:
        return {}
    if loc.fmt == "toml_table":
        data: Any = tomllib.loads(raw.decode("utf-8"))
        for k in loc.container:
            data = data.get(k, {}) if isinstance(data, dict) else {}
        found = dict(data) if isinstance(data, dict) else {}
    else:
        found = mcp_entry.entries(loc.fmt, raw, loc.path, {"container": loc.container})
    return {k: v for k, v in found.items() if isinstance(v, dict) and k != "arbiter" and not mcp_entry.is_ours(v)}


def probe_tools(spec: LaunchSpec) -> list[dict[str, Any]]:
    up = Upstream("probe", spec, timeout_s=20).start()
    try:
        return up.tools
    finally:
        up.close()


def plan_adopt(profile: Profile, env: ClientEnv, server: str, *, probe: Probe = probe_tools) -> AdoptPlan:
    loc = location(profile, env)
    plan = AdoptPlan(profile.id, server, loc)
    if loc is None:
        plan.reason = f"{profile.display_name}: no MCP config location known"
        return plan
    entry = servers(loc).get(server)
    if entry is None:
        plan.reason = f"no MCP server named {server!r} in {loc.path}"
        return plan
    plan.entry = entry
    try:
        spec = LaunchSpec.from_entry(entry)
    except ValueError as exc:
        plan.reason = str(exc)
        return plan
    try:
        plan.tools = probe(spec)
    except Exception as exc:
        plan.reason = f"couldn't start {server} to read its tools: {exc}"[:300]
        return plan
    plan.read_only = sorted(t["name"] for t in plan.tools if annotated_read_only(t))
    plan.mutating = sorted(t["name"] for t in plan.tools if not annotated_read_only(t))
    if plan.mutating and not profile.elicitation:
        plan.reason = (f"{len(plan.mutating)} tool(s) may change things ({', '.join(plan.mutating[:5])}"
                       f"{'...' if len(plan.mutating) > 5 else ''}) and {profile.display_name} can't confirm "
                       "approvals through the gateway; keeping the server registered directly preserves its "
                       "per-tool approvals")
        return plan
    plan.allowed = True
    plan.reason = f"{len(plan.read_only)} read-only tool(s)" + (
        f", {len(plan.mutating)} that will ask for approval on every call" if plan.mutating else "")
    return plan


def _remove(loc: Location, server: str, entry: dict[str, Any]) -> Callable[[bytes | None], tuple[bytes, str | None]]:
    def compute(before: bytes | None) -> tuple[bytes, str | None]:
        if before is None:
            raise cm.ConfigConflict(f"{loc.path} is gone")
        if loc.fmt == "toml_table":
            new, removed = toml_tables.extract(before.decode("utf-8"), ".".join(loc.container + [server]), loc.path)
            return new.encode("utf-8"), removed
        new_b = mcp_entry.remove(loc.fmt, before, loc.path, {"container_path": loc.container, "name": server},
                                 owned=lambda e: e == entry)
        return new_b, None
    return compute


def apply_adopt(paths: ArbiterPaths, profile: Profile, plan: AdoptPlan) -> str:
    """Remove the entry from the client config (backup + manifest) and register it with the gateway."""
    from arbiter_agent.setup.plan import FileChange, apply_changes

    if not plan.allowed or plan.location is None:
        raise PermissionError(plan.reason or "adoption not allowed")
    loc = plan.location
    removed_box: dict[str, str | None] = {}

    def compute(before: bytes | None) -> bytes:
        new, removed = _remove(loc, plan.server, plan.entry)(before)
        removed_box["text"] = removed
        return new

    detail: dict[str, Any] = {"server": plan.server, "format": loc.fmt, "container_path": loc.container,
                              "entry": plan.entry}
    ch = FileChange(profile.id, loc.path, "gateway_adopt", f"move MCP server {plan.server!r} behind the gateway",
                    compute, live=loc.live, detail=detail).prepare()
    if ch.error:
        raise cm.ConfigConflict(ch.error)
    detail["removed_text"] = removed_box.get("text")
    manifest = Manifest.load(paths)
    results = apply_changes(paths, [ch], manifest)
    if any(r.startswith(("FAILED", "skipped")) for r in results):
        raise RuntimeError("; ".join(results))
    reg = Registry.load(paths)
    reg.put(Adopted(profile.id, plan.server, plan.entry,
                    {t["name"]: schema_hash(t) for t in plan.tools if annotated_read_only(t)}, plan.mutating))
    reg.save()
    return results[0]


def restore_entry(current: bytes, path: Path, detail: dict[str, Any]) -> bytes:
    """Bytes with the adopted server's original entry back (release / uninstall)."""
    server, fmt = str(detail["server"]), str(detail["format"])
    keys = [str(k) for k in detail.get("container_path") or []]
    if fmt == "toml_table":
        return toml_tables.restore(current.decode("utf-8"), str(detail.get("removed_text") or ""),
                                   ".".join(keys + [server]), detail["entry"], path).encode("utf-8")
    return mcp_entry.upsert(fmt, current, path, {"container": keys, "name": server}, detail["entry"],
                            owned=lambda e: False)


def release(paths: ArbiterPaths, client: str, server: str) -> str:
    manifest = Manifest.load(paths)
    rec = next((r for r in manifest.active(client) if r.kind == "gateway_adopt" and r.detail.get("server") == server),
               None)
    reg = Registry.load(paths)
    if rec is None:
        reg.drop(client, server)
        reg.save()
        return f"{server} isn't adopted for {client}"
    path = Path(rec.path)
    current = cm.read_bytes(path) or b""
    new = restore_entry(current, path, rec.detail)
    cm.atomic_write(path, new)
    rec.removed_at = time.time()
    manifest.save()
    reg.drop(client, server)
    reg.save()
    return f"restored {server} in {path}"

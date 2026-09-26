"""``arbiter doctor`` (spec §4.5.3): per-client verified tiers and health, daemon health."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from arbiter_agent import PROTOCOL_MAJOR, PROTOCOL_MINOR, __version__
from arbiter_agent.clients.client_env import ClientEnv, arbiter_command, current_env
from arbiter_agent.clients.registry import load_registry
from arbiter_agent.daemon.auth import HOOK_TOKEN_HEADER, read_token
from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable, read_record
from arbiter_agent.integration import capability_probe
from arbiter_agent.integration.codex_version import codex_version
from arbiter_agent.integration.conformance import check_mcp
from arbiter_agent.paths import ArbiterPaths
from arbiter_agent.setup.manifest import Manifest
from arbiter_agent.state.store import connect, storage_bytes


def mcp_round_trip(command: list[str], timeout: float = 15.0) -> dict[str, Any]:
    """Spawn the MCP server exactly as the client would and call arbiter_ping."""
    t = time.perf_counter()
    try:
        p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             text=True, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"can't start {command[0]}: {exc}"}
    try:
        assert p.stdin and p.stdout
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "arbiter-doctor", "version": __version__}}}) + "\n")
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "arbiter_ping", "arguments": {}}}) + "\n")
        p.stdin.flush()
        init = json.loads(p.stdout.readline())
        ping = json.loads(p.stdout.readline())
        text = ping["result"]["content"][0]["text"]
        ok = "pong" in text
        return {"ok": ok, "latency_ms": round((time.perf_counter() - t) * 1000, 1),
                "server": init.get("result", {}).get("serverInfo"), "detail": "" if ok else text[:200]}
    except (ValueError, KeyError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            if p.stdin:
                p.stdin.close()
            p.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            p.kill()


def http_round_trip(paths: ArbiterPaths) -> dict[str, Any]:
    port = (read_record(paths).get("http") or {}).get("port")
    token = read_token(paths.hook_token_file)
    if not port or not token:
        return {"ok": False, "error": "no http hook endpoint (daemon not running?)"}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/hook/claude_code/ArbiterDoctorPing", data=b"{}",
                                 method="POST", headers={"content-type": "application/json",
                                                         HOOK_TOKEN_HEADER: token.decode()})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=3) as r:  # noqa: S310 - loopback
            body = json.loads(r.read() or b"{}")
        return {"ok": body.get("pong") is True, "latency_ms": round((time.perf_counter() - t) * 1000, 1)}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _command_from_entry(entry: Any) -> list[str] | None:
    if isinstance(entry, dict) and entry.get("command"):
        return [str(entry["command"]), *[str(a) for a in entry.get("args") or []]]
    return None


def gather(paths: ArbiterPaths, env: ClientEnv | None = None, round_trips: bool = True) -> dict[str, Any]:
    env = env or current_env()
    report: dict[str, Any] = {"arbiter_version": __version__, "protocol": [PROTOCOL_MAJOR, PROTOCOL_MINOR]}
    try:
        with DaemonClient(paths, component="doctor") as c:
            st = c.request("status", timeout=3.0)
        report["daemon"] = {"running": True, "version": st["version"], "protocol": st["protocol"],
                            "degraded": st["degraded"], "events": st["events"], "breakers": st.get("breakers"),
                            "debug": st["debug"], "http_port": st["http_port"],
                            "protocol_match": st["protocol"][0] == PROTOCOL_MAJOR}
    except DaemonUnavailable as exc:
        report["daemon"] = {"running": False, "reason": exc.reason}
    report["storage_bytes"] = storage_bytes(paths.db) if paths.db.exists() else 0
    which = shutil.which("arbiter")
    ours = Path(arbiter_command(paths.root)[0]).resolve()
    shadowed = which is not None and Path(which).resolve() != ours and ours.name.startswith("arbiter")
    report["path_arbiter"] = {"which": which, "shadowed": shadowed}

    reg = load_registry(paths.config / "profiles")
    manifest = Manifest.load(paths)
    installed = set(manifest.installed_clients())
    conn = connect(paths.db, readonly=True) if paths.db.exists() else None
    clients: list[dict[str, Any]] = []
    try:
        for p in reg.installable():
            found, hits = p.detected(env)
            if not found and p.id not in installed:
                continue
            recs = manifest.active(p.id)
            installed_at = min((r.installed_at for r in recs), default=None)
            try:
                cap = capability_probe.probe(p, env, conn, installed_at=installed_at)
            except Exception as exc:  # doctor must always finish
                clients.append({"client": p.id, "error": str(exc)})
                continue
            entry: dict[str, Any] = {"client": p.id, "name": p.display_name, "installed": p.id in installed,
                                     "detected": hits, **cap.to_dict()}
            if p.id == "codex":
                entry["version"] = codex_version(env)
            if round_trips and p.id in installed:
                cmd = _command_from_entry(check_mcp(p, env).data.get("entry"))
                entry["mcp_round_trip"] = mcp_round_trip(cmd) if cmd else {"ok": False, "error": "no MCP entry"}
                if (p.hooks or {}).get("transport") == "http":
                    entry["http_round_trip"] = http_round_trip(paths)
                    entry["hook_port"] = hook_port_check(p, env, paths)
            clients.append(entry)
    finally:
        if conn is not None:
            conn.close()
    report["clients"] = clients
    from arbiter_agent import appcontainer

    report["packaged"] = appcontainer.package_name()
    return report


def hook_port_check(profile: Any, env: Any, paths: ArbiterPaths) -> dict[str, Any]:
    """The port in the client's Arbiter http hook URLs must be the one the daemon listens on."""
    import re

    daemon_port = (read_record(paths).get("http") or {}).get("port")
    target = profile.expand((profile.hooks or {}).get("file"), env)
    try:
        text = target.read_text(encoding="utf-8") if target else ""
    except OSError:
        text = ""
    ports = sorted({int(m) for m in re.findall(r"127\.0\.0\.1:(\d+)/hook/", text)})
    if not ports:
        return {"ok": False, "error": "no Arbiter http hook URLs found"}
    ok = daemon_port is not None and ports == [int(daemon_port)]
    return {"ok": ok, "hook_ports": ports, "daemon_port": daemon_port,
            **({} if ok else {"error": f"hooks point to {ports}, the daemon listens on {daemon_port}; "
                                       "re-run `arbiter setup` to repair"})}


def render(report: dict[str, Any]) -> str:
    lines = [f"arbiter {report['arbiter_version']} (ipc protocol {report['protocol'][0]}.{report['protocol'][1]})"]
    d = report["daemon"]
    if d.get("running"):
        lines.append(f"daemon: running v{d['version']}  store: {'READ-ONLY' if d['degraded'] else 'ok'}  "
                     f"events: {d['events']}  http port: {d['http_port']}"
                     + ("" if d["protocol_match"] else "  (protocol mismatch: restart the daemon)"))
        if d.get("breakers"):
            tripped = [k for k, v in d["breakers"].items() if v.get("tripped")]
            lines.append(f"breakers: {'tripped: ' + ', '.join(tripped) if tripped else 'all closed'}")
    else:
        lines.append(f"daemon: not running ({d.get('reason')}); it starts automatically on first use")
    lines.append(f"storage: {report['storage_bytes'] / 1e6:.1f} MB")
    if report.get("packaged"):
        lines.append(f"note: this process runs inside the packaged app {report['packaged']}, where new AppData files "
                     "are redirected; setup writes those client configs from outside the package (Arbiter's home "
                     "is ~/.arbiter)")
    if report["path_arbiter"]["shadowed"]:
        lines.append(f"warning: another 'arbiter' is first on PATH: {report['path_arbiter']['which']}")
    if not report["clients"]:
        lines.append("clients: none detected")
    for c in report["clients"]:
        if "error" in c:
            lines.append(f"\n{c['client']}: probe error: {c['error']}")
            continue
        lines.append(f"\n{c['name']}: {'installed' if c['installed'] else 'detected, not set up'}  "
                     f"verified tiers: {c['verified_fmt']}  (configured: {' '.join(c['configured']) or 'none'})")
        for chk in c["checks"]:
            lines.append(f"  [{'ok' if chk['ok'] else '--'}] {chk['name']}: {chk['detail']}")
        for key in ("mcp_round_trip", "http_round_trip", "hook_port"):
            if key in c:
                rt = c[key]
                lines.append(f"  [{'ok' if rt['ok'] else '!!'}] {key.replace('_', ' ')}: "
                             + ((f"{rt['latency_ms']} ms" if "latency_ms" in rt else f"port {rt.get('daemon_port')}")
                                if rt["ok"] else rt.get("error", rt.get("detail", ""))))
        if c.get("version"):
            v = c["version"]
            lines.append(f"  version: session {v.get('session_cli_version')} ({v.get('session_originator')})"
                         + (f", binary {v['binary_version']}" if v.get("binary_version") else ""))
        for note in c["notes"]:
            lines.append(f"  note: {note}")
        for drift in c["drift"]:
            lines.append(f"  drift: {drift}")
        caps = c.get("capabilities") or {}
        if "desktop_exec_tool_hooks" in caps:
            lines.append(f"  desktop code-mode exec tool hooks: {caps['desktop_exec_tool_hooks']}")
    return "\n".join(lines)

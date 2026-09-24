"""``arbiter setup`` (spec §4.5.2).

1. Detect installed clients from profiles.
2. Show a checklist and the exact (token-masked) diff of every file to be touched.
3. Back up, merge, validate, write atomically, record in the manifest.
4. Ask for the privacy scope; start the daemon; print client trust steps; run a short doctor.

Hard invariants (enforced here, not configurable): never modify client permission, approval,
sandbox or model settings; never move/remove/wrap the user's existing MCP servers; never write
Codex ``trusted_hash``.
"""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import yaml

from arbiter_agent.clients.client_env import ClientEnv, arbiter_command, current_env
from arbiter_agent.clients.profile_schema import Profile
from arbiter_agent.clients.registry import load_registry
from arbiter_agent.daemon.auth import ensure_token, read_token
from arbiter_agent.daemon.client import read_record
from arbiter_agent.paths import ArbiterPaths, write_private
from arbiter_agent.setup.manifest import Manifest
from arbiter_agent.setup.plan import FileChange, apply_changes, plan_for


@dataclass
class SetupOptions:
    yes: bool = False
    dry_run: bool = False
    clients: list[str] | None = None
    scope: str | None = None           # all_except_excluded | allow_list
    start_daemon: bool = True
    out: TextIO = field(default_factory=lambda: sys.stdout)
    ask: Callable[[str], str] = input


def stable_http_port(paths: ArbiterPaths) -> int:
    """The loopback port client http hooks will use. Reuses the running daemon's port or the
    persisted one; otherwise picks a free port and persists it for the daemon to adopt."""
    rec = read_record(paths)
    port = (rec.get("http") or {}).get("port")
    if port:
        return int(port)
    f = paths.state / "http_port"
    try:
        return int(f.read_text().strip())
    except (OSError, ValueError):
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    write_private(f, str(port).encode())
    return port


def print_snippet(profile: Profile, out: TextIO, home: Path | None = None) -> None:
    cmd = arbiter_command(home)
    out.write(f"# {profile.display_name}: register Arbiter's MCP server manually\n")
    out.write("# JSON (mcpServers):\n")
    out.write(json.dumps({"mcpServers": {"arbiter": {"command": cmd[0], "args": cmd[1:]}}}, indent=2) + "\n")
    out.write("# TOML (e.g. Codex-style [mcp_servers.arbiter]):\n")
    out.write(f"[mcp_servers.arbiter]\ncommand = {json.dumps(cmd[0])}\nargs = {json.dumps(cmd[1:])}\n")


def _set_scope(paths: ArbiterPaths, scope: str) -> None:
    cfg_file = paths.config_file
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    data = data or {}
    data.setdefault("privacy", {})["project_scope"] = scope
    write_private(cfg_file, yaml.safe_dump(data, sort_keys=False).encode())


def run_setup(paths: ArbiterPaths, opts: SetupOptions, env: ClientEnv | None = None) -> int:
    out = opts.out
    env = env or current_env()
    paths.ensure()
    reg = load_registry(paths.config / "profiles")
    for err in reg.errors:
        out.write(f"warning: {err}\n")

    detected: list[tuple[Profile, list[str]]] = []
    for p in reg.installable():
        found, hits = p.detected(env)
        if found:
            detected.append((p, hits))
    if opts.clients:
        chosen = [reg.get(c) for c in opts.clients]
    else:
        out.write("Detected clients:\n")
        for p, hits in detected:
            tiers = "T1 T2 T3" if not (p.hooks or {}).get("trust_required") else "T1 T3 now, T2 after you trust hooks"
            out.write(f"  - {p.display_name:24s} ({hits[0]})  -> {tiers}\n")
        if not detected:
            out.write("  (none found; use --clients or `arbiter setup --print generic_mcp`)\n")
            return 1
        chosen = [p for p, _ in detected]

    hook_token = ensure_token(paths.hook_token_file).decode()
    port = stable_http_port(paths)
    command = arbiter_command(paths.root)
    changes: list[FileChange] = []
    for p in chosen:
        if p.id == "claude_code":
            from arbiter_agent.clients.claude_code.hooks import plugin_enabled

            plugin = plugin_enabled(env.claude_settings)
            if plugin:
                out.write(f"\nClaude Code: the Arbiter plugin ({plugin}) is enabled, so setup leaves "
                          "Claude Code as is.\n")
                continue
        changes += [c.prepare() for c in plan_for(p, env, command, port=port, hook_token=hook_token)]

    out.write("\nPlanned changes:\n")
    for c in changes:
        out.write(f"* {c.description}\n")
        out.write(c.diff())
    if opts.dry_run:
        out.write("\n(dry run: nothing written)\n")
        return 0
    if any(c.error for c in changes):
        out.write("\nSome files can't be edited safely (see '!!' above); they will be skipped.\n")
    if not opts.yes:
        if not sys.stdin.isatty() and opts.ask is input:
            out.write("\nNot a terminal: re-run with --yes to apply, or --dry-run to preview.\n")
            return 2
        if opts.ask("\nApply these changes? [y/N] ").strip().lower() not in ("y", "yes"):
            out.write("Nothing changed.\n")
            return 1

    manifest = Manifest.load(paths)
    results = apply_changes(paths, changes, manifest)
    for line in results:
        out.write(f"  {line}\n")
    problems = sum(1 for line in results if line.startswith(("skipped", "FAILED")))

    scope = opts.scope
    if scope is None and not opts.yes:
        answer = opts.ask("\nRecord sessions in all projects except excluded ones (default), or only projects you "
                          "allow-list? [all/allow] ").strip().lower()
        scope = "allow_list" if answer.startswith("allow") else "all_except_excluded"
    if scope:
        _set_scope(paths, scope)
        out.write(f"privacy scope: {scope} (change with `arbiter exclude`/`arbiter include`)\n")

    if opts.start_daemon:
        from arbiter_agent.daemon.lifecycle import ensure_daemon

        ok = ensure_daemon(paths, wait=10)
        out.write("daemon: running\n" if ok else "daemon: not reachable yet (it starts on first use)\n")

    for p in chosen:
        hint = (p.hooks or {}).get("trust_hint")
        if hint:
            out.write(f"\nNext step for {p.display_name}: {hint}\n")
    if read_token(paths.hook_token_file) is None:
        out.write("warning: hook token missing\n")
    if problems:
        out.write(f"\n{problems} file(s) were left unchanged because editing them wasn't safe (see above).\n")
    out.write("\nDone. Run `arbiter doctor` any time to check each client.\n")
    return 3 if problems else 0

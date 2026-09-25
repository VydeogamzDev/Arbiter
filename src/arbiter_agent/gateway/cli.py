"""``arbiter gateway status | servers | adopt | release | bench`` (M10)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

DECISIONS = "gateway_decisions.json"


def _env_and_profile(paths: Any, client: str) -> tuple[Any, Any]:
    from arbiter_agent.clients.client_env import current_env
    from arbiter_agent.clients.registry import load_registry

    return current_env(), load_registry(paths.config / "profiles").get(client)


def _tty_yes(prompt: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_gateway(a: argparse.Namespace) -> int:
    from arbiter_agent.config import load_config
    from arbiter_agent.gateway import adopt, benchmark
    from arbiter_agent.gateway.registry import Registry
    from arbiter_agent.paths import get_paths, write_private

    paths = get_paths()
    config = load_config(paths)
    mode = str(config.get("gateway.enabled", "auto")).lower()
    if a.action == "status":
        reg = Registry.load(paths)
        try:
            saved: dict[str, Any] = json.loads((paths.state / DECISIONS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        adopted_rows: list[dict[str, Any]] = [
            {"client": x.client, "server": x.server, "read_only": len(x.confirmed_read_only), "mutating": x.mutating}
            for x in reg.items]
        out = {"mode": mode, "adopted": adopted_rows, "decisions": saved}
        if a.json:
            print(json.dumps(out, indent=1))
        else:
            print(f"gateway: {mode}")
            for row in adopted_rows:
                print(f"  adopted {row['server']} for {row['client']}: {row['read_only']} read-only, "
                      f"{len(row['mutating'])} ask per call")
            for c, d in saved.items():
                print(f"  {c}: {'ON' if d['enabled'] else 'off'} ({'; '.join(d['reasons'])})")
            if not adopted_rows and not saved:
                print("  nothing adopted; run `arbiter gateway bench` to measure")
        return 0
    if a.action in ("servers", "adopt", "release") and not a.client:
        print(f"usage: arbiter gateway {a.action} --client CLIENT" + (" SERVER" if a.action != "servers" else ""),
              file=sys.stderr)
        return 2
    if a.action == "servers":
        env, profile = _env_and_profile(paths, a.client)
        loc = adopt.location(profile, env)
        found = adopt.servers(loc) if loc else {}
        adopted = {x.server for x in Registry.load(paths).for_client(profile.id)}
        for name, entry in found.items():
            kind = "remote" if entry.get("url") or entry.get("httpUrl") else "stdio"
            print(f"  {name:<24} {kind}")
        for name in sorted(adopted):
            print(f"  {name:<24} adopted (behind the gateway)")
        if not found and not adopted:
            print(f"  no MCP servers in {loc.path if loc else '(unknown location)'}")
        return 0
    if a.action == "adopt":
        if mode in ("off", "false"):
            print("the gateway is off (gateway.enabled: off)", file=sys.stderr)
            return 1
        env, profile = _env_and_profile(paths, a.client)
        plan = adopt.plan_adopt(profile, env, a.server)
        print(f"{a.server} for {profile.display_name}: {plan.reason}")
        for t in plan.read_only:
            print(f"  read-only   {t}")
        for t in plan.mutating:
            print(f"  asks first  {t}")
        if not plan.allowed:
            return 1
        if not a.yes and not _tty_yes(f"Move {a.server} behind the gateway and confirm this classification? [y/N] "):
            print("nothing changed (re-run with --yes, or answer yes in a terminal)")
            return 1
        print(adopt.apply_adopt(paths, profile, plan))
        print(f"restart {profile.display_name} to pick up the change")
        return 0
    if a.action == "release":
        print(adopt.release(paths, a.client, a.server))
        return 0
    # bench
    from arbiter_agent.clients.registry import load_registry
    from arbiter_agent.gateway import semantic
    from arbiter_agent.gateway.catalog import Catalog, from_server, own_tools
    from arbiter_agent.gateway.upstream import LaunchSpec, Upstream
    from arbiter_agent.shims.mcp_server import TOOLS

    ranker = semantic.ranker_from_config(config)
    quality = benchmark.search_quality(ranker)
    ref = benchmark.run(int(config.get("gateway.enabled_min_catalog_size", 20)), quality["heldout_recall"])
    reg = Registry.load(paths)
    decisions: dict[str, Any] = {}
    for profile in load_registry(paths.config / "profiles").profiles.values():
        cat = Catalog(own_tools(TOOLS))
        for item in reg.for_client(profile.id):
            try:
                up = Upstream(item.server, LaunchSpec.from_entry(item.entry), timeout_s=20).start()
                cat.add(from_server(item.server, up.tools, item.confirmed_read_only))
                up.close()
            except Exception as exc:
                print(f"  couldn't start {item.server}: {exc}", file=sys.stderr)
        d = benchmark.decide(profile.id, cat, benchmark.core_defs(), native_deferral=profile.native_tool_deferral,
                             mode=mode, min_catalog=int(config.get("gateway.enabled_min_catalog_size", 20)),
                             measured_recall=quality["heldout_recall"])
        decisions[profile.id] = {"enabled": d.enabled, "reasons": d.reasons, "savings": round(d.savings, 3)}
    write_private(paths.state / DECISIONS, json.dumps(decisions, indent=1).encode())
    report = {"search": quality, "reference": ref, "decisions": decisions}
    if a.json:
        print(json.dumps(report, indent=1))
        return 0
    print(f"search mode {quality['mode']}: recall@8 dev {quality['dev_recall']}, held-out {quality['heldout_recall']}")
    for label, s in ref["scenarios"].items():
        dn, dd = s["decisions"]["normal"], s["decisions"]["native_deferral"]
        print(f"  reference {label:<22} {s['tools']:>3} tools  gateway vs normal {dn['savings']:+.0%} "
              f"({'on' if dn['enabled'] else 'off'}), vs native deferral {dd['savings']:+.0%} "
              f"({'on' if dd['enabled'] else 'off'})")
    for c, d in decisions.items():
        print(f"  {c:<16} {'ON' if d['enabled'] else 'off'}  {'; '.join(d['reasons'])}")
    return 0


def add_parser(sub: Any) -> None:
    s = sub.add_parser("gateway", help="tool gateway: status, servers, adopt/release an MCP server, bench (M10)")
    s.add_argument("action", nargs="?", default="status", choices=["status", "servers", "adopt", "release", "bench"])
    s.add_argument("server", nargs="?")
    s.add_argument("--client", help="client id (codex, claude_code, vscode, ...)")
    s.add_argument("--yes", action="store_true", help="adopt: confirm the classification without a prompt")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_gateway)

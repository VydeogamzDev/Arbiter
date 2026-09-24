"""``arbiter`` command line.

Imports are lazy per subcommand: ``arbiter hook`` and ``arbiter mcp`` run on every client hook
or session and must stay on the stdlib-only path (no yaml, no sqlite)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arbiter_agent.paths import ArbiterPaths
    from arbiter_agent.privacy.scope import ProjectScope


def _paths() -> ArbiterPaths:
    from arbiter_agent.paths import get_paths

    return get_paths()


def cmd_hook(a: argparse.Namespace) -> int:
    from arbiter_agent.shims.hook_cli import main

    return main(a.client, a.event)


def cmd_mcp(a: argparse.Namespace) -> int:
    from arbiter_agent.shims.mcp_server import main

    return main()


def cmd_version(a: argparse.Namespace) -> int:
    from arbiter_agent import PROTOCOL_MAJOR, PROTOCOL_MINOR, __version__

    print(f"arbiter-agent {__version__} (ipc protocol {PROTOCOL_MAJOR}.{PROTOCOL_MINOR})")
    return 0


def cmd_paths(a: argparse.Namespace) -> int:
    p = _paths()
    print(json.dumps({"root": str(p.root) if p.root else None, "data": str(p.data), "logs": str(p.logs),
                      "config": str(p.config_file), "db": str(p.db), "pipe": p.pipe_address}, indent=1))
    return 0


def cmd_config(a: argparse.Namespace) -> int:
    from arbiter_agent.config import ConfigError, load_config

    try:
        cfg = load_config(_paths())
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if a.action == "validate":
        print(f"ok ({cfg.source or 'defaults only'})")
    else:
        section = cfg.data if not a.section else cfg.section(a.section)
        print(json.dumps(section, indent=1, default=str))
    return 0


def cmd_daemon(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon import lifecycle

    paths = _paths()
    if a.action == "run":
        from arbiter_agent.daemon.server import run_forever
        from arbiter_agent.daemon.single_instance import AlreadyRunning

        try:
            return run_forever(paths)
        except AlreadyRunning:
            print("arbiter daemon already running", file=sys.stderr)
            return 0
    if a.action == "start":
        if lifecycle.is_running(paths):
            print("already running")
            return 0
        lifecycle.launch(paths, method=a.method)
        ok = lifecycle.ensure_daemon(paths, wait=a.wait)
        print("started" if ok else "launch requested; not reachable yet")
        return 0 if ok else 1
    if a.action == "stop":
        ok = lifecycle.stop(paths)
        print("stopped" if ok else "could not stop daemon")
        return 0 if ok else 1
    if a.action == "restart":
        lifecycle.stop(paths)
        lifecycle.launch(paths, method=a.method)
        ok = lifecycle.ensure_daemon(paths, wait=a.wait)
        print("restarted" if ok else "restart requested; not reachable yet")
        return 0 if ok else 1
    return cmd_status(a)


def cmd_status(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable

    try:
        with DaemonClient(_paths()) as c:
            st = c.request("status", timeout=3.0)
    except DaemonUnavailable as exc:
        print(f"daemon: not running ({exc.reason})")
        return 1
    if getattr(a, "json", False):
        print(json.dumps(st, indent=1))
        return 0
    print(f"daemon: healthy  pid {st['pid']}  v{st['version']}  up {st['uptime_s']}s  "
          f"ipc: {st['endpoint']['kind']}  http hooks: {st['http_port']}")
    print(f"store: {'READ-ONLY (degraded)' if st['degraded'] else 'ok'}  events: {st['events']}  "
          f"sessions: {st['sessions']}  writer backlog: {st['writer_backlog']}")
    if st.get("scope_skips"):
        print(f"excluded (not stored): {st['scope_skips']}")
    print(f"ingest: {st['ingest']}  debug: {'on' if st['debug'] else 'off'}")
    return 0


def cmd_logs(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon.diagnostics import iter_log_lines

    paths = _paths()
    recs = iter_log_lines(paths, a.component)
    for r in recs[-a.lines:]:
        print(json.dumps(r, ensure_ascii=False))
    if not a.follow:
        return 0
    seen = len(recs)
    try:
        while True:
            time.sleep(0.5)
            recs = iter_log_lines(paths, a.component)
            for r in recs[seen:]:
                print(json.dumps(r, ensure_ascii=False), flush=True)
            seen = len(recs)
    except KeyboardInterrupt:
        return 0


def cmd_debug(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon.diagnostics import debug_active, set_debug

    paths = _paths().ensure()
    if a.state == "status":
        print("on" if debug_active(paths) else "off")
        return 0
    until = set_debug(paths, a.state == "on", hours=a.hours)
    _notify_daemon("debug_refresh")
    print(f"debug on until {time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}" if until else "debug off")
    return 0


def _scope() -> ProjectScope:
    from arbiter_agent.config import load_config
    from arbiter_agent.privacy.scope import ProjectScope

    paths = _paths().ensure()
    cfg = load_config(paths)
    return ProjectScope(paths.scope_file, mode=cfg.get("privacy.project_scope"),
                        config_excludes=cfg.get("privacy.project_exclude") or [])


def _notify_daemon(method: str) -> None:
    from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable

    try:
        with DaemonClient(_paths()) as c:
            c.request(method, timeout=1.0)
    except DaemonUnavailable:
        pass


def cmd_exclude(a: argparse.Namespace) -> int:
    _scope().exclude(a.path)
    _notify_daemon("scope_reload")
    print(f"excluded: {a.path} (new sessions there are not recorded)")
    return 0


def cmd_include(a: argparse.Namespace) -> int:
    _scope().include(a.path)
    _notify_daemon("scope_reload")
    print(f"included: {a.path}")
    return 0


def cmd_scope(a: argparse.Namespace) -> int:
    s = _scope()
    print(json.dumps({"mode": s.mode, "config_exclude": s.config_excludes, **s.lists()}, indent=1))
    return 0


def cmd_retention(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable

    try:
        with DaemonClient(_paths()) as c:
            print(json.dumps(c.request("retention_run", timeout=120.0), indent=1))
        return 0
    except DaemonUnavailable as exc:
        print(f"daemon not running ({exc.reason})", file=sys.stderr)
        return 1


def cmd_setup(a: argparse.Namespace) -> int:
    from arbiter_agent.clients.registry import load_registry
    from arbiter_agent.setup.setup import SetupOptions, print_snippet, run_setup

    paths = _paths()
    if a.print_client:
        print_snippet(load_registry(paths.config / "profiles").get(a.print_client), sys.stdout)
        return 0
    clients = [c.strip() for c in a.clients.split(",") if c.strip()] if a.clients else None
    return run_setup(paths, SetupOptions(yes=a.yes, dry_run=a.dry_run, clients=clients, scope=a.scope,
                                         start_daemon=not a.no_start))


def cmd_doctor(a: argparse.Namespace) -> int:
    from arbiter_agent.setup.doctor import gather, render

    report = gather(_paths(), round_trips=not a.quick)
    print(json.dumps(report, indent=1, default=str) if a.json else render(report))
    return 0


def cmd_uninstall(a: argparse.Namespace) -> int:
    import shutil

    from arbiter_agent.daemon import lifecycle
    from arbiter_agent.setup.uninstall import uninstall

    paths = _paths()
    clients = [c.strip() for c in a.clients.split(",") if c.strip()] if a.clients else None
    for line in uninstall(paths, clients):
        print(line)
    if a.purge:
        if not a.yes and input(f"Delete all Arbiter data in {paths.data}? [y/N] ").strip().lower() != "y":
            print("data kept")
            return 0
        lifecycle.stop(paths)
        for d in (paths.data, paths.logs):
            shutil.rmtree(d, ignore_errors=True)
        print("Arbiter data deleted")
    return 0


def cmd_probe(a: argparse.Namespace) -> int:
    from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable

    try:
        with DaemonClient(_paths()) as c:
            print(json.dumps(c.request("probe", timeout=30.0), indent=1))
        return 0
    except DaemonUnavailable as exc:
        print(f"daemon not running ({exc.reason})", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arbiter", description="Arbiter: local control plane for AI coding agents.")
    p.add_argument("--home", help="use this directory for all Arbiter data (sets ARBITER_HOME)")
    p.add_argument("--client-env", action="append", default=[], metavar="NAME=VALUE", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("hook", help="(client hook) forward a hook event on stdin to the daemon")
    s.add_argument("client")
    s.add_argument("event", nargs="?")
    s.set_defaults(fn=cmd_hook)

    sub.add_parser("mcp", help="(client) run the stdio MCP server").set_defaults(fn=cmd_mcp)
    sub.add_parser("version", help="show version").set_defaults(fn=cmd_version)
    sub.add_parser("paths", help="show data, log and config locations").set_defaults(fn=cmd_paths)

    s = sub.add_parser("config", help="show or validate configuration")
    s.add_argument("action", choices=["show", "validate"], nargs="?", default="show")
    s.add_argument("section", nargs="?")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("daemon", help="manage the daemon")
    s.add_argument("action", choices=["run", "start", "stop", "restart", "status"], nargs="?", default="status")
    s.add_argument("--wait", type=float, default=5.0)
    s.add_argument("--method", choices=["auto", "wmi", "detached"], default="auto")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_daemon)

    s = sub.add_parser("status", help="daemon health and counters")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("logs", help="read Arbiter's own logs")
    s.add_argument("-f", "--follow", action="store_true")
    s.add_argument("--component")
    s.add_argument("-n", "--lines", type=int, default=50)
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("debug", help="temporarily raise log verbosity (auto-expires)")
    s.add_argument("state", choices=["on", "off", "status"])
    s.add_argument("--hours", type=float, default=24.0)
    s.set_defaults(fn=cmd_debug)

    s = sub.add_parser("exclude", help="stop recording sessions under a path")
    s.add_argument("path")
    s.set_defaults(fn=cmd_exclude)
    s = sub.add_parser("include", help="undo an exclude (or allow a path in allow-list mode)")
    s.add_argument("path")
    s.set_defaults(fn=cmd_include)
    sub.add_parser("scope", help="show project scope settings").set_defaults(fn=cmd_scope)

    s = sub.add_parser("setup", help="detect agent clients and connect Arbiter to them")
    s.add_argument("--yes", action="store_true", help="apply without asking")
    s.add_argument("--dry-run", action="store_true", help="show the changes only")
    s.add_argument("--clients", help="comma-separated client ids (default: all detected)")
    s.add_argument("--print", dest="print_client", metavar="CLIENT", help="print a manual config snippet")
    s.add_argument("--scope", choices=["all_except_excluded", "allow_list"])
    s.add_argument("--no-start", action="store_true", help="don't start the daemon")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="check each client's verified tiers and Arbiter's health")
    s.add_argument("--json", action="store_true")
    s.add_argument("--quick", action="store_true", help="skip live MCP/HTTP round trips")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("uninstall", help="remove exactly what setup added")
    s.add_argument("--clients")
    s.add_argument("--purge", action="store_true", help="also delete Arbiter's local data")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_uninstall)

    sub.add_parser("probe", help="re-check client capabilities now").set_defaults(fn=cmd_probe)

    s = sub.add_parser("retention", help="run retention now")
    s.add_argument("action", choices=["run"])
    s.set_defaults(fn=cmd_retention)

    from arbiter_agent.cli_tasks import add_parsers

    add_parsers(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.home:
        os.environ["ARBITER_HOME"] = args.home
    for item in args.client_env:   # set by the daemon launcher; only client-home variables
        name, _, value = item.partition("=")
        if name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "ARBITER_CLIENT_HOME") and value:
            os.environ[name] = value
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

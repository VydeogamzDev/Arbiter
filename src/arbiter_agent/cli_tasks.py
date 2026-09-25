"""Task-state subcommands (M3/M4): contracts, verify, sessions, ledger, gate, eval.

Kept out of ``cli.py`` so the hook/MCP fast paths never import them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

VERIFY_TEMPLATE = """# Arbiter verification commands (spec 12.8). Run with `arbiter verify`.
# Arbiter only runs this file after you approve its exact content with `arbiter verify --trust`.
version: 1
commands:
  - name: tests
    run: pytest -q
    kind: test          # test | typecheck | lint | build
    timeout_s: 900
    # report: reports/junit.xml   # optional JUnit XML written by the command
"""


def _client() -> Any:
    from arbiter_agent.daemon.client import DaemonClient

    return DaemonClient(component="cli")


def _bind(a: argparse.Namespace) -> dict[str, Any]:
    return {"session_id": getattr(a, "session", None), "cwd": os.getcwd(), "client": getattr(a, "client", None)}


def _call(method: str, params: dict[str, Any], timeout: float = 15.0) -> Any:
    from arbiter_agent.daemon.client import DaemonError, DaemonUnavailable

    try:
        with _client() as c:
            return c.request(method, params, timeout=timeout)
    except DaemonError as exc:
        print(f"arbiter: {exc.error}", file=sys.stderr)
        raise SystemExit(2) from None
    except DaemonUnavailable as exc:
        print(f"daemon not running ({exc.reason}); start it with `arbiter daemon start`", file=sys.stderr)
        raise SystemExit(1) from None


def cmd_sessions(a: argparse.Namespace) -> int:
    res = _call("sessions", {"limit": a.limit})
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    import time

    for s in res["sessions"]:
        age = time.time() - float(s["updated_at"] or 0)
        print(f"{s['session_id']}  epoch {s['goal_epoch']}  intents {s['intents']}  contracts {s['contracts']}  "
              f"{int(age // 60)}m ago")
    if not res["sessions"]:
        print("no sessions recorded yet")
    return 0


def _recipe_from_args(a: argparse.Namespace) -> Any:
    if a.test:
        return {"type": "test_command", "command": a.test}
    if a.exit is not None:
        return {"type": "command_exit", "command": a.exit, "exit_code": 0}
    if a.exists:
        return {"type": "file_exists", "path": a.exists}
    if a.unchanged:
        return {"type": "paths_unchanged", "paths": a.unchanged}
    if a.recipe:
        return json.loads(a.recipe)
    return {"type": "manual", "description": a.text}


def cmd_contracts(a: argparse.Namespace) -> int:
    b = _bind(a)
    if a.action == "list":
        st = _call("session_status", b)
        if a.json:
            print(json.dumps(st, indent=1))
        else:
            print(f"session {st['session_id']}")
            print(st["ledger_text"])
        return 0
    if a.action == "add":
        if not a.text:
            print("usage: arbiter contracts add TEXT [--test CMD | --exit CMD | --exists PATH | --unchanged PATH...]",
                  file=sys.stderr)
            return 2
        res = _call("contract_add", {**b, "text": a.text, "recipe": _recipe_from_args(a), "quote": a.quote})
        print(json.dumps(res, indent=1))
        return 0 if res.get("accepted") else 1
    if a.action in ("waive", "confirm", "reject"):
        if not a.text:
            print(f"usage: arbiter contracts {a.action} CONTRACT_ID", file=sys.stderr)
            return 2
        res = _call("contract_decide", {**b, "contract": a.text, "decision": a.action, "note": a.note or ""})
        print(f"{res['contract']}: {res['decision']} recorded")
        return 0
    if a.action == "ack-integrity":
        keys = [a.text] if a.text else []
        res = _call("integrity_ack", {**b, "keys": keys})
        print("acknowledged: " + ", ".join(res["acknowledged"]))
        return 0
    return 2


def cmd_ledger(a: argparse.Namespace) -> int:
    res = _call("finish_check", {**_bind(a), "summary": "cli"})
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(res["ledger_text"])
    return 0 if res.get("verdict") == "verified" else 3


def cmd_gate(a: argparse.Namespace) -> int:
    res = _call("gate_mode", {**_bind(a), "mode": a.mode})
    print(f"gate mode for {res['session']}: {res['gate_mode']}")
    return 0


def _tty_confirm(prompt: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def cmd_verify(a: argparse.Namespace) -> int:
    from arbiter_agent.completion import verify_runner as vr

    b = _bind(a)
    if a.init:
        p = Path(os.getcwd()) / ".arbiter" / "verify.yaml"
        if p.exists():
            print(f"{p} already exists")
            return 1
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(VERIFY_TEMPLATE, encoding="utf-8")
        print(f"wrote {p}; edit it, then run `arbiter verify --trust`")
        return 0
    if a.baseline:
        res = _call("baseline", {**b, "replace": True}, timeout=60)
        print(f"baseline {res['baseline']}: {res['files']} test/fixture/harness files, head {res['head']}, "
              f"dirty {res['dirty']}")
        return 0
    plan = _call("verify_plan", b)
    if a.trust:
        if not plan.get("path"):
            print(plan.get("error"), file=sys.stderr)
            return 1
        path = Path(plan["path"])
        print(path.read_text(encoding="utf-8"))
        print(f"sha256 {plan['sha256']}")
        if not _tty_confirm(f"Trust {path} and let Arbiter run these commands? [y/N] "):
            print("not trusted (this needs an interactive terminal and an explicit yes)")
            return 1
        _call("verify_trust", {"path": str(path), "sha256": plan["sha256"], "channel": "cli_tty"})
        print("trusted")
        return 0
    if not plan.get("ok"):
        print(f"not run: {plan.get('error')}", file=sys.stderr)
        return 1
    cmds = [vr.VerifyCommand(**d) for d in plan["commands"]]
    results = vr.run_all(cmds, plan["root"], a.only or None)
    for r in results:
        print(f"{r.name}: {r.status.upper()}  exit {r.exit_code}  {r.duration_s:.1f}s  `{r.command}`")
    try:
        _call("verify_submit", {**b, "results": [vr.result_fact(r) for r in results], "channel": "cli"})
    except SystemExit:
        print("(results not recorded: no matching session; pass --session)", file=sys.stderr)
    return 0 if all(r.status == "pass" for r in results) else 3


def cmd_search(a: argparse.Namespace) -> int:
    from arbiter_agent.shims.mcp_server import render_retrieval

    b = _bind(a)
    if a.symbol:
        res = _call("retrieve", {**b, "op": "symbol", "name": a.query}, timeout=60)
        print(render_retrieval("arbiter_symbol", res))
    elif a.related:
        res = _call("retrieve", {**b, "op": "related", "path": a.query}, timeout=60)
        print(render_retrieval("arbiter_related", res))
    else:
        res = _call("retrieve", {**b, "op": "search", "query": a.query, "limit": a.limit}, timeout=60)
        print(render_retrieval("arbiter_search", res))
    return 0


def cmd_index(a: argparse.Namespace) -> int:
    b = _bind(a)
    if a.action == "gc":
        print(json.dumps(_call("retrieve", {**b, "op": "gc", "keep_days": a.keep_days}, timeout=120), indent=1))
        return 0
    res = _call("retrieve", {**b, "op": "status"}, timeout=300)
    idx = res["index"]
    print(f"index v{idx['version']}  generation {idx['generation']}  files {idx['files']}  pending {idx['pending']}  "
          f"head {str(idx['head'] or 'no git')[:12]}")
    print(f"root {idx['root']}\ndb   {res['db']}")
    return 0


def _confirmed_channel(needs_tty: bool, prompt: str) -> str | None:
    """'cli_tty' after an interactive yes, 'cli' when no confirmation is needed, None if refused."""
    if not needs_tty:
        return "cli"
    return "cli_tty" if _tty_confirm(prompt) else None


def cmd_control(a: argparse.Namespace) -> int:
    from arbiter_agent.policy import authority
    from arbiter_agent.shims.mcp_server import render_controls

    b = _bind(a)
    if a.action in (None, "list"):
        res = _call("control_list", b)
        print(json.dumps(res, indent=1, default=str) if a.json else render_controls(res))
        return 0
    if a.action == "clear":
        if not a.target:
            print("usage: arbiter control clear KEY [--session]", file=sys.stderr)
            return 2
        res = _call("control_clear", {**b, "key": a.target, "scope": "session" if a.session_scope else "global",
                                      "channel": "cli"})
        print("cleared" if res["cleared"] else "nothing to clear")
        return 0
    if a.action == "module":
        if not a.target or a.value not in ("on", "off"):
            print("usage: arbiter control module NAME on|off", file=sys.stderr)
            return 2
        key, value = f"module:{a.target}", a.value
    elif a.action == "controller":
        if a.target not in ("on", "off"):
            print("usage: arbiter control controller on|off [--session]", file=sys.stderr)
            return 2
        key, value = "controller", a.target
    else:
        return 2
    needs_tty = authority.required(key, value) == authority.Actor.USER_TTY
    channel = _confirmed_channel(needs_tty, f"{key} {value} weakens Arbiter's checks. Continue? [y/N] ")
    if channel is None:
        print("not changed (this needs an interactive terminal and an explicit yes)")
        return 1
    scope = "session" if a.session_scope else "global"
    res = _call("control_set", {**b, "key": key, "value": value, "scope": scope, "channel": channel,
                                "reason": a.reason or ""})
    print(f"{res['key']} = {res['value']} ({res['scope']})")
    return 0


def cmd_breakers(a: argparse.Namespace) -> int:
    from arbiter_agent.policy.circuit_breaker import SPECS

    b = _bind(a)
    if a.action == "reset":
        if not a.name:
            print("usage: arbiter breakers reset NAME", file=sys.stderr)
            return 2
        spec = SPECS.get(a.name.split(":", 1)[0])
        channel = _confirmed_channel(bool(spec and spec.integrity),
                                     f"Reset {a.name}? Only do this after inspecting why it tripped. [y/N] ")
        if channel is None:
            print("not reset (this breaker guards integrity: it needs an interactive terminal and an explicit yes)")
            return 1
        res = _call("breaker_reset", {**b, "name": a.name, "channel": channel, "reason": a.reason or ""})
        print(f"{res['breaker']}: {'reset' if res['was_open'] else 'was not open'}")
        return 0
    res = _call("control_list", b)["breakers"]
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    for name, st in res.items():
        print(f"{name:<40} {st['state']:<10} trips {st['trips']:<3} {st['fail_mode']:<18} {st['last_reason']}")
    if not res:
        print("no breakers have seen failures")
    return 0


def cmd_semif(a: argparse.Namespace) -> int:
    from arbiter_agent.config import load_config
    from arbiter_agent.paths import get_paths
    from arbiter_agent.semif import hardware

    paths = get_paths()
    config = load_config(paths)
    if a.action == "export-onnx":
        from arbiter_agent.semif.onnx_export import ParityError, export

        if not a.model or not a.out:
            print("usage: arbiter semif export-onnx --model DIR --out DIR [--precision w8e4|w8|fp16|fp32]",
                  file=sys.stderr)
            return 2
        try:
            m = export(Path(a.model).expanduser(), Path(a.out).expanduser(), a.precision)
        except ParityError as exc:
            print(f"export rejected: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {Path(a.out) / m['file']} ({m['bytes'] / 1e6:.0f} MB); set semif.encoder.model to {a.out}")
        return 0
    if a.action == "bench":
        from arbiter_agent.semif import benchmark
        from arbiter_agent.semif.service import SemIfService

        svc = SemIfService.from_config(config).start()
        try:
            report = benchmark.run(svc, item_list=benchmark.heldout_items() if a.heldout else None)
        finally:
            svc.stop()
        print(json.dumps(report, indent=1) if a.json else benchmark.render(report))
        return 0
    plan = hardware.plan(config)
    if a.action == "status":
        print(json.dumps({"enabled": config.get("semif.enabled"), "encoder": config.get("semif.encoder"),
                          "plan": plan.to_dict()}, indent=1, default=str))
        try:
            print(json.dumps(_call("sensor_status", {}, timeout=5), indent=1))
        except SystemExit:
            pass
        return 0
    if a.action == "disable":
        _write_semif_config(paths, {"enabled": False, "encoder": {"enabled": False}})
        print("sensor disabled (rules only); restart the daemon to apply: `arbiter daemon restart`")
        return 0
    # enable
    print("Sensor plan:")
    print(f"  GPUs: {', '.join(f'{g.name} ({g.vram_gb} GB)' for g in plan.gpus) or 'none detected'}")
    print(f"  tier 0 encoder (CPU OK): {'available' if plan.encoder_available else 'needs the gliner2 package'}")
    print(f"  decoder tier: {plan.decoder or 'none (needs a GPU)'}")
    for n in plan.notes:
        print(f"  note: {n}")
    if plan.decoder:
        print("  decoder runtime: start llama-server with the tier's GGUF on 127.0.0.1:8088, e.g.\n"
              "    llama-server -m <model>.gguf --port 8088 --host 127.0.0.1 -c 8192 -ngl 99")
    if not a.yes:
        print("\nNothing changed. Re-run with --yes to write this to Arbiter's config.")
        return 0
    update: dict[str, Any] = {"encoder": {"enabled": bool(plan.encoder_available)}}
    if plan.decoder:
        update["enabled"] = True
    _write_semif_config(paths, update)
    print("written; restart the daemon to apply: `arbiter daemon restart`")
    return 0


def _write_semif_config(paths: Any, update: dict[str, Any]) -> None:
    import yaml

    from arbiter_agent.paths import write_private

    cfg = paths.config_file
    data = (yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.exists() else None) or {}
    sem = data.setdefault("semif", {})
    for k, v in update.items():
        if isinstance(v, dict):
            sem.setdefault(k, {}).update(v)
        else:
            sem[k] = v
    write_private(cfg, yaml.safe_dump(data, sort_keys=False).encode())


def cmd_eval(a: argparse.Namespace) -> int:
    from arbiter_agent.eval import gates

    if a.faults:
        from arbiter_agent.eval import fault_injection

        rep = fault_injection.run_all()
        print(json.dumps(rep, indent=1) if a.json else fault_injection.render(rep))
        return 0 if rep["passed"] else 4
    report = gates.run_all(Path(a.corpus) if a.corpus else None)
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        print(gates.render(report))
    return 0 if report["passed"] else 4


def add_parsers(sub: Any) -> None:
    def session_args(s: argparse.ArgumentParser) -> None:
        s.add_argument("--session", help="session id (default: the latest active session in this directory)")
        s.add_argument("--client", help="client id to narrow session lookup (codex, claude_code, ...)")

    s = sub.add_parser("sessions", help="list recorded agent sessions")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_sessions)

    s = sub.add_parser("contracts", help="show or manage a session's contracts")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "add", "waive", "confirm", "reject", "ack-integrity"])
    s.add_argument("text", nargs="?", help="contract text (add), contract id (waive/confirm/reject) or finding key")
    s.add_argument("--test", help="recipe: this test command passes")
    s.add_argument("--exit", help="recipe: this command exits 0")
    s.add_argument("--exists", help="recipe: this file exists")
    s.add_argument("--unchanged", nargs="+", help="recipe: these paths stay unchanged")
    s.add_argument("--recipe", help="recipe as JSON")
    s.add_argument("--quote", help="verbatim quote from the session's prompts (optional for your own contracts)")
    s.add_argument("--note")
    s.add_argument("--json", action="store_true")
    session_args(s)
    s.set_defaults(fn=cmd_contracts)

    s = sub.add_parser("ledger", help="evaluate and print the finish ledger for a session")
    s.add_argument("--json", action="store_true")
    session_args(s)
    s.set_defaults(fn=cmd_ledger)

    s = sub.add_parser("gate", help="set the completion gate mode for a session")
    s.add_argument("mode", choices=["annotate", "block", "default"])
    session_args(s)
    s.set_defaults(fn=cmd_gate)

    s = sub.add_parser("verify", help="run .arbiter/verify.yaml commands and record the results")
    s.add_argument("--init", action="store_true", help="write a starter .arbiter/verify.yaml")
    s.add_argument("--trust", action="store_true", help="approve the current verify.yaml (interactive)")
    s.add_argument("--baseline", action="store_true", help="capture the session's test-integrity baseline now")
    s.add_argument("--only", nargs="+", help="run only these command names")
    session_args(s)
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("search", help="search this repository with Arbiter's index (or --symbol / --related)")
    s.add_argument("query", help="search text, a symbol name (--symbol) or a file path (--related)")
    s.add_argument("--symbol", action="store_true", help="find a symbol's definitions and references")
    s.add_argument("--related", action="store_true", help="show a file's imports, importers and tests")
    s.add_argument("--limit", type=int, default=20)
    session_args(s)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("index", help="repository index status (refreshes it) or garbage collection")
    s.add_argument("action", nargs="?", default="status", choices=["status", "gc"])
    s.add_argument("--keep-days", type=float, default=7.0, help="gc: keep unreferenced analysis this long")
    session_args(s)
    s.set_defaults(fn=cmd_index)

    s = sub.add_parser("control", help="manual controls: modules, controller on/off (spec 24)")
    s.add_argument("action", nargs="?", choices=["list", "module", "controller", "clear"])
    s.add_argument("target", nargs="?", help="module name, on|off, or the key to clear")
    s.add_argument("value", nargs="?", help="on|off (for module)")
    s.add_argument("--session", dest="session", help="session to bind to")
    s.add_argument("--this-session", dest="session_scope", action="store_true",
                   help="apply to the current session only (controller, clear)")
    s.add_argument("--reason")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_control)

    s = sub.add_parser("breakers", help="circuit breakers: list, or reset one after inspection")
    s.add_argument("action", nargs="?", default="list", choices=["list", "reset"])
    s.add_argument("name", nargs="?")
    s.add_argument("--reason")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_breakers)

    s = sub.add_parser("semif", help="semantic sensor: status, enable (plan tiers), disable, bench")
    s.add_argument("action", nargs="?", default="status",
                   choices=["status", "enable", "disable", "bench", "export-onnx"])
    s.add_argument("--heldout", action="store_true", help="bench: use the frozen held-out corpus")
    s.add_argument("--model", help="export-onnx: GLiNER2.5-Decide weights folder")
    s.add_argument("--out", help="export-onnx: output folder")
    s.add_argument("--precision", default="w8e4", choices=["w8e4", "w8", "fp16", "fp32"],
                   help="export-onnx: w8e4 (default, CPU), w8, fp16 (GPU) or fp32 (reference)")
    s.add_argument("--yes", action="store_true", help="enable: write the plan to config")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_semif)

    s = sub.add_parser("eval", help="run the evaluation corpus and the numeric gates (spec 20.17)")
    s.add_argument("--corpus", help="corpus directory (default: the packaged corpus)")
    s.add_argument("--faults", action="store_true", help="run the circuit-breaker fault-injection suite instead")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_eval)

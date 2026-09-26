"""Arbiter A/B benchmark harness.

    python -m bench.harness run --agent fake-solution            # free: validates plumbing + scoring
    python -m bench.harness run --agent claude --conditions baseline,full --reps 1   # paid (Opus 5.5)
    python -m bench.harness report D:/ArbiterBench/runs/<run_id>

Each (task, condition, rep) gets a fresh copy of the task repo (a git repo with one commit), a
fresh Arbiter home and daemon, and its own Claude settings/MCP files. Nothing touches the user's
real Arbiter, Claude or Codex configuration: user settings are excluded with
``--setting-sources project`` and MCP servers with ``--strict-mcp-config``. Claude's per-project
transcript folder for each workspace is copied into the results and then removed.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import yaml

from bench import score as scoring
from bench.conditions import CONDITIONS, PRIMARY, Condition

BENCH = Path(__file__).resolve().parent
TASKS = BENCH / "tasks"                       # dev suite (used while tuning Arbiter)
SUITES = {"dev": TASKS, "heldout_v1": BENCH / "tasks_heldout_v1",    # held-out/quality: never tune on these
          "quality_v1": BENCH / "tasks_quality_v1"}
DEFAULT_OUT = Path(os.environ.get("ARBITER_BENCH_OUT", "D:/ArbiterBench/runs"))
DEFAULT_ENCODER = Path.home() / "Downloads" / "GLiNER2.5-Decide-onnx-w8e4"
MODEL = "claude-opus-5-5"
AGENT_PY = shutil.which("python") or sys.executable     # the interpreter the agent's shell finds
ALLOWED = ["Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "LS", "TodoWrite", "mcp__arbiter"]
for _cmd in ("python", "python3", "py", "pytest", "git status", "git diff", "git log", "git show", "ls", "cat",
             "head", "tail", "wc", "find", "grep", "dir", "type", "Get-Content", "Get-ChildItem", "Select-String"):
    ALLOWED += [f"Bash({_cmd}:*)", f"PowerShell({_cmd}:*)"]
PRINT_LOCK = threading.Lock()


def log(msg: str) -> None:
    with PRINT_LOCK:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def claude_exe() -> str:
    found = shutil.which("claude")
    if (found and found.lower().endswith((".cmd", ".ps1"))) or (found and not Path(found).suffix and os.name == "nt"):
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.is_file():
            return str(exe)          # call the binary directly: no cmd.exe quoting of prompts
    if not found:
        raise SystemExit("claude not found on PATH")
    return found


def load_tasks(selected: str, suite: str = "dev") -> list[dict[str, Any]]:
    tasks = []
    for d in sorted(p for p in SUITES[suite].iterdir() if (p / "task.yaml").is_file()):
        t = yaml.safe_load((d / "task.yaml").read_text("utf-8"))
        t["dir"] = d
        tasks.append(t)
    if selected != "all":
        want = set(selected.split(","))
        tasks = [t for t in tasks if t["id"] in want or t["category"] in want]
    return tasks


def git(cwd: Path, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME="bench", GIT_AUTHOR_EMAIL="bench@localhost",
               GIT_COMMITTER_NAME="bench", GIT_COMMITTER_EMAIL="bench@localhost")
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def prepare_workspace(task: dict[str, Any], ws: Path) -> None:
    shutil.copytree(task["dir"] / "repo", ws)
    (ws / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8")
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "initial")


def overlay(src: Path, ws: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, ws, dirs_exist_ok=True)


# ---------------------------------------------------------------- Arbiter per run
class ArbiterRun:
    """A throwaway Arbiter home + daemon for one benchmark run."""

    def __init__(self, cond: Condition, home: Path, encoder: Path | None):
        from arbiter_agent.paths import get_paths

        self.cond = cond
        self.paths = get_paths(home).ensure()
        self.proc: subprocess.Popen[bytes] | None = None
        config = json.loads(json.dumps(cond.config))
        # The bench root carries a .arbiterignore so the user's real daemon (whose transcript watcher
        # sees every Claude transcript) skips benchmark workspaces; the per-run daemon must not.
        config["privacy"] = {"respect_arbiterignore": False}
        if cond.encoder and encoder is not None and encoder.is_dir():
            config.setdefault("semif", {}).update(
                {"shadow": True, "encoder": {"enabled": True, "model": encoder.as_posix(), "runtime": "onnx"}})
        self.paths.config.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(yaml.safe_dump(config) if config else "{}\n", encoding="utf-8")

    def start(self) -> None:
        from arbiter_agent.daemon import lifecycle

        env = {k: v for k, v in os.environ.items() if not k.startswith("ARBITER_")}
        self.proc = subprocess.Popen(lifecycle.daemon_argv(self.paths), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                                     creationflags=0x08000000 if os.name == "nt" else 0)
        deadline = time.monotonic() + 180   # the ONNX encoder loads before the daemon listens
        while time.monotonic() < deadline:
            if lifecycle.is_running(self.paths, timeout=0.5) and self.port():
                return
            time.sleep(0.1)
        raise RuntimeError(f"daemon for {self.paths.root} did not start")

    def port(self) -> int | None:
        from arbiter_agent.daemon.client import read_record

        return (read_record(self.paths).get("http") or {}).get("port")

    def settings(self) -> dict[str, Any]:
        from arbiter_agent.clients.claude_code import hooks as ch
        from arbiter_agent.daemon.auth import read_token

        token = (read_token(self.paths.hook_token_file) or b"").decode()
        groups = ch.hook_groups(int(self.port() or 0), token)
        return {"hooks": {ev: [g] for ev, g in groups.items() if ev in self.cond.hooks}}

    def mcp_config(self) -> dict[str, Any]:
        from arbiter_agent.clients.claude_code import hooks as ch
        from arbiter_agent.clients.client_env import arbiter_command

        return {"mcpServers": {"arbiter": ch.mcp_entry(arbiter_command(self.paths.root))}} if self.cond.mcp else \
            {"mcpServers": {}}

    def metrics(self) -> dict[str, Any]:
        from arbiter_agent.state.store import connect

        if not self.paths.db.exists():
            return {}
        c = connect(self.paths.db, readonly=True)
        try:
            def q(sql: str) -> list[tuple[Any, ...]]:
                try:
                    return c.execute(sql).fetchall()
                except Exception:  # table may not exist in older schemas
                    return []
            ledger = q("SELECT trigger, claim, verdict, mode, blocked, missing_json FROM finish_ledger ORDER BY id")
            events = q("SELECT event_type, COUNT(*) FROM event_log GROUP BY event_type")
            return {
                "events": {e: n for e, n in events},
                "ledger": [{"trigger": r[0], "claim": r[1], "verdict": r[2], "mode": r[3], "blocked": bool(r[4]),
                            "missing": json.loads(r[5] or "[]")} for r in ledger],
                "stop_blocks": sum(1 for r in ledger if r[4]),
                "sensor_rows": (q("SELECT COUNT(*) FROM sensor_log") or [(0,)])[0][0],
                "advisories": (q("SELECT COUNT(*) FROM advisory_decision") or [(0,)])[0][0],
            }
        finally:
            c.close()

    def stop(self) -> None:
        from arbiter_agent.daemon import lifecycle

        try:
            lifecycle.stop(self.paths, timeout=10)
        except Exception:
            pass
        if self.proc is not None:
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---------------------------------------------------------------- agents
def claude_projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def run_claude(task: dict[str, Any], ws: Path, rundir: Path, arb: ArbiterRun | None, args: argparse.Namespace,
               token: str) -> dict[str, Any]:
    exe = claude_exe()
    mcp_file = rundir / "mcp.json"
    mcp_file.write_text(json.dumps(arb.mcp_config() if arb else {"mcpServers": {}}), encoding="utf-8")
    settings_file = rundir / "settings.json"
    settings_file.write_text(json.dumps(arb.settings() if arb and arb.cond.hooks else {}), encoding="utf-8")
    sid = str(uuid.uuid4())
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARBITER_") and k not in ("CODEX_HOME",)}
    turns: list[dict[str, Any]] = []
    for i, prompt in enumerate(task["prompts"]):
        argv = [exe, "-p", "--model", args.model, "--output-format", "json", "--setting-sources", "project",
                "--settings", str(settings_file), "--strict-mcp-config", "--mcp-config", str(mcp_file),
                "--permission-mode", "acceptEdits", "--max-budget-usd", str(args.budget),
                *(["--effort", args.effort] if args.effort else []),
                "--allowedTools", *ALLOWED]
        argv += ["--session-id", sid] if i == 0 else ["--resume", sid]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(argv, input=prompt, cwd=ws, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=args.timeout)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            out, err, code = (exc.stdout or ""), "timeout", None
            out = out if isinstance(out, str) else ""
        try:
            res = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
        except (ValueError, IndexError):
            res = {}
        turns.append({"exit": code, "wall_s": round(time.monotonic() - t0, 1), "stderr": err[-2000:],
                      "result": res.get("result"), "cost_usd": res.get("total_cost_usd"),
                      "num_turns": res.get("num_turns"), "duration_ms": res.get("duration_ms"),
                      "usage": res.get("usage"), "is_error": res.get("is_error"), "subtype": res.get("subtype"),
                      "permission_denials": res.get("permission_denials") or []})
        if code is None:
            break
    # Keep the transcripts, then remove Claude's per-project folder for this throwaway workspace.
    projects = claude_projects_dir()
    if projects.is_dir():
        for d in projects.iterdir():
            if d.is_dir() and token in d.name:
                shutil.copytree(d, rundir / "transcript", dirs_exist_ok=True)
                shutil.rmtree(d, ignore_errors=True)
    return {"turns": turns, "final_messages": [str(t.get("result") or "") for t in turns],
            "cost_usd": round(sum(t["cost_usd"] or 0 for t in turns), 4),
            "num_turns": sum(t["num_turns"] or 0 for t in turns),
            "agent_wall_s": round(sum(t["wall_s"] for t in turns), 1),
            "output_tokens": sum((t["usage"] or {}).get("output_tokens", 0) for t in turns),
            "input_tokens": sum(((t["usage"] or {}).get("input_tokens", 0)
                                 + (t["usage"] or {}).get("cache_read_input_tokens", 0)
                                 + (t["usage"] or {}).get("cache_creation_input_tokens", 0)) for t in turns),
            "permission_denials": sum(len(t["permission_denials"]) for t in turns),
            "errors": sum(1 for t in turns if t["exit"] != 0 or t["is_error"])}


def run_fake(task: dict[str, Any], ws: Path, rundir: Path, arb: ArbiterRun | None, variant: str) -> dict[str, Any]:
    """A scripted agent: applies the reference (or sloppy, or no) change and, when the condition has
    hooks, drives the same hook events Claude Code would, so the gate wiring is exercised for free."""
    from arbiter_agent.clients.fake_host.host import FakeHost

    host = None
    if arb and arb.cond.hooks:
        host = FakeHost(arb.paths, client="claude_code", transport="http", cwd=str(ws),
                        transcript_dir=rundir, autostart=False)
        host.session_start()
    for prompt in task["prompts"]:
        if host:
            host.prompt(prompt)
    if variant in ("solution", "sloppy"):
        overlay(task["dir"] / variant, ws)
        if host:
            for i, f in enumerate(sorted(p for p in (task["dir"] / variant).rglob("*") if p.is_file())):
                target = ws / f.relative_to(task["dir"] / variant)
                host.send_hook("PostToolUse", {**host._common(), "tool_name": "Write",
                                               "tool_input": {"file_path": str(target)},
                                               "tool_response": {"filePath": str(target)},
                                               "tool_use_id": f"write_{i}_{uuid.uuid4().hex[:6]}"})
    if host and arb.cond.mcp and variant == "solution":
        # Record one contract per prompt the way the agent is asked to (verbatim quote + test recipe).
        import re

        for prompt in task["prompts"]:
            quotes = [q.strip().lstrip("- ").rstrip(":;") for q in re.split(r"(?<=[.!?])\s+|\n", prompt)]
            text, is_err = host.mcp_tool("arbiter_contract_propose", {"contracts": [
                {"text": q, "quotes": [q], "recipe": {"type": "test_command", "command": "python -m pytest -q"}}
                for q in quotes if len(q) > 3], "session_id": host.session_id})
            with (rundir / "fake_contract.txt").open("a", encoding="utf-8") as f:
                f.write(f"{is_err} {text}\n")
    if host:
        tests = subprocess.run([AGENT_PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if variant == "solution":
            host.tool("python -m pytest -q", tests.stdout[-4000:], tests.returncode)
    msg = "Done. All requirements are implemented and the tests pass."
    blocked = []
    if host:
        for i in range(4):
            call = host.stop(msg, stop_hook_active=i > 0)
            blocked.append(call.response.get("decision") == "block")
            if not blocked[-1]:
                break
        host.session_end()
        host.close()
    return {"turns": [], "final_messages": [msg], "cost_usd": 0.0, "num_turns": 0, "agent_wall_s": 0.0,
            "output_tokens": 0, "input_tokens": 0, "permission_denials": 0, "errors": 0,
            "fake_stop_blocked": blocked}


# ---------------------------------------------------------------- one run
def run_one(task: dict[str, Any], cond: Condition, rep: int, out: Path, args: argparse.Namespace) -> dict[str, Any]:
    token = uuid.uuid4().hex[:8]
    rundir = out / cond.name / task["id"] / f"rep{rep}"
    if (rundir / "result.json").is_file() and not args.force:
        return json.loads((rundir / "result.json").read_text("utf-8"))
    if rundir.exists():
        shutil.rmtree(rundir, ignore_errors=True)
    rundir.mkdir(parents=True)
    ws = out.parent.parent / "ws" / f"{task['id']}-{cond.name}-{rep}-{token}"   # short path, unique token
    prepare_workspace(task, ws)
    arb = ArbiterRun(cond, rundir / "arbiter-home", args.encoder) if cond.uses_arbiter else None
    t0 = time.monotonic()
    record: dict[str, Any] = {"task": task["id"], "category": task["category"], "condition": cond.name,
                              "rep": rep, "agent": args.agent, "model": args.model if args.agent == "claude" else None,
                              "workspace": str(ws)}
    try:
        if arb:
            arb.start()
        if args.agent == "claude":
            agent = run_claude(task, ws, rundir, arb, args, token)
        else:
            agent = run_fake(task, ws, rundir, arb, args.agent.removeprefix("fake-"))
        record["wall_s"] = round(time.monotonic() - t0, 1)
        record["agent"] = args.agent
        record.update({k: v for k, v in agent.items()})
        record["arbiter"] = arb.metrics() if arb else None
    except Exception:
        record["harness_error"] = traceback.format_exc()
    finally:
        if arb:
            arb.stop()
    try:
        record["score"] = scoring.score(task["dir"], task, ws, rundir / "score-scratch", AGENT_PY,
                                        record.get("final_messages") or [])
        shutil.rmtree(rundir / "score-scratch", ignore_errors=True)
    except Exception:
        record["score_error"] = traceback.format_exc()
    # Keep the final diff for review, then drop the workspace.
    try:
        git(ws, "add", "-A")
        diff = subprocess.run(["git", "diff", "--cached", "--stat", "-p"], cwd=ws, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
        (rundir / "final.diff").write_text(diff, encoding="utf-8")
    except Exception:
        pass
    if not args.keep_workspaces:
        shutil.rmtree(ws, ignore_errors=True)
    (rundir / "result.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    s = record.get("score") or {}
    log(f"{cond.name:13s} {task['id']:22s} rep{rep}  pass={s.get('full_pass')} cov={s.get('coverage')} "
        f"tamper={s.get('tampered')} cost=${record.get('cost_usd')} turns={record.get('num_turns')} "
        f"wall={record.get('wall_s')}s" + ("  HARNESS ERROR" if "harness_error" in record else ""))
    return record


def real_install_sessions() -> int | None:
    """Sessions the user's real Arbiter install has recorded for benchmark workspaces (read-only).
    Must stay unchanged across a run: benchmark runs exclude user settings and MCP servers."""
    from arbiter_agent.paths import get_paths
    from arbiter_agent.state.store import connect

    saved = os.environ.pop("ARBITER_HOME", None)
    try:
        db = get_paths(None).db
    finally:
        if saved is not None:
            os.environ["ARBITER_HOME"] = saved
    if not db.exists():
        return None
    try:
        c = connect(db, readonly=True)
        try:
            return int(c.execute("SELECT COUNT(*) FROM client_session WHERE transcript_path LIKE '%ArbiterBench%' "
                                 "OR transcript_path LIKE '%bench-ws-%'").fetchone()[0])
        finally:
            c.close()
    except Exception:
        return None


def cmd_run(args: argparse.Namespace) -> int:
    tasks = load_tasks(args.tasks, args.suite)
    conds = [CONDITIONS[c] for c in (args.conditions.split(",") if args.conditions != "primary" else PRIMARY)]
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S") + f"-{args.agent}"
    out = args.out / run_id
    out.mkdir(parents=True, exist_ok=True)
    ignore = out.parent.parent / ".arbiterignore"      # covers <root>/ws and <root>/runs
    if not ignore.is_file():
        ignore.write_text("# Arbiter benchmark workspaces: keep them out of the real Arbiter install.\n",
                          encoding="utf-8")
    jobs = [(t, c, r) for r in range(args.reps) for t in tasks for c in conds]
    (out / "run.json").write_text(json.dumps({
        "run_id": run_id, "agent": args.agent, "model": args.model, "reps": args.reps,
        "conditions": [c.name for c in conds], "suite": args.suite, "tasks": [t["id"] for t in tasks],
        "budget_usd": args.budget, "effort": args.effort,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv}, indent=2), encoding="utf-8")
    log(f"run {run_id}: {len(tasks)} tasks x {len(conds)} conditions x {args.reps} reps = {len(jobs)} runs "
        f"(agent {args.agent}, {args.jobs} parallel) -> {out}")
    before = real_install_sessions()
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = [ex.submit(run_one, t, c, r, out, args) for t, c, r in jobs]
        for f in cf.as_completed(futures):
            f.result()
    after = real_install_sessions()
    leaked = None if before is None or after is None else after - before
    meta = json.loads((out / "run.json").read_text("utf-8"))
    meta["real_install_bench_sessions_added"] = leaked
    (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if leaked:
        log(f"WARNING: the real Arbiter install recorded {leaked} benchmark session(s); isolation failed")
    from bench import report

    text = report.write(out)
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.harness")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--agent", default="fake-solution",
                   choices=["claude", "fake-solution", "fake-sloppy", "fake-noop"])
    r.add_argument("--conditions", default="primary", help="comma list, or 'primary' (baseline,full)")
    r.add_argument("--tasks", default="all", help="comma list of task ids or categories, or 'all'")
    r.add_argument("--suite", default="dev", choices=sorted(SUITES))
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="claude --effort for every run (default: the CLI default)")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--model", default=MODEL)
    r.add_argument("--budget", type=float, default=4.0, help="per-turn --max-budget-usd cap")
    r.add_argument("--timeout", type=int, default=1500, help="seconds per claude turn")
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    r.add_argument("--run-id")
    r.add_argument("--encoder", type=Path, default=DEFAULT_ENCODER)
    r.add_argument("--force", action="store_true", help="redo runs that already have a result")
    r.add_argument("--keep-workspaces", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("run_dir", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    from bench import report

    print(report.write(a.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

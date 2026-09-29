"""Arbiter A/B benchmark harness.

    python -m bench.harness run --agent fake-solution            # free: validates plumbing + scoring
    python -m bench.harness run --agent claude --conditions baseline,full --reps 1   # paid (Opus 5.5)
    python -m bench.harness run --agent codex --conditions baseline,full             # ChatGPT plan (gpt-6-luna)
    python -m bench.harness run --agent pi --conditions baseline,full                # Pi, ChatGPT plan (gpt-6-luna)
    python -m bench.harness report D:/ArbiterBench/runs/<run_id>

Each (task, condition, rep) gets a fresh copy of the task repo (a git repo with one commit), a
fresh Arbiter home and daemon, and its own Claude settings/MCP files. Nothing touches the user's
real Arbiter, Claude or Codex configuration: user settings are excluded with
``--setting-sources project`` and MCP servers with ``--strict-mcp-config``. Claude's per-project
transcript folder for each workspace is copied into the results and then removed.

Codex runs with a benchmark-only Codex home (ARBITER_BENCH_CODEX_HOME, default D:/ArbiterBench/codex-home,
its own sign-in) keep their sessions there and resume them for follow-up prompts. Without one:
Codex runs use ``codex exec --ignore-user-config --ephemeral``: the real CODEX_HOME supplies only the
sign-in (so a refreshed token is never written to a copy) and hooks.json (Arbiter's MCP-tool hooks,
enabled for Arbiter conditions with ``--dangerously-bypass-hook-trust``; in baseline they stay
untrusted and their MCP server is undefined). The ``arbiter`` MCP server is pointed at the run's own
home with ``-c``. Ephemeral sessions can't be resumed, so multi-prompt tasks are skipped for Codex.

Pi runs use the real ~/.pi/agent only for its sign-in and model catalog: extensions, skills, prompt
templates and themes are off (``--no-*``), sessions go to the run folder, and Arbiter conditions
load Arbiter's Pi extension (clients/pi/arbiter.ts) with ARBITER_HOME set to the run's home. Pi has
no MCP support, so Arbiter in Pi is hooks only: context pack, post-edit test runs, completion gate.
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
          "quality_v1": BENCH / "tasks_quality_v1", "largerepo_v1": BENCH / "tasks_largerepo_v1",
          "realrepo_v1": BENCH / "tasks_realrepo_v1",     # sympy 1.14.0, 3-prompt sessions (make_realrepo_v1)
          "realrepo_js_v1": BENCH / "tasks_realrepo_js_v1",   # date-fns 4.1.0, written before any run on it
          "hard_v1": BENCH / "tasks_hard_v1"}   # planted bugs + a fast-algorithm feature (routing)
DEFAULT_OUT = Path(os.environ.get("ARBITER_BENCH_OUT", "D:/ArbiterBench/runs"))
DEFAULT_ENCODER = Path.home() / "Downloads" / "GLiNER2.5-Decide-onnx-w8e4"
MODEL = "claude-opus-5-5"
CODEX_MODEL = "gpt-6-luna"
CODEX = os.environ.get("ARBITER_BENCH_CODEX", "D:/ArbiterBench/bin/codex.exe")   # the desktop app's bundled engine
# Cost index for Codex runs, in uncached-input-token units: cached input 0.1x and output 8x input, the
# ratios of OpenAI's current API price lists. Codex bills this account through a ChatGPT plan, so
# this compares conditions; it is not a price.
CODEX_CACHED_RATIO, CODEX_OUTPUT_RATIO = 0.1, 8.0
PI_INSTALL = Path(os.environ.get("ARBITER_BENCH_PI_INSTALL") or Path.home() / ".pi" / "agent" / "install")
PI_PROVIDER = "openai-codex"            # Pi's ChatGPT sign-in
PI_THINKING = "medium"                  # gpt-6-luna's default in Codex, so the two harnesses compare
AGENT_PY = shutil.which("python") or sys.executable     # the interpreter the agent's shell finds
# Shell commands are allowed outright. A narrow allowlist denied compound commands such as
# `git ls-files; Get-Content ...`; baseline agents lost calls to those denials while orienting, which
# inflated Arbiter's measured gain (the context pack removed the orientation step and the denials).
ALLOWED = ["Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "LS", "TodoWrite", "mcp__arbiter", "Bash",
           "PowerShell"]
WARMUP_PROMPT = "Reply with just the word OK. Don't use any tools."
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


def repo_dir(task: dict[str, Any]) -> Path:
    """The task's starting tree: its own ``repo`` folder, or a shared checkout (``repo_src``)."""
    return Path(task["repo_src"]) if task.get("repo_src") else task["dir"] / "repo"


def task_python(task: dict[str, Any]) -> str:
    """The interpreter the agent's shell, Arbiter's test runs and the scorer use for this task."""
    return str(task.get("python") or AGENT_PY)


def with_task_path(task: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    """``python`` on PATH resolves to the task's interpreter (a virtualenv with the repo's deps)."""
    if task.get("python"):
        env = {**env, "PATH": str(Path(task["python"]).parent) + os.pathsep + env.get("PATH", "")}
    return env


def prepare_workspace(task: dict[str, Any], ws: Path) -> None:
    shutil.copytree(repo_dir(task), ws, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                                                     "node_modules"))
    # Append, don't replace: a repo's own .gitignore (date-fns: node_modules) must keep working.
    gi = ws / ".gitignore"
    old = gi.read_text("utf-8", errors="replace") if gi.is_file() else ""
    gi.write_text(old + ("" if old.endswith("\n") or not old else "\n") + "__pycache__/\n.pytest_cache/\n*.pyc\n"
                  + ("node_modules\n" if task.get("link") else ""), encoding="utf-8")
    scoring.link_all(task, ws)
    overlay(task["dir"] / "setup", ws)        # a task's starting changes (a planted bug), part of the base commit
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "initial")


def overlay(src: Path, ws: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, ws, dirs_exist_ok=True)


# ---------------------------------------------------------------- Arbiter per run
class ArbiterRun:
    """A throwaway Arbiter home + daemon for one benchmark run."""

    def __init__(self, cond: Condition, home: Path, encoder: Path | None, known_model: str | None = None,
                 client: str = "claude_code"):
        from arbiter_agent.paths import get_paths

        self.cond = cond
        self.paths = get_paths(home).ensure()
        if known_model:
            # Steady state: an installed daemon remembers each client's last model, so only a client's
            # very first session starts without one. A fresh per-run home would make every run a first
            # session; seeding the model measures ordinary sessions (--first-session to skip).
            self.paths.data.mkdir(parents=True, exist_ok=True)
            (self.paths.data / "models.json").write_text(json.dumps({client: known_model}), encoding="utf-8")
        self.proc: subprocess.Popen[bytes] | None = None
        self.env_extra: dict[str, str] = {}
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
        env.update(self.env_extra)
        self.proc = subprocess.Popen(lifecycle.daemon_argv(self.paths), stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                                     creationflags=0x08000000 if os.name == "nt" else 0)
        deadline = time.monotonic() + 180   # the ONNX encoder loads before the daemon listens
        while time.monotonic() < deadline:
            if lifecycle.is_running(self.paths, timeout=0.5) and self.port():
                return
            time.sleep(0.1)
        raise RuntimeError(f"daemon for {self.paths.root} did not start")

    def index(self, cwd: Path, timeout: float = 300.0) -> dict[str, Any]:
        """Build the repo index now. An installed daemon indexes a repo once, in the background, and
        keeps it; a fresh per-run home would otherwise index 2,000 files during the first prompt."""
        from arbiter_agent.daemon.client import DaemonClient

        with DaemonClient(self.paths) as c:
            return c.request("retrieve", {"op": "index", "cwd": str(cwd)}, timeout=timeout)

    def port(self) -> int | None:
        from arbiter_agent.daemon.client import read_record

        return (read_record(self.paths).get("http") or {}).get("port")

    def settings(self) -> dict[str, Any]:
        from arbiter_agent.clients.claude_code import hooks as ch
        from arbiter_agent.daemon.auth import read_token

        token = (read_token(self.paths.hook_token_file) or b"").decode()
        groups = ch.hook_groups(int(self.port() or 0), token)
        out: dict[str, Any] = {"hooks": {ev: [g] for ev, g in groups.items() if ev in self.cond.hooks}}
        if self.cond.slim:
            from arbiter_agent.setup.slim import settings_for

            out.update(settings_for(self.cond.slim))
        return out

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
    env = with_task_path(task, {k: v for k, v in os.environ.items()
                                if not k.startswith("ARBITER_") and k not in ("CODEX_HOME",)})
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


def codex_bench_home() -> Path | None:
    """A Codex home used only by the benchmark (its own sign-in), or None. With one, Codex runs keep
    their sessions there, so multi-prompt tasks can resume; without, they run ephemeral against the
    real CODEX_HOME and multi-prompt tasks are skipped. It carries Arbiter's standard Codex hooks,
    which only Arbiter conditions enable (--dangerously-bypass-hook-trust)."""
    home = Path(os.environ.get("ARBITER_BENCH_CODEX_HOME", "D:/ArbiterBench/codex-home"))
    if not (home / "auth.json").is_file():
        return None
    from arbiter_agent.clients.codex import hooks as codex_hooks

    hooks = home / "hooks.json"
    want = json.dumps({"hooks": {ev: [g] for ev, g in codex_hooks.hook_groups().items()}}, indent=2)
    if not hooks.is_file() or hooks.read_text("utf-8") != want:
        hooks.write_text(want, encoding="utf-8")
    return home


def run_codex(task: dict[str, Any], ws: Path, rundir: Path, arb: ArbiterRun | None,
              args: argparse.Namespace) -> dict[str, Any]:
    from arbiter_agent.clients.client_env import arbiter_command

    home = codex_bench_home()
    flags = ["--ignore-user-config", "--json", "-m", args.model, "--dangerously-bypass-approvals-and-sandbox",
             "--skip-git-repo-check", *(["-c", f'model_reasoning_effort="{args.effort}"'] if args.effort else [])]
    if home is None:
        flags.append("--ephemeral")
    if arb and arb.cond.uses_arbiter:
        cmd = arbiter_command(arb.paths.root)
        flags += ["-c", f"mcp_servers.arbiter.command={json.dumps(cmd[0])}",
                  "-c", f"mcp_servers.arbiter.args={json.dumps(cmd[1:])}"]
        if arb.cond.hooks:
            flags.append("--dangerously-bypass-hook-trust")
    for setting in arb.cond.codex_config if arb else ():
        flags += ["-c", setting]
    flags += json.loads(os.environ.get("ARBITER_BENCH_CODEX_ARGS", "[]"))   # e.g. a mock model provider
    env = with_task_path(task, {k: v for k, v in os.environ.items() if not k.startswith("ARBITER_")})
    if home is not None:
        env["CODEX_HOME"] = str(home)
    turns: list[dict[str, Any]] = []
    messages: list[str] = []
    thread: str | None = None
    for i, prompt in enumerate(task["prompts"]):
        argv = ([CODEX, "exec", *flags, "-C", str(ws), prompt] if i == 0
                else [CODEX, "exec", "resume", *flags, str(thread), prompt])
        t0 = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=ws, env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=args.timeout, stdin=subprocess.DEVNULL)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            err, code = "timeout", None
        with (rundir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(out)
        usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
        tools = errors = 0
        last = ""
        for line in out.splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "thread.started" and not thread:
                thread = o.get("thread_id")
            elif o.get("type") == "turn.completed":
                for k in usage:
                    usage[k] += int((o.get("usage") or {}).get(k) or 0)
            elif o.get("type") == "turn.failed":
                errors += 1
            elif o.get("type") == "item.completed":
                item = o.get("item") or {}
                if item.get("type") == "agent_message":
                    last = str(item.get("text") or "") or last
                elif item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                    tools += 1
        messages.append(last)
        # `codex exec resume` reports the thread's cumulative usage, not the turn's: summing turns counted
        # prompt 1 three times in a 3-prompt session (found 2026-09-28; codex runs before this doubled).
        thread_usage = dict(usage)
        if turns and all(usage[k] >= turns[-1]["thread_usage"][k] for k in usage):
            usage = {k: usage[k] - turns[-1]["thread_usage"][k] for k in usage}
        turns.append({"exit": code, "wall_s": round(time.monotonic() - t0, 1), "stderr": err[-2000:],
                      "usage": usage, "thread_usage": thread_usage, "tools": tools, "errors": errors})
        if code is None or not thread:
            break
    tot = {k: sum(t["usage"][k] for t in turns) for k in turns[0]["usage"]}
    uncached = tot["input_tokens"] - tot["cached_input_tokens"]
    index = uncached + CODEX_CACHED_RATIO * tot["cached_input_tokens"] + CODEX_OUTPUT_RATIO * tot["output_tokens"]
    return {"turns": turns, "final_messages": messages, "cost_usd": None, "cost_index": round(index),
            "num_turns": sum(t["tools"] for t in turns), "agent_wall_s": round(sum(t["wall_s"] for t in turns), 1),
            "output_tokens": tot["output_tokens"], "reasoning_tokens": tot["reasoning_output_tokens"],
            "input_tokens": tot["input_tokens"], "cached_input_tokens": tot["cached_input_tokens"],
            "thread_id": thread, "codex_home": str(home) if home else None, "permission_denials": 0,
            "errors": sum(t["errors"] + (1 if t["exit"] != 0 else 0) for t in turns)}


def pi_cli() -> list[str]:
    """node + Pi's CLI script (not pi.cmd: cmd.exe would mangle multi-line prompts)."""
    install = PI_INSTALL
    version = (install / "current-version").read_text("utf-8").strip()
    pkg = install / "releases" / version / "node_modules" / "@earendil-works" / "pi-coding-agent"
    meta = json.loads((pkg / "package.json").read_text("utf-8"))
    binp = meta["bin"] if isinstance(meta["bin"], str) else meta["bin"]["pi"]
    return [shutil.which("node") or "node", str((pkg / binp).resolve())]


def run_pi(task: dict[str, Any], ws: Path, rundir: Path, arb: ArbiterRun | None,
           args: argparse.Namespace) -> dict[str, Any]:
    from arbiter_agent.clients.pi import EXTENSION

    sid = str(uuid.uuid4())
    base = [*pi_cli(), "--mode", "json", "-p", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
            "--session-dir", str(rundir / "pi-sessions"), "--session-id", sid,
            "--model", f"{PI_PROVIDER}/{args.model}", "--thinking", args.effort or PI_THINKING]
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARBITER_")}
    env.update({"PI_MANAGED_INSTALL_ROOT": str(PI_INSTALL), "PI_OFFLINE": "1", "PI_TELEMETRY": "0"})
    env = with_task_path(task, env)
    if arb and arb.cond.hooks:
        base += ["-e", str(EXTENSION)]
        env["ARBITER_HOME"] = str(arb.paths.root)
        env.update(dict(arb.cond.agent_env))
    turns: list[dict[str, Any]] = []
    messages: list[str] = []
    for prompt in task["prompts"]:
        t0 = time.monotonic()
        try:
            proc = subprocess.run([*base, "--", prompt], cwd=ws, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=args.timeout, stdin=subprocess.DEVNULL)
            out, err, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            err, code = "timeout", None
        with (rundir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(out)
        usage = {"input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "reasoning": 0}
        tools = errors = 0
        last = ""
        for line in out.splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") == "message_end" and (o.get("message") or {}).get("role") == "assistant":
                m = o["message"]
                for k in usage:
                    usage[k] += int((m.get("usage") or {}).get(k) or 0)
                text = "".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text")
                last = text or last
                if m.get("stopReason") == "error":
                    errors += 1
            elif o.get("type") == "tool_execution_end":
                tools += 1
        messages.append(last)
        turns.append({"exit": code, "wall_s": round(time.monotonic() - t0, 1), "stderr": err[-2000:], "usage": usage,
                      "tools": tools, "errors": errors})
        if code is None:
            break
    tot = {k: sum(t["usage"][k] for t in turns) for k in turns[0]["usage"]}
    index = (tot["input"] + tot["cacheWrite"] + CODEX_CACHED_RATIO * tot["cacheRead"]
             + CODEX_OUTPUT_RATIO * tot["output"])
    return {"turns": turns, "final_messages": messages, "cost_usd": None, "cost_index": round(index),
            "num_turns": sum(t["tools"] for t in turns), "agent_wall_s": round(sum(t["wall_s"] for t in turns), 1),
            "output_tokens": tot["output"], "reasoning_tokens": tot["reasoning"],
            "input_tokens": tot["input"] + tot["cacheRead"] + tot["cacheWrite"],
            "cached_input_tokens": tot["cacheRead"],
            "permission_denials": 0, "errors": sum(t["errors"] + (1 if t["exit"] != 0 else 0) for t in turns)}


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
        targets: list[str] = []
        if task.get("repo_src"):        # a real repo's full suite runs for many minutes: its related tests only
            from arbiter_agent.completion.auto_test import related_tests

            targets = related_tests(ws, [str(f.relative_to(task["dir"] / "solution"))
                                         for f in (task["dir"] / "solution").rglob("*.py")])
        tests = subprocess.run([task_python(task), "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets], cwd=ws,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        if variant == "solution":
            host.tool(" ".join(["python -m pytest -q", *targets]), tests.stdout[-4000:], tests.returncode)
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
    for rel, text in cond.workspace_files:
        # Condition-owned files (an AGENTS.md): untracked and excluded, so diffs and scoring don't see them.
        (ws / rel).write_text(text, encoding="utf-8")
        with (ws / ".git" / "info" / "exclude").open("a", encoding="utf-8") as f:
            f.write(f"/{rel}\n")
    real = args.agent in ("claude", "codex", "pi")
    known = args.model if (real and not getattr(args, "first_session", False)) else None
    client = {"codex": "codex", "pi": "pi"}.get(args.agent, "claude_code")
    arb = ArbiterRun(cond, rundir / "arbiter-home", args.encoder, known, client) if cond.uses_arbiter else None
    t0 = time.monotonic()
    record: dict[str, Any] = {"task": task["id"], "category": task["category"], "condition": cond.name,
                              "rep": rep, "agent": args.agent, "model": args.model if real else None,
                              "workspace": str(ws)}
    try:
        if arb:
            arb.env_extra = {k: v for k, v in with_task_path(task, dict(os.environ)).items() if k == "PATH"}
            arb.start()
            if task.get("pre_index"):
                t1 = time.monotonic()
                record["pre_index"] = {**arb.index(ws), "wall_s": round(time.monotonic() - t1, 1)}
        if args.agent == "claude":
            agent = run_claude(task, ws, rundir, arb, args, token)
        elif args.agent == "codex":
            agent = run_codex(task, ws, rundir, arb, args)
        elif args.agent == "pi":
            agent = run_pi(task, ws, rundir, arb, args)
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
        record["score"] = scoring.score(task["dir"], task, ws, rundir / "score-scratch", task_python(task),
                                        record.get("final_messages") or [])
        shutil.rmtree(rundir / "score-scratch", ignore_errors=True)
    except Exception:
        record["score_error"] = traceback.format_exc()
    # Keep the final diff for review, then drop the workspace.
    try:
        git(ws, "add", "-A")
        diff = subprocess.run(["git", "diff", "--cached", "--stat", "-p"], cwd=ws, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
        (rundir / "final.diff").write_text(diff, encoding="utf-8", newline="\n")
    except Exception:
        pass
    if not args.keep_workspaces:
        shutil.rmtree(ws, ignore_errors=True)
    (rundir / "result.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    s = record.get("score") or {}
    cost = f"{record['cost_index']}idx" if record.get("cost_index") is not None else f"${record.get('cost_usd')}"
    log(f"{cond.name:13s} {task['id']:22s} rep{rep}  pass={s.get('full_pass')} cov={s.get('coverage')} "
        f"tamper={s.get('tampered')} cost={cost} turns={record.get('num_turns')} "
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


def real_codex_sessions() -> int | None:
    """Codex sessions in the user's real Arbiter install (read-only). Benchmark Codex runs define
    their own ``arbiter`` MCP server, so this should only grow with the user's own Codex use."""
    from arbiter_agent.paths import get_paths
    from arbiter_agent.state.store import connect

    saved = os.environ.pop("ARBITER_HOME", None)
    try:
        db = get_paths(None).db
    finally:
        if saved is not None:
            os.environ["ARBITER_HOME"] = saved
    try:
        c = connect(db, readonly=True)
        try:
            return int(c.execute("SELECT COUNT(*) FROM client_session WHERE client_id = 'codex'").fetchone()[0])
        finally:
            c.close()
    except Exception:
        return None


def cmd_run(args: argparse.Namespace) -> int:
    if args.model is None:
        args.model = CODEX_MODEL if args.agent in ("codex", "pi") else MODEL
    tasks = load_tasks(args.tasks, args.suite)
    skipped = []
    if args.agent == "codex" and codex_bench_home() is None:
        skipped = [t["id"] for t in tasks if len(t["prompts"]) > 1]
        tasks = [t for t in tasks if len(t["prompts"]) == 1]
        if skipped:
            log(f"codex: skipping multi-prompt task(s) {', '.join(skipped)} (ephemeral sessions can't resume)")
    conds = [CONDITIONS[c] for c in (args.conditions.split(",") if args.conditions != "primary" else PRIMARY)]
    run_id = args.run_id or time.strftime("%Y%m%d-%H%M%S") + f"-{args.agent}"
    out = args.out / run_id
    out.mkdir(parents=True, exist_ok=True)
    ignore = out.parent.parent / ".arbiterignore"      # covers <root>/ws and <root>/runs
    # Only in a bench root (<root>/runs/<run_id>): `--out <temp dir>` once put one in %TEMP%, and every
    # test workspace under it was then ignored by Arbiter.
    if out.parent.name == "runs" and not ignore.is_file():
        ignore.write_text("# Arbiter benchmark workspaces: keep them out of the real Arbiter install.\n",
                          encoding="utf-8")
    jobs = [(t, c, r) for r in range(args.reps) for t in tasks for c in conds]
    (out / "run.json").write_text(json.dumps({
        "run_id": run_id, "agent": args.agent, "model": args.model, "reps": args.reps,
        "conditions": [c.name for c in conds], "suite": args.suite, "tasks": [t["id"] for t in tasks],
        "budget_usd": args.budget, "effort": args.effort, "first_session": args.first_session,
        "skipped_tasks": skipped,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv}, indent=2), encoding="utf-8")
    log(f"run {run_id}: {len(tasks)} tasks x {len(conds)} conditions x {args.reps} reps = {len(jobs)} runs "
        f"(agent {args.agent}, {args.jobs} parallel) -> {out}")
    before = real_install_sessions()
    codex_before = real_codex_sessions() if args.agent == "codex" else None
    if args.agent == "codex":   # the hooks Arbiter conditions run with come from the real hooks.json
        hooks = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"
        meta = json.loads((out / "run.json").read_text("utf-8"))
        meta["codex_hooks_json"] = json.loads(hooks.read_text("utf-8")) if hooks.is_file() else None
        (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if args.agent == "claude" and args.warmup:
        # One throwaway call per condition writes Claude Code's system-prompt prefix into the prompt
        # cache, so no measured run pays the cold-cache write (~30k tokens; it landed on two baseline
        # runs of the first Sonnet batch and hid a 17% loss). Not scored, not in the report.
        warm = {**tasks[0], "prompts": [WARMUP_PROMPT]}
        spent = 0.0
        for c in conds:
            rec = run_one(warm, c, 0, out / "_warmup", args)
            spent += rec.get("cost_usd") or 0.0
        meta = json.loads((out / "run.json").read_text("utf-8"))
        meta["warmup_cost_usd"] = round(spent, 4)
        (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = [ex.submit(run_one, t, c, r, out, args) for t, c, r in jobs]
        for f in cf.as_completed(futures):
            f.result()
    after = real_install_sessions()
    leaked = None if before is None or after is None else after - before
    meta = json.loads((out / "run.json").read_text("utf-8"))
    meta["real_install_bench_sessions_added"] = leaked
    if codex_before is not None:
        codex_after = real_codex_sessions()
        meta["real_install_codex_sessions_added"] = None if codex_after is None else codex_after - codex_before
    (out / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if leaked:
        log(f"WARNING: the real Arbiter install recorded {leaked} benchmark session(s); isolation failed")
    from bench import report

    text = report.write(out)
    print(text)
    return 0


def cmd_rescore(run_dir: Path) -> int:
    """Re-score finished runs with the current scorer: each final workspace is rebuilt from the task
    repo plus the run's saved final.diff (workspaces themselves are deleted after a run)."""
    import tempfile

    meta = json.loads((run_dir / "run.json").read_text("utf-8"))
    tasks = {t["id"]: t for t in load_tasks("all", meta.get("suite", "dev"))}
    n = 0
    for res in sorted(run_dir.glob("*/*/rep*/result.json")):
        record = json.loads(res.read_text("utf-8"))
        diff = res.parent / "final.diff"
        task = tasks.get(record["task"])
        if task is None or not diff.is_file():
            continue
        with tempfile.TemporaryDirectory(prefix="rescore-") as tmp:
            ws = Path(tmp) / "ws"
            prepare_workspace(task, ws)
            text = diff.read_bytes().decode("utf-8").replace("\r\n", "\n")   # older diffs were saved as CRLF
            if text.strip():
                patch = Path(tmp) / "final.patch"
                patch.write_bytes(text.encode("utf-8"))
                subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=ws, check=True,
                               capture_output=True)
            before = record.get("score") or {}
            record["score"] = scoring.score(task["dir"], task, ws, Path(tmp) / "scratch", task_python(task),
                                            record.get("final_messages") or [])
        changed = {k: (before.get(k), record["score"][k]) for k in ("full_pass", "coverage", "tampered")
                   if before.get(k) != record["score"][k]}
        if changed:
            log(f"rescored {record['condition']}/{record['task']}/rep{record['rep']}: {changed}")
        res.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        n += 1
    log(f"rescored {n} run(s) in {run_dir}")
    from bench import report

    report.write(run_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.harness")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--agent", default="fake-solution",
                   choices=["claude", "codex", "pi", "fake-solution", "fake-sloppy", "fake-noop"])
    r.add_argument("--conditions", default="primary", help="comma list, or 'primary' (baseline,full)")
    r.add_argument("--tasks", default="all", help="comma list of task ids or categories, or 'all'")
    r.add_argument("--suite", default="dev", choices=sorted(SUITES))
    r.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                   help="claude --effort for every run (default: the CLI default)")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--model", default=None, help=f"default {MODEL} (claude) / {CODEX_MODEL} (codex, pi)")
    r.add_argument("--budget", type=float, default=4.0, help="per-turn --max-budget-usd cap")
    r.add_argument("--timeout", type=int, default=1500, help="seconds per claude turn")
    r.add_argument("--out", type=Path, default=DEFAULT_OUT)
    r.add_argument("--run-id")
    r.add_argument("--encoder", type=Path, default=DEFAULT_ENCODER)
    r.add_argument("--force", action="store_true", help="redo runs that already have a result")
    r.add_argument("--keep-workspaces", action="store_true")
    r.add_argument("--first-session", action="store_true",
                   help="don't seed the model: every run is a client's first session (the model is unknown)")
    r.add_argument("--no-warmup", dest="warmup", action="store_false",
                   help="skip the per-condition prompt-cache warm-up call")
    rp = sub.add_parser("report")
    rp.add_argument("run_dir", type=Path)
    rs = sub.add_parser("rescore", help="re-score finished runs with the current scorer")
    rs.add_argument("run_dir", type=Path)
    a = p.parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "rescore":
        return cmd_rescore(a.run_dir)
    from bench import report

    print(report.write(a.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

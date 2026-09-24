"""M4.2 regression: a real Claude Code run must not persist an Arbiter block reason as memory or
instructions (decision 0017). Opt-in because it makes a small paid model call:

    ARBITER_REAL_CLAUDE=1 uv run pytest tests/test_real_claude_memory.py -s

Uses `claude -p` with Opus 5.5 (`claude-opus-5-5`; override with ARBITER_REAL_CLAUDE_MODEL),
hooks passed through --settings (the user's settings files are not modified), a throwaway Arbiter
home in block mode, and a scratch project. Afterwards it deletes only the scratch project and
Claude's per-project folder for it. The one recorded run used Haiku and cost $0.09 (4 turns);
Opus 5.5 costs more per run.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from arbiter_agent.clients.claude_code import hooks as claude_hooks
from arbiter_agent.daemon import lifecycle
from arbiter_agent.daemon.auth import read_token
from arbiter_agent.daemon.client import read_record
from arbiter_agent.paths import get_paths
from arbiter_agent.state.store import connect
from tests.conftest import ORIGINAL_ENV, spawn_daemon, wait_running

pytestmark = pytest.mark.skipif(os.environ.get("ARBITER_REAL_CLAUDE") != "1" or not shutil.which("claude"),
                                reason="opt-in real Claude Code test (set ARBITER_REAL_CLAUDE=1)")

PROMPT = ("Create a file named notes.txt containing the single word hello. "
          "When you have finished, reply with exactly: Done.")
MODEL = os.environ.get("ARBITER_REAL_CLAUDE_MODEL", "claude-opus-5-5")
MARKERS = re.compile(r"\[Arbiter\]|Arbiter|finish ledger|missing evidence|goal epoch", re.I)


def _claude_dir(env: dict[str, str]) -> Path:
    return Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def test_block_reason_is_not_persisted_as_memory(tmp_path):
    token_id = secrets.token_hex(4)
    scratch = Path(ORIGINAL_ENV.get("TEMP") or ORIGINAL_ENV.get("TMPDIR") or "/tmp") / f"arbiter-memtest-{token_id}"
    scratch.mkdir(parents=True)
    env = {k: v for k, v in ORIGINAL_ENV.items() if k not in ("ARBITER_HOME", "CODEX_HOME")}
    claude_dir = _claude_dir(env)
    global_md = claude_dir / "CLAUDE.md"
    global_md_before = _sha(global_md)

    home = get_paths(tmp_path / "arbiter-home").ensure()
    home.config.mkdir(parents=True, exist_ok=True)
    home.config_file.write_text("completion:\n  gate_mode: block\n  max_stop_blocks_per_epoch: 1\n")
    proc = spawn_daemon(home)
    project_dirs: list[Path] = []
    try:
        assert wait_running(home)
        port = int(read_record(home)["http"]["port"])
        token = (read_token(home.hook_token_file) or b"").decode()
        settings = {"hooks": {ev: [g] for ev, g in claude_hooks.hook_groups(port, token).items()
                              if ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop")}}
        sfile = tmp_path / "settings.json"
        sfile.write_text(json.dumps(settings))
        t0 = time.time()
        out = subprocess.run(["claude", "-p", PROMPT, "--model", MODEL, "--settings", str(sfile),
                              "--permission-mode", "acceptEdits", "--output-format", "json"],
                             cwd=str(scratch), env=env, capture_output=True, text=True, encoding="utf-8",
                             timeout=300, shell=os.name == "nt")
        result = json.loads(out.stdout or "{}") if out.stdout.strip().startswith("{") else {}
        print(f"\nclaude exit {out.returncode}; cost ${result.get('total_cost_usd')}; "
              f"turns {result.get('num_turns')}; result: {str(result.get('result'))[:200]!r}")

        # The block must actually have happened, or the test proves nothing.
        rc = connect(home.db, readonly=True)
        try:
            rows = rc.execute("SELECT trigger, claim, verdict, blocked, ledger_text FROM finish_ledger").fetchall()
        finally:
            rc.close()
        print("ledger rows:", [(r[0], r[1], r[2], r[3]) for r in rows])
        assert any(r[3] == 1 for r in rows), "no stop was blocked; the regression check is inconclusive"

        # Claude's per-project folder for this scratch dir (transcripts + auto memory).
        projects = claude_dir / "projects"
        project_dirs = [d for d in projects.iterdir() if d.is_dir() and token_id in d.name] if projects.is_dir() else []
        leaked: list[str] = []
        for d in project_dirs:
            for f in d.rglob("*"):
                if f.is_file() and f.suffix != ".jsonl" and f.stat().st_mtime >= t0 - 5:
                    text = f.read_text("utf-8", errors="replace")
                    if MARKERS.search(text):
                        leaked.append(str(f))
        for f in scratch.rglob("*"):
            if f.is_file() and f.name.upper() in ("CLAUDE.MD", "CLAUDE.LOCAL.MD", "AGENTS.MD"):
                if MARKERS.search(f.read_text("utf-8", errors="replace")):
                    leaked.append(str(f))
        assert not leaked, f"gate text persisted as memory/instructions: {leaked}"
        assert _sha(global_md) == global_md_before, "global CLAUDE.md changed"
    finally:
        lifecycle.stop(home)
        proc.wait(15)
        for d in project_dirs:
            if token_id in d.name:              # only the scratch project's own folder
                shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)

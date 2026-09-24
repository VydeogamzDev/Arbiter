"""Discover the installed Codex version without running a model (spec §4.2)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from arbiter_agent.clients.client_env import ClientEnv


def find_codex_binary(env: ClientEnv) -> Path | None:
    w = shutil.which("codex")
    if w:
        return Path(w)
    for cand in (env.codex_home / ".sandbox-bin" / "codex.exe", env.codex_home / ".sandbox-bin" / "codex"):
        if cand.exists():
            return cand
    return None


def codex_version(env: ClientEnv) -> dict[str, str | None]:
    """Versions from the most recent session (what the desktop actually runs) and the CLI."""
    out: dict[str, str | None] = {"session_cli_version": None, "session_originator": None, "binary_version": None}
    sessions = env.codex_sessions
    if sessions.is_dir():
        files = sorted(sessions.glob("*/*/*/rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    meta = json.loads(fh.readline()).get("payload") or {}
                out["session_cli_version"] = meta.get("cli_version")
                out["session_originator"] = meta.get("originator")
            except (OSError, ValueError):
                pass
    b = find_codex_binary(env)
    if b is not None:
        try:
            r = subprocess.run([str(b), "--version"], capture_output=True, text=True, timeout=10)
            out["binary_version"] = r.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out

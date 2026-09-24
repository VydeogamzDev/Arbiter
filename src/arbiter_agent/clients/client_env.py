"""Where each client keeps its config and transcripts, honoring the clients' own overrides
(``CODEX_HOME``, ``CLAUDE_CONFIG_DIR``). Tests point these at temporary directories."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ClientEnv:
    home: Path
    codex_home: Path
    claude_dir: Path
    claude_global_json: Path

    @property
    def codex_config(self) -> Path:
        return self.codex_home / "config.toml"

    @property
    def codex_hooks(self) -> Path:
        return self.codex_home / "hooks.json"

    @property
    def codex_sessions(self) -> Path:
        return self.codex_home / "sessions"

    @property
    def claude_settings(self) -> Path:
        return self.claude_dir / "settings.json"

    @property
    def claude_projects(self) -> Path:
        return self.claude_dir / "projects"


def current_env() -> ClientEnv:
    home = Path(os.environ.get("ARBITER_CLIENT_HOME") or Path.home())
    codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else home / ".codex"
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        claude_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
        claude_json = claude_dir / ".claude.json"
    else:
        claude_dir = home / ".claude"
        claude_json = home / ".claude.json"
    return ClientEnv(home=home, codex_home=codex_home, claude_dir=claude_dir, claude_global_json=claude_json)


def arbiter_command(home: Path | None = None) -> list[str]:
    """The command clients should run for ``arbiter mcp``: an absolute path, because desktop
    apps don't necessarily share the terminal's PATH. A non-default Arbiter home (ARBITER_HOME)
    is passed explicitly, since clients don't inherit the user's shell environment."""
    base = _arbiter_exe()
    tail = ["--home", str(home), "mcp"] if home is not None else ["mcp"]
    return base + tail


def _arbiter_exe() -> list[str]:
    exe = shutil.which("arbiter")
    here = Path(sys.executable).parent
    for cand in (here / ("arbiter.exe" if sys.platform == "win32" else "arbiter"),
                 here / "Scripts" / "arbiter.exe"):
        if cand.exists():
            return [str(cand.resolve())]
    if exe:
        return [str(Path(exe).resolve())]
    return [str(Path(sys.executable).resolve()), "-m", "arbiter_agent"]

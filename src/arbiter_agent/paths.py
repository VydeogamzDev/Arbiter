"""Platform directories and per-install endpoint names. Stdlib only (used by shims).

Everything lives under one root when ARBITER_HOME is set (tests, portable installs);
otherwise the platform conventions from spec §16.6 apply.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP = "arbiter"


def _platform_roots() -> tuple[Path, Path, Path]:
    """Return (data_dir, log_dir, config_dir) for the current platform."""
    home = Path.home()
    if sys.platform == "win32":
        # Not %LOCALAPPDATA%: packaged apps (MSIX, e.g. the Claude desktop app) redirect their
        # processes' AppData writes into a private copy the daemon never sees (see appcontainer.py).
        base = home / f".{APP}"
        return base / "data", base / "logs", base / "config"
    if sys.platform == "darwin":
        support = home / "Library" / "Application Support" / APP
        return support / "data", home / "Library" / "Logs" / APP, support / "config"
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / APP
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / APP
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / APP
    return data, state / "logs", config


@dataclass(frozen=True)
class ArbiterPaths:
    root: Path | None
    data: Path
    logs: Path
    config: Path

    @property
    def state(self) -> Path:
        return self.data / "state"

    @property
    def db(self) -> Path:
        return self.data / "controller.sqlite"

    @property
    def backups(self) -> Path:
        return self.data / "backups"

    @property
    def config_file(self) -> Path:
        return self.config / "config.yaml"

    @property
    def token_file(self) -> Path:
        return self.state / "ipc.token"

    @property
    def hook_token_file(self) -> Path:
        return self.state / "hook.token"

    @property
    def daemon_record(self) -> Path:
        return self.state / "daemon.json"

    @property
    def lock_file(self) -> Path:
        return self.state / "daemon.lock"

    @property
    def debug_file(self) -> Path:
        return self.state / "debug.json"

    @property
    def scope_file(self) -> Path:
        return self.config / "scope.json"

    @property
    def install_key_file(self) -> Path:
        return self.state / "install.key"

    @property
    def instance_id(self) -> str:
        """Short stable id for this install; keeps endpoints of separate homes apart."""
        return hashlib.sha256(str(self.data.resolve()).lower().encode()).hexdigest()[:12]

    @property
    def pipe_address(self) -> str:
        """Windows named pipe (spec §18.8) or POSIX socket path."""
        user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
        tag = hashlib.sha256(user.lower().encode()).hexdigest()[:8]
        if sys.platform == "win32":
            return f"\\\\.\\pipe\\arbiter-{tag}-{self.instance_id}"
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(runtime) / APP if runtime else self.state / "run"
        sock = base / f"arbiter-{self.instance_id}.sock"
        if len(str(sock).encode()) > 100:  # AF_UNIX path limit (~104 on macOS, 108 on Linux)
            # Ownership and 0700 mode are verified before binding (ipc._check_socket_dir).
            short = Path("/tmp") / f"arbiter-{os.getuid()}"  # type: ignore[attr-defined,unused-ignore]  # noqa: S108
            sock = short / f"{self.instance_id}.sock"
        return str(sock)

    def ensure(self) -> ArbiterPaths:
        for d in (self.data, self.logs, self.config, self.state, self.backups):
            d.mkdir(parents=True, exist_ok=True)
            _restrict_dir(d)
        if sys.platform != "win32":
            sock_dir = Path(self.pipe_address).parent
            try:
                sock_dir.mkdir(parents=True, exist_ok=True)
                _restrict_dir(sock_dir)
            except OSError:
                pass  # ipc.listen() verifies the directory and falls back to loopback TCP
        return self


def _restrict_dir(d: Path) -> None:
    if sys.platform != "win32":
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def get_paths(home: str | os.PathLike[str] | None = None) -> ArbiterPaths:
    root = home or os.environ.get("ARBITER_HOME")
    if root:
        r = Path(root).expanduser().resolve()
        return ArbiterPaths(root=r, data=r / "data", logs=r / "logs", config=r / "config")
    data, logs, config = _platform_roots()
    return ArbiterPaths(root=None, data=data, logs=logs, config=config)


def write_private(path: Path, data: bytes) -> None:
    """Atomically write a file readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    if sys.platform == "win32":
        from arbiter_agent.winsec import restrict_file_to_user

        restrict_file_to_user(path)

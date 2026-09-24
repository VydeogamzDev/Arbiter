"""Per-install tokens (spec §18.8). Stdlib only.

- IPC token: used by shims for the pipe/socket HMAC challenge. Rotated on every daemon start.
- Hook token: sent by client ``http`` hooks in a header. Stable; rotated only by ``arbiter setup``
  so client hook config (and Codex hook trust hashes) don't churn.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from arbiter_agent.paths import write_private

HOOK_TOKEN_HEADER = "X-Arbiter-Token"  # noqa: S105 - a header name, not a secret


def read_token(path: Path) -> bytes | None:
    try:
        data = path.read_bytes().strip()
    except OSError:
        return None
    return data or None


def rotate_token(path: Path) -> bytes:
    token = secrets.token_hex(32).encode()
    write_private(path, token)
    return token


def ensure_token(path: Path) -> bytes:
    return read_token(path) or rotate_token(path)


def tokens_equal(a: str | bytes | None, b: str | bytes | None) -> bool:
    if a is None or b is None:
        return False
    ab = a.encode() if isinstance(a, str) else a
    bb = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(ab, bb)


def file_is_private(path: Path) -> bool:
    """POSIX: mode 0600. Windows: DACL restricted by write_private (checked in tests)."""
    if os.name == "nt":
        return path.exists()
    return path.exists() and (path.stat().st_mode & 0o077) == 0

"""Exact removal and restoration of a user-owned TOML table (Codex ``[mcp_servers.<name>]``).

Adoption removes the server's table (and its sub-tables such as ``[mcp_servers.x.env]``) as text,
keeping every other byte, and stores the removed text so release puts it back verbatim. Both
directions are verified by parsing: the rest of the file must be semantically unchanged.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from arbiter_agent.clients import config_merge as cm

_HEADER = re.compile(r"^\s*\[(?!\[)\s*([^\]]+?)\s*\]\s*(#.*)?$")


def _key(raw: str) -> tuple[str, ...]:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|([^.\s]+)', raw)
    return tuple(a or b or c for a, b, c in parts)


def extract(text: str, table: str, path: Path) -> tuple[str, str]:
    """(text without the table, the removed text). Raises if the table isn't a plain header block."""
    want = _key(table)
    lines = text.splitlines(keepends=True)
    keep: list[str] = []
    removed: list[str] = []
    inside = False
    for ln in lines:
        m = _HEADER.match(ln)
        if m:
            k = _key(m.group(1))
            inside = k[: len(want)] == want
        elif ln.lstrip().startswith("[[") and inside:
            inside = False
        (removed if inside else keep).append(ln)
    if not removed:
        raise cm.ConfigConflict(f"{path}: [{table}] isn't defined as a table header (inline tables aren't supported)")
    new, gone = "".join(keep), "".join(removed)
    before, after = tomllib.loads(text), tomllib.loads(new) if new.strip() else {}
    if cm._without(before, table) != after:
        raise cm.ConfigParseError(f"{path}: removing [{table}] would change other settings")
    return new, gone


def restore(text: str, removed: str, table: str, expected: Any, path: Path) -> str:
    before = tomllib.loads(text) if text.strip() else {}
    if cm._dig(before, table) is not None:
        raise cm.ConfigConflict(f"{path}: [{table}] exists again (added by hand?); not overwriting it")
    body = text.rstrip("\n")
    new = (body + "\n\n" if body else "") + removed.lstrip("\n")
    after = tomllib.loads(new)
    if cm._dig(after, table) != expected or cm._without(after, table) != cm._without(before, table):
        raise cm.ConfigParseError(f"{path}: restoring [{table}] didn't round-trip")
    return new

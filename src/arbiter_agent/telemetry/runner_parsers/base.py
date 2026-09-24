"""Shared types and command normalization for runner parsers."""

from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass
from typing import Any

_WRAPPERS = [
    re.compile(r"^(?:\S*[\\/])?(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+)*-(?:c|command)\s+", re.I),
    re.compile(r"^(?:\S*[\\/])?(?:bash|sh|zsh|dash|cmd)(?:\.exe)?\s+(?:-l\s+)?(?:-[a-z]*c|/c)\s+", re.I),
]
_PREFIXES = re.compile(r"^(?:(?:uv|poetry|pipenv|hatch|pdm)\s+run\s+|npx\s+|pnpm\s+exec\s+|yarn\s+dlx\s+|"
                       r"bunx\s+|time\s+|env\s+(?:\w+=\S+\s+)+|(?:\w+=\S+\s+)+)", re.I)


@dataclass
class RunnerResult:
    runner: str
    kind: str                 # test | typecheck | lint | build
    status: str               # pass | fail | unknown
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    total: int | None = None
    parser_version: int = 1
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def strip_wrappers(command: str) -> str:
    """Remove shell wrappers, `cd x &&` prefixes and runner launchers."""
    cmd = command.strip()
    for _ in range(3):
        before = cmd
        for w in _WRAPPERS:
            cmd = _unquote(w.sub("", cmd))
        if "&&" in cmd or ";" in cmd:
            parts = [p.strip() for p in re.split(r"&&|;", cmd) if p.strip()]
            parts = [p for p in parts if not re.match(r"^(cd|set-location|pushd)\b", p, re.I)]
            cmd = parts[-1] if parts else cmd
        cmd = _PREFIXES.sub("", cmd)
        if cmd == before:
            break
    return cmd.strip()


def fingerprint(command: str) -> str:
    """Normalized command identity used to match recipes and repeated runs."""
    cmd = strip_wrappers(command).lower()
    cmd = re.sub(r"\bpython[\d.]*(?:\.exe)?\s+-m\s+", "", cmd)
    cmd = re.sub(r"\s+", " ", cmd).strip()
    return cmd


def tokens(command: str) -> list[str]:
    try:
        return shlex.split(fingerprint(command), posix=True)
    except ValueError:
        return fingerprint(command).split()


def ints(pattern: str, text: str, flags: int = re.I) -> list[int]:
    return [int(m) for m in re.findall(pattern, text, flags)]

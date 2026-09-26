"""Task context pack (benchmark finding, 2026-09-25): at the first prompt of a new task, hand the
agent what it would otherwise spend its first tool calls collecting.

Real Claude Code runs spent 3-5 calls orienting before the first edit: listing files (often
twice), then reading the files the ranking already knew about. The pack carries:

- a repo map (every indexed path for small repos; top directories plus the picks otherwise);
- the content of the top-ranked files, the files they import (bugs often sit one import away
  from the file the prompt names) and their tests, within a token budget.

Content comes only from files the access policy allows, goes through secret redaction, and is
labeled as a snapshot taken at this prompt.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

WORDING = "[Arbiter] Task context, pre-read at this prompt (snapshot; files may change later):"
MAP_ALL_MAX = 80          # list every path up to this many files
MAX_FILE_CHARS = 6000     # one file's share before it's cut


def repo_map(files: list[str], picks: list[str]) -> str:
    if len(files) <= MAP_ALL_MAX:
        return ", ".join(files)
    tops = Counter(f.split("/", 1)[0] + ("/" if "/" in f else "") for f in files)
    head = ", ".join(f"{d} ({n})" if d.endswith("/") else d for d, n in tops.most_common(25))
    return f"{len(files)} files; top level: {head}; likely relevant: {', '.join(picks)}"


def select(ranked: list[str], pins: list[str], deps: dict[str, set[str]], tests_of: Callable[[str], list[str]],
           files: set[str], limit: int = 8) -> list[str]:
    """Pins, then the top of the ranking, then what the top picks import, then their tests."""
    head = list(dict.fromkeys(pins + ranked[:3]))
    out = list(head)
    for p in head[:3]:
        out += sorted(d for d in deps.get(p, set()) if d in files)
    for p in head[:2]:
        out += tests_of(p)
    out += ranked[3:5]
    seen: list[str] = []
    for p in out:
        if p in files and p not in seen:
            seen.append(p)
    return seen[:limit]


MAP_WORDING = "[Arbiter] Task context at this prompt (snapshot):"


def build(root: Path, files: list[str], picks: list[str], read: Callable[[str], str | None],
          max_tokens: int, contents: bool = True) -> str | None:
    """``contents=False`` gives the map-only pack: the repo map and the ranked likely-relevant files,
    without file contents (models that re-read files before editing gain nothing from contents)."""
    if not contents:
        if not picks:
            return None
        # Top 4 only: Sonnet 5 opened every listed file (reads 39 -> 48 with 8 listed).
        top = picks[:4]
        return "\n".join([MAP_WORDING, f"Repo files: {repo_map(files, top)}",
                          f"Most likely relevant, in order: {', '.join(top)}"])[: max_tokens * 4]
    budget = max_tokens * 4
    lines = [WORDING, f"Repo files: {repo_map(files, picks)}"]
    used = sum(len(x) + 1 for x in lines)
    shown: list[str] = []
    for p in picks:
        text = read(p)
        if text is None or not text.strip():
            continue                                   # empty __init__.py and friends add nothing
        body = text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS] + "\n... (cut; read the file for more)"
        block = f"--- {p} ---\n{body.rstrip()}\n"
        if used + len(block) > budget:
            if not shown:
                continue
            break
        lines.append(block)
        shown.append(p)
        used += len(block) + 1
    if not shown:
        return None
    return "\n".join(lines)


def summary(pack: dict[str, Any]) -> str:
    return f"{len(pack.get('shown', []))} file(s), ~{pack.get('tokens', 0)} tokens"

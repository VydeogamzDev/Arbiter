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

import ast
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

# "snapshot; files may change later" read as an invitation to re-read: Pi re-read 21 of 65 files the
# pack showed in full (all complete), which caused both of its losing held-out tasks (2026-09-27).
WORDING = ("[Arbiter] Task context, read from disk at this prompt. Files shown in full are complete and current: "
           "edit them directly, and re-read one only after it changes.")
MAP_ALL_MAX = 80          # list every path up to this many files
MAX_FILE_CHARS = 6000     # one file's share before it's cut
OUTLINE_LINES = 40        # an outline's share
DECL = re.compile(r"^\s*(export\s+)?(async\s+)?(def|class|function|fn|func|struct|interface|type|enum|impl|"
                  r"public|private|protected|const|let|var)\b")


def outline(path: str, text: str) -> str:
    """Signatures only: definitions, module-level names and the first docstring line. For the
    lower-confidence picks of a large repo, where half the full contents went unused."""
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            lines = text.splitlines()
            out: list[str] = []
            doc = ast.get_docstring(tree)
            if doc:
                out.append('"""' + doc.strip().splitlines()[0] + '"""')

            def walk(nodes: list[ast.stmt], depth: int) -> None:
                for n in nodes:
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        out.append("    " * depth + lines[n.lineno - 1].strip())
                        if isinstance(n, ast.ClassDef) and depth == 0:
                            walk(n.body, 1)
                    elif depth == 0 and isinstance(n, (ast.Assign, ast.AnnAssign)):
                        out.append(lines[n.lineno - 1].strip()[:120])
            walk(tree.body, 0)
            return "\n".join(out[:OUTLINE_LINES])
    return "\n".join([ln.rstrip() for ln in text.splitlines() if DECL.match(ln)][:OUTLINE_LINES])


def repo_map(files: list[str], picks: list[str]) -> str:
    if len(files) <= MAP_ALL_MAX:
        return ", ".join(files)
    tops = Counter(f.split("/", 1)[0] + ("/" if "/" in f else "") for f in files)
    head = ", ".join(f"{d} ({n})" if d.endswith("/") else d for d, n in tops.most_common(25))
    return f"{len(files)} files; top level: {head}; likely relevant: {', '.join(picks)}"


def core(ranked: list[str], pins: list[str], deps: dict[str, set[str]],
         tests_of: Callable[[str], list[str]], files: set[str]) -> set[str]:
    """The picks a large repo's pack shows in full: pins, the top two, what those import, their
    tests. Scores can't separate the rest: a fix's target scored 0.42 of the top while unrelated
    files scored 0.5-0.6 (largerepo_v1), so the rest stays in the pack, as outlines."""
    head = list(dict.fromkeys(pins + ranked[:2]))
    out = set(head)
    for p in head[:3]:
        out |= {d for d in deps.get(p, set()) if d in files}
    for p in head[:2]:
        out |= set(tests_of(p))
    return out


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
          max_tokens: int, contents: bool = True, test_note: str | None = None,
          full: set[str] | None = None) -> str | None:
    """``contents=False`` gives the map-only pack: the repo map and the ranked likely-relevant files,
    without file contents (models that re-read files before editing gain nothing from contents).

    ``test_note`` says that Arbiter runs the tests itself after each code edit (completion.auto_test).
    Without it, Opus 5.5 still issued its own test run in the same turn as an edit, before it could
    see Arbiter's result (5 redundant runs in 10 held-out tasks).

    ``full``: in a large repo (more than MAP_ALL_MAX files) only these picks are shown in full, the
    others as outlines; ``None`` shows every pick in full."""
    if not contents:
        if not picks:
            return None
        # Top 4 only: Sonnet 5 opened every listed file (reads 39 -> 48 with 8 listed).
        top = picks[:4]
        head = [MAP_WORDING, f"Repo files: {repo_map(files, top)}",
                f"Most likely relevant, in order: {', '.join(top)}"]
        return "\n".join(head + ([test_note] if test_note else []))[: max_tokens * 4]
    budget = max_tokens * 4
    lines = [WORDING, f"Repo files: {repo_map(files, picks)}"] + ([test_note] if test_note else [])
    used = sum(len(x) + 1 for x in lines)
    shown: list[str] = []
    for p in picks:
        text = read(p)
        if text is None or not text.strip():
            continue                                   # empty __init__.py and friends add nothing
        if full is not None and len(files) > MAP_ALL_MAX and p not in full:
            body = outline(p, text)
            if not body.strip():
                continue
            block = f"--- {p} (outline: signatures only; read the file for the body) ---\n{body.rstrip()}\n"
        else:
            body = text if len(text) <= MAX_FILE_CHARS else \
                text[:MAX_FILE_CHARS] + "\n... (cut; read the file for more)"
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

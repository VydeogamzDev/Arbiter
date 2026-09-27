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
FOLLOWUP_WORDING = ("[Arbiter] More task context for this prompt: files not yet shown in this conversation, read "
                    "from disk now (complete and current unless marked).")
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


def excerpt(path: str, text: str, query: str, line: int | None = None, classes: bool = True) -> str | None:
    """For a file over MAX_FILE_CHARS: the definitions the prompt names (and the one holding the
    index's best-matching line) instead of the file's head. Real repos have long modules: sympy's
    iterables.py is ~3,000 lines, and its first 6,000 characters are imports and unrelated
    functions. None when nothing matches (the caller falls back to the head). ``classes=False``
    (a file the conversation already holds): named methods only, not a named class's whole body."""
    if not path.endswith(".py"):
        return None
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query))
    defs: list[ast.AST] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(n)
            if isinstance(n, ast.ClassDef):
                defs += [m for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    chosen: list[ast.AST] = []
    for n in defs:
        name = getattr(n, "name", "")
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        end = getattr(n, "end_lineno", n.lineno) or n.lineno
        holds = line is not None and start <= line <= end and not isinstance(n, ast.ClassDef)
        if isinstance(n, ast.ClassDef) and not classes:
            continue
        if name in words or holds:
            chosen.append(n)
    # A named class whose methods are chosen too: keep the methods, not the whole class.
    spans: list[tuple[int, int, str]] = []
    for n in chosen:
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        end = getattr(n, "end_lineno", n.lineno) or n.lineno
        if isinstance(n, ast.ClassDef) and any(start <= m.lineno <= end for m in chosen if m is not n):
            end = start          # just the class line
        spans.append((start, end, getattr(n, "name", "")))
    if not spans:
        return None
    lines = text.splitlines()
    out: list[str] = []
    used = 0
    for start, end, _name in sorted(set(spans)):
        seg = "\n".join(lines[start - 1:end])
        if used + len(seg) > MAX_FILE_CHARS:
            if not out:
                out.append(seg[:MAX_FILE_CHARS] + "\n... (cut)")
            break
        out.append(f"# lines {start}-{end}\n{seg}")
        used += len(seg)
    return "\n\n".join(out)


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
    """Pins, then the top of the ranking, then what the top picks import, then their tests. With a
    pin (a file the prompt names or that defines a named symbol) the ranking adds no guesses:
    on sympy they were unrelated modules (core/numbers.py for an IntegerPartition task)."""
    head = list(dict.fromkeys(pins + ([] if pins else ranked[:3])))
    out = list(head)
    for p in head[:3]:
        out += sorted(d for d in deps.get(p, set()) if d in files)
    for p in head[:2]:
        out += tests_of(p)
    if not pins:
        out += ranked[3:5]
    seen: list[str] = []
    for p in out:
        if p in files and p not in seen:
            seen.append(p)
    return seen[:limit]


MAP_WORDING = "[Arbiter] Task context at this prompt (snapshot):"


def build(root: Path, files: list[str], picks: list[str], read: Callable[[str], str | None],
          max_tokens: int, contents: bool = True, test_note: str | None = None,
          full: set[str] | None = None, query: str = "", hit_lines: dict[str, int] | None = None,
          skip: set[str] | None = None, shown_keys: list[str] | None = None) -> str | None:
    """``contents=False`` gives the map-only pack: the repo map and the ranked likely-relevant files,
    without file contents (models that re-read files before editing gain nothing from contents).

    ``test_note`` says that Arbiter runs the tests itself after each code edit (completion.auto_test).
    Without it, Opus 5.5 still issued its own test run in the same turn as an edit, before it could
    see Arbiter's result (5 redundant runs in 10 held-out tasks).

    ``full``: in a large repo (more than MAP_ALL_MAX files) only these picks are shown in full, the
    others as outlines; ``None`` shows every pick in full.

    ``skip``: paths (files the conversation already holds: shown, read or edited) and block keys
    (excerpts already shown) for a follow-up prompt of the same session.
    Those are left out, and so are the repo map and test note; with nothing new there's no pack. On
    sympy, every follow-up prompt re-sent a ~1.7k-token pack of files already in context, which then
    rode along in every later request (2026-09-27). ``shown_keys`` receives the keys of the blocks shown."""
    if not contents:
        if not picks:
            return None
        # Top 4 only: Sonnet 5 opened every listed file (reads 39 -> 48 with 8 listed).
        top = picks[:4]
        head = [MAP_WORDING, f"Repo files: {repo_map(files, top)}",
                f"Most likely relevant, in order: {', '.join(top)}"]
        return "\n".join(head + ([test_note] if test_note else []))[: max_tokens * 4]
    budget = max_tokens * 4
    skip = skip or set()
    if skip:
        lines = [FOLLOWUP_WORDING]
    else:
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
        elif len(text) > MAX_FILE_CHARS and (ex := excerpt(p, text, query, (hit_lines or {}).get(p),
                                                                  classes=p not in (skip or ()))):
            # An excerpt is judged by its content below: another function of a big file is new.
            n = text.count("\n") + 1
            block = f"--- {p} (excerpt of a {n}-line file: the definitions below are complete) ---\n{ex.rstrip()}\n"
        else:
            body = text if len(text) <= MAX_FILE_CHARS else \
                text[:MAX_FILE_CHARS] + "\n... (cut; read the file for more)"
            block = f"--- {p} ---\n{body.rstrip()}\n"
        key = block_key(p, block)
        if key in skip or (p in skip and "(excerpt of a" not in block):
            continue                     # already in the conversation (shown, read or edited)
        if used + len(block) > budget:
            if not shown:
                continue
            break
        lines.append(block)
        shown.append(p)
        if shown_keys is not None:
            shown_keys.append(key)
        used += len(block) + 1
    if not shown:
        return None
    return "\n".join(lines)


def block_key(path: str, block: str) -> str:
    """A shown block's identity: the same file shown the same way (a changed excerpt is new)."""
    import hashlib

    return f"{path}#{hashlib.sha256(block.encode('utf-8', 'replace')).hexdigest()[:12]}"


def summary(pack: dict[str, Any]) -> str:
    return f"{len(pack.get('shown', []))} file(s), ~{pack.get('tokens', 0)} tokens"

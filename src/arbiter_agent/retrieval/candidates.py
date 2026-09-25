"""Candidate generation for context retrieval (spec §11.2, §11.3).

Cheap deterministic channels over the M6 index, each producing a ranked list of files:
- ``lexical``: full-text chunks (FTS5/BM25);
- ``identifier``: symbols whose names contain a query word (FTS can't match inside identifiers);
- ``path``: query words that name a file or directory;
- ``symbol``: definitions (and importers) of identifiers in the query;
- ``graph``: imports and importers of the strongest files;
- ``tests``: tests mapped to the strongest files.

Pins bypass ranking entirely (§11.3): files referenced by the current error, changed files, files
the user named, definitions of symbols the user named. The sensor may later rerank candidates; it
never searches the repository itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from arbiter_agent.state.dependency_graph import is_test

STOP = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and", "or", "it", "this", "that", "with",
        "should", "be", "not", "when", "we", "do", "does", "which", "where", "how", "what", "fix", "make", "add",
        "at", "by", "from", "as", "can", "all", "every", "our", "my", "look", "change", "instead", "wrong", "real",
        "write", "one", "tests", "test", "exist", "values", "support", "need", "needs"}
_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:_[A-Za-z0-9_]+|[a-z][A-Z][A-Za-z0-9]*))\b")
_TRACE = [
    re.compile(r"File \"([^\"]+)\", line (\d+)"),                     # Python
    re.compile(r"\(([^()\s]+\.[A-Za-z]{1,5}):(\d+)(?::\d+)?\)"),       # JS/Java-style "(path:line)"
    re.compile(r"(?:^|\s)([\w./\\-]+\.[A-Za-z]{1,5}):(\d+)(?::\d+)?"),  # "path:line[:col]"
]


@dataclass
class QueryContext:
    query: str
    errors: str = ""
    paths: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    phase: str = ""                       # debug | implement | review | ...
    budget_tokens: int = 8000
    misses: int = 0                       # recent retrieval misses in this session

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryContext:
        return cls(str(d.get("query") or ""), str(d.get("errors") or ""), list(d.get("paths") or []),
                   list(d.get("symbols") or []), list(d.get("changed") or []), str(d.get("phase") or ""),
                   int(d.get("budget_tokens") or 8000), int(d.get("misses") or 0))


@dataclass
class Candidates:
    pins: dict[str, str] = field(default_factory=dict)                  # path -> reason
    channels: dict[str, list[str]] = field(default_factory=dict)        # channel -> ranked paths
    lines: dict[str, int] = field(default_factory=dict)                 # path -> best line
    all_files: list[str] = field(default_factory=list)


# Common programming concepts and the words code uses for them. Deliberately generic: it bridges
# how people describe a task ("endpoint", "environment") and how code names it (route, environ).
CONCEPTS: dict[str, list[str]] = {
    "endpoint": ["route", "handler", "api", "view"], "route": ["handler", "endpoint", "api"],
    "api": ["route", "handler"], "url": ["route"],
    "environment": ["environ", "env", "config", "settings"], "env": ["environ", "config"],
    "production": ["prod"], "prod": ["production"], "config": ["settings", "environ"],
    "setting": ["config"], "option": ["config", "settings"],
    "database": ["db", "session", "sql"], "db": ["database", "session"], "sql": ["query", "session"],
    "atomic": ["transaction", "lock", "session", "commit"], "transaction": ["session", "commit"],
    "login": ["auth", "password", "credential"], "password": ["auth", "hash", "credential"],
    "auth": ["login", "password", "permission"], "permission": ["auth", "role"],
    "email": ["mail", "notify"], "mail": ["email", "notify"], "notification": ["notify", "email"],
    "signature": ["hmac", "verify"], "log": ["logging", "logger"],
    "retry": ["attempt", "backoff"], "cache": ["ttl", "memo"],
}


def stem(w: str) -> str:
    for suf, rep in (("ies", "y"), ("ses", "s"), ("es", ""), ("s", "")):
        if len(w) > 4 and w.endswith(suf) and not w.endswith("ss"):
            return w[: len(w) - len(suf)] + rep
    return w


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]


def expand(ws: list[str]) -> list[str]:
    """Stems plus concept neighbours, original words first."""
    out = list(dict.fromkeys(ws + [stem(w) for w in ws]))
    for w in list(out):
        for n in CONCEPTS.get(w, []) + CONCEPTS.get(stem(w), []):
            if n not in out:
                out.append(n)
    return out


def _match_file(ref: str, files: list[str]) -> str | None:
    ref = ref.replace("\\", "/").lstrip("./")
    if ref in files:
        return ref
    tails = [f for f in files if ref.endswith("/" + f) or f.endswith("/" + ref)]
    return min(tails, key=len) if tails else None


def trace_refs(errors: str, files: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for rx in _TRACE:
        for m in rx.finditer(errors):
            f = _match_file(m.group(1), files)
            if f and (f, int(m.group(2))) not in out:
                out.append((f, int(m.group(2))))
    return out


def gather(idx: Any, root: str, ctx: QueryContext, *, deps: dict[str, set[str]] | None = None,
           rev: dict[str, set[str]] | None = None, files: list[str] | None = None, policy: Any = None) -> Candidates:
    deps, rev = (deps, rev) if deps is not None and rev is not None else idx.graph(root)
    files = files if files is not None else idx.files(root)
    if policy is not None:
        files = [f for f in files if policy.allowed(f)]
    fileset = set(files)
    c = Candidates(all_files=files)

    # ---- pins (§11.3)
    for f, line in trace_refs(ctx.errors, files):
        c.pins.setdefault(f, "referenced by the current error")
        c.lines.setdefault(f, line)
    for p in ctx.paths + re.findall(r"[\w./-]+\.[A-Za-z]{1,5}", ctx.query):
        hit = _match_file(p, files)
        if hit:
            c.pins.setdefault(hit, "named by the user")
    for p in ctx.changed:
        hit = _match_file(p, files)
        if hit:
            c.pins.setdefault(hit, "changed in this task")
    named = list(dict.fromkeys(ctx.symbols + _IDENT.findall(ctx.query)))
    for name in named[:6]:
        found = idx.symbol(root, name, limit=10)
        for d in found["definitions"]:
            if d["path"] in fileset:
                c.pins.setdefault(d["path"], f"defines {name}")
                c.lines.setdefault(d["path"], int(d["line"]))
        c.channels.setdefault("symbol", [])
        for p in found.get("importers", []) + [r["path"] for r in found.get("references", [])]:
            if p in fileset and p not in c.channels["symbol"]:
                c.channels["symbol"].append(p)

    # ---- lexical (any term, prefix match: recall first)
    lex: list[str] = []
    qws = expand(words(ctx.query + " " + ctx.errors[-400:]))
    q = " ".join(qws) or ctx.query
    for h in idx.search(root, q, limit=40, policy=policy, any_terms=True):
        if h["path"] in fileset and h["path"] not in lex:
            lex.append(h["path"])
            c.lines.setdefault(h["path"], int(h.get("line") or 1))
    c.channels["lexical"] = lex

    # ---- identifiers containing query words (``tax`` -> ``add_tax``): FTS can't match inside tokens
    ident: list[str] = []
    for path, _name in idx.symbols_like(root, [stem(w) for w in expand(words(ctx.query))]):
        if path in fileset and path not in ident:
            ident.append(path)
    c.channels["identifier"] = ident

    # ---- path prior: query words (stemmed, with concept neighbours) naming files or directories
    qw = {stem(w) for w in expand(words(ctx.query))}
    scored = []
    for f in files:
        parts = {stem(w) for w in words(" ".join(PurePosixPath(f).with_suffix("").parts))}
        overlap = len(qw & parts)
        if overlap:
            scored.append((-overlap, len(f), f))
    c.channels["path"] = [f for _, _, f in sorted(scored)]

    # ---- graph and tests around the strongest files
    seeds = list(c.pins) + lex[:4] + c.channels["path"][:3]
    graph: list[str] = []
    tests: list[str] = []
    for s in dict.fromkeys(seeds):
        for n in sorted(deps.get(s, set())) + sorted(rev.get(s, set())):
            if n in fileset and n not in graph and not is_test(n):
                graph.append(n)
            elif n in fileset and is_test(n) and n not in tests:
                tests.append(n)
        base = PurePosixPath(s).stem
        for t in files:
            if is_test(t) and t not in tests and PurePosixPath(t).stem in (f"test_{base}", f"{base}_test"):
                tests.append(t)
    c.channels["graph"] = graph
    c.channels["tests"] = tests
    return c

"""Repository index (spec §11.2, §11.5; M6.1-M6.4).

Layout: one SQLite database per repository (keyed by its git common directory), with
- **content-addressed analysis**: chunks (FTS5), symbols, imports and calls keyed by the file's
  SHA-256, shared by every branch and worktree of the repository;
- **views**: one per worktree root, mapping repository paths to content hashes at the view's
  current HEAD. A branch switch only re-analyzes content the index hasn't seen, and switching
  back costs nothing (the overlay design of §11.5).

Freshness: every query first refreshes its view by file stats (size, mtime; recent files are
re-hashed regardless). Files that couldn't be re-analyzed within the time budget are dropped
from the view until the next refresh, so a query never returns stale content. Every result
carries the index version, view generation and HEAD.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from arbiter_agent.retrieval import languages
from arbiter_agent.retrieval.access_policy import AccessPolicy
from arbiter_agent.state.dependency_graph import Resolver, go_module_of, is_test, tested_stem
from arbiter_agent.state.repo_identity import RepoIdentity, git

INDEX_VERSION = 1            # bump when the schema or analyzers change: blobs re-analyze lazily
CHUNK_LINES = 60
MAX_FILES = 50_000
RECENT_S = 2.0               # files modified this recently are re-hashed even if size/mtime match
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blobs (sha TEXT PRIMARY KEY, lang TEXT, size INTEGER, lines INTEGER,
  redactions INTEGER, analyzer INTEGER, last_seen REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(text, sha UNINDEXED, start_line UNINDEXED,
  end_line UNINDEXED, tokenize = "unicode61 tokenchars '_'");
CREATE TABLE IF NOT EXISTS symbols (sha TEXT, name TEXT, kind TEXT, line INTEGER, end_line INTEGER,
  parent TEXT, signature TEXT);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS symbols_sha ON symbols(sha);
CREATE TABLE IF NOT EXISTS imports (sha TEXT, module TEXT, line INTEGER, level INTEGER, names TEXT);
CREATE INDEX IF NOT EXISTS imports_sha ON imports(sha);
CREATE TABLE IF NOT EXISTS calls (sha TEXT, name TEXT, line INTEGER);
CREATE INDEX IF NOT EXISTS calls_name ON calls(name);
CREATE TABLE IF NOT EXISTS views (id INTEGER PRIMARY KEY, root TEXT UNIQUE, head TEXT, generation INTEGER,
  refreshed_at REAL, files INTEGER, pending INTEGER);
CREATE TABLE IF NOT EXISTS view_files (view_id INTEGER, path TEXT, sha TEXT, size INTEGER, mtime_ns INTEGER,
  PRIMARY KEY (view_id, path));
CREATE INDEX IF NOT EXISTS view_files_sha ON view_files(view_id, sha);
"""


@dataclass
class IndexStamp:
    version: int
    generation: int
    head: str | None
    root: str
    files: int
    pending: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RefreshResult:
    changed: int = 0
    removed: int = 0
    pending: int = 0
    analyzed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0


def list_files(ident: RepoIdentity) -> list[str]:
    root = Path(ident.root)
    if ident.in_repo:
        out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", timeout=30)
        if out is not None:
            return [p for p in out.split("\0") if p][:MAX_FILES]
    files: list[str] = []
    from arbiter_agent.retrieval.access_policy import GENERATED_DIRS

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in GENERATED_DIRS and not d.startswith(".")]
        for fn in filenames:
            files.append(str((Path(dirpath) / fn).relative_to(root)).replace("\\", "/"))
            if len(files) >= MAX_FILES:
                return files
    return files


def _fts_query(tokens: list[str], op: str, prefix: bool = False) -> str:
    star = "*" if prefix else ""
    return f" {op} ".join('"' + t.replace('"', "") + '"' + star for t in tokens)


class RepoIndex:
    def __init__(self, db_path: Path, redact: Any = None) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.redact = redact                    # callable(text) -> (text, count) or None
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._graph_cache: dict[tuple[int, int], tuple[dict[str, set[str]], dict[str, set[str]]]] = {}

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ------------------------------------------------------------------ views
    def _view(self, root: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM views WHERE root = ?", (root,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO views(root, head, generation, refreshed_at, files, pending) "
                              "VALUES (?, NULL, 0, 0, 0, 0)", (root,))
            row = self.conn.execute("SELECT * FROM views WHERE root = ?", (root,)).fetchone()
        return row

    def stamp(self, root: str) -> IndexStamp:
        v = self._view(root)
        return IndexStamp(INDEX_VERSION, int(v["generation"]), v["head"], root, int(v["files"] or 0),
                          int(v["pending"] or 0))

    # ------------------------------------------------------------------ refresh
    def refresh(self, ident: RepoIdentity, policy: AccessPolicy | None = None,
                budget_s: float | None = None) -> RefreshResult:
        t0 = time.monotonic()
        policy = policy or AccessPolicy.for_repo(Path(ident.root))
        res = RefreshResult()
        listing = list_files(ident)
        root = Path(ident.root)
        with self.lock:
            view = self._view(ident.root)
            vid = int(view["id"])
            existing = {r["path"]: (r["sha"], r["size"], r["mtime_ns"])
                        for r in self.conn.execute("SELECT path, sha, size, mtime_ns FROM view_files WHERE view_id = ?",
                                                   (vid,))}
            now_ns = time.time_ns()
            allowed: set[str] = set()
            todo: list[tuple[str, os.stat_result]] = []
            for rel in listing:
                reason = policy.path_reason(rel)
                if reason:
                    res.skipped[reason] = res.skipped.get(reason, 0) + 1
                    continue
                try:
                    st = os.stat(root / rel)
                except OSError:
                    continue
                allowed.add(rel)
                prev = existing.get(rel)
                recent = now_ns - st.st_mtime_ns < RECENT_S * 1e9
                if prev and prev[1] == st.st_size and prev[2] == st.st_mtime_ns and not recent:
                    continue
                todo.append((rel, st))
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                gone = [p for p in existing if p not in allowed]
                for p in gone:
                    self.conn.execute("DELETE FROM view_files WHERE view_id = ? AND path = ?", (vid, p))
                res.removed = len(gone)
                pending: list[str] = []
                for i, (rel, st) in enumerate(todo):
                    if budget_s is not None and time.monotonic() - t0 >= budget_s:
                        pending = [r for r, _ in todo[i:]]
                        break
                    try:
                        data = (root / rel).read_bytes()
                    except OSError:
                        continue
                    reason = policy.content_reason(data)
                    if reason:
                        res.skipped[reason] = res.skipped.get(reason, 0) + 1
                        self.conn.execute("DELETE FROM view_files WHERE view_id = ? AND path = ?", (vid, rel))
                        continue
                    sha = hashlib.sha256(data).hexdigest()
                    if existing.get(rel, (None,))[0] != sha:
                        res.changed += 1
                    res.analyzed += self._ensure_blob(sha, rel, data)
                    self.conn.execute("INSERT OR REPLACE INTO view_files(view_id, path, sha, size, mtime_ns) "
                                      "VALUES (?,?,?,?,?)", (vid, rel, sha, st.st_size, st.st_mtime_ns))
                for rel in pending:   # never serve stale content: drop until re-analyzed
                    self.conn.execute("DELETE FROM view_files WHERE view_id = ? AND path = ?", (vid, rel))
                res.pending = len(pending)
                files = self.conn.execute("SELECT COUNT(*) FROM view_files WHERE view_id = ?", (vid,)).fetchone()[0]
                head = ident.head
                bump = 1 if (res.changed or res.removed or res.pending or head != view["head"]) else 0
                self.conn.execute("UPDATE views SET head = ?, generation = generation + ?, refreshed_at = ?, "
                                  "files = ?, pending = ? WHERE id = ?",
                                  (head, bump, time.time(), files, res.pending, vid))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        res.seconds = round(time.monotonic() - t0, 3)
        return res

    def _ensure_blob(self, sha: str, rel: str, data: bytes) -> int:
        row = self.conn.execute("SELECT analyzer FROM blobs WHERE sha = ?", (sha,)).fetchone()
        if row is not None and int(row["analyzer"]) == INDEX_VERSION:
            self.conn.execute("UPDATE blobs SET last_seen = ? WHERE sha = ?", (time.time(), sha))
            return 0
        if row is not None:
            self._drop_blob(sha)
        text = data.decode("utf-8", "replace")
        redactions = 0
        if self.redact is not None:
            text, redactions = self.redact(text)
        lines = text.splitlines()
        for start in range(0, max(1, len(lines)), CHUNK_LINES):
            chunk = "\n".join(lines[start:start + CHUNK_LINES])
            if chunk.strip():
                self.conn.execute("INSERT INTO chunks(text, sha, start_line, end_line) VALUES (?,?,?,?)",
                                  (chunk, sha, start + 1, min(len(lines), start + CHUNK_LINES)))
        a = languages.analyze(rel, text)
        for s in a.symbols:
            self.conn.execute("INSERT INTO symbols VALUES (?,?,?,?,?,?,?)",
                              (sha, s.name, s.kind, s.line, s.end_line, s.parent, s.signature))
        for imp in a.imports:
            self.conn.execute("INSERT INTO imports VALUES (?,?,?,?,?)",
                              (sha, imp.module, imp.line, imp.level, json.dumps(imp.names)))
        for name, line in a.calls:
            self.conn.execute("INSERT INTO calls VALUES (?,?,?)", (sha, name, line))
        self.conn.execute("INSERT OR REPLACE INTO blobs(sha, lang, size, lines, redactions, analyzer, last_seen) "
                          "VALUES (?,?,?,?,?,?,?)", (sha, a.lang, len(data), len(lines), redactions, INDEX_VERSION,
                                                     time.time()))
        return 1

    def _drop_blob(self, sha: str) -> None:
        for table in ("chunks", "symbols", "imports", "calls", "blobs"):
            self.conn.execute(f"DELETE FROM {table} WHERE sha = ?", (sha,))

    def gc(self, keep_days: float = 7.0) -> int:
        """Drop analysis no view references and nobody has seen for ``keep_days`` (branch reuse window)."""
        cutoff = time.time() - keep_days * 86400
        with self.lock:
            shas = [r[0] for r in self.conn.execute(
                "SELECT sha FROM blobs WHERE last_seen < ? AND sha NOT IN (SELECT sha FROM view_files)", (cutoff,))]
            self.conn.execute("BEGIN IMMEDIATE")
            for sha in shas:
                self._drop_blob(sha)
            self.conn.execute("COMMIT")
        return len(shas)

    # ------------------------------------------------------------------ queries
    def _paths_by_sha(self, vid: int, shas: set[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        if not shas:
            return out
        q = f"SELECT sha, path FROM view_files WHERE view_id = ? AND sha IN ({','.join('?' * len(shas))})"
        for r in self.conn.execute(q, (vid, *shas)):
            out.setdefault(r["sha"], []).append(r["path"])
        return out

    def search(self, root: str, query: str, limit: int = 20, policy: AccessPolicy | None = None,
               path_glob: str | None = None, any_terms: bool = False) -> list[dict[str, Any]]:
        """``any_terms``: match any query word as a prefix (``tax`` finds ``tax_rate``); used by context
        retrieval, where recall matters more than the tightest match."""
        import fnmatch

        tokens = [t for t in re.findall(r"[A-Za-z0-9_]{2,}", query)][:12]
        if not tokens:
            return []
        with self.lock:
            vid = int(self._view(root)["id"])
            rows: list[sqlite3.Row] = []
            for op in (("OR",) if any_terms else ("AND", "OR")):
                rows = self.conn.execute(
                    "SELECT c.sha, c.start_line, c.end_line, c.text, bm25(chunks) AS score FROM chunks c "
                    "WHERE chunks MATCH ? AND c.sha IN (SELECT sha FROM view_files WHERE view_id = ?) "
                    "ORDER BY score LIMIT ?", (_fts_query(tokens, op, any_terms), vid, limit * 3)).fetchall()
                if rows:
                    break
            paths = self._paths_by_sha(vid, {r["sha"] for r in rows})
            low = [t.lower() for t in tokens]
            hits: list[dict[str, Any]] = []
            for r in rows:
                lines = str(r["text"]).splitlines()
                idx = next((i for i, ln in enumerate(lines) if any(t in ln.lower() for t in low)), 0)
                snippet = "\n".join(lines[max(0, idx - 1): idx + 3])[:600]
                for p in paths.get(r["sha"], []):
                    hits.append({"path": p, "line": int(r["start_line"]) + idx, "start_line": int(r["start_line"]),
                                 "end_line": int(r["end_line"]), "snippet": snippet, "score": round(-float(r["score"]),
                                                                                                    3),
                                 "channel": "lexical"})
            for r in self.conn.execute("SELECT path FROM view_files WHERE view_id = ?", (vid,)):
                p = str(r["path"])
                if all(t in p.lower() for t in low):
                    hits.append({"path": p, "line": 1, "start_line": 1, "end_line": 1, "snippet": "",
                                 "score": 50.0, "channel": "path"})
        if policy is not None:
            hits = [h for h in hits if policy.allowed(h["path"])]
        if path_glob:
            hits = [h for h in hits if fnmatch.fnmatch(h["path"], path_glob)]
        hits.sort(key=lambda h: -h["score"])
        seen: set[tuple[str, int]] = set()
        out = []
        for h in hits:
            k = (h["path"], h["start_line"])
            if k not in seen:
                seen.add(k)
                out.append(h)
        return out[:limit]

    def symbol(self, root: str, name: str, limit: int = 30) -> dict[str, Any]:
        with self.lock:
            vid = int(self._view(root)["id"])
            rows = self.conn.execute(
                "SELECT s.*, vf.path FROM symbols s JOIN view_files vf ON vf.sha = s.sha AND vf.view_id = ? "
                "WHERE s.name = ? LIMIT ?", (vid, name, limit)).fetchall()
            if not rows:
                rows = self.conn.execute(
                    "SELECT s.*, vf.path FROM symbols s JOIN view_files vf ON vf.sha = s.sha AND vf.view_id = ? "
                    "WHERE s.name = ? COLLATE NOCASE LIMIT ?", (vid, name, limit)).fetchall()
            defs = [{"path": r["path"], "line": r["line"], "end_line": r["end_line"], "kind": r["kind"],
                     "parent": r["parent"], "signature": r["signature"]} for r in rows]
            def_lines = {(d["path"], d["line"]) for d in defs}
            calls = self.conn.execute(
                "SELECT c.line, vf.path FROM calls c JOIN view_files vf ON vf.sha = c.sha AND vf.view_id = ? "
                "WHERE c.name = ? LIMIT ?", (vid, name, limit)).fetchall()
            refs = [{"path": r["path"], "line": r["line"], "channel": "call"} for r in calls]
        for h in self.search(root, name, limit=limit):
            if h["channel"] == "lexical" and (h["path"], h["line"]) not in def_lines and re.search(
                    rf"\b{re.escape(name)}\b", h["snippet"]):
                refs.append({"path": h["path"], "line": h["line"], "channel": "text"})
        seen: set[tuple[str, int]] = set()
        uniq = []
        for r in refs:
            k = (r["path"], r["line"])
            if k not in seen and k not in def_lines:
                seen.add(k)
                uniq.append(r)
        importers = sorted({i for d in defs for i in self.related(root, d["path"])["importers"]})
        return {"definitions": defs, "references": uniq[:limit], "importers": importers}

    def symbols_like(self, root: str, fragments: list[str], limit: int = 40) -> list[tuple[str, str]]:
        """(path, symbol) for symbols whose name contains any fragment (``tax`` -> ``add_tax``)."""
        frags = [f for f in fragments if len(f) >= 3][:10]
        if not frags:
            return []
        with self.lock:
            vid = int(self._view(root)["id"])
            where = " OR ".join("s.name LIKE ?" for _ in frags)
            rows = self.conn.execute(
                f"SELECT DISTINCT vf.path, s.name FROM symbols s JOIN view_files vf "
                f"ON vf.sha = s.sha AND vf.view_id = ? WHERE {where} LIMIT ?",
                (vid, *[f"%{f}%" for f in frags], limit)).fetchall()
        return [(str(r["path"]), str(r["name"])) for r in rows]

    def files(self, root: str) -> list[str]:
        """Paths in the worktree's current view."""
        with self.lock:
            vid = int(self._view(root)["id"])
            return sorted(r["path"] for r in self.conn.execute("SELECT path FROM view_files WHERE view_id = ?",
                                                               (vid,)))

    def graph(self, root: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """(file -> dependencies, file -> importers) for the view, cached per generation."""
        with self.lock:
            view = self._view(root)
            key = (int(view["id"]), int(view["generation"]))
            if key in self._graph_cache:
                return self._graph_cache[key]
            files = {r["path"]: r["sha"] for r in self.conn.execute(
                "SELECT path, sha FROM view_files WHERE view_id = ?", (key[0],))}
            gomod = None
            if "go.mod" in files:
                try:
                    gomod = (Path(root) / "go.mod").read_text(encoding="utf-8", errors="replace")
                except OSError:
                    gomod = None
            resolver = Resolver(files, go_module_of(gomod))
            deps: dict[str, set[str]] = {}
            rev: dict[str, set[str]] = {}
            langs = {r["sha"]: r["lang"] for r in self.conn.execute("SELECT sha, lang FROM blobs")}
            imports: dict[str, list[sqlite3.Row]] = {}
            for r in self.conn.execute("SELECT * FROM imports WHERE sha IN (SELECT sha FROM view_files "
                                       "WHERE view_id = ?)", (key[0],)):
                imports.setdefault(r["sha"], []).append(r)
            for path, sha in files.items():
                for imp in imports.get(sha, []):
                    for target in resolver.resolve(path, langs.get(sha, ""), imp["module"], int(imp["level"] or 0),
                                                   json.loads(imp["names"] or "[]")):
                        if target != path:
                            deps.setdefault(path, set()).add(target)
                            rev.setdefault(target, set()).add(path)
            self._graph_cache = {key: (deps, rev)}
            return deps, rev

    def related(self, root: str, path: str) -> dict[str, Any]:
        deps, rev = self.graph(root)
        with self.lock:
            vid = int(self._view(root)["id"])
            all_paths = [r["path"] for r in self.conn.execute("SELECT path FROM view_files WHERE view_id = ?", (vid,))]
        stem = Path(path).stem.split(".")[0]
        tests = sorted(p for p in all_paths if is_test(p) and (path in deps.get(p, set()) or tested_stem(p) == stem)
                       and p != path)
        tested = sorted(d for d in deps.get(path, set()) if not is_test(d)) if is_test(path) else []
        return {"path": path, "imports": sorted(deps.get(path, set())), "importers": sorted(rev.get(path, set())),
                "tests": tests, "tests_cover": tested}

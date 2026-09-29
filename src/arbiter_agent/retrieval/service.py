"""Daemon-side retrieval service (M6.6): one :class:`RepoIndex` per repository, refreshed before
every query within a time budget, warmed in the background when a session first reports its
working directory. Content leaves only after the access policy is checked again, and every
response carries the index stamp (version, generation, HEAD)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from arbiter_agent.concurrency import Priority, WorkQueue
from arbiter_agent.retrieval.access_policy import AccessPolicy
from arbiter_agent.retrieval.embeddings import backend_from_config
from arbiter_agent.retrieval.index import RepoIndex
from arbiter_agent.state.repo_identity import RepoIdentity, identify

log = logging.getLogger("arbiter.retrieval")
HUB_IMPORTERS = 25      # a module imported by more files than this is a hub, not task context
CODE_EXTS = (".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java",
             ".kt", ".cs", ".rb", ".php", ".swift", ".scala", ".c", ".cc", ".cpp", ".h", ".hpp", ".vue", ".svelte")
USAGE_MAX_UNDEFINED = 40   # a name the repo doesn't define, mentioned in more files than this, isn't its code
USAGE_SKIP = {"True", "False", "None", "self", "cls", "null", "undefined", "true", "false", "this", "options",
              "args", "kwargs", "default", "value", "string", "number", "object", "Date", "list", "dict"}


def new_text_blocks(tool_input: Any, raw_path: str, rel: str) -> list[str]:
    """The text an edit put into a file: Pi/Claude replacement strings, or the resulting lines of
    each hunk of an apply_patch patch. Whole-file writes give nothing (the agent has the file)."""
    out: list[str] = []
    if isinstance(tool_input, dict):
        listed = tool_input.get("edits")
        for e in listed if isinstance(listed, list) else [tool_input]:
            if not isinstance(e, dict):
                continue
            if isinstance(e.get("file_path"), str) and e["file_path"] not in (raw_path, rel):
                continue
            for k in ("newText", "new_string", "new_str", "newString"):
                if isinstance(e.get(k), str) and e[k].strip():
                    out.append(e[k])
        if out:
            return out
    blob = tool_input if isinstance(tool_input, str) else json.dumps(tool_input, ensure_ascii=False)
    if "*** Update File:" not in blob:
        return []
    if not isinstance(tool_input, str):
        blob = blob.replace("\\n", "\n").replace('\\"', '"')
    for sec in re.split(r"\*\*\* (?=Update File:|Add File:|Delete File:|End Patch)", blob):
        m = re.match(r"Update File: ([^\n]+)\n", sec)
        if not m:
            continue
        name = m.group(1).strip().replace("\\", "/").lstrip("./")
        if not (rel.endswith(name) or name.endswith(rel)):
            continue
        for hunk in re.split(r"\n@@[^\n]*", sec[m.end() - 1:]):
            rows = hunk.splitlines()
            body = [ln[1:] for ln in rows if ln[:1] in (" ", "+")]
            if any(ln[:1] == "+" for ln in rows) and body:
                out.append("\n".join(body))
    return out


class RetrievalService:
    def __init__(self, data_dir: Path, key: bytes, config: Any, redaction_enabled: bool = True) -> None:
        self.dir = data_dir / "indexes"
        self.key = key
        self.config = config
        self.redaction_enabled = redaction_enabled
        self.budget_s = float(config.get("retrieval.refresh_budget_s", 3.0))
        self.include_generated = bool(config.get("retrieval.include_generated", False))
        self.embeddings = backend_from_config(config)
        self._indexes: dict[str, RepoIndex] = {}
        self._lock = threading.Lock()
        self.queue = WorkQueue("retrieval", capacity=16)
        self.stats = {"queries": 0, "refreshes": 0, "warmups": 0}

    def start(self) -> RetrievalService:
        self.queue.start()
        return self

    def stop(self) -> None:
        self.queue.stop()
        with self._lock:
            for idx in self._indexes.values():
                idx.close()
            self._indexes.clear()

    # ------------------------------------------------------------------ plumbing
    def _redact(self, text: str) -> tuple[str, int]:
        from arbiter_agent.privacy.redaction import Redactor

        r = Redactor(self.key, enabled=self.redaction_enabled)
        out = r.redact_text(text)
        return out, r.total

    def index_for(self, ident: RepoIdentity) -> RepoIndex:
        key = hashlib.sha256((ident.common_dir or ident.root).lower().encode()).hexdigest()[:16]
        with self._lock:
            idx = self._indexes.get(key)
            if idx is None:
                idx = self._indexes[key] = RepoIndex(self.dir / f"{key}.sqlite", redact=self._redact)
            return idx

    def prepare(self, cwd: str, budget_s: float | None = -1.0) -> tuple[RepoIdentity, RepoIndex, AccessPolicy, Any]:
        ident = identify(cwd)
        idx = self.index_for(ident)
        policy = AccessPolicy.for_repo(Path(ident.root), self.include_generated)
        res = idx.refresh(ident, policy, self.budget_s if budget_s == -1.0 else budget_s)
        self.stats["refreshes"] += 1
        return ident, idx, policy, res

    def warm(self, cwd: str) -> None:
        """Build or update the index in the background (full, no time budget)."""
        def job() -> None:
            try:
                self.prepare(cwd, budget_s=None)
                self.stats["warmups"] += 1
            except Exception:
                log.exception("index warm-up failed")

        self.queue.submit(job, Priority.BACKGROUND, key=f"warm:{cwd.lower()}")

    # ------------------------------------------------------------------ operations
    def _envelope(self, ident: RepoIdentity, idx: RepoIndex, res: Any, body: dict[str, Any]) -> dict[str, Any]:
        stamp = idx.stamp(ident.root).to_dict()
        out = {"index": stamp, "refresh": {"changed": res.changed, "removed": res.removed, "pending": res.pending,
                                           "seconds": res.seconds}, **body}
        if res.pending:
            out["note"] = (f"{res.pending} changed file(s) are still being indexed and were left out of these "
                           "results; ask again shortly")
        return out

    def search(self, cwd: str, query: str, limit: int = 20, path_glob: str | None = None) -> dict[str, Any]:
        ident, idx, policy, res = self.prepare(cwd)
        self.stats["queries"] += 1
        hits = idx.search(ident.root, query, limit=limit, policy=policy, path_glob=path_glob)
        return self._envelope(ident, idx, res, {"query": query, "hits": hits})

    def symbol(self, cwd: str, name: str, limit: int = 30) -> dict[str, Any]:
        ident, idx, policy, res = self.prepare(cwd)
        self.stats["queries"] += 1
        found = idx.symbol(ident.root, name, limit=limit)
        found["definitions"] = [d for d in found["definitions"] if policy.allowed(d["path"])]
        found["references"] = [r for r in found["references"] if policy.allowed(r["path"])]
        return self._envelope(ident, idx, res, {"name": name, **found})

    def related(self, cwd: str, path: str) -> dict[str, Any]:
        ident, idx, policy, res = self.prepare(cwd)
        self.stats["queries"] += 1
        rel = path.replace("\\", "/")
        root = Path(ident.root)
        p = Path(path)
        if p.is_absolute():
            try:
                rel = str(p.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                return self._envelope(ident, idx, res, {"error": f"{path} is outside the repository"})
        if not policy.allowed(rel):
            return self._envelope(ident, idx, res, {"error": f"{rel} is excluded by the access policy"})
        info = idx.related(ident.root, rel)
        for k in ("imports", "importers", "tests", "tests_cover"):
            info[k] = [x for x in info[k] if policy.allowed(x)]
        return self._envelope(ident, idx, res, info)

    def context(self, cwd: str, ctx: dict[str, Any], budget_s: float | None = -1.0) -> dict[str, Any]:
        """Files to read for a task (M9.2, advisory): pins first, then the fused ranking, adaptive k.
        ``budget_s`` bounds the index refresh (files left pending are omitted, never stale)."""
        from arbiter_agent.retrieval import candidates, reranker

        ident, idx, policy, res = self.prepare(cwd, budget_s)
        self.stats["queries"] += 1
        qc = candidates.QueryContext.from_dict(ctx)
        cands = candidates.gather(idx, ident.root, qc, policy=policy)
        return self._envelope(ident, idx, res, reranker.rerank(cands, qc).to_dict())

    def context_pack(self, cwd: str, ctx: dict[str, Any], budget_s: float | None, max_tokens: int) -> dict[str, Any]:
        """The ranking of :meth:`context` plus a pre-read pack of the files a task needs first."""
        from arbiter_agent.retrieval import candidates, context_pack, reranker

        ident, idx, policy, res = self.prepare(cwd, budget_s)
        self.stats["queries"] += 1
        qc = candidates.QueryContext.from_dict(ctx)
        ranking = reranker.rerank(candidates.gather(idx, ident.root, qc, policy=policy), qc).to_dict()
        files = [f for f in idx.files(ident.root) if policy.allowed(f)]
        deps, rev = idx.graph(ident.root)
        # Hub modules (imported by many files, like sympy/core/numbers.py) say nothing about a task.
        hubs = {f for f, importers in (rev or {}).items() if len(importers) > HUB_IMPORTERS}
        deps = {f: {d for d in ds if d not in hubs} for f, ds in deps.items()}
        pins = [p["path"] for p in ranking.get("pins", [])]
        ranked = [r["path"] for r in ranking.get("ranked", [])]

        fileset = set(files)
        tests_cache: dict[str, list[str]] = {}
        own_tests = bool(self.config.get("retrieval.auto_context_own_tests", False))

        def tests_of(p: str) -> list[str]:
            """With retrieval.auto_context_own_tests: the file's own test file by the repo's naming
            layout first (src/addDays/index.ts -> src/addDays/test.ts; pkg/misc.py ->
            pkg/tests/test_misc.py), then index tests named after it. Otherwise the index's tests, which
            put tests that merely import the file first, alphabetically (sympy's misc.py got
            geometry/tests/test_point.py; date-fns targets got none), and measured cheaper (2026-09-28)."""
            if not own_tests:
                return [t for t in idx.related(ident.root, p)["tests"] if policy.allowed(t)]
            if p not in tests_cache:
                from arbiter_agent.completion.auto_test import related_tests

                own = [t for t in related_tests(Path(ident.root), [p]) if t in fileset and t != p]
                stem = Path(p).parent.name if Path(p).stem in ("index", "__init__") else Path(p).stem.split(".")[0]
                named = [t for t in idx.related(ident.root, p)["tests"] if stem and stem in Path(t).name]
                tests_cache[p] = [t for t in dict.fromkeys(own + named) if policy.allowed(t)]
            return tests_cache[p]

        if ctx.get("pins_only"):
            # A follow-up prompt of the same conversation: only files the prompt names outright. The
            # ranking's guesses for a short follow-up ("do the same for `runs`") were mostly noise.
            picks = pins[:4]
            full = set(picks)
        else:
            picks = context_pack.select(ranked, pins, deps, tests_of, set(files), tests_first=own_tests)
            full = context_pack.core(ranked, pins, deps, tests_of, set(files))
            # The package __init__ that re-exports the top file: adding a public function means
            # exporting it there (every digital_root run on sympy read ntheory/__init__.py itself).
            # A package's hub __init__ (sympy/__init__.py: 161 importers) isn't task context; a hub module
            # can be (sympy's permutations.py is imported by 30+ files and was the target).
            picks = [p for p in picks if p in pins or not (p in hubs and p.endswith("__init__.py"))]
            strongest = pins[:3] or [p for p in picks if not p.endswith("__init__.py")][:1]
            for top in strongest:
                init = f"{top.rsplit('/', 1)[0]}/__init__.py" if "/" in top else None
                if init and init in set(files) and init in (rev or {}).get(top, set()):
                    picks = [p for p in picks if p != init]
                    picks.insert(min(2, len(picks)), init)
                    full.add(init)
                    break
        root = Path(ident.root)

        def read(rel: str) -> str | None:
            if not policy.allowed(rel):
                return None
            try:
                raw = (root / rel).read_bytes()
            except OSError:
                return None
            if b"\0" in raw[:4096]:
                return None
            return self._redact(raw.decode("utf-8", errors="replace"))[0]

        note = None
        if str(self.config.get("completion.auto_test", "off")) == "after_edit":
            from arbiter_agent.completion.auto_test import AutoTester

            tester = AutoTester(self.config)
            cmd = tester.base_command(root)
            if cmd and tester.scope(root) == "related":
                note = (f"Tests: after each code edit Arbiter runs the tests related to the changed files (based "
                        f"on `{cmd}`) and adds the result to that edit's tool output. Make all the edits a change "
                        "needs in one response: they run together and are tested once.")
            elif cmd:
                note = (f"Tests: Arbiter runs `{cmd}` after each code edit and adds the result to that edit's "
                        "tool output. Make all the edits a change needs in one response: they run together and "
                        "are tested once.")
        skip = {str(s).replace("\\", "/") for s in ctx.get("skip") or []}
        shown_keys: list[str] = []
        hit_lines = {**{r["path"]: int(r.get("line") or 1) for r in ranking.get("ranked", [])},
                     **{q["path"]: int(q.get("line") or 1) for q in ranking.get("pins", [])}}
        extra = []
        if self.config.get("retrieval.auto_context_usages", False):
            try:
                ub = context_pack.usage_block(self._usages(idx, ident.root, str(ctx.get("query") or ""),
                                                           files, picks, policy), max_chars=max_tokens)
            except Exception:
                log.exception("usage lookup failed")
                ub = None
            if ub:
                extra.append(ub)
        lean = context_pack.LEAN_NOTE if self.config.get("retrieval.auto_context_lean_note", False) else None
        text = context_pack.build(root, files, picks, read, max_tokens,
                                  contents=bool(self.config.get("retrieval.auto_context_pack_contents", True)),
                                  test_note=note, full=full, query=str(ctx.get("query") or ""), hit_lines=hit_lines,
                                  skip=skip, shown_keys=shown_keys, extra=extra, note=lean)
        map_text = None if skip else context_pack.build(root, files, picks, read, max_tokens, contents=False,
                                                        test_note=note)
        return self._envelope(ident, idx, res, {**ranking, "picks": picks, "text": text, "map_text": map_text,
                                                "shown_keys": shown_keys,
                                                "tokens": (len(text) + 3) // 4 if text else 0})

    def _usages(self, idx: RepoIndex, root: str, query: str, files: list[str], picks: list[str],
                policy: AccessPolicy) -> list[dict[str, Any]]:
        """For each code name the prompt mentions: every file that mentions it (the index's full-text
        channel finds candidates, a word-boundary scan of each confirms them and gives the lines).
        Files are scanned raw; only the snippets shown are redacted (redacting every file scanned
        took 2 s for a name in 100+ files)."""
        from arbiter_agent.completion.auto_test import is_test_file
        from arbiter_agent.retrieval import candidates

        fileset = set(files)
        project = {Path(root).name.lower()} | {f.split("/", 1)[0].lower() for f in files if "/" in f}
        names = []
        for n in candidates.named_symbols(query, project):   # `f(date, options)` -> f
            m = re.match(r"[A-Za-z_$][\w$]*", n)
            if m and len(m.group(0)) > 2 and m.group(0) not in USAGE_SKIP and m.group(0) not in names:
                names.append(m.group(0))
        out: list[dict[str, Any]] = []
        for name in names[:4]:
            rx = re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")
            found = idx.symbol(root, name, limit=10)
            defs = [d for d in found["definitions"] if d["path"] in fileset]
            if len({d["path"] for d in defs}) > candidates.MAX_DEFINERS:
                continue                                  # a generic name (`key`): everywhere, says nothing
            paths = list(dict.fromkeys(h["path"] for h in idx.search(root, name, limit=400, policy=policy)
                                       if h["path"] in fileset))
            if not defs and len(paths) > USAGE_MAX_UNDEFINED:
                continue                                  # a builtin or common word (`len`): not the repo's code
            code: list[dict[str, Any]] = []
            other: list[str] = []
            rootp = Path(root)
            for path in paths:
                try:
                    data = (rootp / path).read_bytes()
                except OSError:
                    continue
                if b"\0" in data[:4096]:
                    continue
                text = data.decode("utf-8", errors="replace")
                lines = text.splitlines()
                hits = [i + 1 for i, ln in enumerate(lines) if rx.search(ln)]
                if not hits:
                    continue
                if not path.endswith(CODE_EXTS):
                    other.append(path)
                    continue
                entry: dict[str, Any] = {"path": path, "lines": hits}
                def_lines = {int(d["line"]) for d in defs if d["path"] == path}
                if path not in picks:
                    first = next((n for n in hits if n not in def_lines), None)
                    if first is not None:
                        entry["snippet"] = self._redact(lines[first - 1].strip()[:110])[0]
                code.append(entry)
            if not code and not defs and f"`{name}" not in query:
                continue                                  # an unticked word that isn't code: say nothing
            rank = {d["path"]: 0 for d in defs}
            code.sort(key=lambda e: (rank.get(e["path"], 1 if is_test_file(e["path"].rsplit("/", 1)[-1]) else 2),
                                     e["path"]))
            item: dict[str, Any] = {"name": name, "definitions": defs, "files": code, "other": other}
            if defs and "/" in defs[0]["path"]:
                folder = defs[0]["path"].rsplit("/", 1)[0]
                kids = sorted({f[len(folder) + 1:].split("/", 1)[0] for f in files if f.startswith(folder + "/")})
                if len(kids) <= 8:
                    item["folder"] = (folder, kids)
            out.append(item)
        return out

    def edit_region(self, cwd: str, tool_input: Any, paths: list[str]) -> str | None:
        """The changed region of each edited file, for the edit's tool result (see
        :func:`context_pack.edit_region`). Only allowed files, redacted; None for whole-file writes."""
        from arbiter_agent.retrieval import context_pack

        ident = identify(cwd)
        root = Path(ident.root)
        policy = AccessPolicy.for_repo(root, self.include_generated)
        out: list[str] = []
        for raw in paths[:2]:
            p = Path(raw)
            try:
                full = (p if p.is_absolute() else root / p).resolve()
                rel = full.relative_to(root.resolve()).as_posix()
            except (ValueError, OSError):
                continue
            if not policy.allowed(rel):
                continue
            blocks = new_text_blocks(tool_input, raw, rel)
            if not blocks:
                continue
            try:
                data = full.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:4096]:
                continue
            region = context_pack.edit_region(rel, data.decode("utf-8", errors="replace"), blocks)
            if region:
                out.append(self._redact(region)[0])
        return "\n".join(out)[:4000] or None

    def status(self, cwd: str) -> dict[str, Any]:
        ident, idx, _, res = self.prepare(cwd, budget_s=0.5)
        return self._envelope(ident, idx, res, {"db": str(idx.db_path)})

    def gc(self, cwd: str, keep_days: float = 7.0) -> dict[str, Any]:
        ident = identify(cwd)
        return {"removed_blobs": self.index_for(ident).gc(keep_days)}

    def summary(self) -> dict[str, Any]:
        return {**self.stats, "indexes": len(self._indexes), "backlog": self.queue.backlog,
                "embeddings": self.embeddings.name}

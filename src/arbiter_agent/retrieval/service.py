"""Daemon-side retrieval service (M6.6): one :class:`RepoIndex` per repository, refreshed before
every query within a time budget, warmed in the background when a session first reports its
working directory. Content leaves only after the access policy is checked again, and every
response carries the index stamp (version, generation, HEAD)."""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from arbiter_agent.concurrency import Priority, WorkQueue
from arbiter_agent.retrieval.access_policy import AccessPolicy
from arbiter_agent.retrieval.embeddings import backend_from_config
from arbiter_agent.retrieval.index import RepoIndex
from arbiter_agent.state.repo_identity import RepoIdentity, identify

log = logging.getLogger("arbiter.retrieval")


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
        deps, _ = idx.graph(ident.root)
        pins = [p["path"] for p in ranking.get("pins", [])]
        ranked = [r["path"] for r in ranking.get("ranked", [])]

        def tests_of(p: str) -> list[str]:
            return [t for t in idx.related(ident.root, p)["tests"] if policy.allowed(t)]

        picks = context_pack.select(ranked, pins, deps, tests_of, set(files))
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

        text = context_pack.build(root, files, picks, read, max_tokens,
                                  contents=bool(self.config.get("retrieval.auto_context_pack_contents", True)))
        return self._envelope(ident, idx, res, {**ranking, "picks": picks, "text": text,
                                                "tokens": (len(text) + 3) // 4 if text else 0})

    def status(self, cwd: str) -> dict[str, Any]:
        ident, idx, _, res = self.prepare(cwd, budget_s=0.5)
        return self._envelope(ident, idx, res, {"db": str(idx.db_path)})

    def gc(self, cwd: str, keep_days: float = 7.0) -> dict[str, Any]:
        ident = identify(cwd)
        return {"removed_blobs": self.index_for(ident).gc(keep_days)}

    def summary(self) -> dict[str, Any]:
        return {**self.stats, "indexes": len(self._indexes), "backlog": self.queue.backlog,
                "embeddings": self.embeddings.name}

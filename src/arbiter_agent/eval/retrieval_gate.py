"""M9.2 exit gate: retrieval recall on the fixture repository (spec §20.17).

Recall per query = gold files found in pins + ranked top-k, divided by gold files. The gate is the
mean over queries (>= 0.95). Precision-like cost is reported as the average number of files selected.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

CORPUS = Path(__file__).resolve().parent / "corpus" / "retrieval"
MIN_RECALL = 0.95


def run(corpus: Path | None = None, queries: str = "queries.yaml") -> dict[str, Any]:
    from arbiter_agent.retrieval import candidates, reranker
    from arbiter_agent.retrieval.access_policy import AccessPolicy
    from arbiter_agent.retrieval.index import RepoIndex
    from arbiter_agent.state.repo_identity import identify

    corpus = corpus or CORPUS
    data = yaml.safe_load((corpus / queries).read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="arbiter-retrieval-eval-"))
    try:
        repo = tmp / "repo"
        shutil.copytree(corpus / str(data["repo"]), repo)
        ident = identify(str(repo))
        idx = RepoIndex(tmp / "index.sqlite")
        policy = AccessPolicy.for_repo(Path(ident.root), False)
        idx.refresh(ident, policy, None)
        deps, rev = idx.graph(ident.root)
        files = idx.files(ident.root)
        rows = []
        for q in data["queries"]:
            qc = candidates.QueryContext.from_dict(q)
            ranked = reranker.rerank(candidates.gather(idx, ident.root, qc, deps=deps, rev=rev, files=files,
                                                       policy=policy), qc)
            sel = ranked.selected()
            gold = list(q["gold"])
            got = [g for g in gold if g in sel]
            rows.append({"id": q["id"], "recall": len(got) / len(gold), "missing": [g for g in gold if g not in sel],
                         "selected": len(sel), "k": ranked.k, "pins": len(ranked.pins)})
        idx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(rows)
    recall = sum(r["recall"] for r in rows) / n if n else 0.0
    return {"queries": n, "recall": round(recall, 4), "avg_selected": round(sum(r["selected"] for r in rows) / n, 2),
            "full_recall_rate": round(sum(1 for r in rows if r["recall"] == 1.0) / n, 3),
            "passed": recall >= MIN_RECALL, "detail": rows}

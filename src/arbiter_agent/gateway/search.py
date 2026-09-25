"""Two-stage tool search behind the gateway (spec §10.4).

1. **Family selection:** score capability families from the query (name/description words).
2. **Tool ranking** within the chosen families, then across the rest.

A recall floor keeps at least ``min_results`` candidates, drawn from outside the top family when
needed. When the agent reports a capability miss (``broaden``), the search skips family selection
and ranks the whole catalog. The sensor may rerank candidates later; it never adds or removes tools.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from arbiter_agent.gateway.catalog import FAMILIES, Catalog, Tool, family_of

log = logging.getLogger("arbiter.gateway")
MIN_RESULTS = 5
FAMILY_BONUS = 1.5


# Generic vocabulary bridges between how tasks are phrased and how tools are named.
SYNONYMS = {
    "bug": ["issue"], "bugs": ["issue"], "ticket": ["issue"], "pr": ["pull", "request"], "prs": ["pull"],
    "folder": ["directory"], "folders": ["directory"], "dir": ["directory"], "contents": ["list", "read"],
    "rows": ["table", "query"], "row": ["table", "query"], "column": ["table", "describe"],
    "columns": ["describe", "table"], "said": ["history", "message"], "chat": ["message", "channel"],
    "remove": ["delete"], "rename": ["move"], "open": ["navigate", "create"], "site": ["url", "page"],
    "errors": ["console", "logs"], "ci": ["workflow"], "build": ["workflow"], "logs": ["log"],
    "readme": ["file", "contents"], "repo": ["repository"], "approve": ["review"],
}


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower().replace("_", " ")):
        if len(w) > 1:
            out.add(w)
            out.update(SYNONYMS.get(w, []))
            if len(w) > 4 and w.endswith("s"):
                out.add(w[:-1])
    return {w for w in out if len(w) > 2 or w in ("pr", "ci")}


def _score(t: Tool, qw: set[str]) -> float:
    name_w = _words(t.name)
    desc_w = _words(t.description)
    return 3.0 * len(qw & name_w) + 1.0 * len(qw & desc_w)


LEXICAL_KEEP, SEMANTIC_ADD = 5, 3


def search(catalog: Catalog, query: str, *, family: str | None = None, max_results: int = 8,
           broaden: bool = False, min_results: int = MIN_RESULTS, ranker: Any = None) -> dict[str, Any]:
    """``ranker`` (optional, ``semantic.EncoderRanker``): hybrid mode keeps the top lexical hits and adds
    the encoder's top picks, then fills with the rest of the lexical ranking."""
    qw = _words(query)
    tools = catalog.all()
    fam = family or (None if broaden else family_of(query, query))
    if fam == "other" or fam not in {f for f, _ in FAMILIES}:
        fam = None
    # stage 1 (family) is a preference, not a filter: a strong name match outside the family still ranks
    scored = sorted(((_score(t, qw) + (FAMILY_BONUS if fam and t.family == fam and _score(t, qw) > 0 else 0.0), t)
                     for t in tools), key=lambda x: (-x[0], x[1].tool_id))
    picked = [t for s, t in scored if s > 0][:max_results]
    if ranker is not None:
        try:
            sem = [t for i in ranker.rank(query, tools)[:SEMANTIC_ADD] if (t := catalog.get(i)) is not None]
            merged: dict[str, Tool] = {}
            for t in picked[:LEXICAL_KEEP] + sem + picked:
                merged.setdefault(t.tool_id, t)
            picked = list(merged.values())[:max_results]
        except Exception:                              # the encoder only ever adds candidates; say so if it fails
            log.warning("semantic tool ranking failed; lexical results only", exc_info=True)
    if len(picked) < min(min_results, len(tools)):                 # recall floor
        picked += [t for _, t in scored if t not in picked][:min_results - len(picked)]
    out: dict[str, Any] = {"query": query, "family": fam, "broadened": broaden or fam is None,
                           "mode": "hybrid" if ranker is not None else "lexical",
                           "results": [t.summary() for t in picked], "catalog_size": len(tools)}
    if picked:
        out["best"] = picked[0].canonical()        # the top hit's full schema: usually no tool_describe needed
    return out

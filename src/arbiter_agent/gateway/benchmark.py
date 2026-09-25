"""Per-client gateway benchmark and the enable/disable policy (spec §10.6, §20.7; M10.3).

Three ways a client can carry a tool catalog:
- **normal**: every schema in every request;
- **native deferral** (Claude Code, Cursor): tool names and short descriptions in every request, a full
  schema loaded when the agent looks a tool up (one extra round trip per tool);
- **gateway**: Arbiter's always-visible core plus three gateway schemas in every request, with
  ``tool_search`` + ``tool_describe`` per new tool (two extra round trips).

Session cost is measured in input-token equivalents: stable content is billed in full once and at
the cached rate on later calls; each extra round trip re-reads the conversation context (cached)
and produces a short output (weighted). The gateway is enabled for a client only if it's a
**material** improvement (>= 10% lower session cost, §20.17) over that client's own best mode, the
catalog is at least ``gateway.enabled_min_catalog_size``, and search recall stays >= 0.95. The
numbers are deterministic for a given catalog and session model, so the comparison needs no
confidence interval; the session model is recorded with every decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from arbiter_agent.gateway import search
from arbiter_agent.gateway.call import GATEWAY_TOOLS
from arbiter_agent.gateway.catalog import CORE_TOOLS, Catalog, from_server, tokens_estimate

CORPUS = Path(__file__).resolve().parent.parent / "eval" / "corpus" / "gateway"
MATERIAL = 0.10
MIN_RECALL = 0.95


@dataclass(frozen=True)
class SessionModel:
    calls: int = 30                 # model calls in a session
    tools_used: int = 6             # distinct tools the agent looks up and uses
    context_tokens: int = 12_000    # average conversation context re-read per extra round trip
    cache_rate: float = 0.1         # cached input billed at this fraction
    output_weight: float = 4.0      # output tokens cost this many input tokens
    lookup_output: int = 60         # output tokens for a search/describe round trip
    search_tokens: int = 220        # a tool_search result (short summaries)
    describe_rate: float = 0.3      # share of tools that need a separate tool_describe (the top hit's schema
                                    # comes back with tool_search)


def _stable(tokens: float, m: SessionModel) -> float:
    """Content present in every request: full price once, cached afterwards."""
    return tokens + tokens * m.cache_rate * (m.calls - 1)


def _round_trips(n: float, m: SessionModel) -> float:
    return n * (m.context_tokens * m.cache_rate + m.lookup_output * m.output_weight)


def _loaded(tokens: float, m: SessionModel) -> float:
    """Content loaded mid-session: full price once, then cached for (on average) half the session."""
    return tokens * (1 + m.cache_rate * m.calls / 2)


def session_costs(catalog: Catalog, core_defs: list[dict[str, Any]], m: SessionModel) -> dict[str, float]:
    core = sum(tokens_estimate(d) for d in core_defs)
    all_schema = catalog.schema_tokens() + core
    names_only = core + sum(tokens_estimate({"name": t.name, "description": t.description[:100]})
                            for t in catalog.all())
    gateway_fixed = core + sum(tokens_estimate(d) for d in GATEWAY_TOOLS)
    per_tool = catalog.schema_tokens() / len(catalog) if len(catalog) else 0.0
    u = m.tools_used
    return {
        "normal": _stable(all_schema, m),
        "native_deferral": _stable(names_only, m) + u * _loaded(per_tool, m) + _round_trips(u, m),
        "gateway": _stable(gateway_fixed, m) + u * _loaded(m.search_tokens + per_tool, m)
        + _round_trips(u * (1 + m.describe_rate), m),
    }


def break_even_schema_tokens(catalog: Catalog, core_defs: list[dict[str, Any]], m: SessionModel) -> float:
    """Total catalog schema size (tokens) above which the gateway beats normal schemas by the
    material margin, holding the per-tool lookup costs of this catalog fixed."""
    costs = session_costs(catalog, core_defs, m)
    core = sum(tokens_estimate(d) for d in core_defs)
    # normal = stable(S + core) must exceed gateway / (1 - MATERIAL)
    target = costs["gateway"] / (1 - MATERIAL)
    return max(0.0, target / (1 + m.cache_rate * (m.calls - 1)) - core)


def recall(catalog: Catalog, tasks: list[dict[str, Any]], k: int = 8, ranker: Any = None) -> tuple[float, list[str]]:
    rel = [t for t in tasks if catalog.get(t["gold"])]
    misses = []
    for t in rel:
        ids = [r["tool_id"] for r in search.search(catalog, t["task"], max_results=k, ranker=ranker)["results"]]
        if t["gold"] not in ids:
            misses.append(t["task"])
    return (1.0 - len(misses) / len(rel)) if rel else 1.0, misses


@dataclass
class Decision:
    client: str
    enabled: bool
    reasons: list[str]
    catalog_size: int
    costs: dict[str, float]
    baseline_mode: str
    savings: float
    recall: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(client: str, catalog: Catalog, core_defs: list[dict[str, Any]], *, native_deferral: bool,
           tasks: list[dict[str, Any]] | None = None, mode: str = "auto", min_catalog: int = 20,
           model: SessionModel | None = None, measured_recall: float | None = None) -> Decision:
    """``measured_recall``: held-out recall of the active search mode (from ``arbiter gateway bench``);
    when given it's used instead of recomputing on ``tasks``."""
    m = model or SessionModel()
    costs = session_costs(catalog, core_defs, m)
    baseline_mode = "native_deferral" if native_deferral else "normal"
    base = costs[baseline_mode]
    savings = (base - costs["gateway"]) / base if base else 0.0
    rec = measured_recall if measured_recall is not None else recall(catalog, tasks or [])[0]
    why = [f"catalog {len(catalog)} tools", f"session cost vs {baseline_mode}: {savings:+.0%}",
           f"search recall {rec:.2f}"]
    if mode == "off":
        return Decision(client, False, why + ["gateway.enabled: off"], len(catalog), costs, baseline_mode, savings, rec)
    if mode == "on":
        return Decision(client, True, why + ["gateway.enabled: on (forced)"], len(catalog), costs, baseline_mode,
                        savings, rec)
    ok = len(catalog) >= min_catalog and savings >= MATERIAL and rec >= MIN_RECALL
    if len(catalog) < min_catalog:
        why.append(f"below the {min_catalog}-tool minimum")
    if savings < MATERIAL:
        why.append(f"not a material gain over {baseline_mode}")
    if rec < MIN_RECALL:
        why.append("search recall too low")
    return Decision(client, ok, why, len(catalog), costs, baseline_mode, savings, rec)


def reference(servers: list[str] | None = None) -> tuple[Catalog, list[dict[str, Any]]]:
    """The reference catalog (optionally a subset of servers), all tools confirmed as annotated."""
    from arbiter_agent.gateway.catalog import own_tools, schema_hash
    from arbiter_agent.shims.mcp_server import TOOLS

    data = yaml.safe_load((CORPUS / "servers.yaml").read_text(encoding="utf-8"))["servers"]
    tasks = yaml.safe_load((CORPUS / "tasks.yaml").read_text(encoding="utf-8"))["tasks"]
    cat = Catalog(own_tools(TOOLS))
    for name, tools in data.items():
        if servers is None or name in servers:
            cat.add(from_server(name, tools, {t["name"]: schema_hash(t) for t in tools}))
    return cat, tasks


def core_defs() -> list[dict[str, Any]]:
    from arbiter_agent.shims.mcp_server import TOOLS

    return [t for t in TOOLS if t["name"] in CORE_TOOLS]


SCENARIOS = {"arbiter only": [], "+ filesystem": ["filesystem"], "+ github, filesystem": ["github", "filesystem"],
             "+ all five servers": ["github", "filesystem", "postgres", "browser", "slack"]}


def heldout_tasks() -> list[dict[str, Any]]:
    return list(yaml.safe_load((CORPUS / "heldout_tasks_v1.yaml").read_text(encoding="utf-8"))["tasks"])


def search_quality(ranker: Any = None) -> dict[str, Any]:
    """Recall@8 of a search mode on the full reference catalog: development and held-out tasks."""
    cat, dev = reference()
    d, dm = recall(cat, dev, ranker=ranker)
    h, hm = recall(cat, heldout_tasks(), ranker=ranker)
    return {"mode": "hybrid" if ranker is not None else "lexical", "dev_recall": round(d, 3),
            "heldout_recall": round(h, 3), "heldout_misses": hm, "dev_misses": dm}


def run(min_catalog: int = 20, measured_recall: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"model": asdict(SessionModel()), "scenarios": {}}
    for label, servers in SCENARIOS.items():
        cat, tasks = reference(servers)
        rows = {}
        for nd in (False, True):
            d = decide("normal client" if not nd else "native-deferral client", cat, core_defs(), native_deferral=nd,
                       tasks=tasks, min_catalog=min_catalog, measured_recall=measured_recall)
            rows["native_deferral" if nd else "normal"] = {"enabled": d.enabled, "savings": round(d.savings, 3),
                                                            "recall": round(d.recall, 3), "reasons": d.reasons}
        rec, misses = recall(cat, tasks)
        out["scenarios"][label] = {"tools": len(cat), "schema_tokens": cat.schema_tokens(),
                                   "break_even_schema_tokens": round(break_even_schema_tokens(cat, core_defs(),
                                                                                              SessionModel())),
                                   "costs": {k: round(v) for k, v in
                                             session_costs(cat, core_defs(), SessionModel()).items()},
                                   "recall": round(rec, 3), "recall_misses": misses, "decisions": rows}
    return out

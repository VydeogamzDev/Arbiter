# 0033 — M10 tool gateway: approval-preserving proxying, benchmark-gated per client, hybrid search

- Status: Accepted
- Date: 2026-09-25
- Spec: §10, §20.7, §20.17, §4.5.2
- Builds on: [0006](0006-gateway-adoption-and-approvals.md), [0031](0031-tier0-runtimes-and-heldout-corpus.md), [0032](0032-m9-advisory-modules.md)

## Context
A stable gateway (`tool_search` / `tool_describe` / `tool_call`) saves prompt tokens only when a client carries a large tool catalog, and only if the agent can still find the right tool. Proxying other servers must never weaken the client's per-tool approvals or widen what a server can reach.

## Decisions
1. **Catalog** (`gateway/catalog.py`). It holds Arbiter's own tools, with classifications in code, plus servers the user explicitly adopted.
   - Descriptions, schemas and annotations come from the server's own `tools/list`: the text the client would show.
   - A tool is **read-only** only if it's annotated `readOnlyHint` and not `destructiveHint`, *and* the user confirmed that at adoption. The confirmation stores a schema hash, so a changed tool loses it.
   - Arbiter's hook and core task tools always stay directly visible.
2. **Gateway tools** (`gateway/call.py`):
   - `tool_call` checks the id against the current catalog and the arguments against the canonical schema (a stdlib JSON Schema subset), then applies the authorization bridge and dispatches;
   - every failure is a structured error;
   - `tool_search` returns the top hit's full schema, so the usual case is one round trip.
3. **Search** (`gateway/search.py`, `semantic.py`):
   - two-stage (family, then tool) with the family as a *preference*, not a filter: as a filter it pushed correctly named tools out of the top results;
   - a recall floor, and `broaden` for capability misses;
   - generic synonyms;
   - **hybrid mode** adds the tier-0 encoder's top three picks to the top five lexical hits, in one encoder pass that scores every tool as a label. The sensor only adds candidates; it can't hide, grant or authorize anything.
4. **Approval preservation** (`authorization_bridge.py`, `approval_mirror.py`):
   - read-only tools run;
   - tools that may change things need a **per-call** MCP elicitation showing the exact tool and arguments. Only `accept` with a boolean `approve: true` counts;
   - there's no inheritance between tool ids, and denials are sticky for the same call in a session;
   - without elicitation, mutating proxied calls are denied, and **adoption is refused** for servers with mutating tools, so they stay direct and keep native approvals;
   - profiles gained `elicitation` (conservative: only VS Code is marked), and the shim double-checks the client's declared capabilities.
5. **No scope expansion** (`upstream.py`):
   - adopted servers run with exactly the client's launch spec: command, args, env overlay, cwd;
   - the gateway offers them no roots, sampling or elicitation, and refuses server-to-client requests;
   - remote (HTTP/SSE) servers can't be adopted yet.
6. **Adoption** (`adopt.py`, `toml_tables.py`, `arbiter gateway adopt|release|servers`):
   - it goes through setup's machinery: diff, backup, a manifest record `gateway_adopt`, validation, restore on failure;
   - Codex tables are removed and restored as exact text;
   - JSON entries are removed only if unchanged, and re-added only if absent;
   - `arbiter uninstall` restores adopted servers too.
7. **Benchmark and per-client policy** (`benchmark.py`, `arbiter gateway bench`):
   - the session cost model: stable content is billed in full once and at the cached rate afterwards; each extra round trip re-reads the context;
   - the gateway is enabled for a client only on a **material** gain (≥ 10%, §20.17) over that client's own best mode (normal schemas or native deferral), with at least 20 tools, and **held-out** search recall ≥ 0.95 for the active search mode;
   - decisions are recorded for the shim;
   - a client with adopted servers always gets gateway mode (those tools exist nowhere else); `gateway.enabled: on|off` forces it.
8. **M10.4 bounded retrieval automation.** Reranked context (pins, fused ranking, adaptive k) is live in `arbiter_context`, `arbiter advice` and the host `rerank_candidates` operation.
   - Automatic injection into prompts (`retrieval.auto_context`) is implemented but **opt-in**, like `ui.inject_status`, because it adds tokens and client formats vary.
   - When on: it fires only on prompts that start a task, within a 250 ms deadline (skipped, never waited for), only when confident, and capped at about 150 tokens.

## Results (2026-09-25)

| Reference catalog | Tools | Gateway vs normal | Gateway vs native deferral |
| --- | --- | --- | --- |
| Arbiter only | 10 | −197% (off) | −41% (off) |
| + filesystem | 21 | −85% (off) | −27% (off) |
| + GitHub, filesystem | 47 | **+24% (on, hybrid)** | −3% (off) |
| + all five servers | 73 | **+44% (on, hybrid)** | **+13% (on, hybrid)** |

- **Search recall@8:**
  - lexical: 0.975 on development tasks, **0.85 held-out**, so the gateway stays off everywhere with lexical search;
  - hybrid: 0.975 development, **0.95 held-out**, so it's on where the savings are material.
- **Break-even:** about 5,000 tokens of total catalog schema. Many published servers exceed that alone.
- **A bug the benchmark found:** hybrid search silently fell back to lexical because of an unhashable dedupe that a catch-all hid. It now logs, and a regression test covers it.

## Consequences
- **Exit:**
  - material benefit where enabled: met in the session model, with the caveat that it's modeled, not measured from real agent runs;
  - zero approval-granularity regressions: covered by tests (read-only-only proxying without elicitation, per-call approval, no inheritance, sticky denials, refused adoption);
  - no scope expansion: covered by tests (exact env and cwd, server requests refused).
- On a machine without the `[encoder]` extra, search is lexical and the gateway stays off unless servers are adopted. That's the correct outcome of "the scheduler must earn its complexity" (§10.6).
- Next steps for real evidence: measure actual sessions on Codex, the primary client and one without native deferral, with a large adopted catalog; and adopt HTTP servers once an upstream HTTP client exists.

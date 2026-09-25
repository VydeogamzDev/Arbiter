# 0032 — M9 advisory modules: diff risk, context retrieval, model x effort, Host Advisory API v0

- Status: Accepted
- Date: 2026-09-25
- Spec: §4.6, §8, §11, §13, §20.17
- Builds on: [0028](0028-m6-repository-indexes.md), [0030](0030-m8-policy-core-and-breakers.md), [0031](0031-tier0-runtimes-and-heldout-corpus.md)

## Context
M9 adds the first modules that recommend something instead of only checking. They must be explainable, must never widen what a host allows, and must be measured on data they weren't tuned on.

## Decisions
1. **Diff risk (M9.3, shadow)** (`review/`):
   - deterministic tags from paths and changed lines, with levels low / medium / high / critical;
   - critical is reserved for tags that can never be skipped by a semantic score: destructive schema or data changes, removed security or access checks, deploy pipelines;
   - review floors come from `review.*_floor`;
   - blast radius over the M6 import graph can raise a file one level;
   - hard-coded credentials reuse the ingest secret detectors;
   - the analysis also maps tests, orders tests (recent failures, then risk), allocates review (§13.4), and draws a stratified low-risk audit sample (§13.6);
   - it's shown in advice and logged, and no gate reads it.
2. **Context retrieval (M9.2)** (`retrieval/candidates.py`, `reranker.py`, `miss_detector.py`):
   - pins first: files referenced by the error, files the user named or changed, definitions of named symbols;
   - then five channels fused with weighted reciprocal-rank fusion: any-term prefix full-text, identifier substrings, paths, the import graph, and tests;
   - query words get light stemming and a small map of generic programming concepts;
   - top-k adapts to score entropy, recent misses, phase, and the prompt budget;
   - misses (edits or errors outside the surfaced set, repeated searches) broaden the next retrieval before reasoning escalates;
   - surfaces: MCP `arbiter_context`, `arbiter context`, and the host `rerank_candidates` operation.
3. **Reasoning scheduler (M9.1)** (`reasoning/`):
   - a capability lattice built from the host's allowed set, never hard-coded. Capability is relative to that set, and absolute tier floors stay with the host;
   - a wanted level from task tier, phase, failures, loop and no-progress. A retrieval miss doesn't escalate: broaden retrieval first;
   - a risk floor from diff risk, integrity alerts and contracts;
   - leases with hysteresis: de-escalating needs two progress checkpoints;
   - the choice is the lowest expected cost per successful call within a 0.05 non-inferiority margin of the best estimated success. Cost counts cached vs uncached input, so a cold model is priced, plus the effort's output multiplier;
   - the success prior is a steep logistic in the capability margin, updated by the host's reported outcomes for the task. It's a ranking device, not a probability.
4. **Host Advisory API v0, shadow** (`host/api.py`). Operations over authenticated IPC: `host.capabilities`, `recommend_call`, `rerank_candidates`, `session_signals` and `report_outcome`.
   - Responses carry `mode: shadow`, so hosts log recommendations without applying them.
   - Anything outside the allowed set, a missed deadline or an error becomes an abstention.
   - Decisions and outcomes live in per-project partitions (§4.6.3): outcomes never cross projects.
   - `report_outcome` keeps only known fields, and only the host that received a decision can report on it.
5. **Hosted clients (T1)** get advice, not control (§8.5): MCP `arbiter_advice` and `arbiter advice` show suggested effort with reasons, broaden-retrieval or replan advice, signals, and diff risk with test order.
6. **Audit trail:** every recommendation, rerank and session-advice read goes into `advisory_decision` (migration 0006) with its inputs, reasons and outcome. `arbiter advice --history` shows it.
7. **Evaluation discipline.** Each module has a development set and a frozen held-out set. A held-out set runs once; when it has been used to tune, it's retired to the development pool and replaced. `arbiter eval --advisory` runs the current gates.

## Results (2026-09-25)
- **Retrieval recall:** 0.992 on 30 development queries (`shop`, Python); 1.0 on 12 held-out `shop` queries and 1.0 on 15 held-out queries on a TypeScript `notes` repo. About 8.5 files are selected per query.
  - Caveat: the fixtures are small (20–35 files), so recall at k ≈ 9 is weaker evidence than on a real repository; 9 random files would reach about 45% on `notes`.
- **Diff risk, dangerous misses** (a high/critical change rated below high):
  - 0 of 161 cases across the four development sets, including three retired held-out sets;
  - each held-out set measured on its single run: v1 20% (6/30), v2 7.5% (3/40), v3 22.5% (9/40);
  - no high-risk change was ever rated *low* (skippable review); every miss was rated medium, so it still got normal review.
- **Audit trail:** every advisory output has an id, inputs, human-readable reasons, and an outcome slot.

## Consequences
- **The M9 exit is only partly met.** Recall and the audit trail meet it. The ≤1% dangerous-miss target is met on development data only. On unseen diffs, deterministic rules keep meeting new categories (SSRF, traversal, template escaping, dynamic dispatch...).
  - The spec already expects semantic features to complement the rules (§13.2). Closing the gap needs real, independently labeled diffs (from real repositories, labeled by someone other than the rule author) and a diff-risk sensor family, not more rounds of assistant-written cases.
  - Until then diff risk stays shadow-only, and the rules never lower a level.
- Nothing in M9 changes what a client or host does. Promotion from shadow to applied is M11 (Hivemind host, non-inferiority gates) and M12.

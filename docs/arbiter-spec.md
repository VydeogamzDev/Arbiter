---
title: "Arbiter — Adaptive Agent Control Plane"
subtitle: "Master Technical Specification v3.2 — Red-Team Hardened, Multi-Client"
date: "September 24, 2026"
previous: "v3.1 (Adaptive Codex Agent Control Plane), September 21, 2026"
---

# 1. Executive Summary

This project is a local control plane for Codex that uses SemIf with Qwen3.5-4B as one semantic sensor inside a larger deterministic scheduling system. The local model does not replace Codex/Astra, write the solution, certify correctness, or authorize dangerous actions. Its role is narrower: help decide where expensive model attention, prompt space, retrieval bandwidth, tool exposure, review effort, and optional parallel exploration are likely to pay off.

Version 3.1 keeps the six integrated control-plane functions from v3 while hardening the architecture against the main failure modes found in a red-team review:

1. **Reasoning Scheduler** — choose the supported reasoning effort for the next substantive model call from the model's real capability set, based on the marginal value of more thinking and the measured cache cost of changing effort.
2. **Context Scheduler** — maintain HOT/WARM/COLD prompt state without destroying the durable audit record; active-session “discard” means eviction from model-visible context, not irreversible deletion.
3. **Tool / Schema Scheduler** — reduce tool-schema overhead primarily through a stable capability gateway rather than frequent mid-thread schema mutation, while leaving authorization to upstream policy.
4. **Semantic Code Retrieval Scheduler** — rerank lexical, AST, symbol, dependency, and optional vector candidates while maintaining deterministic recall floors and branch-aware index validity.
5. **Completion & Verification Gate** — prevent premature completion and optional overwork through evidence-backed contracts, test-integrity checks, and explicit UNKNOWN states rather than semantic confidence alone.
6. **Diff Risk & Review Scheduler** — allocate review and testing effort according to blast radius, consequence, and evidence while preserving deterministic minimum review floors.

Version 3.1 also retains four cross-cutting layers:

1. **Operational Task-State IR + Contract Compiler** — compile noisy conversation/tool history into a bounded operational state while preserving immutable raw user intent and observable facts separately from inferred state.
2. **Constrained Learned Utility Policy** — learn which controller actions improve the cost/latency frontier subject to non-inferiority and risk constraints; do not optimize a single scalar reward without hard quality floors.
3. **Speculative Pure-Read Execution** — prefetch only operations that are genuinely side-effect-free under a capability allowlist, strict resource limits, dependency-aware cache keys, and cancellation.
4. **Selective Disagreement Branching** — briefly explore competing frontier-model hypotheses or patches in strongly isolated sandboxes only when measured uncertainty and expected value justify the extra remote compute.

A hierarchical decision cascade handles controller judgments with the cheapest mechanism that is adequate: deterministic rules first, then optional learned predictors, then SemIf, and only rarely a frontier-model audit. SemIf probabilities are treated as uncalibrated model scores until workload-specific calibration proves otherwise.

The red-team review identified one especially important implementation constraint: **the full architecture cannot be assumed to work as a pure external app-server sidecar today.** Current Codex exposes useful events and reasoning settings, but custom replacement of active conversation history is not a stable external API, and several relevant app-server/context-management paths remain experimental or have open bugs. Version 3.1 therefore defines explicit deployment modes and never claims a feature is available until a startup capability probe and conformance test prove it on the exact Codex build in use. [S2][S4][S9][S10][S11][S12]

The system is therefore closer to an operating-system scheduler than another autonomous agent. Codex/Astra remains the process doing the intellectual work; the control plane allocates scarce resources around it, preserves task truth outside the model window, and degrades conservatively when any optimization layer is uncertain.

## Multi-client scope (v3.2)

Arbiter is not Codex-only. The core is host-agnostic. It connects through a **client adapter layer** (§4.4) with **one-command setup** (§4.5) to:

- Codex desktop (the primary development target);
- Codex CLI;
- Claude Code;
- other agent CLIs and desktop apps.

Where this document says "Codex" or "Codex/Astra" in a general sense, read "the host agent." The Codex-specific upstream findings [S2]–[S20] apply to the Codex adapter. Every other client goes through its own capability probe and conformance suite. No finding about one client is assumed to hold for another.

Integration is tiered, because clients expose very different surfaces. Nearly every agent supports MCP (tier 1). Clients with hook systems add enforcement (tier 2). Known transcript formats add passive telemetry (tier 3). Arbiter-hosted "driver" sessions add full control (tier 4). Tiers are additive sets, not a ladder. A client may be T1+T3 without T2. Each module declares the tier set it needs, and each client runs with the tier set it has verifiably passed, never an assumed one.

## Orchestrator hosts (v3.2)

Hosted client apps such as Codex desktop can't give Arbiter per-call control of model, effort, or prompt contents. The T4 columns of this spec are therefore delivered through **orchestrator hosts**: applications that already assemble every prompt and launch every agent call themselves. The first host is **Hivemind AI**, the user's multi-agent coding orchestrator.

- Arbiter stays a **separate project**, and Hivemind calls it through a versioned Host Advisory API (§4.6).
- Later, Arbiter ships inside the Hivemind app as an **optional component** users can enable.
- Hivemind must remain fully functional without it.
- Arbiter's standalone hosted-client mode (T1–T3) continues unchanged for everyday use in Codex desktop, Claude Code, and other clients.

## Primary objective

Minimize total remote-model usage and wall-clock time to a correct, evidence-supported task completion **subject to a predeclared quality and safety floor** relative to an appropriate baseline. Cost and latency are optimized only after correctness, contract preservation, permissions, and high-consequence verification requirements are satisfied.

The objective is system-level constrained optimization, not any single local metric. A controller that saves 40% of reasoning tokens but increases retries or false completion is a failure. A context compressor that removes 85% of history but forces rediscovery is a failure. A retrieval filter that hides the one relevant file is a failure. A learned policy that wins on average but creates a severe tail-risk regression is a failure.

## v3.1 hardening summary

The master now explicitly addresses the material weaknesses found during red-team review:

- separates **external sidecar**, **stable capability-gateway**, and **patched/native Codex** deployment modes;
- treats SemIf as a fallible semantic sensor, not a correctness oracle;
- replaces “authoritative inferred state” with an operational IR backed by immutable raw user intent and provenance;
- uses an append-only audit/event log and forbids irreversible active-task evidence deletion;
- makes fail behavior asymmetric: optimization fails open to stock Codex, but verification/integrity/speculation fail conservatively;
- adds actual-tokenizer request budgeting, output validation, bounded queues, cancellation, backpressure, and circuit breakers;
- replaces frequent dynamic tool-schema churn with a stable tool gateway where possible;
- removes speculative tests and arbitrary shell commands from the default safe-read set;
- treats git worktrees as a storage convenience, not a security boundary, and requires stronger branch isolation;
- adds privacy, secret-handling, encryption/retention, index-access, and local-data controls;
- adds goal epochs and interruption cancellation so new user requests cannot be overwritten by stale task state;
- adds test-integrity defenses so “progress” cannot be manufactured by deleting or weakening tests;
- corrects the learned-policy plan for confounding, stochastic counterfactuals, propensity logging, and safe experimentation;
- adds paired evaluation, non-inferiority margins, confidence intervals, multiple baselines, and severity-weighted failures;
- downgrades Q8 on NVIDIA from an assumed deployment path to an optional future optimization that must first exist and pass parity tests.

## v3.2 multi-client summary

- Generalizes the host from Codex to any agent CLI or desktop app, with Codex desktop as the primary target.
- Adds a single per-user **Arbiter daemon**. It owns the SQLite writer, SemIf, and indexes. Clients reach it through thin, stateless shims.
- Adds an **MCP shim**, a **hook CLI**, **transcript watchers**, and **T4 surfaces** (orchestrator hosts, §4.6; optional driver mode) as the integration surfaces.
- Adds **declarative, versioned client profiles**, so supporting a new agent means adding a data file, not writing custom code.
- Adds **capability tiers (T1–T4)** and a per-module minimum-tier requirement.
- Adds **`arbiter setup` / `doctor` / `uninstall`**. Setup auto-detects clients and makes idempotent, backed-up, reversible config edits at user scope.
- Makes the GPU optional at install. A null (always-abstain) SemIf backend lets the control plane run rules-only until SemIf is explicitly enabled.
- Bounds completion-gate hook blocking so a gate can never trap the host agent in a stop loop.

## v3.2 audit revisions

A pre-build audit (September 24, 2026) added the following. Rationale for each is recorded in `docs/decisions/`.

- **Completion gate.** Acts only on recognized completion claims, not on every turn end. `annotate` is the default and `block` is opt-in (§12.7).
- **Contracts.** Proposed by the host agent with verbatim quote provenance. Arbiter verifies quotes, recipes, and coverage deterministically, and the agent never sets PASS (§6.8.1).
- **Evidence origin** (`arbiter_observed` / `host_reported` / `agent_asserted`) limits what each source can prove (§6.4.1).
- **Verification observation.** Runner parsers, report files, `arbiter verify`, and a session baseline for test integrity (§12.4, §12.8).
- **Goal epochs without a model.** Candidate/confirmed epochs, so ordinary replies don't wipe task state (§6.3.1).
- **Gateway.** Explicit adoption of existing MCP servers only, read-only proxying unless approval can be mirrored, and disabled where the client already defers tool schemas (§10.6–10.7).
- **Honest hosted-session scope.** What Arbiter can't control in app-started sessions (§4.4.3), plus a separate hosted-client planning range (§30.0).
- **Security and privacy.** IPC trust (§18.8); project scope, redaction at ingest, and retention/storage caps (§16.3.1–16.3.3).
- **Operability.** Versioned upgrades (§16.5), Arbiter's own diagnostics (§16.6), repository identity and path normalization (§11.9).
- **Hook hygiene.** SemIf is never awaited in synchronous hooks, and injected context has a token budget.
- **Structure and gates.**
  - One availability taxonomy (§2).
  - Numeric default gates (§20.17).
  - Circuit breakers before automation in the rollout itself (§23).
  - A defined v0.1 scope (§23.3).
  - A separate research track.
  - Assumption spikes (§27, step 0).
- **Package name** is `arbiter-agent` (§4.5.1).

## v3.2 orchestrator-host revision

Added on September 24, 2026, after reviewing Hivemind AI. Rationale is in `docs/decisions/0013` and `0014`.

- **Orchestrator hosts.** T4 is delivered through orchestrator hosts via a versioned Host Advisory API (§4.6). Hivemind is the first host. Arbiter stays a separate project, and later becomes an optional component in the Hivemind app.
- **Model-tier selection** moved into the core Reasoning Scheduler (§8.8), always within the host's allowed set.
- **Economics use billed tokens,** because cache reads dominate measured input (§17.5, §30.4).
- **Host context control is conservative:** no blunt caps, pressure-triggered, referenced content pinned, and gated on the host's integrity probes (§9.11).
- **Host data stays confined to its project** (§4.6.3).
- **Arbiter-hosted driver sessions** are deprioritized to a later extension.

# 2. Scope: Six Core Modules + Four Cross-Cutting Upgrades

| Module | Core question | Primary resource controlled |
| --- | --- | --- |
| Reasoning Scheduler | Would a supported higher reasoning tier materially improve the next action after cache/latency cost? | Reasoning compute / latency |
| Context Scheduler | What must remain model-visible, and what can be evicted while remaining durably recoverable? | Prompt tokens / cache pressure |
| Tool / Schema Scheduler | Which capabilities should be discoverable now without destabilizing the prompt prefix? | Tool-schema tokens / choice complexity |
| Semantic Retrieval Scheduler | Which candidate code/evidence chunks deserve prompt space without sacrificing recall? | Repository context / retrieval bandwidth |
| Completion & Verification Gate | Is the requested work verified enough to finish, and what evidence is still missing? | Extra model turns / verification work |
| Diff Risk & Review Scheduler | Which changes deserve expensive review and which tests have highest immediate value? | Review compute / test time |

The six modules remain the user-facing control-plane functions. Version 3.1 retains four cross-cutting upgrades:

| Upgrade | Core question | Primary effect |
| --- | --- | --- |
| Operational Task-State IR + Contract Compiler | What operational state is supported by immutable intent, facts, and evidence? | Shared state / requirement retention |
| Constrained Learned Utility Policy | Which allowed action improves the cost/latency frontier while preserving quality floors? | Global resource allocation |
| Speculative Pure-Read Execution | Which genuinely side-effect-free result is likely enough to prefetch? | Wall-clock latency |
| Selective Disagreement Branching | When are two short isolated trajectories more valuable than deepening one trajectory? | Hard-task reliability / dead-end avoidance |

Several ideas remain deliberately outside the standalone-module count:

- **Ask-the-user / clarification gating remains excluded.** The controller may preserve ambiguity and choose conservative defaults, but it does not add a dedicated interruption policy.
- **Persistent memory admission is part of Context Scheduling.**
- **Test scheduling is part of Diff Risk + Verification.**
- **Model-tier selection is part of the Reasoning Scheduler (§8.8), not a later extension.** Host evidence showed model-tier choice to be a far larger cost lever than effort or context trimming. Arbiter chooses model × effort only inside a host-supplied allowed set.
- **The decision cascade, capability probe, circuit breaker, audit log, privacy layer, and tool gateway are infrastructure, not additional semantic use cases.**

## Deployment-scope rule

v3.2 uses **one** availability taxonomy. It replaces the v3.1 tags SIDE-CAR SAFE / GATEWAY SAFE and the Mode A/B labels.

- **Client tier requirement.** Each feature declares the minimum set of client tiers it needs (T1 MCP, T2 hooks, T3 transcripts, T4 driver; §4.4.3). The feature is active for a client only if that client's **verified tier set** satisfies the requirement.
- **`NATIVE` flag.** The feature additionally needs a patched host, or an upstream API that is not stable enough to assume (formerly Mode C / NATIVE/PATCHED).
- **`EXPERIMENTAL` flag.** The feature stays shadow-only until the exact client build passes its conformance tests.

The master must never silently treat a `NATIVE` capability as available on an unpatched host, or treat a tier as verified because a profile claims it.

# 3. Design Principles

1. **SemIf schedules; Codex solves.** The local 4B model is a bounded semantic sensor, not a second coding agent and not an authority source.
2. **Raw user intent is immutable evidence.** The Task-State IR may summarize or interpret it, but it may not overwrite the original goal/constraint turns.
3. **Facts, intent, and inference are different types.** Observable tool/test/file facts are stored separately from inferred hypotheses and semantic labels.
4. **Optimize under constraints, not one reward.** Correctness, permissions, contracts, and high-consequence verification are hard constraints; cost/latency optimization happens inside that feasible set.
5. **Use bounded questions.** Binary or small typed-option judgments are preferable to free-form plans, but raw model scores are not probabilities until calibrated.
6. **Deterministic policy owns authority.** SemIf and learned policies may rank or recommend; they cannot grant permissions, raise hard budgets, bypass safeguards, or mark contracts PASS by themselves.
7. **Prefer objective telemetry over self-report.** Error recurrence, test deltas, diff churn, causal evidence, and tool outcomes outweigh statements such as “I am stuck.”
8. **Protect test integrity.** Progress metrics must detect test deletion, weakening, skipped suites, fixture tampering, and harness changes so the agent cannot appear to improve by moving the goalposts.
9. **Separate trusted state from untrusted evidence.** Repository text, tool output, webpages, issue bodies, and generated files are data, never controller configuration.
10. **No irreversible active-task evidence deletion.** Context eviction affects model visibility; the append-only audit record remains recoverable until retention policy runs after the task/session lifecycle.
11. **Continuously score, occasionally rewrite.** Metadata can update often; prompt/history rewrites happen only at explicit, version-checked epochs.
12. **Use deterministic floors for high-consequence cases.** Security boundaries, migrations, destructive changes, public APIs, test harnesses, and major architecture cannot be down-routed solely because a small model is confident.
13. **Make expensive states temporary.** High reasoning, broad retrieval, full review, branches, and speculation use leases/budgets and mandatory reevaluation.
14. **Fail behavior is asymmetric.** Optimization modules fail open to stock Codex/default settings; integrity, verification, speculative execution, and branch isolation fail conservatively rather than silently skipping checks.
15. **Everything is auditable.** Store original decision inputs, model/prompt/calibration versions, returned scores, policy outcome, and downstream result. Re-running the model need not be deterministic for the original decision to be explainable.
16. **Compile state once, project many times.** All modules consume typed projections from one operational IR while retaining access to immutable source evidence.
17. **Requirements become versioned contracts.** Explicit requirements remain PASS/FAIL/UNKNOWN with evidence pointers. UNKNOWN never silently becomes PASS.
18. **New user input creates a new goal epoch.** It cancels stale speculation/branch work and prevents an old checkpoint from resurrecting a superseded objective.
19. **Speculate only on pure reads.** Default speculation uses direct file/index/history operations with no shell execution, network access, hooks, plugins, tests, or external side effects.
20. **Worktrees are not sandboxes.** Branch execution requires isolation of filesystem writes, HOME/TMP/cache state, credentials, network, ports, and mutable services.
21. **Learn conservatively.** Learned policies begin in shadow mode, log propensities where randomized trials are used, abstain out of distribution, and never override deterministic floors.
22. **Use the cheapest adequate controller.** Obvious cases terminate in code; semantic ambiguity reaches SemIf; rare high-consequence ambiguity may reach the frontier model.
23. **Bound controller overhead.** Every queue, classifier request, background job, archive operation, and reconciliation path has explicit size/time/concurrency limits and cancellation.
24. **Capability-probe before use.** No upstream Codex feature is assumed from source code alone; the exact installed build must pass a startup conformance suite.
25. **Stable prefixes beat clever churn.** Tool/context/effort changes that destroy prompt-cache reuse can cost more than they save; the economic controller must price this explicitly.
26. **Local data stays local by default.** Telemetry, source code, prompts, archives, and learned-policy datasets are not uploaded unless the user explicitly configures a destination.
27. **Secrets are not ordinary context.** Secret-bearing files/output are access-controlled, redacted from logs/indexes where possible, encrypted at rest when persisted, and never used as training examples by default.
28. **Automatic circuit breakers outrank optimization.** A spike in false completion, stale-cache incidents, controller errors, or quality regressions disables the offending module and returns to baseline behavior.
29. **Host-agnostic core, thin adapters.** Controller logic never depends on one client's event names, config format, or transcript layout. Client specifics live in versioned profiles and normalizers.
30. **Degrade by tier; never fake a capability.** Each client runs with the tier set it has verifiably passed. A module that needs a higher tier is advisory or off for that client, never approximated silently.
31. **Setup is reversible and transparent.** Every config edit is diffed, backed up, idempotent, and recorded in an install manifest so uninstall removes exactly what Arbiter added. Setup never changes a client's permission, approval, or sandbox settings.
32. **One daemon per user.** A single daemon owns the database writer, SemIf, and indexes. Shims spawned by any number of clients are stateless and forward to it.
33. **Seamless beats clever.** Arbiter must install with one command and configure detected clients with one more. Any feature that needs manual file editing in the common path is a setup bug.

# 4. Technical Basis, Integration Reality, and Hardware Target

SemIf is useful because it performs decision-native inference: it accepts state plus runtime criteria/options and reads option logits directly rather than generating an explanation. On the published Qwen3.5-4B RTX 3090 workload, fresh direct scoring achieved 2.33 decisions/s, serial reuse 10.75 decisions/s, and parallel shared-state suffixes 20.03 decisions/s. The fast reuse paths are experimental and changed 5–6 of 777 argmaxes versus fresh BF16 scoring. More importantly, the same repository reports only 0.813 balanced accuracy on its authored decision set, 0.637 on WANLI, and 0.700 composed accuracy on its action-firewall example. Those results are good enough to justify experimentation, not to treat the model as a correctness oracle. [S1]

The current published CUDA quick-start uses Qwen3.5-4B BF16. Quantized browser artifacts exist, but **an optimized NVIDIA Q8 SemIf path is not an assumed current capability**. Q8 remains a future deployment target only if a suitable backend is implemented and passes semantic/outcome parity tests. [S1]

Codex exposes structured app-server events, model metadata, thread settings, and reasoning-effort fields. Current model metadata includes `supports_reasoning_effort_updates`, and thread settings support effort changes for future turns. However, an open issue reports that Astra effort changes can still take a request path that defeats cache-preserving `configuration_update` behavior. Current Codex context management and app-server surfaces also have open reports involving stale token accounting, state loss after context rollover, stale-task resurrection, and per-thread config hangs. [S2][S3][S4][S10][S11][S12][S13]

A second major constraint is custom compaction. Current Codex source can replace compacted history internally, but an open upstream request explicitly notes that `PreCompact` hooks cannot yet return a replacement transcript. Therefore the full Context Scheduler cannot be promised as an external plugin today; it requires either a patched/native integration or a future upstream replacement-history API. [S9][S14]

## 4.1 Integration surfaces (formerly deployment modes)

The v3.1 Modes A–C are retained below as design background, written against Codex. Since v3.2 they are expressed only through the single taxonomy of §2:

| v3.1 name | v3.2 equivalent |
| --- | --- |
| Mode A — external sidecar | T2 (hooks) and/or T3 (transcripts) |
| Mode B — stable capability gateway | T1 (MCP shim) |
| Mode C — native/patched | `NATIVE` flag |
| (new) driver session | T4 — stable host protocol or agent SDK; `NATIVE` only if it also patches the host |

### Mode A — External sidecar

**Goal:** zero Codex fork/patch.

Reliable candidates:

- telemetry/event normalization;
- Task-State IR and contract ledger outside the model window;
- reasoning-effort recommendations/updates when capability probes pass;
- loop detection;
- diff-risk analysis;
- completion evidence auditing;
- repository indexing/retrieval assistance;
- controller logging/evaluation.

Limitations:

- cannot assume arbitrary replacement of Codex's active history;
- cannot assume per-turn dynamic-tool schema mutation is cache-efficient or even supported in the desired way;
- cannot guarantee cache-preserving reasoning updates until measured on the installed build.

### Mode B — Stable capability gateway

Expose a small, stable tool surface such as:

```text
tool_search(query, phase)
tool_describe(tool_ids)
tool_call(tool_id, validated_args)
```

or a small fixed set of family gateways. The control plane performs discovery/routing behind this stable schema, while authorization and argument validation remain deterministic. This is the preferred way to achieve large schema-token savings without changing the model-visible tool list every turn.

Built-in Codex tools that cannot safely sit behind the gateway remain pinned and are not hidden merely for token savings.

### Mode C — Native/patched Codex integration

Required for the strongest version of:

- replacement-history context compaction;
- context-epoch installation with compare-and-swap semantics;
- deep prompt/context instrumentation;
- tightly integrated dynamic capability surfaces.

A native integration must pin a known Codex commit/version, carry integration tests, and be rebased deliberately. It must not depend on unstable internals without a compatibility layer.

## 4.2 Startup capability probe and conformance suite

Before enabling any automation, probe and record:

- exact Codex build/commit/version;
- model slug and supported reasoning efforts;
- whether effort settings update successfully and which event confirms application;
- whether changing effort preserves expected cache behavior;
- app-server event ordering and tool lifecycle behavior;
- whether dynamic tools/gateway features are supported in the selected mode;
- context-management mode and whether replacement-history control is available;
- permissions/sandbox mode;
- model context window and actual tokenizer/counting path if available.

If a conformance test fails, disable only the affected optimization. Do not guess.

The probe runs **per connected client**, not once globally. For each client it also records:

- profile ID and profile version;
- detected client version;
- which hook events actually fire, with a round-trip test;
- whether the MCP shim is reachable from that client;
- transcript location and whether its parser still matches;
- the verified tier.

A client whose config format, hook payloads, or transcript format has drifted from its profile loses only the tiers whose checks now fail and keeps the rest. `arbiter doctor` reports the drift.

## 4.3 Initial hardware and software target

| Component | Target |
| --- | --- |
| Router GPU | RTX 3080 Ti, 12 GB |
| Router model | Two stages behind a backend interface (decision 0026): tier 0 GLiNER2.5-Decide encoder (CPU or GPU, no GPU required) for short typed decisions; then a decoder tier by VRAM: JevK5 (Qwen3.5-4B with a distilled LoRA merged in, 6 GB+) or K2 Horizon 7B (12 GB+), GGUF Q4–Q6 via llama.cpp first. SemIf BF16 is kept as the reference path |
| Supported reference inference | BF16 fresh scoring |
| Optimized reference | BF16 shared-state only after drift gate |
| Optional future deployment | NVIDIA Q8 only after backend implementation + parity |
| Primary coding model | Any Codex/Astra-family model available in the environment; capabilities discovered at runtime |
| Durable metadata | SQLite in WAL mode with a single-writer queue |
| Durable audit log | Append-only event log separate from model-visible context |
| Cold evidence store | Encrypted/permissioned local blobs/files where configured |
| Repository index | lexical + symbol/AST + dependency graph + optional embeddings |
| Speculation cache | content-addressed, dependency-fingerprinted, TTL bounded |
| Branch isolation | disposable sandbox/container; git worktree may be the storage substrate but not the security boundary |
| Main integration | capability-probed sidecar/gateway first; native patch only for features that require it |
| Host clients | Codex desktop (primary), Codex CLI, Claude Code, other MCP-capable agents via client profiles |
| Controller process | one per-user Arbiter daemon; stateless MCP/hook shims |
| GPU at install | not required; null/abstain SemIf backend until `arbiter semif enable` |
| Platforms | Windows, macOS, Linux |

The 3080 Ti target is sufficient for prototyping BF16 Qwen3.5-4B only if measured VRAM headroom remains adequate at the chosen SemIf state length/batch size. OOM behavior must be tested rather than inferred from weight size alone.

Decision 0026 makes the model and runtime pluggable. On 12 GB, the default tier is K2 Horizon 7B at Q4_K_M (about 5.6 GB of weights plus about 147 KB of KV cache per token). The estimated 3080 Ti rates are about 4,600 prefill tok/s and about 4–5 fresh 1k-token judgments/s. Both are far above the sensor's demand, so the model is chosen by measured accuracy, not speed (see `docs/research/semantic-sensor-models.md`).

The GPU is an accelerator for the semantic layer, not a prerequisite for the control plane. Without it, Arbiter runs with the null backend: deterministic rules plus conservative fallbacks (§7.7). The following all remain fully functional:

- telemetry;
- the event log;
- the Task-State IR;
- contracts;
- test integrity;
- indexes;
- the tool gateway;
- diff risk;
- the completion gate.

## 4.4 Client adapter layer

### 4.4.1 Components

```text
     Codex desktop    Codex CLI    Claude Code    Cursor / others
           │              │             │                │
   ┌───────┴──────────────┴─────────────┴────────────────┴───────┐
   │  MCP shim (T1)   hook CLI (T2)   transcript watcher (T3)     │
   │  T4: orchestrator hosts (Hivemind) / Arbiter-hosted driver   │
   └──────────────────────────────┬───────────────────────────────┘
                                  │ local IPC (named pipe / unix socket)
                          ┌───────▼────────┐
                          │ arbiter daemon │ single writer, SemIf, indexes,
                          │                │ event log, IR, policy
                          └────────────────┘
```

- **Arbiter daemon.** At most one per user, enforced by a single-instance lock. It owns:
  - the SQLite writer queue;
  - the audit log;
  - the Task-State IR;
  - indexes;
  - the SemIf service;
  - policy.

  It listens on local IPC: a named pipe on Windows, a Unix domain socket elsewhere. A localhost TCP port is the fallback. The IPC trust rules are in §18.8.

  The first shim call starts it lazily if it isn't running, so no manual start or service install is required. The daemon is always launched **outside the calling client's process tree** (decision 0018). Desktop clients run inside Windows job objects that may kill all their descendants on exit, and they may deny breakaway.
  - **Windows:** the launcher uses WMI `Win32_Process.Create`. The daemon then runs outside any job, parented to `WmiPrvSE.exe` (~0.8 s, once per boot). If WMI is unavailable, it tries a detached spawn with breakaway and `doctor` reports the reduced survivability.
  - **macOS/Linux:** `setsid`/double-fork.
  - **Everywhere:** per-user login autostart is optional (Task Scheduler, launchd, a systemd user unit).
- **MCP shim** (`arbiter mcp`). A stdio MCP server that each client spawns, possibly many copies. It is stateless and forwards to the daemon. It exposes:
  - the stable tool gateway (§10.2), when enabled;
  - retrieval;
  - the contract tools: `arbiter_contract_propose`, `arbiter_contract_list`, `arbiter_scope_change` (§6.8, §6.3);
  - `arbiter_finish_check` (§12.7);
  - `arbiter_verify`, which runs repo-configured verification commands (§12.8);
  - status.
- **Hook transports** (decisions 0019, 0021). On Windows, process-spawning hooks cost 200–670 ms, so T2 uses each client's **in-process** hook handler first:
  - **Codex** (CLI and desktop): `mcp_tool` hooks call the `arbiter_hook` tool on the already-running MCP shim, in 2–4 ms.
  - **Claude Code:** `http` hooks POST to the daemon's loopback endpoint (§18.8), in about 1 ms.
  - **Other clients:** the **hook CLI** (`arbiter hook <client> <event>`, stdin JSON in, client-specific JSON out, stdlib-only, deadline-bounded) is the fallback for clients with no in-process handler type. It runs `async` for telemetry wherever possible, and it's excluded from gating paths whose measured p95 exceeds 300 ms.
- **Transcript watchers.** These run inside the daemon. They tail known session logs where the profile declares a parser (formats in decision 0020): Codex `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` and Claude Code `~/.claude/projects/<cwd-key>/<sessionId>.jsonl`.
  - Watchers are read-only and **tail from a saved byte offset**. Live Codex rollouts can exceed 1 GB.
  - Dedupe keys are Codex `(session_id, ordinal)` and Claude `(sessionId, uuid)`. Hook payloads carry `transcript_path`, which binds a hook session to its file.
  - Transcripts supply what hooks don't: exit codes (Codex `CommandExecution.exit_code`), per-response usage including cached tokens, Codex plan usage-limit percentage (`rate_limits.primary.used_percent`), and effort/model changes (`thread_settings_applied`).

  Watchers ingest only sessions whose working directory is in scope under the privacy rules of §16.3. Excluded projects are skipped before parsing, not filtered afterwards. Secret redaction runs at ingest, before anything is written to the event log.
- **Orchestrator hosts (T4, primary).** Applications such as Hivemind that assemble every call themselves and consult Arbiter through the Host Advisory API (§4.6). The host applies Arbiter's per-call model × effort and context recommendations within its own allowed sets.
- **Driver mode (T4, later extension).** An optional Arbiter-hosted session through a stable host protocol, such as a Codex app-server client or the Claude Agent SDK. Neither T4 surface can attach to a session the user started inside a separate desktop app.

### 4.4.2 Client profiles

Each client is described by a declarative, versioned profile. Supporting a new agent means adding a profile, a normalizer mapping, and recorded fixtures. Controller code does not change. Built-in profiles ship with Arbiter. Users may add their own under `~/.arbiter/profiles/`.

Illustrative profile (paths and fields are verified by the probe, not trusted from the file):

```yaml
id: codex
display_name: Codex (desktop + CLI)
profile_version: 1
detect:
  any_path: ["~/.codex/config.toml", "~/.codex"]
  any_command: ["codex"]
mcp:
  config_file: "~/.codex/config.toml"
  format: toml
  entry_path: "mcp_servers.arbiter"
  entry: {command: "arbiter", args: ["mcp"]}
native_tool_deferral: false   # true if the client already loads MCP tool schemas lazily (§10.6)
hooks:
  supported: true             # decision 0015; trust must be granted by the user once
  transport: mcp_tool         # server 'arbiter', tool 'arbiter_hook' (decision 0021)
  trust_required: true        # setup never writes trusted_hash; doctor reports trust status
  events: {}                  # native event -> normalized event, filled when verified
transcripts:
  glob: "~/.codex/sessions/**/*.jsonl"
  parser: codex_rollout_v1
effort_control: advisory      # applied only via an orchestrator host, driver mode, or a verified API
driver: codex_app_server      # optional
```

Profiles declare:

- detection rules;
- the config file and format;
- where the MCP entry goes;
- hook event mappings and response formats;
- how a completion claim is recognized for that client (§12.7);
- whether the client already defers MCP tool schemas natively;
- test-runner output locations, where the client exposes shell results (§12.8);
- the transcript location and parser;
- the effort-control path;
- driver support.

A generic MCP profile covers any client that has no dedicated profile: `arbiter setup --print generic` emits a standard MCP entry for manual paste.

### 4.4.3 Capability tiers

| Tier | Surface | Requires | Enables |
| --- | --- | --- | --- |
| T1 | MCP shim | client supports MCP | tool gateway, retrieval, contract ledger, finish-check tool, status |
| T2 | hook CLI | client has a hook system whose events pass the probe | intent log + goal epochs on prompt submit, tool telemetry, pre-compaction checkpoint, completion gate on completion claims |
| T3 | transcript watcher | known transcript format | passive telemetry, loop detection, error fingerprints, post-hoc audit |
| T4 | orchestrator host (§4.6) or Arbiter-hosted driver session | host calls the Host Advisory API, or a stable host protocol / SDK | per-call model × effort choice, per-call context selection, full event stream |

Tiers are additive; a client may be T1+T3 without T2. The status of each client shows its verified tier set, for example `claude-code: T1 T2 T3` or `cursor: T1`.

**What Arbiter cannot control in hosted sessions (T1–T3).** In a session the user started in a client app, the host agent uses its own built-in tools: file reads, search, shell, and edits. Arbiter cannot:

- narrow or rerank what those built-in tools return;
- serve prefetched results into them;
- remove material the host has already placed in context;
- change reasoning effort.

In hosted sessions, retrieval, the gateway, and speculation help only when the agent chooses Arbiter's own tools. The real levers there are:

- the completion gate and contracts;
- test integrity;
- loop detection;
- advisory effort recommendations.

Planning ranges for this case are in §30.0. An optional, opt-in pre-tool hook may suggest Arbiter's retrieval tool when the agent starts a broad repository search. It never silently blocks the host's tools.

Minimum tier set per module:

| Module | Advisory | Enforcing / automatic |
| --- | --- | --- |
| Tool gateway | T1 | T1 |
| Retrieval scheduler | T1 | T1 |
| Task-State IR + goal epochs | T3 | T2 (prompt-submit hook) or T4 |
| Loop detection / telemetry | T3 | T2 or T4 |
| Diff risk & review | T1 (on request) | T2 or T4 |
| Completion gate | T1 (finish-check tool) | T2 (stop-type hook, on completion claims only) or T4 |
| Reasoning scheduler | T1/T3 (recommendation only) | T4, or a verified client effort API |
| Context scheduler | T3 (external metadata) | T4 or `NATIVE` |
| Speculation | T2 or T4 | T2 or T4 |
| Branching | T4 | T4 |

### 4.4.4 Event normalization

Each profile maps native hook events, transcript records, and MCP calls onto the normalized envelope of §6.1. Unknown native events are stored raw with their hash and otherwise ignored. Hook payloads and transcript contents are untrusted evidence (§6.6), exactly like tool output.

Every session is keyed by `(client_id, native_session_id)`. Several clients may run concurrently against the same repository. They share repository facts and indexes through overlays (§11.5), but each session keeps its own goal epochs and contracts.

### 4.4.5 Hook deadlines and fail behavior

- **Telemetry hooks** are fire-and-forget with a short deadline (default 150 ms). If the daemon is unreachable, the hook exits successfully and changes nothing.
- **Gating hooks** such as prompt submit and stop have a longer deadline (default 1500 ms, with a p95 target of 300 ms). On timeout or daemon failure they pass through unchanged.
- **No SemIf on the synchronous path.** Gating hooks decide from deterministic rules plus the most recent *already computed* asynchronous scores. They never wait for a new SemIf request, because the SemIf timeout (1200 ms) would consume the whole hook budget. SemIf is triggered asynchronously, and its result affects the next decision point.
- **Completion gate on stop.** A stop-type hook fires whenever the agent ends a turn. That includes asking the user a question, reporting partial progress, or pausing in conversation, so a turn ending is **not** a completion claim. The gate acts only on a recognized completion claim (§12.7). In the default `annotate` mode it records the finish ledger and marks the result verified or unverified, without blocking. In the opt-in `block` mode it may return the agent to work with the specific missing evidence, at most `max_stop_blocks_per_epoch` times per goal epoch. After that the stop is allowed and the result is marked unverified. "Fail conservative" for completion means the gate never reports PASS on failure. It does not mean blocking the host agent indefinitely.
- **Injected context budget.** When a hook injects context into the agent's turn (a status summary, missing evidence, a recommendation), the injection is capped at `max_injected_tokens`. It is emitted only when its content has changed since the last injection, and it contains no volatile fields such as timestamps or counters that change every turn. Injections are appended late in the turn, never into the stable prefix (§17.1).
- **Measured overhead.** Hook overhead is measured continuously (§20.15). A client whose p95 hook latency exceeds budget drops the affected hooks to async-only or disables them, via its circuit breaker.

## 4.5 Installation and setup

### 4.5.1 Distribution

- **Names.** The distribution is **`arbiter-agent`** on PyPI (`arbiter` is taken by an unrelated library). The import package is `arbiter_agent`, so it can't collide with that library in a shared environment. The command is `arbiter`.
- Primary install: `uv tool install arbiter-agent`. `pipx install arbiter-agent` is also supported. A bootstrap script that installs `uv` when it's missing is a later convenience.
- The base install contains no GPU dependencies and no model weights. The SemIf backend is an optional extra (`arbiter-agent[semif]`) plus an explicit `arbiter semif enable` step. That step checks CUDA and VRAM and downloads weights only after confirmation.
- The core daemon is Python because SemIf depends on the PyTorch ecosystem. Shims keep a stdlib-only import path. The M0 spikes found that no process-spawning hook meets the §20.17 budget on Windows, because Codex wraps command hooks in Windows PowerShell 5.1 (decision 0019). T2 therefore uses in-process handlers, and a compiled hook binary is off the critical path.

### 4.5.2 `arbiter setup`

1. Detect installed clients from built-in and user profiles, across Windows, macOS, and Linux path conventions.
2. Present a checklist of detected clients and the tier each is expected to get.
3. Build a write plan and show the exact diff for every file it will touch.
4. Back up each file, parse it, merge Arbiter's entries, validate the result, and write it atomically. If any file cannot be parsed or validated, abort without writing it.
5. Record every owned entry in an install manifest.
6. Run `arbiter doctor` to verify.

Setup requirements:

- **Idempotent.** Re-running setup never duplicates entries.
- **Non-interactive mode.** `arbiter setup --yes --clients codex,claude-code` is supported for scripted installs.
- **User scope by default.** Setup never edits repository files unless the user opts in; adding lines to `AGENTS.md` or `CLAUDE.md` is off by default.
- **Permissions untouched.** Setup never modifies a client's permission, approval, sandbox, or model settings. This is a hard invariant in code, not a configuration option (§26).
- **Existing MCP servers untouched.** Setup never moves, removes, or wraps the user's existing MCP server entries. Routing them through the gateway is a separate explicit opt-in (`arbiter gateway adopt`, §10.7).
- **Client trust steps are the user's** (decision 0015). Codex skips non-managed hooks until the user trusts them, and trust is bound to each hook definition's hash.
  - Setup writes the hook definitions, then tells the user how to trust them once: `/hooks` in the Codex CLI, or the desktop hooks settings.
  - Setup never writes `trusted_hash` itself.
  - Arbiter's hook definitions are **hash-stable across upgrades** (fixed server, tool and input templates, no version strings), so upgrading doesn't re-arm review.
  - `arbiter doctor` reports untrusted hooks and the resulting tier set (for example Codex T1+T3 until trusted).
- **Privacy scope chosen at setup.** Setup asks which projects are in scope. The default is all projects, except any listed in the global exclude list or containing an `.arbiterignore` file (§16.3).

### 4.5.3 `arbiter doctor`

Reports, for each client:

- detected version and profile version;
- verified tiers;
- MCP round trip;
- hook round trip;
- transcript parser match;
- measured hook latency;
- drift warnings.

It also reports:

- daemon health, and daemon/shim protocol versions (§16.5);
- database and WAL status;
- SemIf backend (`null`, `cuda_bf16_fresh`, ...);
- circuit-breaker state;
- the privacy/retention configuration.

### 4.5.4 `arbiter uninstall`

Removes exactly the entries recorded in the install manifest, leaving user edits made since install intact, and optionally restores backups. With `--purge` it also deletes Arbiter's local data, subject to the retention and encryption semantics of §16.3.

### 4.5.5 Client packaging

Where a client supports a native package format that bundles MCP servers and hooks, such as Claude Code plugins, Arbiter also ships as that package. The package points at the same daemon and shims. Setup detects an existing package install and does not register twice.

## 4.6 Orchestrator hosts and the Host Advisory API

### 4.6.1 Role split

An **orchestrator host** is an application that builds every model call itself: the prompt contents, model, effort, and tools. It owns deterministic authority over what actually runs. Arbiter is an **advisor** to such a host:

| Arbiter proposes | The host disposes |
| --- | --- |
| model × effort for the next call, chosen from the host's allowed set | the allowed set itself (tier floors, budgets, permissions) and the final choice |
| which context items to include, keep, or evict for a call | the prompt actually assembled and sent |
| loop / no-progress / retrieval-miss alerts | whether to redirect, re-plan, or stop |
| retrieval candidate ranking | what the worker is given |
| completion-evidence gaps | acceptance: the host's own gates remain authoritative |

Arbiter output is **advisory only**. A host must never let an Arbiter recommendation, or a SemIf score, enter guarantee-enforcing code. For Hivemind specifically, its determinism boundary forbids LLM judgment in gates, and Arbiter recommendations sit in the same proposal lane as its cache-aware routing tiebreaker.

A recommendation can **never widen** the host's allowed set. If the host offers {Terra, Sol} for a High-tier task, Arbiter may pick either but can't propose Luna. A timeout, error, circuit-breaker trip, or abstention means the host proceeds exactly as it would without Arbiter.

### 4.6.2 Host Advisory API (v1)

A versioned request/response API over the daemon's authenticated local IPC (§18.8). It uses the same handshake as shims (§16.5) and carries a per-call deadline the host chooses. The operations are:

```text
recommend_call(call_context, allowed_set, deadline)      -> {model, effort, scores, abstain, decision_id}
score_context(items[], budget, pins[], deadline)          -> {include[], evict[], reasons, decision_id}
rerank_candidates(query_context, candidates[], deadline)  -> {ranked[], floor_pins[], decision_id}
session_signals(session_ref)                              -> {loop, no_progress, retrieval_miss, evidence_gaps}
report_outcome(decision_id, outcome)                      -> ack
capabilities()                                            -> {api_version, backends, semif_state, breakers}
```

Rules:

- **Allowed set.** `allowed_set` is supplied by the host and is authoritative. Arbiter validates its own response against it and abstains rather than returning anything outside it.
- **Metadata over raw text.** `call_context` carries structured metadata (task tier, phase, failure counts, diff stats, cache warmth, prior outcomes). Raw source text is included only when the host chooses to, bounded by an exact token count.
- **Outcome reporting.** `report_outcome` returns realized usage (including cached vs. uncached input), success, rework, and latency. This feeds Arbiter's calibration and evaluation (§20–21) and attributes each decision to the host.
- **Latency.** Operations meant for the host's hot path must answer within the host deadline from rules plus precomputed scores. SemIf runs asynchronously and never blocks a host call (§4.4.5).
- **Versioning.** The API is versioned independently of Arbiter's internals. Hosts pin a major version, and a breaking change needs a new major version plus a deprecation window.

### 4.6.3 Project confinement for host data

Hivemind's memory is permanently project-local: no cross-project cache, shared canon, or aggregation. Arbiter therefore stores host-originated data in a **per-project partition**, keyed by normalized repository identity (§11.9). By default:

- Evidence, outcomes, and calibration data from one host project are never used for another project's recommendations.
- Global, cross-project calibration is available only for data from Arbiter's own standalone sessions, and only if the user opts in. It is never applied to a host project whose policy forbids it (`hosts.<id>.project_confined: true`).
- `arbiter exclude`, retention, and purge (§16.3) apply per partition.

### 4.6.4 Learned policy and host canon

Per-call recommendations inside the allowed set are proposals. A **learned policy** that would shift a host's routing over time (research track R2–R3) is delivered to the host as a *proposal with evidence*. Whether it gains authority is up to the host's own review process. For Hivemind, that means human promotion to Tier-2 canon. Arbiter never makes a learned policy authoritative inside a host by itself.

### 4.6.5 Granularity

A host that launches one CLI process per worker attempt (for example `codex exec` or `claude -p`) can apply recommendations only **per call**. Inside a single worker run, the provider still manages its own context, and effort is fixed for that run. Per-turn control inside an attempt needs the host's structured or SDK integration (app-server, Agent SDK). Hosts that keep each call within one invocation, as Hivemind does, get most of the benefit from per-call control.

### 4.6.6 Embedding as an optional host component (later)

Later, a host may bundle Arbiter as an optional, user-enabled component:

- The host installs and updates Arbiter, and starts or stops its daemon, only when the user has enabled it.
- If a standalone Arbiter is already installed, the host connects to the existing single per-user daemon (§4.4.1) after the version handshake. It does not start a second one.
- Disabling the component returns the host to its exact pre-Arbiter behavior and leaves no hooks or config behind.
- Packaging (a bundled Python runtime or a frozen sidecar binary) is decided at embedding time. The Host Advisory API contract doesn't change.
- The host's UI surfaces Arbiter's recommendations, scores, and breaker state using the host's own presentation rules. Arbiter never claims calibrated probabilities there (§24).


# 5. Top-Level Architecture

```text
        HOST CLIENTS (Codex desktop / Codex CLI / Claude Code / others)
                                      |
             CLIENT ADAPTERS: MCP shim | hook CLI | transcript watchers | driver
                                      |
                         ARBITER DAEMON (one per user)
                                      |
                                      v
                          IMMUTABLE USER INTENT / EVENT LOG
                                      |
                                      v
                      OPERATIONAL STATE + CONTRACT COMPILER
                         facts != inferences != intent
                                      |
                         goal_epoch / state_version
                                      |
                 +--------------------+--------------------+
                 |                                         |
                 v                                         v
        TRUSTED TELEMETRY / DIFFS                   REPO / CAPABILITY INDEXES
                 |                                         |
                 +--------------------+--------------------+
                                      |
                                      v
                            DECISION CASCADE
                rules -> optional predictor -> SemIf -> abstain/audit
                                      |
         +----------------+-----------+-----------+----------------+
         |                |                       |                |
         v                v                       v                v
     REASONING         CONTEXT                RETRIEVAL       DIFF/VERIFY
     SCHEDULER         SCHEDULER              SCHEDULER        SCHEDULER
         |                |                       |                |
         |          [native/patched               |                |
         |           for history rewrite]         |                |
         |                |                       |                |
         +----------------+-----------+-----------+----------------+
                                      |
                                      v
                      CONSTRAINED ECONOMIC CONTROLLER
                    quality/risk floors before cost optimization
                                      |
                    +-----------------+------------------+
                    |                                    |
                    v                                    v
             STABLE TOOL GATEWAY                  PURE-READ PREFETCH
           fixed schemas + policy                 cancellable / bounded
                    |                                    |
                    +-----------------+------------------+
                                      |
                                      v
                         HOST AGENT (Codex / Astra / other)
                                      |
                           edits / tools / evidence
                                      |
                     +----------------+----------------+
                     |                                 |
                     v                                 v
              CONTRACT/FINISH GATE          OPTIONAL ISOLATED BRANCHES
                     |                       (rare, base-version pinned)
                     +----------------+----------------+
                                      |
                                      v
                             OUTCOME + AUDIT DATA
                                      |
                     conservative learning / evaluation
```

The Task-State IR is an **operational intermediate representation**, not the ultimate source of truth. Raw user intent and the append-only event/evidence log remain available to correct it. Every action is tagged with `goal_epoch` and `state_version`; results from an older epoch/version cannot silently affect the current task.

The learned utility layer is constrained. It may choose among actions already allowed by permissions, contracts, risk floors, and budgets, but it cannot weaken those constraints.

# 6. Event Model and Operational Task-State IR

## 6.1 Primary integration boundary

Use capability-probed client surfaces, normalized behind the client adapter layer (§4.4):

- hook events;
- transcript records;
- MCP calls;
- the Codex app-server or another host protocol in driver mode.

Do not let controller logic depend directly on unstable upstream event names or ordering assumptions.

Every normalized event receives:

- `client_id`, `client_profile_version`, and `native_session_id`;
- ingest surface (`mcp`, `hook`, `transcript`, `driver`);
- `thread_id`;
- `turn_id` / tool-call ID where available;
- monotonic local sequence number;
- upstream timestamp plus local ingest timestamp;
- `goal_epoch`;
- `state_version` observed at ingest;
- source type and raw-event hash;
- idempotency/deduplication key.

The same underlying event may arrive through more than one surface, for example a hook and the transcript watcher. Deduplication keys must therefore be surface-independent where the client provides stable IDs, and profile-defined where it does not.

The event reducer must tolerate duplicate, delayed, missing, and out-of-order notifications. `turn.completed` is not assumed to prove that every pending tool result has been reconciled; pending calls remain explicit until resolved or timed out.

## 6.2 Meaningful events

Track at least:

- user input, interruption, cancellation, and scope changes;
- model-turn start/end/failure;
- tool-call start/end/failure/timeout;
- tests, compiler, linter, build, and static-analysis outcomes;
- changed files, hunks, test files, and harness/config changes;
- repeated error fingerprints and command intent fingerprints;
- context/token/cache signals where actually exposed;
- model/effort settings updates and their confirmed outcomes;
- compaction/resume/fork/reset events;
- verification evidence;
- controller timeout/fallback/circuit-breaker events;
- external filesystem changes detected through watcher/reconciliation.

## 6.3 Goal epochs and interruption semantics

Every new user instruction that changes the requested work increments `goal_epoch`. On epoch change:

1. append the raw user turn to immutable intent history;
2. cancel outstanding SemIf batches, speculative jobs, and branch launches tied to older epochs;
3. invalidate controller recommendations that were not yet applied;
4. recompile affected contracts and mark superseded obligations explicitly;
5. keep old evidence available but prevent stale task state from becoming the active objective;
6. require any resumed/compacted context to contain the latest goal-epoch sentinel before the next substantive model turn.

This directly guards against stale-task resurrection after context rollover/resume.

### 6.3.1 Epoch decisions without a semantic model

Deciding whether a message changes the requested work is semantic. So is telling "yes, continue" apart from "actually, do something else." Without SemIf, Arbiter uses a two-step rule so that ordinary replies never wipe task state:

1. **Every user prompt is logged as INTENT** and opens a *candidate* epoch. Cheap, stale-prone work is cancelled immediately, because cancelling it costs little and never loses evidence:
   - outstanding speculation and branch launches;
   - unapplied recommendations;
   - queued SemIf batches.
2. **The epoch is confirmed**, meaning contracts are recompiled and superseded obligations marked, only when one of these holds:
   - the host agent calls `arbiter_scope_change`, or proposes contracts that declare what they supersede;
   - a deterministic rule fires: an explicit new-task marker, the first prompt of a new session, a resume or compaction boundary, or a user-configured phrase rule;
   - later, a SemIf scope-change judgment that passes the cascade (§7.7).

   Otherwise the prompt joins the current epoch as additional intent.
3. **Uncertain cases are treated as a new constraint, not a new task.** The prompt is attached to the current epoch as intent that must be covered by a contract (§6.8 coverage check), rather than silently discarding existing contracts.

As implemented in v0.1 (decision 0023):
- A resume boundary confirms a new epoch only with the first *non-continuation* prompt after it.
- Compaction is recorded, but doesn't confirm an epoch, because auto-compaction isn't a user boundary.
- Confirming an epoch carries forward contracts that quote the new epoch's own intents.

## 6.4 Typed source-of-truth layers

The state system distinguishes four classes:

- **INTENT** — verbatim user requests, explicit constraints, later modifications/waivers;
- **OBSERVED FACT** — tool/test/file outcomes that were directly observed and have provenance;
- **INFERENCE** — hypotheses, relevance judgments, risk labels, estimated progress;
- **CONTRACT** — operational obligations derived from INTENT and policy, with evidence rules.

INFERENCE may never overwrite INTENT or OBSERVED FACT. Contradiction creates an explicit conflict/UNKNOWN state that must be reconciled.

### 6.4.1 Evidence origin

Every OBSERVED FACT also carries an **origin**. The origin limits which contracts it can satisfy:

| Origin | Meaning | Example |
| --- | --- | --- |
| `arbiter_observed` | Arbiter produced or read it directly | `arbiter verify` run, Arbiter's own file hash/read, git state read by the daemon |
| `host_reported` | Reported by the host client through a hook or transcript | shell output of a test command in a PostToolUse payload |
| `agent_asserted` | Claimed by the agent through an MCP call | "tests pass" passed as an argument to a tool |

- Evidence recipes (§6.8) declare the minimum origin they accept. By default, a recipe that says "tests pass" requires `arbiter_observed`, or `host_reported` output that parses as a recognized test-runner result (§12.8).
- `agent_asserted` evidence can never satisfy a contract on its own.
- Evidence arriving over IPC records the authenticated channel it came in on (§18.8).

## 6.5 Trusted structured controller state

The SemIf-visible controller state should be compact and machine-generated. Example:

```json
{
  "goal_epoch": 4,
  "state_version": 91,
  "phase": "debugging",
  "objective_id": "G4",
  "current_effort": "medium",
  "supported_efforts": ["low", "medium", "high", "xhigh"],
  "tests_passed": 211,
  "tests_failed": 3,
  "test_integrity_alert": false,
  "same_error_occurrences": 3,
  "files_changed_since_progress": 4,
  "reverted_lines": 63,
  "minutes_since_objective_progress": 9.2,
  "verification_debt": "medium",
  "risk_tags": ["public_api_adjacent"],
  "context_hot_tokens": 48200,
  "budget_fraction_remaining": 0.73
}
```

## 6.6 Untrusted evidence

Source code, comments, READMEs, issue text, generated files, webpages, model narration, tool output, and external content are untrusted evidence. Prefer metadata/features over raw text where possible. When raw excerpts are necessary:

- limit them by exact tokenizer count;
- identify source/path/span;
- keep controller policy outside the text;
- do not include secrets unless required and permitted;
- never allow excerpt content to change budgets, permissions, or hard floors.

Prompt delimiters help readability but are not treated as a security boundary.

## 6.7 Operational Task-State IR

The IR represents the controller's best current operational model, not unquestionable truth.

```json
{
  "goal_epoch": 4,
  "goal": {"id": "G4", "source_intent_ids": ["U31"], "text": "fix checkout quantity overflow without changing public API"},
  "contracts": [
    {"id": "C1", "status": "unknown", "source_intent_ids": ["U31"]},
    {"id": "C2", "status": "pass", "source_intent_ids": ["U31"], "evidence": ["E77"]}
  ],
  "facts": ["E77", "E81", "E82"],
  "hypotheses": [
    {"id": "H1", "support_score": 0.91, "score_kind": "model_score"}
  ],
  "decisions": ["D12"],
  "failed_approaches": ["A7"],
  "open_questions": ["Q9"],
  "active_files": ["src/checkout/parser.ts"],
  "progress": {"tests_passed": 24, "tests_failed": 1},
  "consistency_status": "consistent"
}
```

Rules:

- every consequential field has provenance;
- model scores are labeled as scores, not probabilities unless calibrated;
- raw conversation history is evidence, not the only representation of present state;
- modules may request narrower views but may not keep incompatible private task narratives;
- the IR has hard per-field and total-token/byte caps;
- stale inferences expire or are revalidated after relevant evidence changes;
- repo/worktree/branch identity is part of the state key.

## 6.8 Contract Compiler

Compile explicit requirements with a high-recall strategy. Preserve a direct pointer to the exact user text for every contract. A contract has:

- ID and goal epoch;
- raw intent sources;
- normalized obligation;
- scope;
- status PASS/FAIL/UNKNOWN/WAIVED;
- deterministic or risk-based verification recipe;
- evidence pointers;
- confidence in the **mapping**, separate from status.

A contract becomes PASS only when its configured evidence rule is satisfied. SemIf may propose mappings or missing evidence, but low-confidence mapping of an explicit user requirement triggers a stronger audit rather than silent omission.

Task changes create new contract versions; they do not rewrite old intent history.

### 6.8.1 Where contracts come from

Arbiter has no semantic model of its own until SemIf is enabled, and SemIf is only a bounded sensor even then. So the **host agent proposes contracts** and Arbiter **enforces their integrity**.

1. **Proposal.** The agent calls `arbiter_contract_propose` with, for each obligation:
   - the normalized text;
   - one or more **verbatim quotes** from the user's intent turns;
   - scope;
   - a proposed verification recipe (for example "`pytest tests/checkout` passes", "public exports of `src/api/` unchanged", "file X exists").
2. **Provenance check.** Arbiter verifies deterministically that every quote exists in the immutable intent log for the current or a prior epoch. A proposal whose quotes don't match is rejected.
3. **Recipe check.** Recipes must come from a typed set that Arbiter can evaluate: test-command result, file/diff predicate, public-surface diff, command exit status, or manual user confirmation. Free-text recipes are stored with status UNKNOWN, and only the user can mark them.
4. **Coverage check.** Arbiter flags intent turns in the active epoch that no contract quotes, using rules first (imperative sentences, explicit constraints such as "don't," "must," "without," named files and symbols) and SemIf later. Flagged, uncovered intent appears in the finish ledger. It blocks a verified completion in `block` mode, and it is reported in `annotate` mode.
5. **Deterministic extraction.** Some constraints are extracted by rules alone and need no agent proposal: explicitly named files or paths, "don't change X" patterns, and requested commands.
6. **Status authority.** The agent can propose and describe contracts but never set PASS. Status changes come only from evidence rules (§6.4.1), or from explicit user waivers and confirmations through `arbiter contracts` or the MCP status view.
7. **Weak-contract defense.** A contract whose recipe is trivially satisfied, such as "a file exists" for "fix the bug," is flagged as low-strength when a deterministic strength heuristic or a later SemIf check rates it weaker than its quoted intent. Low-strength contracts are shown as such in the ledger.

On clients without MCP write access (T3-only), contracts can only be created through `arbiter contracts` on the command line or by deterministic extraction.

## 6.9 State consistency and two-phase reconciliation

Reconciliation triggers include branch merge/rollback, resume, compaction, user interruption, external file change, contradictory tests, and tool-result correction.

For any destructive context/reset operation use a two-phase protocol:

1. build checkpoint from current `goal_epoch`/`state_version`;
2. persist append-only event + snapshot + restore manifest in one durable transaction;
3. fsync/commit and verify the checkpoint can be read back;
4. perform the context transition;
5. require a post-transition restore sentinel matching the expected goal epoch/state hash;
6. if the sentinel or restore validation fails, block further optimized execution and recover from the durable checkpoint/baseline path.

If state cannot be reconciled, mark affected fields UNKNOWN, disable aggressive pruning/learning, widen retrieval/verification, and continue conservatively.

# 7. Shared SemIf Inference Service

## 7.1 One service, many bounded judgments

Use one local service rather than separate models for each module. Batch only judgments whose state prefix is actually identical. The published shared-state throughput must not be extrapolated to heterogeneous per-candidate states that cannot reuse the same prefix. [S1]

Example same-prefix batch:

```text
Reasoning:
  Would HIGH materially improve expected outcome over MEDIUM?
  Would XHIGH materially improve expected outcome over HIGH?

Context:
  Is T184 likely to be needed verbatim before task completion?
  Is T184 cheaply and exactly reproducible?

Tools:
  Is package-registry capability plausibly useful in the next phase?
```

## 7.2 Exact request budgeting

Before dispatch:

- use the exact tokenizer/model template for the deployed backend;
- reserve headroom for the largest criterion/options/envelope in the batch;
- enforce hard state, criterion, and total-request limits;
- preserve unresolved/pending tool state before dropping older detail;
- reject impossible budgets before GPU work begins.

Do not use character-count heuristics for safety-critical request fitting. The fast-jev-compaction hardening reports show how state-only fitting can leave no room for the question itself. [S15]

## 7.3 Input/output validation

Each score result must carry and validate:

- model name + exact revision;
- backend/precision;
- prompt-template version;
- tokenizer version/hash where practical;
- state hash and criterion hash;
- option list/order;
- finite scores/probabilities with expected shape;
- latency and cache/reuse mode.

Reject NaN/Inf, malformed option maps, missing options, unexpected model revision, or out-of-range normalized probabilities. Invalid classifier output causes abstention/fallback, never a destructive action. Similar validation failures have been identified in external compaction implementations. [S16]

## 7.4 Mirroring, perturbation, and uncertainty

Important binary judgments may be scored in both option orders and, for high-impact cases, with one semantically equivalent paraphrase. However, these variants are **correlated measurements**, not independent votes. Their purpose is to expose instability.

Compute:

- orientation-corrected score aggregate;
- disagreement spread;
- entropy/margin;
- OOD score where the learned layer supports it.

High disagreement increases abstention or stronger review. Do not assume averaging removes systematic bias.

## 7.5 Calibration

SemIf's raw option scores are not treated as calibrated real-world probabilities. Calibrate per decision family on held-out traces. Use terms such as `model_score` until calibration is demonstrated.

## 7.6 Confidence is not authority

A score of 0.99 cannot bypass deterministic policy, contract rules, permissions, archive guarantees, review floors, or completion evidence requirements.

## 7.7 Decision cascade

```text
event / requested controller decision
        |
        v
deterministic rule or hard floor
        | obvious -> action
        v
optional cheap learned predictor
        | confident + in-distribution -> recommendation
        v
SemIf bounded judgment
        | stable + policy-allowed -> recommendation
        v
abstain -> conservative default or rare frontier audit
```

The first release should use rules -> SemIf -> conservative fallback. Add the cheap predictor only after enough trace data exists.

## 7.8 Service reliability

The SemIf service must use:

- bounded request queue;
- deadline/cancellation on every request;
- maximum concurrent batch count;
- overload shedding for low-value decisions;
- GPU OOM recovery;
- health checks and restart backoff;
- per-module circuit breakers;
- no ability to block the main agent indefinitely;
- never awaited on a synchronous hook path. Gating hooks consume only previously computed scores (§4.4.5);
- a **null backend** that returns abstention for every request, used when no GPU, weights, or SemIf install is present. With it the cascade terminates at rules or conservative defaults, and shadow logging records that no semantic score was available.

The service sits behind a backend interface (`null`, `cuda_bf16_fresh`, later shared-state and quantized backends), so modules never depend on which backend is active.

A controller decision that exceeds its latency budget falls back according to the module's fail policy.

## 7.9 Backends, model tiers, and fine-tuned adapters

Decision 0026 applies. SemIf's direct-logit scoring is the technique; the runtime and model sit behind a backend interface.

- **Backends:**
  - `null` (rules only);
  - `encoder` (GLiNER2.5-Decide and similar typed-decision encoders; CPU or GPU);
  - `llama_cpp` (GGUF, multi-LoRA, prefix caching; the first decoder backend);
  - `semif_bf16` (reference and parity path);
  - `exl3`, later.

  Every backend returns the same scored-option result and passes §7.3 validation.
- **Two stages:**
  - **Tier 0 encoder (GLiNER2.5-Decide, 340M):** handles short, high-volume typed decisions on any machine: completion claims, scope changes, requirement detection, contract coverage. It needs no GPU.
  - **Decoder tier:** handles long-context or reasoning-heavy judgments (context and retrieval relevance, review risk, effort): JevK5 (Qwen3.5-4B with a distilled LoRA merged in) at 6 GB+, K2 Horizon 7B at 12 GB+ (Q6_K at 16 GB+). Reasoning is disabled for scoring.
  - Each decision family is routed to one stage by benchmark results. The encoder is trained on 512-token-class inputs, so longer inputs go to the decoder or are summarized deterministically first.
- **Claim detection stays rules-first.** The encoder may only *add* completion claims the rules missed, which widens what the gate checks. It never removes a rule-detected claim and never marks anything verified.
- **Adapters:** one base model stays in VRAM, with a LoRA adapter per decision family (completion claim, scope change, context relevance, retrieval relevance, review risk).
- **Selection by measurement:** a sensor benchmark in the eval corpus reports accuracy, calibration, abstention and latency per model × quant × backend. Calibration and parity run on the exact quantized artifact (§22).
- **Authority unchanged:** a stronger or fine-tuned sensor still can't certify, widen permissions or block by itself (§7.6).

# 8. Module A — Reasoning Scheduler

## 8.1 Objective

Choose the cheapest **actually supported** reasoning tier whose estimated marginal quality benefit no longer justifies incremental usage, latency, and cache cost, subject to risk/contract floors.

Do not hard-code the existence of LOW/MEDIUM/HIGH/XHIGH/MAX. At startup, build an ordered capability lattice from model metadata and conformance tests. Logical policy labels map onto the nearest supported tiers.

## 8.2 Transition questions

For adjacent supported tiers only:

```text
Would the next supported higher effort materially improve expected next-step outcome?
Would staying at the current effort create meaningful expected rework?
```

Auxiliary signals include reversibility, repeated failure, contradictory evidence, architecture consequence, retrieval coverage, diff risk, and whether a dedicated replan is likely to help.

## 8.3 Objective telemetry and loop detector

Track repeated normalized errors, same-hunk churn, rollback volume, repeated command intent, test-pass delta, time since verified progress, scope expansion, failed tools, and verification debt.

Protect the loop detector from false progress:

- compare product-code and test-code changes separately;
- detect deleted/skipped/weakened tests;
- pin baseline test/harness hashes where practical;
- treat large test-harness/config edits as high risk;
- do not count a reduced test count as progress without explanation.

## 8.4 Effort leases and hysteresis

Leases are defined in logical tiers and projected onto the real model lattice. High-cost tiers are short-lived; de-escalation requires stronger evidence than escalation.

If the model exposes only two or three efforts, the policy adapts rather than inventing unsupported levels.

## 8.5 Applying an effort change

For each update:

1. verify target effort exists in the current model capability set;
2. apply through the supported thread/turn settings path;
3. wait for explicit success/outcome or verify subsequent thread state;
4. log whether the update was Applied, Rejected, or TargetUnavailable where available;
5. measure input/cached tokens on the next call;
6. if the build exhibits cache-destructive effort changes, feed that cost back into the scheduler or temporarily disable dynamic effort.

Current Codex source exposes reasoning-effort update capability metadata, but an open issue reports a cache-preservation gap for Astra, so this must be measured on the installed build. [S2][S4]

**Hosted clients.** In a session the user started inside a client app, such as Codex desktop or Claude Code, Arbiter generally cannot change reasoning effort itself. There the scheduler is advisory. Its recommendation is surfaced through:

- the MCP status tool;
- hook-injected context where the client supports it;
- `arbiter status`.

The user or agent applies it. Automatic effort changes require T4: an orchestrator host applying Arbiter's recommendation (§4.6), an Arbiter-hosted driver session, or a client-exposed effort API that passes the §4.2 conformance checks.

## 8.6 Constrained economic policy

Do not maximize one unconstrained utility number. First determine the allowed effort set from:

- permissions/policy;
- risk floors;
- contract/verification requirements;
- remaining quota/budget;
- supported effort capabilities.

Then choose among allowed efforts to minimize expected remote usage/time subject to a configured quality non-inferiority margin.

A scalar utility may be used only as a local ranking device after the constraints are applied.

## 8.7 Counterfactual learning data

Retain state features, chosen effort, alternative scores, downstream progress, regressions, retries, cache effects, total usage, wall time, and rework. When safe, run randomized or replayed alternate-effort trials on selected checkpoints. Because frontier-model behavior is stochastic, a single replay is not assumed to reveal the true counterfactual; repeated seeds/samples are used for evaluation cohorts where economically feasible.

## 8.8 Model-tier selection

The scheduler chooses **model × effort**, not effort alone. Host evidence (§17.5) showed that picking the right model tier for a task can change cost per successful task by more than an order of magnitude. That is a far larger lever than effort changes or context trimming.

- The candidate set is the **host-supplied allowed set** (§4.6.2) or, in Arbiter-hosted sessions, the policy-allowed set. Risk tiers and contracts set the floor; Arbiter never proposes below it.
- The decision target is **expected cost per successful call or task**, including the expected cost of a failed cheap attempt followed by a retry on a stronger tier. It is not cost per call.
- Transition questions extend §8.2. For example: "would the next stronger model tier materially improve the expected outcome of this call?" and "is the cheaper tier likely to succeed without rework?" Deterministic signals include task tier, the failure history of this task, prior tier outcomes for this task type, and cache warmth per model.
- **Cache warmth is a cost input.** Switching model or effort can forfeit a warm cache, and that is priced under §17.
- Recommendations are logged with the allowed set, the choice, the scores, and the realized outcome, so model-tier policy can be evaluated with paired runs (§20). Policies that would change a host's routing over time follow §4.6.4.

# 9. Module B — Context Scheduler

## 9.1 Objective

Keep the expensive model window focused without destroying the durable task record. Context management is a **visibility hierarchy**, not a deletion engine.

The key question is:

> What information must remain model-visible now, what can be represented structurally, and what can be evicted while remaining exactly recoverable if needed?

## 9.2 Four visibility tiers

| Tier | Model-visible meaning | Durable storage meaning |
| --- | --- | --- |
| HOT | Keep verbatim or near-verbatim in active context | retained |
| WARM | Keep compact structured form in active context | retained with source pointers |
| COLD | Remove from active prompt; recoverable on demand | retained verbatim/structured evidence |
| EVICTED | Excluded from normal retrieval because redundant/reproducible | **still present in append-only audit log during active task** |

There is no automatic irreversible evidence deletion during an active task. Long-term garbage collection is a separate post-task retention process with privacy policy and user-configurable retention.

## 9.3 Deployment limitation

Full replacement-history compaction is **NATIVE/PATCHED** unless the installed Codex build exposes and passes a supported replacement-history API. As of this revision, an upstream request exists precisely because current PreCompact hooks cannot replace active history. Sidecar mode may still maintain external state, retrieval, and archives, but it must not pretend it controls Codex's actual prompt history. [S9]

The same rule applies to every other client. A pre-compaction hook, where one exists, is used for the two-phase checkpoint (§6.9) and restore sentinel. It is not assumed to replace history unless that client's profile proves a replacement API through conformance tests. Physical context control otherwise requires T4 (an orchestrator host assembling each prompt, §4.6 and §9.11, or a driver session) or a `NATIVE` integration.

## 9.4 Active projection

The model-visible state should normally include bounded projections of:

- latest goal epoch and exact high-value user constraints;
- active contracts;
- current failures/evidence;
- strongest live hypotheses and known failed approaches;
- active files/symbols;
- unresolved questions;
- compact evidence pointers.

The operational IR itself is not blindly re-injected in full every turn; prompt-cache economics determine when a changed projection is worth adding.

## 9.5 Retention judgments

Evaluate currently useful, likely useful later, reproducibility, reconstruction cost, causal importance, unresolved evidence, external/non-deterministic origin, supersession, active-contract reference, secret/sensitivity class, and dependency relations.

Hard pins include:

- latest user intent for active goal epoch;
- active constraints/contracts;
- unresolved tool calls;
- non-reproducible evidence used by a live decision;
- evidence required by completion contracts;
- controller restore sentinel/checkpoint metadata.

## 9.6 Continuous metadata, versioned epochs

Score metadata continuously but mutate model-visible history only at explicit epochs. An epoch uses compare-and-swap on the expected history/state version:

```text
snapshot version V
 -> construct candidate projection
 -> validate token budget with exact tokenizer
 -> validate pinned evidence + pending calls
 -> shadow/state-equivalence checks
 -> durable checkpoint
 -> CAS install only if history still V
 -> otherwise abort and rebuild
```

This prevents clone-then-replace races that have historically affected compaction systems. [S17]

## 9.7 Rehydration

COLD/EVICTED evidence can be recovered by ID, file/symbol, error fingerprint, contract, causal edge, or semantic query. Rehydration is logged and inserted only into a bounded temporary evidence window or the next safe context epoch.

## 9.8 Context pressure accounting

Use actual model context limits and tokenizer counts when available. Account for:

- pending user input;
- active tool/result envelope;
- question/criteria headroom;
- required startup/context packets;
- model output reserve.

Do not trigger compaction solely from stale server-reported usage; maintain a conservative local estimate because open Codex reports show large tool output can invalidate stale token accounting. [S13]

## 9.9 Durable record separation

Model-window compaction must never be allowed to destroy the audit transcript. Keep controller archives/event logs separate from any Codex rollout file that upstream may rewrite. An open Codex report describes destructive rollout rewriting during compaction; this architecture treats upstream rollout storage as non-authoritative for its own audit record. [S18]

## 9.10 Persistent project memory

Promote facts to PROJECT memory only when stable, high-confidence, provenance-backed, and non-secret. Store scope and invalidation conditions. A memory such as “uses pnpm” expires if package-manager files change; project architecture memories are not immortal assertions.

## 9.11 Conservative context control in hosts

The host lesson (Hivemind CR7.a–e, 2026-09-12): blunt byte, message, and turn caps silently truncated content the next step needed. In one real run, a 5.5 KB answer was cut to 4 KB and the follow-up could no longer act on it. The user explicitly rejected fixed history limits. So context control delivered through a host must meet these rules:

- **No blunt caps.** No fixed per-message, per-turn, or aggregate truncation. Eviction is a per-item judgment with hard pins (§9.5).
- **Pressure-triggered only.** Context is reduced only when the exact-tokenizer estimate approaches the *real* model limit (§9.8), or when a cost analysis on **billed** tokens shows a material gain (§17.5). Estimated pressure alone is not enough.
- **Referenced content is pinned.** Content the latest user turn or the pending action refers to, or content a follow-up would need verbatim (drafts, outlines, specs, proposed patches), is never evicted while it is referenced.
- **Recoverable and visible.** Anything evicted stays exactly recoverable (§9.7). The host is told what was evicted, so it can show the user and rehydrate on request.
- **Regression gate.** Before any context-control feature is enabled for a host, it must pass that host's existing conversation-integrity probes (for Hivemind, the CR7 full-history and no-truncation checks). It also stays off by default until §20.17 context gates pass.

# 10. Module C — Tool / Capability Scheduler

## 10.1 Objective

Reduce tool-schema prompt overhead and action-choice complexity without frequent model-visible schema churn and without giving SemIf authorization power.

## 10.2 Preferred architecture: stable tool gateway

Instead of repeatedly hiding/showing dozens of schemas, expose a fixed minimal gateway where feasible:

```text
tool_search(query, capability_family, max_results)
tool_describe(tool_ids)
tool_call(tool_id, args)
```

or a small fixed set of family gateways. The controller ranks tools behind the gateway; the tool's canonical schema is retrieved only when needed. This preserves a stable prompt prefix and makes tool catalogs scale to hundreds of capabilities.

The gateway must:

- validate `tool_id` against the current catalog;
- validate arguments against the canonical schema;
- preserve upstream permission/approval semantics;
- enforce filesystem/network scope;
- expose canonical tool descriptions, not repository-supplied descriptions;
- return structured errors for unavailable or denied capabilities.

## 10.3 Built-in tools and dynamic tools

Do not force built-in Codex tools behind the gateway if doing so loses important native semantics. Maintain an always-visible core set when warranted.

Dynamic tool/schema mutation is EXPERIMENTAL unless the exact build passes the compatibility suite. Current app-server support includes dynamic-tool-related surfaces, but dynamic behavior and per-thread configuration have had open bugs; stable gateway schemas are the default design. [S3][S19]

## 10.4 Selection policy

Use two-stage relevance behind the gateway:

1. family/capability selection;
2. individual tool ranking.

Maintain an exploration/recall floor. If a capability miss occurs, broaden immediately and record the miss for evaluation.

## 10.5 Safety

SemIf controls discoverability/ranking only. It cannot:

- grant permissions;
- auto-approve dangerous calls;
- expand network/filesystem scope;
- expose hidden credentials;
- raise remote-spend limits.

Tool descriptions and permission metadata are trusted controller configuration, not model-generated text.

## 10.6 Auto-disable criterion

If the catalog is small or gateway indirection increases latency/errors more than it saves schema tokens, keep the normal tool surface. The scheduler must earn its complexity.

Some clients already defer MCP tool schemas natively, loading them only when the agent searches for them; Claude Code is one. For those clients (`native_tool_deferral: true` in the profile, confirmed by the probe), the gateway's schema-token benefit is largely already delivered. The gateway stays off unless a per-client benchmark (§20.7) shows a net gain on top of native deferral.

## 10.7 Proxying existing MCP servers and approval preservation

The gateway's catalog is:

- Arbiter's own tools;
- any MCP servers the user **explicitly adopts** with `arbiter gateway adopt <server>`.

Setup never adopts servers implicitly (§4.5.2). Adoption rewrites the client's MCP config, so it goes through the same diff, backup, manifest, and uninstall machinery as setup. `arbiter gateway release <server>` restores the original entry.

**Proxying collapses the client's approval granularity.** A client that asks per tool before running `github.create_issue` sees only `tool_call` once that tool sits behind the gateway. Unless handled, approving `tool_call` once would approve every proxied tool. The gateway therefore enforces:

1. **Read-only by default.** Only tools classified read-only are proxied without further checks. The classification comes from the server's own MCP tool annotations (for example read-only and non-destructive hints) confirmed by a trusted catalog entry, never from description text.
2. **Mutating tools stay direct, or get mirrored approval.** A tool that is not confirmed read-only either stays registered directly with the client, which keeps the client's native approval, or is proxied only if the gateway can mirror the client's approval policy for that tool. Mirroring means asking the user through MCP elicitation, or refusing when the client doesn't support elicitation.
3. **No approval inheritance.** Approval of `tool_call` for one `tool_id` is never treated as approval for another.
4. **Denials are sticky.** If the client or user denies a proxied call, the gateway doesn't retry it through a different route.

If approval mirroring can't be verified for a client, only read-only tools are proxied for that client.

# 11. Module D — Semantic Code Retrieval Scheduler

## 11.1 Objective

Prevent irrelevant repository material from entering expensive model context while preserving a high-recall path to any potentially relevant code/evidence.

## 11.2 Candidate generation

Use inexpensive channels:

- exact lexical/ripgrep matches;
- filenames/path priors;
- definitions/references;
- AST/import/call/dependency graph;
- stack-trace references;
- test-to-code mappings;
- git history/blame where useful;
- optional embeddings/vector retrieval.

SemIf reranks a candidate set; it never “searches the repository” by itself.

## 11.3 Unprunable evidence classes

Pin before semantic reranking:

- exact file/line/symbol references from current errors;
- directly changed files around the failing behavior;
- definitions of explicitly named symbols;
- active-contract evidence;
- latest user-specified files/paths;
- required build/test configuration when failures point there.

## 11.4 Adaptive top-k

Do not use a fixed 5–15 chunk rule for every repository/task. Select breadth from:

- retrieval-score entropy/disagreement;
- available prompt budget;
- candidate redundancy;
- phase;
- risk;
- recent miss history.

High uncertainty means broader retrieval. Low uncertainty with exact deterministic anchors permits narrower retrieval.

## 11.5 Index correctness

Indexes are content-hash/version aware:

- invalidate lexical/symbol/embedding entries on file change;
- maintain branch/worktree overlays instead of sharing stale main-branch state;
- include generated/config files only according to policy;
- respect ignore rules and permission boundaries;
- never index secrets/credential stores by default;
- record index version in every retrieval decision.

## 11.6 Retrieval miss detection

Broaden coverage when repeated edits fail, the model repeatedly searches the same concept, new stack traces point elsewhere, uncertainty stays high at high reasoning effort, or diff scope expands unexpectedly.

A retrieval miss should normally trigger breadth expansion before another expensive reasoning escalation.

## 11.7 Secret and access control

Retrieval cannot become a secret-exfiltration mechanism. File access policy is enforced before candidate generation and again before content injection. Secret-scanner hits are either blocked, redacted, or shown only through a narrowly scoped evidence path when genuinely required.

## 11.8 Hosted-session scope

In hosted sessions (§4.4.3), the retrieval scheduler can't filter what the host's built-in search and read tools return. It serves only calls to Arbiter's own retrieval tools, and supplies retrieval-miss signals to the loop detector. Savings claims for retrieval in hosted sessions count only Arbiter-served retrievals.

## 11.9 Repository identity and path normalization

All indexes, evidence, contracts, fingerprints, and speculation keys use a normalized identity:

- **Repository identity** = (canonical repository root, git common directory, worktree path, current HEAD/ref). Two worktrees of one repository share base objects but get separate overlays.
- **Path keys** are repository-relative and use `/` separators.
  - They are case-folded only on volumes detected as case-insensitive; detection is per volume, not assumed per OS.
  - Unicode is normalized to NFC.
  - Symlinks are resolved to their target for content identity, with the link path recorded as an alias.
  - Windows long-path (`\\?\`) and drive-letter-case variants resolve to one key.
- **Content hashes** are computed on raw bytes. Line-ending conversion (`core.autocrlf`) is recorded, not normalized away.
- Paths outside any repository are keyed by canonical absolute path and are never merged with repository keys.

# 12. Module E — Completion & Verification Gate

## 12.1 Objective

Prevent premature completion and unnecessary post-solution work. The gate reports whether the task is **verified to the configured evidence standard**, not whether the software is absolutely correct.

## 12.2 Completion evidence

Evaluate:

- active-goal contracts;
- requested deliverable presence;
- unresolved known errors;
- relevant targeted tests;
- risk-required broader checks;
- diff-review completion;
- unverified assumptions/environment gaps;
- test-integrity status;
- evidence backing final claims.

## 12.3 Contract-backed completion

Every required active contract must be PASS or explicitly WAIVED by an allowed user/policy action. UNKNOWN remains incomplete for automatic finish.

Evidence grades may include:

- **DIRECT** — exact observable state/test/API diff proves the contract's verification recipe;
- **INDIRECT** — strong supporting evidence but not the primary recipe;
- **UNAVAILABLE** — environment/tool limitation prevents verification;
- **CONFLICTED** — evidence disagrees.

Only configured evidence grades satisfy each recipe.

## 12.4 Test integrity

Before counting test improvements as evidence, detect:

- test deletion/skip markers;
- assertion weakening;
- fixture/golden-file rewrites;
- test runner filters changed to exclude failures;
- coverage/config reductions;
- broad harness modifications;
- snapshot regeneration that may hide regressions.

High-impact test-harness changes receive a review floor and may invalidate earlier PASS evidence until reverified.

Integrity is judged against a **session baseline**, captured at session start (SessionStart hook, the first observed event, or `arbiter verify --baseline`). The baseline records:

- the git HEAD, and whether the working tree was dirty;
- content hashes of test files, fixtures, snapshots, and runner/harness configuration, located by per-language path rules plus repo configuration;
- the discovered test inventory where cheaply available, from a collect-only run executed through `arbiter verify`, never speculatively.

Without a baseline, test-integrity status is UNKNOWN. It is not reported as OK.

## 12.5 Finish audit

Produce a compact ledger:

```text
Goal epoch: 4
Objective: satisfied to configured evidence standard
Contracts: 6 PASS / 0 FAIL / 1 UNKNOWN (integration environment unavailable)
Targeted tests: 24/24 pass
Broader verification: typecheck PASS; integration suite UNAVAILABLE
Test integrity: no weakening detected
High-risk hunks reviewed: 3/3
Known unresolved issues: none blocking under current scope
```

If verification is unavailable, the final user-facing response should state the limitation rather than silently asserting success.

## 12.6 Overwork prevention

Once required contracts are satisfied and risk-required checks are complete, optional cleanup cannot trigger another expensive exploration cycle unless the user explicitly requested exhaustive polishing or a policy says the residual risk justifies it.

## 12.7 Enforcement surfaces

**A turn ending is not a completion claim.** Agents end turns to ask questions, report partial progress, wait for approval, or converse. The gate evaluates only **completion claims**, recognized by:

1. the agent calling `arbiter_finish_check`, which is the strongest signal;
2. deterministic rules on the final assistant message: explicit completion statements in the client's recognized forms, with no trailing question to the user and no pending tool calls;
3. later, a SemIf "is this a completion claim?" judgment. It is scored asynchronously and applied on the next stop, never awaited in the hook (§4.4.5).

A final message that ends by asking the user something, or that the rules can't classify, is **not** gated. The M0 spikes confirmed that both Codex and Claude Code fire their stop hook on turns ending in a question, and that both deliver `last_assistant_message` for these rules (decisions 0015, 0017).

**Block-reason wording** (decision 0017). In the spike, a Claude Code agent saved a stop-hook reason as a persistent memory, treating it as standing user feedback. Every gate message must therefore be:

- a factual list of the missing evidence;
- prefixed `[Arbiter]`;
- explicitly scoped to the current turn;
- free of imperative instructions that could be read as standing preferences.

Where a client offers `additionalContext`, it's preferred over a block reason when that's enough. Regression tests confirm that no gate message is persisted as memory or instructions.

**Modes.** `completion.gate_mode`:

- `annotate` (the default): records the finish ledger and marks the session or result verified or unverified. It never blocks.
- `block` (opt-in per user, repo, or session): on a recognized completion claim with missing required evidence, it returns the agent to work once with the specific missing items. This is bounded by `max_stop_blocks_per_epoch`; after that the stop is allowed and marked unverified.

**Per surface:**

- **T2/T4 clients.** The gate runs at the stop-type hook, or in the driver loop, in the configured mode.
- **T1-only clients.** The gate is available as `arbiter_finish_check` and in the finish ledger. It is advisory because the host can stop without calling it. Sessions that finish without a passing check are recorded as unverified.
- **Any client.** If the gate cannot run (daemon down, timeout, circuit breaker open), it reports no PASS and marks the session unverified. It never fabricates completion, and it never blocks because of its own failure.

**Verdict rules in v0.1** (decision 0024). A claim is verified only when all of these hold:
- at least one active contract exists;
- every active contract is PASS or WAIVED;
- no requirement-like intent is uncovered;
- no PASS contract is low-strength (unless the user waives it);
- where any PASS rests on test evidence, test integrity is OK against a session baseline.

Test evidence must be observed after the latest non-documentation change, and it must come from a run that matches the recipe without narrowing filters.

## 12.8 Observing verification results

Arbiter learns test, build, typecheck, and lint outcomes in three ways, in increasing order of trust:

0. **Exit codes come from transcripts, not hooks** (decisions 0015, 0017, 0020). Neither client's PostToolUse payload carries a numeric exit code.
   - Codex rollouts do: `CommandExecution.exit_code`, with separate stdout and stderr.
   - Claude Code transcripts carry only `is_error`, `stdout`, `stderr` and `interrupted`.

   Hook events give the fast signal, and the T3 watcher supplies the exit code where the client records one.
1. **Parsed host output** (`host_reported`). Shell tool results from hooks and transcripts are matched against runner parsers: pytest, unittest, Jest/Vitest, Mocha, Go test, cargo test, JUnit/TRX XML, dotnet test, tsc, eslint, ruff, mypy, and so on. They're recognized by command fingerprint plus output shape. Unrecognized output is kept as raw evidence and never parsed into a PASS.
2. **Report files** (`host_reported`, stronger). JUnit XML or similar report files written during the session, read by the daemon and bound to the command that produced them.
3. **`arbiter verify`** (`arbiter_observed`). This runs verification commands configured for the repository in an opt-in `.arbiter/verify.yaml`, or in user config for repos where the user doesn't want files. It is exposed through the CLI and MCP (`arbiter_verify`), and it runs in the foreground, under the repository's normal permissions, only when requested or when a `block`-mode gate needs it. It is never speculative (§14.3).

Parsers are versioned and covered by fixture tests. A parser that fails on output it previously recognized trips the verification circuit breaker for that runner, so results fall back to UNKNOWN rather than being misread.

# 13. Module F — Diff Risk & Review Scheduler

## 13.1 Objective

Allocate review/testing effort according to consequence and blast radius rather than line count alone.

## 13.2 Risk signals

Combine deterministic and semantic features:

- auth/security boundaries;
- persistence/migrations/wire formats;
- public API/ABI/schema change;
- concurrency/synchronization;
- state mutation and error handling;
- deletion/destructive operations;
- dependencies/build/config/toolchain;
- test harness/fixtures/snapshots;
- generated code or generator changes;
- comments/docs-only;
- complexity delta;
- changed-callers/transitive dependency count;
- package/export surface;
- deployment/runtime configuration;
- reversibility and rollback cost;
- hidden dependency likelihood.

Deterministic tags establish minimum review floors.

## 13.3 Transitive blast radius

Risk is evaluated on both the hunk and affected graph:

```text
changed symbol
 -> direct callers/importers
 -> public/exported boundary
 -> persistent data/wire consumers
 -> mapped tests
```

A tiny change in a central parser or auth function may outrank hundreds of low-consequence lines.

## 13.4 Review allocation

```text
LOW
  -> static checks + sampled audit
MEDIUM
  -> normal model review + targeted tests
HIGH
  -> explicit high-effort review + broader relevant tests
CRITICAL deterministic tag
  -> mandatory configured verification; cannot be skipped by semantic score
```

## 13.5 Test scheduling

Inner-loop test selection is a prioritization problem, not permission to omit final verification. Tests may themselves mutate state, so run them according to the repository's configured test-sandbox policy.

## 13.6 Low-risk sampling

Use stratified sampling by file type/risk source/repository area, not purely random sampling. Estimate false-negative rates with stronger review on sampled “low-risk” hunks and automatically widen review when misses rise.

# 14. V3.1 Cross-Cutting Step-Up Layer

## 14.1 Operational Task-State IR + Contract Compiler

The state/contract system is defined in Section 6. The critical v3.1 rule is that **the IR is operational, not sovereign**. Immutable user intent and observable evidence can correct it at any time.

The compiler separates goal, contracts, constraints, facts, hypotheses, decisions, failed approaches, open questions, and verified progress. Inference fields carry confidence/provenance and expiration conditions.

## 14.2 Constrained Learned Utility Policy

Hand-tuned thresholds bootstrap the system, but learned policy must not turn the project into unconstrained RL.

### Decision formulation

First construct the allowed action set from hard constraints. Then estimate for each allowed action:

```text
P(material_progress)
P(correct_completion)
expected_rework
expected_remote_usage
expected_wall_time
risk/tail-loss estimates
```

Select the least-cost/latency action that satisfies the configured non-inferiority and risk margins. A scalar utility can rank ties, but it is not allowed to trade away required correctness for cheaper usage.

### Logged-policy confounding

Ordinary trajectories do not identify the value of unchosen actions. The dataset must record action propensities for any randomized safe experiment, and evaluation should use paired trials and, where appropriate, inverse-propensity or doubly robust estimators. Counterfactual replay is useful but not ground truth because model outputs, tools, repositories, and external services can be stochastic or change over time.

### Training path

1. rules only;
2. rules + calibrated SemIf;
3. safe randomized micro-experiments on low-consequence states with logged propensities;
4. paired checkpoint replay where reproducible;
5. simple interpretable predictors first;
6. repository/time/task-class holdouts;
7. shadow policy;
8. conservative automatic policy improvement only where uncertainty margins are satisfied.

Calibration may begin with hundreds to low-thousands of decisions, but broad learned resource allocation should expect **thousands to tens of thousands of diverse decisions**, not assume 300 checkpoints are sufficient.

## 14.3 Speculative Pure-Read Execution

**Hosted-session scope.** Prefetched results can be consumed only by Arbiter's own tools, or injected through a T2 hook within the injection budget (§4.4.5). They can't speed up the host's built-in reads. Wall-time benefits for hosted sessions are measured only on those paths.

The default allowlist contains only operations implemented as pure reads by the controller itself:

- direct file reads/stat calls within allowed roots;
- controller-owned lexical search;
- controller-owned symbol/AST/index queries;
- controller-owned local embedding lookup;
- hardened local git-object/history reads only when hooks, pagers, textconv/external diff, and network are disabled.

Default exclusions:

- arbitrary shell commands;
- tests/builds;
- package managers;
- network/documentation fetches;
- database queries unless a dedicated immutable snapshot backend exists;
- commands that load repository plugins/config with executable hooks;
- any external mutation.

### Cache validity

Cache keys include only dependencies that can affect the result, for example:

```text
operation + args + cwd + relevant file content hashes + git object/ref version
+ parser/index version + selected sanitized environment fingerprint
```

A whole-repository hash is too expensive; an underspecified key is unsafe. Each operation class defines its dependency fingerprint.

### Resource control

- low OS priority;
- bounded queue and concurrency;
- cancellation on goal/state change;
- automatic disable under foreground pressure;
- no credential inheritance unless explicitly needed for a pure local read;
- no network by default.

## 14.4 Selective Disagreement Branching

Branching is driven by **distinct hypotheses generated by the frontier model or deterministic evidence**, not by asking SemIf to invent alternative solutions. SemIf/utility policy only estimates whether branching is worth the extra compute.

### Trigger conditions

- at least two genuinely distinct plausible hypotheses/strategies;
- consequential expected rework if the wrong one is pursued;
- enough remaining remote quota;
- branch isolation available;
- expected value of information exceeds branch cost;
- no active user interruption or state conflict.

### Isolation

A git worktree alone is not a security boundary. Branch execution uses an ephemeral sandbox/container with:

- branch-specific writable workspace;
- isolated HOME/TMP/tool caches;
- scrubbed credentials;
- no network by default;
- isolated ports/process namespace where supported;
- separate mutable databases/services or none;
- explicit CPU/memory/time quotas.

A worktree may back the branch workspace for efficient storage, but shared `.git` metadata, hooks, LFS, caches, and external services are treated as potential cross-branch channels.

### Base-version correctness

Every branch is pinned to `base_state_version` and repository base hash. Before applying a winning patch:

1. verify main state still matches the expected base or explicitly rebase;
2. apply patch transactionally;
3. rebuild affected indexes;
4. rerun required verification on **main**, not just the branch;
5. only then record the branch as adopted.

### Budgets and rate limits

Normally two branches, 1–3 substantive frontier calls each, bounded total remote usage, and rate-limit-aware concurrency. Branching auto-disables if the provider throttles, the cost ratio exceeds policy, or winner quality is not measurably better than single-trajectory baselines.

## 14.5 Hierarchical routing

The controller fast path is:

```text
deterministic rule
 -> optional learned predictor
 -> SemIf
 -> conservative fallback / rare frontier audit
```

Every layer has an abstention path. A lower layer may save compute but can never weaken permissions, contracts, security floors, or verification requirements.

# 15. Cross-Module Orchestration

The modules act over one event-sourced state, not as independent heuristics.

## 15.1 Standard checkpoint flow

```text
1. Ingest/deduplicate event under current goal_epoch.
2. Reconcile observable facts and update the operational IR.
3. Cancel stale jobs whose goal_epoch/state_version no longer matches.
4. Update contracts, test-integrity state, loop features, diff graph, and context metadata.
5. Apply deterministic permissions/risk/contract floors.
6. Run bounded controller decisions through the cascade.
7. Context layer may update metadata; physical rewrite only in a supported native mode and safe epoch.
8. Tool gateway + retrieval define the next information surface.
9. Pure-read speculation may prefetch within resource budget.
10. Reasoning scheduler selects an actually supported effort and confirms the update.
11. Optional branch trigger may run isolated alternatives before the main decision is committed.
12. Codex/Astra acts.
13. Diff/test/verification state is recomputed from resulting observable state.
14. Completion Gate evaluates active contracts and evidence.
15. Persist outcome, controller versions, scores, cache behavior, and failures.
```

## 15.2 Backpressure and deadlines

Controller work must not become the bottleneck:

- bounded event queue;
- bounded SemIf queue;
- bounded index/retrieval jobs;
- per-module deadlines;
- cancellation propagation;
- priority classes: verification/integrity > foreground routing > background learning/speculation;
- overload shedding disables low-value optimization before it delays Codex.

## 15.3 Circuit breakers

Disable a module automatically when rolling metrics exceed configured limits, for example:

- false-complete incident;
- stale speculation result consumed;
- repeated schema miss;
- context restore failure;
- Q8 parity drift;
- learned-policy realized loss outside bound;
- controller timeout/error spike.

Circuit breaker state is visible and requires a clean recovery window or manual reset.

## 15.4 Important interactions

- bad retrieval can look like insufficient reasoning;
- context loss can look like model weakness;
- high-risk diffs override cheap reasoning suggestions;
- completion can trigger COLD rehydration;
- required verification capabilities remain available even if tool relevance is low;
- speculation/branches die immediately on goal epoch change;
- branch evidence may update hypotheses, but filesystem changes enter main only through explicit adoption;
- learned utility can choose among allowed actions only;
- test-integrity alerts invalidate “progress” and completion evidence until reviewed.

## 15.5 Conflict priority

1. Platform/upstream safety and permission policy.
2. Explicit current user instruction within allowed policy.
3. Active contracts and deterministic high-consequence requirements.
4. Integrity/verification requirements.
5. Context/evidence preservation.
6. Diff-risk and retrieval/tool coverage floors.
7. Isolation, rate-limit, and hard budget constraints.
8. Circuit breakers and compatibility gates.
9. Learned utility optimization.
10. Reasoning cost optimization.
11. SemIf preference among otherwise allowed options.

A user may disable the controller or request a cheaper effort, but cannot use the controller to bypass upstream safety/permissions. If a user elects to finish despite missing verification, the system may comply where allowed while clearly marking the result as unverified rather than converting UNKNOWN to PASS.

# 16. Persistence, Privacy, and Data Model

Use SQLite for metadata with WAL mode, foreign keys, schema migrations, and a single-writer queue. Large evidence blobs live in a separate local store with permissions and optional encryption. The controller's append-only audit log is independent from Codex rollout/history files.

## 16.1 Transaction model

- one serialized DB writer; readers use snapshots;
- WAL + busy timeout;
- foreign keys enabled;
- explicit migrations with rollback/backup plan;
- every state transition and context epoch is transactional;
- branch/speculation cleanup uses a durable cleanup journal;
- no destructive garbage collection during an active task.

## 16.2 Core schema

```text
client(
  id, profile_id, profile_version, client_version,
  verified_tiers_json, capabilities_json, detected_at, last_probe_at, drift_json
)

client_session(
  id, client_id, native_session_id, repo_id, thread_id,
  started_at, ended_at, verified_tiers_json
)

install_manifest(
  id, client_id, config_file, entry_path, entry_hash,
  backup_pointer, installed_at, removed_at
)

event_log(
  seq, client_id, session_id, surface, thread_id, goal_epoch, state_version,
  upstream_id, event_type, raw_hash, received_at, payload_pointer, sensitivity_class
)

thread(
  id, client_id, created_at, codex_version, model, integration_mode,
  current_goal_epoch, current_state_version, current_effort,
  controller_enabled, circuit_breakers_json
)

checkpoint(
  id, thread_id, goal_epoch, state_version, timestamp, phase,
  effort_before, effort_after, effort_update_outcome,
  loop_score, risk_tags_json, context_epoch,
  tool_surface_hash, retrieval_set_hash,
  controller_versions_json, sem_if_scores_json, policy_reason_json,
  cached_tokens, input_tokens, output_tokens, latency_ms, outcome_json
)

context_item(
  id, thread_id, goal_epoch, created_at, type, source,
  token_count, visibility_tier, sensitivity_class,
  current_relevance, future_relevance, reproducibility,
  reconstruction_cost, causal_importance, unresolved_evidence,
  archive_pointer, content_hash, superseded_by, metadata_json
)

context_edge(parent_id, child_id, relation_type)

intent(
  id, thread_id, goal_epoch, raw_text_pointer, timestamp,
  supersedes_intent_id, user_visible_hash
)

contract(
  id, thread_id, goal_epoch, version, normalized_text,
  source_intent_ids_json, quotes_json, proposed_by, scope, status,
  mapping_confidence, strength_flag, verification_recipe_json,
  min_evidence_origin, evidence_json, superseded_by
)

session_baseline(
  id, session_id, repo_identity_json, head, dirty,
  test_file_hashes_json, harness_config_hashes_json, test_inventory_pointer, captured_at
)

task_state_snapshot(
  id, thread_id, goal_epoch, state_version, checkpoint_id,
  state_json, provenance_json, consistency_status
)

tool_decision(
  checkpoint_id, catalog_version, family, tool_id,
  score, discoverable, reason
)

retrieval_candidate(
  checkpoint_id, repo_id, index_version, file_hash,
  repo_path, symbol, span, generator, base_rank,
  semantic_score, selected, reason
)

diff_hunk(
  checkpoint_id, file_path, file_hash, hunk_id,
  deterministic_tags_json, semantic_risk,
  review_tier, tests_selected_json, verification_result_json
)

verification(
  checkpoint_id, contract_id, evidence_type, evidence_origin, ingest_channel,
  parser_id, parser_version, evidence_pointer, evidence_grade, status, confidence
)

utility_decision(
  checkpoint_id, policy_version, action_family,
  candidate_actions_json, action_propensities_json,
  predicted_outcomes_json, uncertainty_json,
  chosen_action, realized_outcome_json
)

speculative_action(
  id, checkpoint_id, goal_epoch, base_state_version,
  action_type, dependency_fingerprint, started_at, completed_at,
  hit, invalidated, cancellation_reason, cost_json, result_pointer
)

branch_run(
  id, checkpoint_id, goal_epoch, base_state_version, base_repo_hash,
  branch_type, hypothesis_id, sandbox_id, budget_json,
  status, outcome_json, winner, cleanup_status
)
```

## 16.3 Privacy and secret handling

Default policy:

- local-only storage;
- restrictive filesystem permissions;
- do not upload trace datasets automatically;
- redact known secret formats from logs where the raw value is not required;
- never embed/index credential stores by default;
- mark evidence sensitivity class;
- encrypt persisted raw blobs when the user enables durable archives on shared/multi-user machines;
- use a keyed/HMAC identifier where content-address identifiers could reveal guessable secret content;
- exclude secret-bearing examples from training datasets by default;
- support configurable retention and logical deletion after task/session expiry; on SSDs, do not promise forensic secure erasure—prefer encryption-at-rest with key disposal where stronger deletion semantics are required.

### 16.3.1 Project scope

Because Arbiter is installed at user scope and watchers can see every session on the machine, ingest is limited by an explicit project scope:

- **Default scope:** all projects, except those excluded. The user confirms or changes this during setup.
- **Exclusion:**
  - a global exclude list in user config (paths and globs);
  - an `.arbiterignore` file at a repository or directory root, which excludes that tree;
  - `arbiter exclude <path>` / `arbiter include <path>`.
- **Allow-list mode** (`privacy.project_scope: allow_list`) ingests only listed projects.
- **Skipped before parsing.** Excluded sessions are skipped before their content is parsed. Only a counter of skipped sessions is kept.
- **Excluding a project later** stops ingest immediately and, if the user chooses, purges that project's existing records.

### 16.3.2 Redaction at ingest

Secret redaction runs **at ingest**, before the event log or blob store is written. It covers known token formats, private keys, `.env`-style assignments, and high-entropy strings in credential-like contexts. Redacting only on log output isn't enough. Redaction replaces the value with a typed placeholder and a keyed HMAC, so repeated occurrences can still be correlated without storing the value.

### 16.3.3 Retention and storage caps

- Default retention is **30 days** after session end for raw event payloads and blobs. Contracts, finish ledgers, and aggregate metrics are kept for **180 days**. Both are configurable.
- A total storage cap (default **2 GB**) triggers oldest-first logical deletion of expired or closed-session raw payloads. It never touches active-session evidence (§18.7).
- Large tool outputs are stored once (content-addressed, keyed per §16.3) and referenced. Payloads above a size cap are truncated with a hash of the full content and a note of the truncation.
- `arbiter doctor` reports storage used, what is scheduled for deletion, and the scope settings.

## 16.4 Recovery

On resume:

- discover exact Codex/model capabilities again;
- reconcile current goal epoch and latest user intent;
- rebuild repo/diff facts from observable filesystem state;
- invalidate speculative results with mismatched dependency fingerprints;
- clean/reconcile orphan sandboxes from durable cleanup journal;
- validate active contracts and evidence;
- do not assume prior reasoning/tool/context updates succeeded merely because they were requested.

## 16.5 Versioning and upgrades

Arbiter is upgraded while clients are running, so every boundary is versioned:

- **IPC protocol.** Every shim–daemon connection starts with a handshake: protocol version, Arbiter version, and capability flags.
  - A compatible minor mismatch is served.
  - If the shim is newer than an incompatible daemon, the shim asks the daemon to drain and restart. The daemon finishes in-flight writes, flushes the WAL, exits, and is lazily restarted from the new install. While that happens the shim passes through, failing open.
  - If the daemon is newer than an incompatible shim, the daemon answers with pass-through instructions, and `arbiter doctor` reports the stale shim.
- **Database schema.** Migrations run only at daemon start, after an automatic backup of the database file. A failed migration restores the backup and starts the daemon in read-only degraded mode. It never runs on a half-migrated schema.
- **Client profiles.** Each profile has its own `profile_version` and a profile schema version. Built-in profiles upgrade with Arbiter. User profiles with an unsupported schema version are ignored and reported, not partially applied.
- **Install manifest.** Upgrades that change what setup writes run a manifest migration. `arbiter setup` after an upgrade shows the diff before applying it.
- **Downgrades** are supported for one minor version via the pre-migration backup. Older downgrades require `--purge`.

## 16.6 Arbiter's own diagnostics

- Structured JSON logs go to the platform log directory (`%LOCALAPPDATA%\arbiter\logs`, `~/Library/Logs/arbiter`, or `$XDG_STATE_HOME/arbiter/logs`), with size-based rotation. Logs pass through the same redaction as ingest.
- `arbiter logs [--follow] [--component X]` reads them. `arbiter debug on|off` raises verbosity temporarily, and debug mode expires automatically after 24 hours.
- Every shim writes a single local line when it fails open, so a silently degraded client is discoverable.
- `arbiter doctor --bundle` produces a redacted diagnostic bundle locally for bug reports. It is never uploaded automatically.

# 17. Prompt Cache and Economic Controller

Prompt-cache behavior can dominate theoretical savings. Reasoning settings, context rewriting, tool schemas, and injected state can all change reusable prefixes.

## 17.1 Stable context ABI

Treat the model-visible control-plane surface as an ABI:

- stable tool/gateway schemas;
- stable system/developer instruction prefix;
- bounded canonical context packets with version IDs;
- append-only deltas within an epoch where possible;
- physical history rewrite only at deliberate native compaction epochs.

Current Codex contributor guidance explicitly warns against frequent context changes/history rewrite and requires bounded context items. The control plane follows the same principle. [S20]

Hook-injected context (§4.4.5) is part of this ABI:

- it is capped at `max_injected_tokens`;
- it is emitted only when changed;
- it contains no per-turn volatile fields;
- it is placed late in the turn.

Injected tokens are counted as controller cost in §17.2.

## 17.2 Economic decision

For an optimization action estimate:

```text
expected_remote_usage_saved
+ expected_latency_saved
+ expected_rework_avoided
- cache_rewrite_penalty
- local_controller_cost
- expected_information_loss
- expected_quality_risk
```

But apply the result only after correctness/risk constraints are satisfied.

## 17.3 Billing modes

Do not assume every Codex environment has a dollar-per-token price. Support:

- API dollar pricing;
- subscription/quota usage units;
- token/reasoning counts;
- wall time as a separate objective.

Reports must state which cost proxy was used.

## 17.4 Required telemetry

- effort transition requested/confirmed;
- context epoch and history version;
- tool/gateway surface hash;
- retrieval additions/removals;
- actual input/cached/output tokens where exposed;
- first-token/total latency;
- local SemIf/controller latency;
- provider rate-limit/throttle events;
- rework/retry outcomes;
- speculation launched/hit/waste;
- branch cost/winner evidence;
- learned predicted vs realized outcomes.

## 17.5 Price billed tokens, not raw tokens

The first real measurements, from Hivemind's M10.7 corpus of six Codex calls, found that **87.6% of input tokens were cache reads**, served at a 90% input-price discount. The same corpus found per-successful-task cost differences between model tiers of roughly **2.4× (Terra vs Sol) to 54.7× (Luna vs Sol)** on its fixed task shapes. These numbers are tied to one corpus, one set of models, and one date, and are not universal. Consequences for every economic estimate in this spec:

- Savings are computed on **billed** usage: uncached input + discounted cached input + output + reasoning. Raw token counts are not the measure. A context reduction that removes mostly cached tokens saves little money or quota, and it can *cost* money if it breaks a warm prefix.
- Input-token reduction is reported separately from cost reduction, because it still helps latency and context headroom.
- Model-tier choice (§8.8) is expected to be the dominant cost lever, followed by avoiding rework. Context trimming comes after both.
- Cache-hit rate is measured per host, client, and model, never assumed (§17.4).

# 18. Security and Trust Boundaries

## 18.1 Repository/tool prompt injection

Repository text may contain controller-like instructions. They are data. The system reduces exposure by preferring structured facts/metadata over raw text and by enforcing permissions/budgets outside the model.

Prompt delimiters are not a security mechanism. A malicious file may still bias semantic scores, so any score-driven action that can spend money or remove evidence has deterministic caps and an abstention path.

## 18.2 Tool authority

The Tool Scheduler/gateway controls discovery/routing only. Authorization remains upstream/deterministic. The gateway validates canonical schemas and scopes every call. Proxying never collapses the client's per-tool approval granularity (§10.7).

## 18.3 Secret boundaries

- no automatic indexing of secret stores;
- no secret values in controller prompts unless necessary;
- redact from ordinary logs;
- scrub branch/speculation environments;
- no speculative network access;
- credentials are injected only into explicitly authorized foreground operations.

## 18.4 Speculation safety

“Read-only” is capability-defined, not command-name-defined. Arbitrary shell commands are not considered pure reads. Tests/builds are not pure reads. Git operations are hardened against hooks/pagers/external diff/textconv. If purity cannot be proven, the operation is not speculated.

## 18.5 Branch isolation

Git worktrees share too much state to be a security boundary. Branch execution uses sandbox/container isolation with separate writable state, scrubbed credentials, controlled network, process/resource limits, and cleanup leases.

## 18.6 Fail behavior matrix

| Component | Failure behavior |
| --- | --- |
| Reasoning optimizer | fail open to baseline/default effort |
| Retrieval optimizer | fail open to broader/default retrieval |
| Tool discoverability optimizer | fail open to normal allowed tool surface |
| Context compaction | fail open to existing Codex context/built-in compaction; never install partial rewrite |
| SemIf | fail open for optimization, abstain for destructive/high-consequence decisions |
| Completion/contract evidence | fail conservative: remain UNKNOWN/not automatically complete |
| Speculation | fail closed: cancel/disable |
| Branch isolation | fail closed: do not branch/apply patch |
| Privacy/secret policy | fail closed for data export/persistence that exceeds policy |
| Client adapters / MCP + hook shims | fail open: pass through to the host agent unchanged; never block it |
| Completion gate hook | never reports PASS on failure; never blocks because of its own failure; gates only recognized completion claims; in `block` mode blocks at most `max_stop_blocks_per_epoch`, then allows stop marked unverified |
| Verification parsers | unrecognized or previously-recognized-now-failing output → UNKNOWN, never PASS |
| IPC authentication | fail closed: reject unauthenticated connections; shims then pass through |
| Upgrade / migration | restore pre-migration backup; daemon starts read-only degraded; shims pass through |
| Setup / uninstall | fail closed: abort without writing any config file that cannot be parsed, merged, and validated |
| Client profile drift | drop only the tiers whose conformance checks fail; keep the rest |

## 18.7 Permanent retention/deletion

During an active task, no evidence is irreversibly deleted by semantic judgment. Post-task retention/GC is policy-based, auditable, and independent of prompt compaction.

## 18.8 Local IPC trust

The daemon accepts events that can move contracts toward PASS, so its IPC endpoint is a trust boundary:

- **Endpoint access control.**
  - On Windows, the named pipe has a DACL granting access only to the current user's SID. Remote pipe clients are rejected.
  - On macOS and Linux, the Unix socket lives in a per-user runtime directory with mode `0700`.
- **Per-install token.** A random token is stored in a user-only file (`0600`, or a user-only ACL on Windows). Every shim connection presents it, including over the TCP fallback, which binds to loopback only. Shim tokens rotate on daemon restart. The token is never logged.
- **HTTP hook endpoint** (decision 0021). The daemon serves `POST /hook/<event>` on a **fixed loopback port** chosen at setup, for clients with `http` hook handlers (Claude Code).
  - Requests must carry the hook token in a header. That token is written into the client's user-only settings at setup and rotates only on `arbiter setup`, so hook config stays stable.
  - Unauthenticated requests are rejected, and the shims and hooks then fail open.
- **Channel provenance.** Every ingested event records its surface and authenticated channel. Evidence origin (§6.4.1) limits what each channel can prove. No IPC message can directly set a contract to PASS; only evidence rules can.
- **Payload hygiene.** Payloads are size-capped, schema-validated per profile, and treated as untrusted evidence (§6.6).
- **Threat model scope.** A process already running as the same user with full access to the user's files is outside what Arbiter can defend against. The goal is to stop other users, remote clients, and casual forgery, and to ensure a forged host report can never be mistaken for `arbiter_observed` evidence.

# 19. Failure Modes and Mitigations

The register below contains the major architectural failure classes found in red-team review.

## Integration and upstream compatibility

- **Assuming app-server can perform custom history replacement:** Feature matrix + capability probe; full Context Scheduler requires native/patched mode until an upstream replacement-history API exists. [S9]
- **Effort change invalidates cache:** Measure exact installed build; price cache loss or disable dynamic effort. [S4]
- **Per-thread config/update hangs:** Version pin, deadlines, startup conformance, no reliance on a failing path. [S12]
- **Context rollover loses or resurrects task:** Goal epochs, durable two-phase checkpoint, restore sentinel, latest-intent validation. [S10][S11]
- **Stale token accounting overflows context:** conservative local exact-token estimate and output reserve. [S13]
- **Compaction destroys transcript:** independent append-only controller audit log; never trust upstream rollout as sole durable record. [S18]
- **Upstream event duplication/out-of-order behavior:** idempotent reducer, monotonic local sequence, pending-call reconciliation.

## Clients and setup

- **Client update changes its config, hook, or transcript format:** Versioned profiles, per-client probe with drift detection, recorded-fixture contract tests, drop only the failing tiers.
- **Many clients spawn many controllers:** Single-instance daemon lock; shims are stateless forwarders; only the daemon writes the database or loads SemIf.
- **Hooks add perceptible latency:** Stdlib-only hook CLI, async telemetry hooks, strict deadlines, measured p95 with per-client circuit breaker; compiled shim if needed.
- **Stop hook traps the agent in a loop:** `annotate` is the default mode; in `block` mode, `max_stop_blocks_per_epoch`, then allow stop marked unverified.
- **Gate blocks an agent that is asking the user a question:** Gate acts only on recognized completion claims; a final message ending in a question to the user, or one that can't be classified, is never gated (§12.7).
- **Contract compiler has no semantic engine (rules-only mode):** Host agent proposes contracts with verbatim quotes; Arbiter verifies quotes, recipes, and coverage deterministically (§6.8.1).
- **Agent games its own contracts:** Quote provenance, typed recipes, coverage check, weak-contract flag, agent cannot set PASS, `agent_asserted` evidence insufficient alone.
- **Ordinary reply wipes task state:** Candidate epochs; contracts recompiled only on confirmed scope change (§6.3.1).
- **Gateway proxy collapses per-tool approvals:** Read-only-only proxying by default, mirrored approval via elicitation or refusal, no approval inheritance, explicit `gateway adopt` (§10.7).
- **Gateway duplicates native tool deferral:** Profile flag + per-client benchmark; gateway off where native deferral already delivers the benefit.
- **Forged "tests passed" event over IPC:** User-only endpoint ACL, per-install token, channel provenance, evidence-origin limits (§18.8, §6.4.1).
- **Test output misparsed as PASS:** Versioned runner parsers with fixtures; unrecognized output stays UNKNOWN; parser circuit breaker (§12.8).
- **No baseline for test-integrity comparison:** Session baseline capture; integrity UNKNOWN without it (§12.4).
- **Watchers ingest out-of-scope or secret-bearing sessions:** Project scope with `.arbiterignore`/exclude list, skip-before-parse, redaction at ingest (§16.3.1–16.3.2).
- **Unbounded storage growth:** Default retention, storage cap, content-addressed large outputs (§16.3.3).
- **Daemon killed with the client's process tree (Windows job objects):** Detached spawn; login autostart fallback (§4.4.1).
- **Shim/daemon version skew after upgrade:** Protocol handshake, drain-and-restart, pass-through during restart (§16.5).
- **Path aliasing across case, separators, symlinks, worktrees:** Normalized repository identity and path keys (§11.9).
- **Injected context inflates every turn:** Token cap, inject-on-change, no volatile fields (§4.4.5, §17.1).
- **SemIf latency stalls a gating hook:** SemIf never awaited on the synchronous path (§4.4.5, §7.8).
- **Setup corrupts a user config file:** Parse-merge-validate, backup, atomic write, abort on any parse failure, owned-entry manifest for exact uninstall.
- **Setup weakens a client's safety settings:** Setup is forbidden from touching permission/approval/sandbox/model settings.
- **Daemon down or crashed:** Shims fail open; lazy restart on next call; completion gate reports no PASS.
- **Concurrent sessions from different clients on one repo:** Sessions keyed by client + native session ID; per-session goal epochs and contracts; shared repository facts via overlays.
- **Same event ingested twice via hook and transcript:** Surface-independent or profile-defined dedupe keys.
- **Hook payload or transcript content tries to steer the controller:** Treated as untrusted evidence (§6.6); cannot change limits, permissions, or floors.

## SemIf/model judgment

- **Treating raw scores as probabilities:** workload-specific calibration; label uncalibrated values as model scores.
- **Option-order sensitivity:** mirrored/perturbed variants used to detect instability, not assumed to remove bias.
- **Shared-state optimization changes decisions:** fresh BF16 reference and per-module drift gates.
- **Shared-state throughput overestimated:** batch only identical prefixes; benchmark real controller mixtures.
- **Malformed/NaN classifier output:** strict schema/range validation and abstention. [S16]
- **Classifier request overflows:** exact tokenizer budgeting with question/envelope reserve. [S15]
- **SemIf timeout/OOM:** bounded queue, cancellation, restart, fail policy.

## State/contracts

- **IR overwrites true user intent:** immutable intent layer; IR is operational and correctable.
- **Contract compiler misses requirement:** high-recall mapping, exact source pointers, stronger audit for low-confidence explicit requirements.
- **New user request is lost behind old state:** increment goal epoch, cancel stale work, pin latest intent.
- **Inference presented as fact:** typed FACT/INTENT/INFERENCE/CONTRACT fields.
- **Resume state inconsistent with filesystem:** rebuild observable facts and mark uncertain fields UNKNOWN.

## Reasoning/progress

- **Router predicts difficulty instead of marginal value:** adjacent-tier benefit questions + constrained policy.
- **Unsupported effort tier requested:** runtime capability lattice.
- **Effort update request silently fails:** confirm applied outcome/state.
- **Agent “improves” by weakening tests:** test-integrity detector and harness risk floor.
- **Repeated failure caused by missing evidence, not reasoning:** broaden retrieval before repeated expensive escalation.
- **Effort oscillation/sticky high effort:** hysteresis, leases, cooldown.

## Context

- **Unique evidence disappears:** no active-task irreversible deletion; archive/audit log always remains.
- **Pending tool call omitted:** unresolved calls pinned until reconciliation.
- **Context rewrite races concurrent writes:** history-version CAS + two-phase epoch; abort on version mismatch. [S17]
- **Compaction rewrites too often and kills cache:** stable epochs and economic trigger.
- **COLD archive leaks secrets:** sensitivity classes, redaction, encryption, access control.
- **Rehydration retrieves stale branch/file state:** evidence keyed by repo/worktree/content versions.

## Tools/retrieval

- **Dynamic schema churn costs more than it saves:** stable gateway by default.
- **Tool miss blocks task:** core surface + gateway search + immediate miss recovery.
- **Gateway becomes permission bypass:** canonical schema validation + upstream authorization on every call.
- **Retrieval hides key file:** deterministic pins, adaptive breadth, miss detector.
- **Index stale after edits/branch:** content-hash invalidation + branch overlays.
- **Retrieval exposes secrets:** access policy and secret scanning before indexing/injection.

## Completion/review

- **Completion gate says done too early:** contracts + evidence recipes; UNKNOWN remains incomplete.
- **Tests pass because tests were weakened:** test-integrity check invalidates evidence.
- **Low-risk hunk actually high blast radius:** transitive graph analysis and stratified low-risk audits.
- **Targeted tests miss regression:** risk-based final verification; targeted tests are inner-loop optimization only.

## Learning

- **Logged-policy confounding:** propensity logging for randomized experiments; offline policy evaluation with appropriate estimators.
- **Single counterfactual replay mistaken for truth:** repeated seeds/samples and reproducibility tagging.
- **Proxy reward Goodharting:** constrained non-inferiority/risk formulation rather than unconstrained scalar reward.
- **Repository/model drift:** repo/time/model-version holdouts, drift detector, conservative fallback.
- **Insufficient data:** delay broad learned automation until diverse decision count is adequate.
- **Training data leaks secrets:** privacy filters, local-only datasets, secret exclusion.

## Speculation/branching

- **“Read-only” command has hidden side effect:** pure-operation allowlist; no arbitrary shell/tests/network by default.
- **Speculative result stale:** dependency fingerprint + state/goal version + TTL.
- **Speculation steals resources:** low priority, concurrency/I/O caps, auto-disable under foreground pressure.
- **Worktree leaks state:** stronger sandbox isolation with separate HOME/TMP/cache/network/credentials.
- **Main changed while branch ran:** base-version CAS/rebase then reverify on main.
- **Branch cost explodes/rate limits hit:** two-branch default, remote quota/rate-limit budget, early stop.
- **Branch winner only works in branch:** reapply/merge transactionally and rerun verification on main.

## Reliability

- **SQLite contention/corruption:** WAL, single writer, migrations/backups, bounded queues.
- **Controller blocks Codex:** deadlines, cancellation, overload shedding.
- **Module quality suddenly degrades:** rolling incident metrics + automatic circuit breaker.
- **Manual controller override conflicts with safety:** upstream policy remains above controller/user optimization controls.

# 20. Evaluation Framework

The evaluation goal is not classifier accuracy. It is whether the control plane moves the **end-to-end cost/latency vs correctness Pareto frontier** without creating unacceptable tail failures.

## 20.1 Baselines

Use multiple baselines because “stock Codex” may itself change:

- stock/current Codex default behavior;
- fixed MEDIUM or closest supported middle tier;
- fixed HIGH/strong tier;
- full-context/full-tool or normal Codex context behavior as appropriate;
- v3.1 with each module ablated.

Record exact model/version/config/permissions for every baseline.

## 20.2 Dataset and split

Collect diverse tasks across repositories, languages, task classes, project sizes, and phases. Split by whole repository and time; also hold out task classes where possible.

For early calibration, hundreds to low-thousands of decisions can be useful. For learned cross-module utility policies, expect several thousand or more diverse decisions before broad automation is credible.

## 20.3 Paired and randomized design

Prefer paired tasks/checkpoints. Where safe and affordable:

- randomize among allowed low-consequence controller actions;
- log action propensities;
- run multiple seeds/samples for stochastic model comparisons;
- predefine non-inferiority margins;
- use bootstrap confidence intervals for paired differences;
- report both mean and tail/severity outcomes.

Do not cherry-pick successful long tasks.

## 20.4 Quality ground truth

Use the strongest available evidence:

- hidden/held-out tests where possible;
- repository's real test/build/typecheck/lint suites;
- mutation tests or adversarial regression cases on selected benchmark projects;
- human/strong-model review blinded to policy for ambiguous quality judgments;
- contract satisfaction and final user-visible correctness.

A passed self-modified test suite is not sufficient quality ground truth.

## 20.5 Reasoning benchmark

Fork checkpoints across supported efforts. Measure progress, regressions, rework, calls-to-resolution, remote usage, cache behavior, and latency. Repeated samples estimate variance.

## 20.6 Context benchmark

Compare full/native baseline, ordinary summarization, semantic visibility management, and rehydration. Measure completion quality, repeated work, recovery events, prompt tokens, cache hit rate, and restore failures.

Only run replacement-history experiments in a deployment mode that truly supports them.

## 20.7 Tool/gateway benchmark

Compare normal schemas, stable gateway, and any supported dynamic-schema mode. Measure prompt tokens, discovery latency, wrong-tool attempts, misses, permissions behavior, cache effects, and task completion.

## 20.8 Retrieval benchmark

Track recall@k/coverage of later-proven relevant files/symbols, useful-chunk precision, time-to-evidence, token reduction, and downstream progress. Report dangerous misses separately from harmless extra context.

## 20.9 Completion benchmark

Label false-complete, correct-complete, and overwork cases. Measure false-complete severity, extra turns after verified completion, unverified claims, and final quality.

## 20.10 Diff-risk benchmark

Use stronger review/hidden tests on sampled hunks. Measure severity-weighted false negatives, review compute saved, targeted-test hit rate, and regressions found only by broader verification.

## 20.11 State/contract benchmark

Include explicit requirements, later modifications, interruptions, compaction/resume, branch merge, conflicting evidence, and stale-task traps. Measure requirement recall, wrong contract mapping, false PASS, state consistency, restore correctness, and provenance completeness.

## 20.12 Learned-policy benchmark

Compare rules, learned advisory, and bounded learned auto policy. Use repository/time/model holdouts. Report calibration, propensity-aware off-policy estimates where used, realized paired outcomes, OOD abstention, and worst task-class regression.

## 20.13 Speculation benchmark

Measure hit rate, latency saved, wasted I/O/CPU, foreground slowdown, stale-result incidents, cancellation correctness, and zero-side-effect compliance. Evaluate each operation class separately.

## 20.14 Branching benchmark

On genuine competing-hypothesis states, compare single HIGH/XHIGH with bounded branching. Measure correct resolution, dead-end time, total remote usage, throttling, merge/reverify failures, and branch-isolation incidents.

## 20.15 Controller overhead benchmark

Measure local GPU utilization, CPU/disk load, DB latency, queue wait, SemIf latency, index maintenance cost, per-client hook and MCP shim latency (p50/p95/p99), daemon cold-start time, controller-induced stalls, and power use if relevant. A 10% remote saving is not useful if the controller adds equivalent wall-time contention.

## 20.16 Primary release criterion

Predeclare a quality non-inferiority margin and severe-failure ceiling. The system ships a module only if it:

1. stays within the quality/tail-risk bounds; and
2. materially improves at least one of remote usage or wall-clock time without unacceptable controller overhead.

Report confidence intervals and task mix, not a single headline percentage alone.

## 20.17 Default numeric gates

Qualitative exit criteria ("very low," "stable," "measurable") are replaced by the provisional defaults below.

- **Tightening** a number is always allowed.
- **Loosening** one requires a decisions-log entry (`docs/decisions/`) recorded **before** the relevant evaluation run starts.
- **Preregistration.** A stage's numbers are frozen (preregistered) when its evaluation cohort is defined.

| Area | Gate | Provisional default |
| --- | --- | --- |
| Quality | Non-inferiority margin on correct completion vs. strong baseline | lower 95% CI bound of (Arbiter − baseline) ≥ −2 pp |
| Quality | Severe-failure ceiling (severity-1: false PASS on a critical contract, data loss, safety/permission regression) | 0 in the evaluation cohort |
| Benefit | "Material" improvement | ≥ 10% reduction in remote usage **or** wall time, with 95% CI excluding 0 |
| Hooks | Telemetry hook p95 added latency (async) | ≤ 50 ms |
| Hooks | Gating hook p95 latency | ≤ 300 ms; hard deadline 1500 ms |
| Daemon | Cold start to first served request | ≤ 2 s (p95) |
| Setup | Fixture-matrix setup/uninstall success | 100%; uninstall with no intervening user edits restores byte-identical files |
| Setup | Config files corrupted | 0 |
| Completion | False PASS in benchmark (incl. adversarial cases) | 0 |
| Completion | Gate firing on non-completion turns (e.g. agent asked the user a question) | ≤ 2% of stops |
| Completion | False-complete rate, severity-weighted | ≤ 50% of the stock baseline's |
| Contracts | Explicit-requirement recall (quoted and covered) on held-out tasks | ≥ 0.95 |
| Test integrity | Detection of test-weakening benchmark cases | ≥ 0.95, with 0 missed deletions of whole test files |
| Verification | Runner-parser misreads (FAIL/UNKNOWN read as PASS) on fixtures | 0 |
| Loop detection (advisory) | Precision of loop alerts | ≥ 0.80 |
| Retrieval | Recall@selected of later-proven relevant files | ≥ 0.95; dangerous misses ≤ 1% of tasks |
| SemIf | Malformed results consumed | 0; queue saturation never blocks a hook |
| Circuit breakers | Fault-injection trip rate | 100% |
| Speculation | Stale results consumed / side-effect incidents | 0 / 0 |
| Context | Restore or state-loss incidents | 0 |
| Branching | Isolation failures | 0 |
| Stability | "Stable latency" for a module | p95 within 20% of its week-1 value over 7 days of use |

# 21. Calibration, Learning, and Data Flywheel

SemIf is a semantic feature source; the learned layer estimates when those features are useful. Keep calibration and policy learning separate.

## 21.1 Calibration

- calibrate by decision family and model/backend version;
- use repository/time holdouts;
- monitor expected calibration error/Brier-style metrics where labels are probabilistic/binary;
- use isotonic or Platt-style calibration only when data volume supports it;
- recalibrate after prompt, model, quantization, or upstream Codex changes;
- preserve an abstention region rather than forcing every score into a decision.

## 21.2 Outcome dataset

Each record includes:

```text
STATE
  operational IR features + provenance summary + compatibility mode

CANDIDATE ACTIONS
  only actions permitted by hard policy

POLICY INFO
  policy version, SemIf/predictor versions, propensities if randomized

CHOSEN ACTION
  what actually executed and whether application succeeded

OUTCOME
  progress, contracts, tests, regressions, rework,
  remote usage, cache effects, wall time, controller overhead,
  final completion and failure severity
```

## 21.3 Counterfactual data limits

Replay is most valuable when the environment is reproducible and the checkpoint can be restored exactly. Tag each replay with reproducibility class. Do not combine live external API state, changed package registries, or different repository heads as if they were clean counterfactuals.

## 21.4 Policy learning sequence

1. deterministic rules;
2. calibrated SemIf advisory;
3. safe low-consequence randomized experiments with propensity logging;
4. learned shadow model;
5. learned recommendations with deterministic execution;
6. conservative auto policy for high-confidence in-distribution states;
7. wider coverage only after time/repository holdouts and circuit-breaker data remain healthy.

## 21.5 Drift

Track model version, Codex version, repository family, tool catalog version, SemIf revision/backend, and prompt/calibration version. Automatic drift triggers revert the affected learned policy to shadow mode.

## 21.6 Data privacy

Training/evaluation datasets remain local by default. Raw source/prompt/evidence is minimized; secret-bearing records are excluded unless explicitly required in a protected local benchmark. Export requires explicit configuration and a redaction pass.

# 22. SemIf Precision/Backend Parity Ladder

The production starting point is **BF16 CUDA fresh scoring**, because that is the currently documented native CUDA path. Optimization proceeds only after the backend exists and is measurable.

1. BF16 + fresh scoring — semantic reference.
2. BF16 + shared-state/reuse — isolate reuse drift.
3. Optional NVIDIA quantized backend, if implemented — isolate quantization drift.
4. Quantized + shared-state/reuse — deployment candidate only if prior gates pass.
5. Only then tune batching, KV/state precision, kernels, and queueing.

## Parity gates

- decision/outcome agreement measured per module, not one global number;
- no systematic shift toward higher spend, more aggressive context eviction, narrower retrieval, or weaker review;
- no high-severity error cluster;
- recalibrated scores remain useful;
- system-level completion quality remains within the preregistered non-inferiority margin.

A provisional 98.5% argmax agreement can be a diagnostic for low-consequence judgments, but high-consequence categories use outcome/severity gates rather than a single percentage.

If a quantized NVIDIA path never beats BF16 on total controller throughput/VRAM economics for this workload, keep BF16. Quantization is an optimization, not a project requirement.

# 23. Rollout Strategy

The rollout has a **core track**, which is the product, and a separate **research track**. Research stages start only after the core track is stable, and they never block a core release. All numeric exit gates refer to §20.17.

## 23.1 Core track

| Stage | Behavior | Exit criterion |
| --- | --- | --- |
| 0 — Assumption spikes | Throwaway experiments that confirm or refute the load-bearing assumptions (§27, step 0). | Each spike has a written result in `docs/decisions/`; the design is adjusted where a spike fails. |
| 1 — Foundations + clients | Daemon, IPC trust, MCP/hook shims, storage, event log, privacy scope, first client profiles, `setup`/`doctor`/`uninstall`, per-client capability probe. | Detected clients are configured reversibly with one command; each client's exact environment and verified tier set are recorded; §20.17 setup, daemon, and hook gates pass. |
| 2 — Task state + rules-only gate → **v0.1** | Intent, candidate/confirmed epochs, typed IR, agent-proposed contracts, verification observation, test integrity, loop telemetry, completion gate in `annotate` mode (opt-in `block`), eval harness v0. Minimal circuit breakers: hook latency, gate errors, parser failures. | §20.17 completion, contract, test-integrity, and verification gates pass on the benchmark corpus. **v0.1 ships.** |
| 3 — Shadow SemIf | Null backend first; BF16 when the GPU is available. Scores decisions with strict validation and no behavior change. | §20.17 SemIf and stability gates; calibration data collected. |
| 4 — Decision cascade + full circuit breakers | Policy core, cascade, full fail-mode matrix, overload shedding. **Required before any automatic stage**, per principle 28. | 100% fault-injection trip rate; lower controller cost without quality regression. |
| 5 — Advisory | Reasoning, retrieval, and diff-risk recommendations. | Dangerous-miss and loop-precision gates; understandable audit trail. |
| 6 — Gateway/retrieval bounded auto | Gateway where it beats native deferral; read-only proxying; conservative reranking. | Material benefit per §20.17, with retrieval recall gates. |
| 7 — Host integration + model/effort bounded auto | Hivemind calls the Host Advisory API; Arbiter's model × effort recommendations are applied by the host within its allowed sets. | Non-inferiority and material-benefit gates against the host's existing routing, on the host's paired corpus. |
| 8 — Verification/review auto | Enforcing gate and review floors on T2/T4 clients; stratified audits. | False-PASS = 0; severity-weighted false-complete gate. |
| 9 — Physical context experiment | T4 or `NATIVE` only; no irreversible deletion. | 0 restore/state-loss incidents; material token savings. |

## 23.2 Research track

| Stage | Behavior | Exit criterion |
| --- | --- | --- |
| R1 — Pure-read speculation | File/index/history prefetch under tight limits. | Positive wall-time delta; 0 stale/side-effect incidents. |
| R2 — Learned utility shadow | Propensity-aware/paired evaluation; no auto execution. | Stable held-out predictions; no hidden task-class regression. |
| R3 — Learned utility bounded auto | High-confidence in-distribution choices only. | Better constrained Pareto frontier than rules. |
| R4 — Selective branching | Strong sandbox, base-version CAS, tiny hard-state cohort. | Net hard-task gain after cost; 0 isolation failures. |
| R5 — Backend optimization | BF16 shared-state and optional quantized backend. | Per-module parity + end-to-end non-inferiority. |

Roll out each module behind an independent feature flag and circuit breaker so one failure does not require disabling the entire control plane.

## 23.3 v0.1 release scope

v0.1 is the first release meant for daily use. It must be useful **without a GPU**, and on Codex (desktop and CLI) and Claude Code:

- one-command install and setup, plus `doctor` and `uninstall`;
- daemon, shims, event log, and privacy scope;
- a session baseline, verification observation, and `arbiter verify`;
- test-integrity detection;
- loop and repeated-error alerts (advisory);
- agent-proposed contracts with quote provenance and coverage checks;
- the completion gate in `annotate` mode, with opt-in `block` mode on T2 clients;
- `arbiter status` and the MCP status tool.

Not in v0.1:

- SemIf;
- the gateway;
- retrieval reranking;
- any automatic effort changes;
- context control;
- the research track;
- client profiles beyond Codex, Claude Code, and generic MCP.

# 24. Observability and Manual Controls

The UI should expose both optimization state and whether the underlying integration is healthy.

```text
Daemon: healthy  pid 12044  ipc: named-pipe  db: WAL ok
Clients: codex-desktop T1 T3 | codex-cli T1 T2 T3 | claude-code T1 T2 T3 | cursor T1
Session: codex-desktop / 7f3a…  repo: arbiter  tiers: T1 T3  native: no
Codex: 0.15x.x  model: astra  goal_epoch: 4 (candidate: none)  state_version: 91
Gate: annotate  last claim: unverified (2 contracts UNKNOWN)  baseline: captured
Compatibility: effort=PASS  custom_history=UNAVAILABLE  dynamic_tools=GATEWAY
Controller: AUTO  circuit_breakers: none
Effort: HIGH  lease: 2  update: confirmed
Context: external HOT/WARM state  | native compaction: disabled
Tools: gateway 9 discovered / 63 catalog
Retrieval: 11 / 74 candidates selected | index v442
Diff: 2 high-risk | 5 medium | 11 low
Contracts: 4 PASS | 2 UNKNOWN | test_integrity=OK
SemIf: M->H score=.82  disagreement=.06  backend=BF16-fresh
Queues: semif 0/32 | retrieval 2/64 | speculation 1/3
Cache: 91% reused on last measured turn
Budget/quota: 73% remaining
```

Controls:

- AUTO / manual effort lock within allowed model/policy capabilities;
- completion gate mode: `annotate` / `block`, per session, repo, or globally;
- project scope: `arbiter exclude` / `arbiter include`;
- disable individual modules;
- pause native compaction/context rewriting;
- pin evidence HOT/COLD retrieval;
- bypass retrieval narrowing for next turn;
- expose normal tool surface / bypass gateway for next turn;
- force full review / configured broad verification;
- cancel speculation/branches;
- clear/reset a circuit breaker after inspection;
- disable controller and return to baseline Codex behavior.

The UI never presents a SemIf score as a calibrated probability unless the active calibrator is valid for that exact decision family/model/backend.

The status and controls are available through every surface:

- `arbiter status` and `arbiter doctor` on the command line;
- the MCP status tool, inside any T1 client;
- hook-injected summaries, where the client supports context injection.

Controls apply per session or globally.

# 25. Repository / Module Layout

Distribution `arbiter-agent`, import package `arbiter_agent`, command `arbiter` (§4.5.1).

```text
arbiter_agent/
  cli.py                      # arbiter setup | doctor | uninstall | status | mcp | hook
                              #         | verify | contracts | exclude | include | logs | debug
                              #         | gateway | semif

  daemon/
    server.py
    ipc.py                    # named pipe (Windows) / unix socket; loopback TCP fallback
    auth.py                   # endpoint ACLs, per-install token (§18.8)
    protocol.py               # versioned handshake (§16.5)
    single_instance.py
    lifecycle.py              # lazy autostart, drain/restart, optional login autostart
    launcher_windows.py       # WMI Win32_Process.Create launch outside client job objects (decision 0018)
    http_hooks.py             # loopback POST /hook/<event> for http hook handlers (decision 0021)
    diagnostics.py            # structured logs, debug mode, doctor bundle (§16.6)

  shims/
    mcp_server.py             # stateless stdio MCP -> daemon
    hook_cli.py               # fallback command-hook transport: stdlib-only, deadline-bounded -> daemon
    hook_tool.py              # `arbiter_hook` MCP tool used by Codex mcp_tool hooks (decision 0021)

  clients/
    profile_schema.py
    registry.py
    detect.py
    config_merge.py           # toml/json parse-merge-validate, atomic write
    event_normalizer.py
    event_dedupe.py
    normalizers/
      codex.py
      claude_code.py
      generic.py
    watchers/
      transcript_tail.py
      parsers/
    driver/
      codex_app_server.py
      claude_agent_sdk.py
    codex/
      client.py
      capability_cache.py
      settings_update.py
    profiles/
      codex.yaml
      claude_code.yaml
      generic_mcp.yaml
      # further profiles: cursor, windsurf, vscode_copilot, gemini_cli,
      # cline, zed, opencode, goose, claude_desktop, ...
    fixtures/                 # recorded hook/transcript payloads per client version
    fake_host/                # scriptable fake client for tests: emits hooks/transcripts, calls MCP

  hosts/                      # orchestrator hosts (§4.6)
    api.py                    # Host Advisory API v1: recommend_call, score_context, rerank, signals, outcomes
    allowed_set.py            # validate responses never widen the host's allowed set
    partitions.py             # per-project data confinement for host-originated data
    conformance.py            # host-side contract tests (shipped for hosts to run)
    hivemind.py               # Hivemind host profile (the adapter itself lives in the Hivemind repo)

  setup/
    setup.py
    doctor.py
    uninstall.py
    manifest.py
    backup.py
    packaging/                # e.g. Claude Code plugin bundle

  integration/
    tiers.py                  # verified tier sets, NATIVE/EXPERIMENTAL flags
    capability_probe.py
    conformance.py
    codex_version.py

  audit/
    event_log.py
    checkpoints.py
    replay.py
    cleanup_journal.py

  telemetry/
    collector.py
    errors.py
    tests.py
    runner_parsers/           # pytest, jest/vitest, go, cargo, junit/trx xml, tsc, eslint, ... (§12.8)
    baseline.py               # session baseline capture (§12.4)
    test_integrity.py
    diff_stats.py
    cache_metrics.py
    progress.py

  state/
    schema.py
    store.py
    builder.py
    intent.py
    contract_compiler.py      # proposal intake, quote provenance, recipe typing (§6.8.1)
    contract_coverage.py      # uncovered-intent detection, weak-contract flags
    contracts.py
    facts.py                  # includes evidence origin (§6.4.1)
    repo_identity.py          # repository identity + path normalization (§11.9)
    hypotheses.py
    evidence.py
    reconcile.py
    dependency_graph.py
    goal_epochs.py
    migrations/

  semif/
    service.py
    backends/
      null.py                 # always abstains (§7.8)
      cuda_bf16_fresh.py
    prompts.py
    request_budget.py
    validation.py
    mirroring.py
    batching.py
    calibration.py
    health.py

  reasoning/
    scheduler.py
    capability_lattice.py
    update_confirmation.py
    loop_detector.py
    leases.py
    hysteresis.py
    risk_floors.py

  context/
    scheduler.py
    projection.py
    retention.py
    archive.py
    rehydrate.py
    epochs.py
    history_cas.py
    memory_admission.py

  gateway/
    catalog.py
    search.py
    describe.py
    call.py
    schema_validation.py
    authorization_bridge.py
    approval_mirror.py        # per-tool approval preservation, elicitation (§10.7)
    adopt.py                  # explicit adopt/release of existing MCP servers
    miss_recovery.py

  retrieval/
    lexical.py
    symbols.py
    ast_graph.py
    dependencies.py
    embeddings.py
    candidates.py
    reranker.py
    miss_detector.py
    overlays.py
    access_policy.py

  review/
    diff_risk.py
    blast_radius.py
    review_policy.py
    test_mapper.py
    test_scheduler.py

  completion/
    requirements.py
    evidence_ledger.py
    evidence_grades.py
    verification_policy.py
    claim_detection.py        # completion-claim recognition (§12.7)
    verify_runner.py          # arbiter verify (§12.8)
    gate.py

  policy/
    authority.py
    constraints.py
    budgets.py
    conflict_resolution.py
    utility_model.py
    conservative_policy.py
    ood.py
    cascade.py
    circuit_breaker.py
    fail_modes.py

  speculation/
    predictor.py
    pure_ops.py
    executor.py
    cache.py
    fingerprints.py
    invalidation.py
    budgets.py

  branching/
    trigger.py
    sandbox.py
    workspace.py
    runner.py
    evaluator.py
    base_version.py
    early_stop.py
    cleanup.py

  privacy/
    sensitivity.py
    secrets.py
    redaction.py
    encryption.py
    retention.py
    scope.py                  # project scope, .arbiterignore, exclude/include (§16.3.1)

  concurrency/
    queues.py
    deadlines.py
    cancellation.py
    priorities.py

  eval/
    trace_replay.py
    corpus/                   # benchmark task set incl. adversarial gate/test-weakening cases
    gates.py                  # §20.17 numeric gates, preregistration records
    checkpoint_fork.py
    paired_trials.py
    propensity.py
    off_policy.py
    metrics.py
    statistics.py
    calibration_fit.py
    policy_eval.py
    speculation_eval.py
    branching_eval.py
    contract_eval.py
    reports.py

  ui/
    status.py
    overrides.py

  config/
    defaults.yaml

user data (outside the package, e.g. ~/.arbiter/ or the platform data dir):
  controller.sqlite
  audit/
  archive/
  indexes/
  sandboxes/
  backups/                    # pre-setup config backups
  profiles/                   # user-supplied client profiles
  models/                     # SemIf weights, only after `arbiter semif enable`
```

# 26. Initial Configuration

Bootstrap defaults are deliberately conservative.

```yaml
daemon:
  single_instance: true
  ipc: auto                     # named pipe on Windows, unix socket elsewhere
  tcp_fallback_requires_token: true
  lazy_autostart: true
  login_autostart: false
  idle_shutdown_minutes: 0      # 0 = stay up while any client session is active

clients:
  enabled: detected_and_accepted
  builtin_profiles: true
  user_profile_dir: ~/.arbiter/profiles
  degrade_on_drift: true
  transcript_watchers: true

hooks:
  transport_preference: [in_process, command]   # mcp_tool / http first; command CLI fallback (decision 0021)
  http_port: fixed_at_setup     # loopback only; stable so client config and trust don't churn
  telemetry_async: true
  telemetry_deadline_ms: 150
  telemetry_p95_budget_ms: 50
  gating_deadline_ms: 1500
  gating_p95_budget_ms: 300
  await_semif: false            # gating hooks use only precomputed scores
  fail_open: true
  max_injected_tokens: 300
  inject_only_on_change: true
  suggest_arbiter_retrieval_on_broad_search: false   # opt-in pre-tool nudge

setup:
  scope: user
  backup_before_write: true
  show_diff: true
  atomic_write: true
  abort_on_parse_failure: true
  modify_repo_instruction_files: false
  # Hard invariants (enforced in code, deliberately NOT configurable):
  #   - never modify client permission/approval/sandbox/model settings
  #   - never move/remove/wrap existing MCP servers outside `arbiter gateway adopt`

ipc:
  require_token: true
  tcp_fallback_bind: loopback
  rotate_token_on_restart: true

integration:
  native_enabled: false         # NATIVE-flagged features require an explicitly pinned patched host
  require_capability_probe: true
  pin_codex_version_for_native_mode: true
  custom_history_rewrite: disabled
  dynamic_schema_mutation: disabled

semif:
  enabled: false                # set by `arbiter semif enable` after GPU/VRAM/weights checks
  backend_when_disabled: null_abstain
  timeout_ms: 1200
  queue_capacity: 32
  max_concurrent_batches: 2
  reference_backend: cuda_bf16_fresh
  backend: auto                 # auto | llama_cpp | exl3 | semif_bf16 | null (decision 0026); inert until M7
  encoder:                      # tier 0: short typed decisions on CPU or GPU, no GPU required (decision 0026)
    enabled: false
    model: fastino/GLiNER2.5-Decide
    device: auto                # auto | cpu | cuda
    families: [completion_claim, scope_change, requirement_detection, contract_coverage]
  model_tiers:                  # decoder tiers; `arbiter semif enable` proposes the largest that fits the GPU
    - {min_vram_gb: 6, model: JevK5, base: Qwen3.5-4B, quant: Q4_K_M}
    - {min_vram_gb: 12, model: K2-Horizon-7B, quant: Q4_K_M}
    - {min_vram_gb: 16, model: K2-Horizon-7B, quant: Q6_K}
  adapters: {}                  # decision family -> fine-tuned adapter or encoder checkpoint (research track R6)
  llama_cpp_url: http://127.0.0.1:8088   # loopback llama-server for decoder tiers (M7.2)
  model_revision: null          # pin: results from any other revision are rejected (§7.3)
  shadow: true                  # log sensor judgments next to rule decisions (never applied)
  route_families: []            # families the cascade may let the sensor recommend on (benchmark-backed only)
  shared_state_enabled: false
  nvidia_q8_enabled: false
  mirror_binary_questions: true
  paraphrase_high_impact: true
  malformed_result_action: abstain

hosts:
  enabled: true                 # accept Host Advisory API connections from authenticated local hosts
  api_major_version: 1
  default_deadline_ms: 150      # hosts may pass their own; SemIf never awaited on the hot path
  hivemind:
    project_confined: true      # Hivemind memory is project-local; no cross-project data use
    context_scoring_enabled: false   # until §9.11 regression gate passes on Hivemind's CR7 probes

reasoning:
  hosted_client_mode: advisory  # automatic only via orchestrator hosts, T4 sessions, or verified effort APIs
  choose_model_tier: true       # model × effort within allowed set (§8.8)
  objective: cost_per_successful_task
  fail_open_to_codex_default: true
  derive_efforts_from_model_metadata: true
  high_lease_calls: 2
  expensive_tier_lease_calls: 1
  deescalate_progress_checkpoints: 2
  confirm_setting_update: true
  disable_dynamic_effort_on_cache_regression: true

context:
  active_task_irreversible_delete: false
  archive_before_evict: true
  exact_tokenizer_budgeting: true
  compaction_trigger_fraction: 0.72
  emergency_trigger_fraction: 0.88
  require_history_version_cas: true
  require_two_phase_checkpoint: true
  native_compaction_enabled: false

gateway:
  enabled: false                # enabled per client only after benchmark vs native deferral
  enabled_min_catalog_size: 20
  stable_schema_surface: true
  normal_surface_miss_recovery: true
  authorization_bridge_required: true
  adopt_existing_servers: explicit_only
  proxy_policy: read_only_unless_approval_mirrored
  approval_inheritance: false

retrieval:
  semantic_rerank: true
  min_pinned_deterministic_candidates: 3
  adaptive_top_k: true
  broaden_on_no_progress: true
  branch_overlay_indexes: true
  secret_indexing: deny_by_default
  refresh_budget_s: 3.0         # per-query incremental refresh budget; unindexed changes are left out, never stale
  include_generated: false      # vendored/generated trees, lockfiles, minified bundles
  embeddings: "off"             # optional vector channel (M6.5); only "off" ships

review:
  security_floor: high
  database_migration_floor: high
  destructive_change_floor: high
  public_api_floor: high
  test_harness_floor: high
  final_broad_verification_by_risk: true
  stratified_low_risk_audit: true

completion:
  gate_mode: annotate           # annotate | block (opt-in per user/repo/session)
  gate_only_on_completion_claims: true
  max_stop_blocks_per_epoch: 3  # block mode only
  contracts_proposed_by: host_agent
  require_verbatim_quote_provenance: true
  agent_asserted_evidence_sufficient: false
  flag_uncovered_intent: true
  require_session_baseline_for_integrity_ok: true
  verify_config_file: .arbiter/verify.yaml   # opt-in; user config fallback
  semif_cannot_certify_alone: true
  require_contract_evidence: true
  unknown_never_counts_as_pass: true
  require_test_integrity_check: true
  allow_user_finish_with_unknown: true
  mark_unverified_when_unknown: true
  record_non_claims: false      # also write finish-ledger rows for stops that aren't completion claims

state:
  immutable_intent_log: true
  goal_epochs: true
  epoch_confirmation: explicit_or_rule   # candidate epoch per prompt; confirm on scope change (§6.3.1)
  new_task_phrases: []          # extra regexes that confirm a new goal epoch (user phrase rules)
  path_case_folding: per_volume_detect
  reconcile_on_resume: true
  reconcile_on_user_interrupt: true
  reconcile_on_branch_apply: true

ui:
  inject_status: false          # hook additionalContext status summaries (on change, within max_injected_tokens)

learning:
  auto_enabled: false
  shadow: true
  constrained_noninferiority: true
  ood_abstain: true
  log_propensity_when_randomized: true
  require_repo_time_model_holdouts: true

speculation:
  enabled: false
  max_concurrent_jobs: 3
  ttl_seconds: 30
  max_io_share: 0.15
  allowlist: [file_read, file_stat, lexical_search, symbol_lookup, ast_query, local_git_object_read]
  arbitrary_shell: false
  tests: false
  network: false
  credentials: false

branching:
  enabled: false
  max_branches: 2
  max_substantive_calls_per_branch: 3
  max_budget_fraction: 0.15
  require_sandbox: true
  worktree_alone_is_sandbox: false
  network_default: deny
  credentials_default: none
  require_main_reverify: true
  early_stop: true

privacy:
  local_only: true
  redact_at_ingest: true
  redact_secrets_in_logs: true
  raw_blob_encryption: optional
  training_excludes_secret_records: true
  project_scope: all_except_excluded     # or allow_list
  project_exclude: []
  respect_arbiterignore: true
  raw_payload_retention_days: 30
  ledger_and_metrics_retention_days: 180

storage:
  sqlite_wal: true
  sqlite_foreign_keys: true
  single_writer: true
  append_only_audit_log: true
  storage_cap_gb: 2
  max_payload_kb: 512           # larger payloads stored truncated + full-content hash
  backup_before_migration: true

diagnostics:
  log_rotation_mb: 20
  debug_mode_expiry_hours: 24

policy:
  repository_text_can_change_limits: false
  upstream_safety_priority: absolute
  controller_user_override_priority: below_upstream_safety
  optimization_fail_open: true
  integrity_fail_conservative: true
  circuit_breakers: true
  breakers: {}                  # per-kind overrides, e.g. {client: {threshold: 10}}; kinds in policy/circuit_breaker.py
  budgets:                      # hard per-session, per-turn controller budgets (§15.5 level 7)
    sensor_calls_per_turn: 4
    inject_tokens_per_turn: 600
```

# 27. Recommended Build Order

The order front-loads the design's load-bearing assumptions, then installability, auditability, and safety, then semantic optimization. The core-track steps (0–33) line up with the §23.1 stages. Research steps (R1–R8) line up with §23.2 and start only after the core track is stable. Full circuit breakers (step 23) precede every automatic stage (steps 27 onward), per principle 28.

**Stage 0 — assumption spikes.** Each spike is time-boxed and throwaway, and each result is recorded in `docs/decisions/`.

0. Run the assumption spikes:
   - a. Codex hooks: which events exist and fire, in both CLI and desktop, and with what payloads.
   - b. Codex desktop: does it honor MCP servers in `~/.codex/config.toml`?
   - c. Claude Code stop-hook semantics: what distinguishes a completion from a question or pause, and what a block response does.
   - d. Windows: can a daemon lazily spawned from a hook survive the client's job object or process tree? Otherwise, login autostart.
   - e. Hook start-up latency on Windows for a stdlib-only Python entry point, installed via `uv tool` (the §20.17 budget).

   **Done 2026-09-24.** Evidence is in `docs/spikes/m0/`; decisions 0015–0021 record the results, and every spike-forced design change has been applied to this spec.
   - f. Transcript formats: locations and stable record IDs for Codex and Claude Code sessions.

**Stage 1 — foundations + clients.**

1. Scaffold the project: `arbiter-agent` package, CLI, config loader, feature flags, CI (lint, type check, tests on Windows/macOS/Linux), structured logging, and debug mode.
2. Build a scriptable fake host client that emits hooks and transcripts and calls MCP, for testing without real clients.
3. Build the daemon skeleton: local IPC with endpoint ACLs and token auth, versioned handshake, single-instance lock, detached lazy autostart (or the fallback from spike 0d), and a health endpoint.
4. Build the MCP shim and hook CLI: stateless, stdlib-only hook path, strict deadlines, fail-open forwarding.
5. Configure SQLite WAL, single writer, migrations with pre-migration backup, and crash-recovery tests.
6. Build the append-only normalized event log: per-client normalizers, dedupe, sequence numbers, pending-call reconciliation, replay, redaction at ingest, project scope, retention, and storage caps.
7. Build the client profile format, registry, and first profiles: Codex desktop, Codex CLI, Claude Code, generic MCP.
8. Build `arbiter setup` / `doctor` / `uninstall`: detection, diff, backup, parse-merge-validate, atomic write, install manifest, privacy-scope prompt.
9. Build the per-client capability probe and startup conformance suite, including exact Codex version/model/effort discovery.

   **Steps 1–9 done 2026-09-24** (M1, M2). Evidence is in `docs/spikes/m1/` and `docs/spikes/m2/`.

**Stage 2 — task state + rules-only gate → v0.1.**

10. Implement immutable intent and candidate/confirmed goal epochs (§6.3.1), with interruption cancellation.
11. Implement the typed FACT/INFERENCE/CONTRACT IR, with provenance, evidence origin, repository identity and path normalization, and reconciliation.
12. Implement contract intake: the `arbiter_contract_propose` tool, quote provenance, typed recipes, coverage check, deterministic extraction, and the evidence ledger. Statuses stay UNKNOWN until evidence proves them.
13. Implement verification observation: runner parsers, report files, `arbiter verify`, and the session baseline.
14. Implement test-integrity and harness-change detection against the baseline.
15. Build deterministic progress, error-fingerprint, diff, cache, and loop telemetry.
16. Build eval harness v0: trace replay, a small benchmark corpus including adversarial gate and test-weakening cases, and the §20.17 gate checks.
17. Implement the rules-only Completion Gate: completion-claim detection, `annotate` mode, opt-in bounded `block` mode, the finish ledger, `arbiter_finish_check`, and minimal circuit breakers (hook latency, gate errors, parser failures).
18. Release **v0.1** (§23.3): publish `arbiter-agent`, and ship the Claude Code plugin package.

   **Steps 10–18 built 2026-09-24** (M3, M4; decisions 0023–0025). Evidence is in `docs/spikes/m3/` and `docs/spikes/m4/`. Publishing to PyPI is pending the owner's account.

**After v0.1.**

19. Add recorded-fixture contract tests for every profile, then further profiles (Cursor, Windsurf, VS Code/Copilot, Gemini CLI, Cline, Zed, OpenCode, Goose, Claude Desktop, ...). Each runs with the tier set it verifiably supports.
20. Build repository indexes with content-hash invalidation, branch overlays, and access policy.

**Stage 3 — shadow SemIf.**

21. Integrate SemIf behind a backend interface. The null backend first. Then, once the GPU is available, BF16 fresh scoring with exact request budgeting, validation, deadlines, and bounded queues, plus `arbiter semif enable`.
22. Run SemIf in shadow mode and collect calibration/instability data.

**Stage 4 — decision cascade + full circuit breakers.**

23. Implement the policy core, the decision cascade, full module- and client-specific circuit breakers, the complete fail-mode matrix, and overload shedding.

**Stage 5 — advisory.**

24. Implement the capability-derived Reasoning Scheduler (model × effort, §8.8) in advisory mode. Surface it through MCP status and hooks within the injection budget, and through Host Advisory API v0 (`recommend_call`, `session_signals`, `report_outcome`) in shadow mode for Hivemind.
25. Implement retrieval reranking in shadow/advisory mode, with deterministic pins and adaptive breadth.
26. Implement Diff Risk, transitive blast radius, and the targeted test planner in shadow mode.

**Stage 6 — gateway/retrieval bounded auto.**

27. Implement the stable Tool Gateway, authorization bridge, approval mirroring, and explicit adopt/release. Benchmark per client against normal schemas **and** native deferral.
28. Enable bounded retrieval/gateway automation where evaluation supports it.

**Stage 7 — host integration + model/effort bounded auto.**

29. Harden the Host Advisory API to v1 (§4.6.2): per-project partitions, outcome reporting, and a host-side conformance suite. Add Hivemind as the first host. The Hivemind-side adapter lives in the Hivemind repository.
30. Enable host-applied model × effort recommendations (§8.8) where paired evaluation on the host's corpus meets the §20.17 gates. Dynamic effort in Arbiter-hosted sessions is enabled only on builds that pass update/cache conformance.

**Stage 8 — verification/review auto.**

31. Make the contract gate, test integrity, and review floors enforcing on T2/T4 clients where evaluation supports it. Add stratified low-risk audits.

**Stage 9 — context.**

32. Build external Context Scheduler metadata/archive/rehydration. Expose `score_context` to hosts under the conservative rules of §9.11, gated on the host's conversation-integrity probes. Beyond that, physical context control via Arbiter-hosted sessions or a pinned `NATIVE` integration stays experimental, with history-version CAS and two-phase restore.

**Later — embedded host component.**

33. Package Arbiter as an optional, user-enabled component of the Hivemind app (§4.6.6). The host manages installation and lifecycle, connects to an existing standalone daemon if one is present, and disabling it leaves no trace.

**Research track** (§23.2):

- R1.1. Implement pure-read speculation, disabled by default. Validate each operation class and dependency fingerprint. Enable it at low volume and measure the wall-time delta and foreground contention.
- R2.1. Build paired/randomized experiment infrastructure and propensity logging.
- R2.2. Train simple constrained outcome predictors; shadow only.
- R3.1. Enable conservative learned choices on high-confidence, in-distribution, low-consequence decisions.
- R4.1. Build the hardened branch sandbox, base-version checks, and main re-verification, without model automation.
- R4.2. Run a tiny two-branch experimental cohort against a single strong-effort baseline.
- R5.1. Validate BF16 shared-state SemIf against BF16 fresh per decision family. Only if a suitable NVIDIA quantized backend exists, implement and benchmark it; otherwise retain BF16.

Afterwards, expand XHIGH/expensive-tier automation, native context control, learning, or branching only after held-out results and circuit-breaker data support each one separately.

# 28. Acceptance Criteria

Wherever a criterion below says "preregistered," "high," "low," or "measurable," the governing number is in §20.17.

## Setup and client coverage

- A single install command plus `arbiter setup` configures every detected, profiled client without manual file edits.
- Setup is idempotent. Uninstall removes exactly the manifest-recorded entries and leaves later user edits intact.
- Setup never modifies client permission, approval, sandbox, or model settings, and never edits repository files without opt-in.
- Any config file that cannot be parsed or validated is left untouched, and setup reports it.
- Every MCP-capable client with a profile reaches at least T1. `arbiter doctor` reports each client's actual verified tiers.
- Hook p95 overhead stays within the configured budget, and a daemon crash or timeout never blocks the host agent.
- Concurrent sessions from multiple clients share one daemon with no database write contention.
- Client profile drift is detected and drops only the failing tiers rather than breaking the client.
- The base install requires no GPU or model weights. Rules-only operation is fully functional.
- Setup never adopts existing MCP servers into the gateway without `arbiter gateway adopt`.
- Excluded projects are never parsed; secrets are redacted before first write; retention and storage caps behave as configured.
- IPC rejects unauthenticated and cross-user connections; forged host reports never count as `arbiter_observed`.
- Shim/daemon version skew and failed migrations degrade to pass-through, never to a blocked host or corrupted database.

## Compatibility and reliability

- Exact Codex/model capabilities are discovered at startup; unsupported features are disabled rather than guessed.
- SemIf failure, timeout, queue saturation, or OOM cannot stall the Codex session indefinitely.
- Optimization failures return to baseline behavior; integrity/verification failures do not silently skip checks.
- Every controller decision can be audited from persisted inputs, versions, scores, and outcome even if model re-execution is nondeterministic.
- Goal-epoch changes cancel stale controller work.
- Module circuit breakers automatically disable unhealthy automation.

## State/contracts

- Raw user intent remains recoverable and immutable.
- Explicit task requirements achieve a preregistered high-recall target on held-out tasks.
- Every contract quotes verbatim user text that exists in the intent log; the agent can never set PASS; uncovered intent is surfaced.
- Ordinary follow-up replies ("yes, continue") never supersede active contracts in the benchmark.
- UNKNOWN never silently becomes PASS.
- Resume/compaction/branch application restores a state consistent with latest user intent and observable repository facts.
- Stale goal state cannot be resurrected after rollover/resume in the test suite.

## Reasoning

- Scheduler uses only supported effort tiers and confirms updates.
- No sustained effort oscillation.
- Cache-destructive dynamic effort auto-disables or proves net benefit.
- Quality remains inside non-inferiority bounds while remote usage/time improves.

## Context

- No active-task evidence is irreversibly deleted by semantic judgment.
- Context rewrite automation is enabled only in a deployment mode that truly supports it.
- Epoch install is compare-and-swap/version checked and two-phase recoverable.
- Audit transcript remains intact even if upstream Codex compaction rewrites its own rollout.
- Rehydration retrieves the correct repo/branch/evidence version.

## Tool gateway

- Gateway calls preserve upstream permissions and validate canonical schemas.
- Proxying never collapses per-tool approval: non-read-only tools stay direct or get mirrored approval; approvals are never inherited across tools.
- The gateway is enabled for a client only where it beats that client's native tool deferral.
- Schema/tool misses recover without session failure.
- Gateway mode produces measurable prompt/choice benefit where enabled.
- Gateway cannot be used to expand capability scope.

## Retrieval

- Held-out recall meets the preregistered floor, with dangerous misses tracked separately.
- Index invalidation/branch overlays prevent stale results after edits/branches.
- Secret/access policy prevents unauthorized indexing or injection.

## Completion / review

- SemIf cannot certify completion or contract PASS alone.
- The gate acts only on recognized completion claims; it never gates a turn that ends with a question to the user; `annotate` is the default.
- Runner parsers never read FAIL/UNKNOWN output as PASS; test integrity is UNKNOWN without a session baseline.
- False-complete severity remains below the chosen production ceiling.
- Test weakening/harness manipulation is detected in benchmark cases.
- High-risk classes always receive deterministic minimum review.
- Targeted tests never replace required final verification.

## Learned policy

- Automatic policy is evaluated with repository/time/model holdouts and appropriate paired/propensity-aware methods.
- It improves or preserves the constrained Pareto frontier versus rules.
- OOD/high-uncertainty states abstain.
- No learned decision can relax permissions, contracts, isolation, or hard budgets.

## Speculation

- Default speculation executes only operation classes proven side-effect-free under the controller implementation.
- Zero known external mutation incidents.
- No stale speculative result is silently consumed.
- Positive net wall-time benefit after wasted work and contention.

## Branching

- Worktree-only isolation is rejected.
- Sandbox/credential/network separation passes adversarial tests.
- Base-version mismatch blocks direct application.
- Adopted branch patches are reverified on main.
- Hard-state gains justify added cost on held-out trials.

## Privacy

- Local-only is the default.
- Secret-bearing data is excluded from ordinary logs/indexes/training unless explicitly permitted.
- Retention and encryption settings behave as configured.

## Backend optimization

- BF16 fresh remains the semantic reference.
- Shared-state or quantized backends pass per-module and end-to-end gates before production use.
- No systematic expensive-routing, aggressive-eviction, narrow-retrieval, or weak-review bias appears after optimization.

# 29. What Success Looks Like

The desired system is not "a 4B model controlling a larger model." It is a disciplined local scheduler surrounding an expensive agent, with an operational state representation backed by immutable intent/evidence, contract-backed verification, safe pure-read speculation, and selective parallel reasoning only when economically justified.

A successful session should look like this:

```text
1. Startup conformance identifies which optimizations are actually supported on this Codex/model build.
2. Tool-heavy deployments use a stable capability gateway or another measured schema strategy instead of frequent prompt-schema churn.
3. Retrieval narrows repository evidence without hiding deterministic anchors; native context rewriting is used only in a mode that truly supports it.
4. Routine implementation uses the cheapest supported reasoning tier that preserves expected quality.
5. Repeated failures trigger retrieval broadening, replanning, or temporary effort escalation instead of blind retries.
6. High-risk diffs trigger stronger review and risk-appropriate tests.
7. Completion requires contract evidence and test-integrity checks; UNKNOWN remains visible.
8. Once required work is verified, optional expensive wandering is suppressed.
9. Evicted evidence remains recoverable from the independent audit/archive path.
10. Pure local reads may be prefetched when expected latency benefit is positive and dependency validity is clear.
11. Rare ambiguous hard states may branch inside strong isolation and reverify any winning patch on main.
12. New user instructions immediately invalidate stale controller work through goal epochs.
13. Learned scheduling is judged by downstream paired outcomes, not by classifier confidence alone.
14. Any unhealthy module trips a circuit breaker and the session degrades to a safer baseline rather than failing globally.
```

The long-term target must be stated as a preregistered non-inferiority result, for example:

> Keep correct-completion quality within the chosen non-inferiority margin and severe-failure ceiling of a strong baseline while materially reducing remote usage and/or wall-clock time.

The margin is selected before evaluating the final benchmark and reported with confidence intervals. Production status is earned only by held-out data on the exact integration mode and model/version in use.

# 30. Performance Hypotheses and Stretch Targets

These are planning ranges, not claims. The red-team review lowers confidence in blanket 2–3x claims because the full savings depend on integration mode, cache behavior, task length, tool-catalog size, and whether native context control is available.

## 30.0 Hosted-client target (T1–T3, including Codex desktop)

This is the expected case for sessions started inside a client app, which includes the primary target, Codex desktop. Arbiter can't intercept the host's built-in tools or change effort there (§4.4.3). Savings therefore come mainly from preventing wasted work and false completion, not from shrinking prompts.

| Category | Planning range vs stock client | Main contributors |
| --- | --- | --- |
| Premature-completion failures (reported as done but not verified) | 20–50% lower, or made visible as "unverified" | contracts, completion gate, test integrity |
| Tests "passing" via weakened tests going unnoticed | most benchmark cases detected (§20.17) | test integrity + baseline |
| Wasted/redundant actions | 5–20% lower | loop and repeated-error alerts, advisory effort |
| Remote-model usage/cost proxy | 0–10% lower | fewer rework cycles; advisory effort only |
| Frontier input tokens | roughly neutral (±5%) | injection budget keeps controller overhead small; no prompt control |
| Wall-clock task time | 0–10% lower | fewer dead-end loops |

The larger ranges in §30.1–30.2 apply only to T4 (orchestrator hosts or driver sessions; see §30.4), or to clients whose own tools route through Arbiter. They should not be quoted for hosted Codex desktop use.

## 30.1 Sidecar + stable gateway mature target

These ranges assume Arbiter's retrieval and gateway sit on the agent's actual tool path: T4 (an orchestrator host or driver session), or a client whose tool use routes through Arbiter. They count input tokens, not billed cost; see §17.5 and §30.4.

| Category | Planning range vs current/stock baseline | Main contributors |
| --- | --- | --- |
| Remote-model usage/cost proxy | 15–35% lower | reasoning, retrieval, tool gateway, completion |
| Frontier input tokens | 20–45% lower | retrieval + gateway; no assumed native history rewrite |
| Reasoning/output usage | 20–40% lower | effort scheduler |
| Wall-clock task time | 10–30% lower | less search/rework + pure-read speculation |
| Wasted/redundant actions | 20–45% lower | loop detection, retrieval, completion |
| Premature-completion failures | 30–60% lower | contracts + evidence gate |
| End-to-end task success | approximately +2 to +10 percentage points on difficult mixed tasks | retrieval, review, contracts |
| Useful work per remote-usage unit | roughly 1.3–1.8x stretch | combined system |

## 30.2 Full native/patched mature target

If replacement-history context control and cache-preserving effort changes are proven on a pinned build:

| Category | Planning range | Main contributors |
| --- | --- | --- |
| Remote-model usage/cost proxy | 30–55% lower | context + reasoning + retrieval + gateway + learning |
| Frontier input tokens | 40–65% lower | context visibility hierarchy + retrieval + gateway |
| Wall-clock task time | 20–40% lower | less rework + speculation + selective branching on hard states |
| Useful work per remote-usage unit | roughly 1.5–2.2x plausible mature target | combined system |

A **2–3x** gain remains an extreme stretch case for long, context-heavy, tool-heavy workflows where the baseline wastes substantial repeated input and the full native architecture works as intended. It should not be used as the default project forecast.

## 30.4 Orchestrator-host (Hivemind) interpretation

The T4 ranges in §30.1–30.2 are reachable in practice through an orchestrator host (§4.6), not through Codex desktop. Two corrections apply:

- **Input-token savings ≠ cost savings.** With cache-read shares like the 87.6% measured in §17.5, a 40% input-token reduction may cut billed input cost by much less. It can even raise cost if it breaks warm prefixes. Report input-token and billed-cost effects separately.
- **The main value is in model × effort choice** (§8.8), plus loop and rework avoidance. The host already enforces tier floors and routes by tier, so Arbiter's contribution is the *within-tier* and *per-call* choice on top. It is measured against the host's existing routing as the baseline, using the host's own paired corpus (for Hivemind, the M10.7 capability corpus).

No numeric range is claimed for host integration until that paired measurement exists.

## 30.3 Release interpretation

Large efficiency gains with material quality/tail-risk regression are failures. All headline results must include:

- integration mode;
- exact Codex/model/backend versions;
- task mix;
- baseline definition;
- confidence intervals;
- severe-failure count;
- local controller overhead.

# 31. Later Extensions

Only after the v3.1 control plane is stable:

- ~~Dynamic model routing~~ — moved into the core Reasoning Scheduler (§8.8), within host- or policy-allowed sets.
- **Arbiter-hosted driver sessions** (Codex app-server client, Claude Agent SDK) — lower priority now that orchestrator hosts deliver T4 (§4.6). Build only if there's demand for T4 outside a host.
- **Additional orchestrator hosts** beyond Hivemind, through the same Host Advisory API.
- **Per-repository priors:** lightweight priors for test cost, architecture, and common failure modes without overfitting.
- **Multi-agent coordination:** extend selective branching into durable specialized coding/review agents only after two-branch isolation and utility accounting are proven.
- **Remote/shared controller service:** support several developer machines once local behavior is proven.

The clarification/ask-user gate remains intentionally outside this master unless explicitly reconsidered later.

# 32. Sources and Design References

[S1] TheoLeeCJ/SemIf — direct-logit decision model, Qwen3.5-4B quality/speed benchmarks, shared-state drift, and CUDA BF16 quick-start.

https://github.com/theoleecj/semif

[S2] OpenAI Codex `openai_models.rs` — supported reasoning efforts and `supports_reasoning_effort_updates` metadata.

https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs

[S3] OpenAI Codex app-server thread protocol — thread settings/dynamic-tool-related protocol surfaces.

https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/src/protocol/v2/thread.rs

[S4] OpenAI Codex issue #42996 — Astra reasoning-effort cache-preservation concern.

https://github.com/openai/codex/issues/42996

[S5] tamaratran/fast-jev-compaction — semantic context pruning concept that motivated the Context Scheduler.

https://github.com/tamaratran/fast-jev-compaction

[S6] TypeSafeAI/typesafe-playground — experimental decision-routing/reranking/governance patterns.

https://github.com/TypeSafeAI/typesafe-playground

[S7] nassim-arifette/jevgrep — semantic code-search/reranking experiment.

https://github.com/nassim-arifette/jevgrep

[S8] TypeSafeAI/typesafe-router — probabilistic routing separated from deterministic execution/policy.

https://github.com/TypeSafeAI/typesafe-router

[S9] OpenAI Codex issue #46337 — request for PreCompact replacement-history support; documents current external-hook limitation.

https://github.com/openai/codex/issues/46337

[S10] OpenAI Codex issue #43709 — experimental context rollover can lose active task after checkpoint write.

https://github.com/openai/codex/issues/43709

[S11] OpenAI Codex issue #45464 — experimental compaction can resurrect stale task and lose current request.

https://github.com/openai/codex/issues/45464

[S12] OpenAI Codex issue #45361 — app-server per-thread config override hang report.

https://github.com/openai/codex/issues/45361

[S13] OpenAI Codex issue #32888 — stale token usage after tool output can cause context overflow.

https://github.com/openai/codex/issues/32888

[S14] OpenAI Codex `compact.rs` — internal replacement-history/compaction implementation demonstrating that deep context replacement is a core capability rather than a currently stable external sidecar API.

https://github.com/openai/codex/blob/main/codex-rs/core/src/compact.rs

[S15] fast-jev-compaction issue #38 — request budgeting must reserve criterion/question headroom, not fit state in isolation.

https://github.com/tamaratran/fast-jev-compaction/issues/38

[S16] fast-jev-compaction issue #29 — strict probability/response validation before deletion decisions.

https://github.com/tamaratran/fast-jev-compaction/issues/29

[S17] OpenAI Codex issue #13946 — historical compaction clone/replace race and retry concerns; informs versioned CAS design even where upstream implementation has since changed.

https://github.com/openai/codex/issues/13946

[S18] OpenAI Codex issue #44363 — report of destructive rollout rewrite during compaction; motivates independent append-only audit storage.

https://github.com/openai/codex/issues/44363

[S19] OpenAI Codex issue #35894 — app-server dynamic-tool request broadcast/routing bug report; reinforces conservative dynamic-tool integration.

https://github.com/openai/codex/issues/35894

[S20] OpenAI Codex `AGENTS.md` — contributor guidance on no frequent history rewrite, cache stability, and bounded context items.

https://github.com/openai/codex/blob/main/AGENTS.md

[S21] fast-jev-compaction issue #25 — non-reproducible evidence must be protected before relevance-based deletion.

https://github.com/tamaratran/fast-jev-compaction/issues/25

[S22] fast-jev-compaction issue #32 — unresolved tool calls must remain visible to the classifier state.

https://github.com/tamaratran/fast-jev-compaction/issues/32

[S23] Model Context Protocol specification — the tier-1 integration surface shared by Codex, Claude Code, and most agent clients.

https://modelcontextprotocol.io/specification

[S24] Claude Code hooks reference — prompt-submit, tool, pre-compaction, and stop hook events used by the tier-2 Claude Code profile.

https://docs.claude.com/en/docs/claude-code/hooks

[S25] OpenAI Codex configuration documentation — `config.toml` and MCP server registration shared by Codex CLI and desktop.

https://github.com/openai/codex/blob/main/docs/config.md

[S26] Hivemind AI overview and evidence (local project, `D:\Projects\Hivemind AI\`) — orchestrator-host architecture, the determinism boundary, M8 project confinement, the M10.7 cache-read and model-tier cost corpus, and the CR7 conversation-integrity repairs (`docs/CONVERSATION-REPAIR.md`).


# Arbiter — Milestones

Derived from [arbiter-spec.md](arbiter-spec.md) (v3.2, audit-revised). Each milestone maps to a rollout stage (§23), build steps (§27), and an exit gate. Numeric thresholds come from §20.17. Rationale for design choices is in [decisions/](decisions/README.md).

Conventions:
- Distribution `arbiter-agent`, import package `arbiter_agent/`, command `arbiter` (§4.5.1). Module paths follow §25.
- Every module ships behind its own feature flag and circuit breaker (§23).
- **Tier** is the tier set a submilestone needs to be useful (§4.4.3): T1 MCP, T2 hooks, T3 transcripts, T4 orchestrator host (Hivemind) or driver session.
- "Exit" means automated checks against §20.17. A milestone isn't done until they pass.
- **GPU**: only M7.2+ and research tracks R5/R6 need it.

```text
Core:  M0 spikes ✅ → M1 foundations ✅ → M2 clients+setup ✅ → M3 task state ✅ → M4 gate ✅ ══► v0.1 (not published: building the full version)
       → M5 coverage ✅ → M6 indexes ✅ → M7 SemIf (code ✅, GPU runs pending) → M8 breakers ✅ → M9 advisory ✅ (diff-risk held-out gap)
       → M10 gateway ✅ → M11 Hivemind host + model/effort → M12 verify auto → M13 context
       → M14 optional component inside the Hivemind app (later)
Research (after core is stable, never blocks it): R1 speculation · R2/R3 learned policy · R4 branching · R5 backend · R6 fine-tuned sensors
```

---

## M0 — Assumption spikes ✅ Done 2026-09-24
Stage 0 · Build step 0 · Evidence: [spikes/m0](spikes/m0/README.md) · Decisions [0015–0021](decisions/README.md)

| Spike | Result | Decision |
| --- | --- | --- |
| **M0.a Codex hooks** | **Confirmed.** Hooks work in `codex exec` and through `codex app-server` (the desktop path), and payloads match Claude Code's shape. `decision: block` on Stop continues the turn. Hooks are **silently skipped until the user trusts them** (per-hash trust). Command hooks run under Windows PowerShell 5.1. `mcp_tool` hooks work, and a missing template field fails the hook. PostToolUse has no exit code. Legacy `notify` is broken on Windows. | [0015](decisions/0015-spike-a-codex-hooks.md) |
| **M0.b Codex desktop MCP** | **Confirmed.** Desktop threads load `[mcp_servers.*]` from `~/.codex/config.toml` (eveos, bonsai-mcp and 21st in the logs). Desktop and CLI share one engine and one config. | [0016](decisions/0016-spike-b-codex-desktop-mcp.md) |
| **M0.c Claude stop semantics** | **Confirmed.** Stop fires on every turn end, including questions, and provides `last_assistant_message`. `decision: block` works, with `stop_hook_active` on the retry. **The block reason was saved into auto-memory**, which led to a wording rule. | [0017](decisions/0017-spike-c-claude-stop-semantics.md) |
| **M0.d Windows daemon survival** | **The design changed.** Desktop clients run in job objects, and a kill-on-close job kills detached children and denies breakaway. A WMI `Win32_Process.Create` launch escapes the job (parent `WmiPrvSE`, ~0.8 s). | [0018](decisions/0018-spike-d-windows-daemon-launch.md) |
| **M0.e Hook latency** | **The design changed.** Codex command hooks cost 537 ms at p50 (PowerShell wrapper), Claude exec-form 209 ms. Codex `mcp_tool` hooks take 2–4 ms and HTTP round trips ~1 ms. In-process handlers become primary; a compiled binary isn't needed. | [0019](decisions/0019-spike-e-hook-latency.md), [0021](decisions/0021-hook-transport-per-client.md) |
| **M0.f Transcripts** | **Confirmed.** Formats, paths and dedupe keys are documented: Codex `(session_id, ordinal)`, Claude `(sessionId, uuid)`. Codex rollouts have exit codes, cached-token usage, **plan usage-limit %** and effort changes. Files can exceed 1 GB, so watchers tail by offset. | [0020](decisions/0020-spike-f-transcripts.md) |

**Exit:** met. All six findings are recorded, and the spec changes they forced are merged (§4.4.1, §4.4.2, §4.5.1–4.5.2, §12.7, §12.8, §18.8, §25, §26).

**Open item carried to M2.3:** whether PreToolUse/PostToolUse fire for Codex desktop's code-mode `exec` tool calls. That needs a live desktop turn after the user trusts Arbiter's hooks.

## M1 — Foundations ✅ Done 2026-09-24
Stage 1 · Build steps 1–6 · Evidence: [spikes/m1](spikes/m1/README.md) · Decision [0022](decisions/0022-m1-implementation-choices.md)

**Result:** every exit criterion below passed. Headline numbers:
- cold start p95 1.04 s (gate 2 s);
- hook p95: MCP 1.1 ms, HTTP 25 ms (gate 300 ms);
- no secret on disk, excluded projects never parsed;
- crash, migration-rollback and replay tests green;
- Windows plus a Linux smoke test.

**Open:** the CI workflow runs once a GitHub remote exists.

- **M1.1 Scaffold + CI**: `uv` project `arbiter-agent`, `arbiter_agent/cli.py`, and a `config/defaults.yaml` (§26) loader with typed validation. Also a feature-flag registry, and CI that runs lint, type checks, and tests on Windows, macOS, and Linux.
- **M1.2 Diagnostics**: `daemon/diagnostics` provides structured redacted logs, `arbiter logs`, and `arbiter debug` with auto-expiry (§16.6).
- **M1.3 Fake host harness**: `clients/fake_host/` is a scriptable client that emits hook payloads and transcripts and calls MCP. Every later milestone tests against it.
- **M1.4 Daemon**: `daemon/server`, `ipc`, `auth`, `protocol`, `single_instance`, `lifecycle`.
  - Endpoints limited to the current user, plus a per-install token (§18.8).
  - A versioned handshake with drain-and-restart (§16.5).
  - Lazy autostart **outside the client process tree**: WMI `Win32_Process.Create` on Windows, `setsid` elsewhere (decision 0018).
  - `daemon/http_hooks`: a loopback `POST /hook/<event>` endpoint with a fixed port and a token header, for Claude Code `http` hooks (decision 0021).
- **M1.5 Shims**: `shims/mcp_server` (stateless), including the `arbiter_hook` tool that serves Codex `mcp_tool` hooks, and `shims/hook_cli`, the fallback command transport (stdlib-only, deadline-bounded, fail-open). Starts with ping, status and hook passthrough.
- **M1.6 Storage**: SQLite with WAL, foreign keys, and a single-writer queue. Migrations with pre-migration backup, the §16.2 schema, and crash-recovery tests.
- **M1.7 Event log + privacy**: `clients/event_normalizer`, `event_dedupe`, `audit/event_log`, `replay`, and `privacy/scope`, `secrets`, `redaction`, `retention`.
  - The §6.1 envelope, with surface-independent dedupe.
  - Redaction at ingest.
  - Project scope and `.arbiterignore`.
  - Retention and a storage cap (§16.3.1–16.3.3).

**Exit:**
- §20.17 gates for daemon cold start and hooks pass.
- Unauthenticated and cross-user IPC is rejected.
- Replaying the log reproduces reducer state.
- Crash-recovery and migration-rollback tests pass.
- Excluded projects are never parsed, and secrets are redacted before the first write.
- A daemon crash never blocks the fake host.

## M2 — Clients + setup ✅ Done 2026-09-24
Stage 1 · Build steps 7–9 · Tier T1–T3 · Evidence: [spikes/m2](spikes/m2/README.md)

- **M2.1 Profiles**: `clients/profile_schema`, `registry`, `detect`, `config_merge`. Profiles for `codex` (desktop + CLI), `claude_code`, and `generic_mcp` (§4.4.2), plus normalizers and transcript parsers for Codex and Claude Code, using the formats from decision 0020.
  - **Codex hooks:** `mcp_tool` handlers with per-event templates of guaranteed fields only, plus separate subagent templates. Definitions must be hash-stable.
  - **Claude Code hooks:** `http` handlers.
  - **Tool-name mapping:** Codex `Bash`, Claude `PowerShell`/`Bash`, and desktop code-mode `exec`.
- **M2.2 Setup / doctor / uninstall**: `setup/*`. Detection, a checklist, diffs, backups, parse-merge-validate, and atomic writes. Also the install manifest, the privacy-scope prompt, and `--yes/--clients/--print`. Setup never touches permissions or existing MCP servers (§4.5.2).
  - **Codex trust step:** setup explains the one-time trust in `/hooks` or the desktop settings and never writes `trusted_hash`. Doctor reads trust status from config or the app-server `hooks/list`, and shows T1+T3 until the hooks are trusted.
- **M2.3 Per-client capability probe**: `integration/capability_probe`, `conformance`, `tiers`, `codex_version`, and `clients/codex/*`. Records the verified tier set for each client, and drift drops only the failing tiers (§4.2).
  - Checks carried over from M0: whether tool hooks fire for desktop code-mode `exec` calls, whether hooks are trusted, and whether the transcript parser version still matches.

**Exit:**
- `uv tool install arbiter-agent` followed by `arbiter setup` configures Codex and Claude Code with no manual edits.
- The fixture matrix passes 100%, and uninstall restores byte-identical files.
- Zero config files corrupted.
- `arbiter doctor` shows the verified tier set for each client.

**Result:** all four exit items met.
- The locally built wheel installed with `uv tool install` into a throwaway tool directory; its `arbiter setup` configured sandboxed Codex and Claude Code homes with no manual edits, and `arbiter uninstall` restored `config.toml` byte for byte ([m4/tool_install_check.json](spikes/m4/tool_install_check.json)). Installing from PyPI waits on publishing (M4.5).
- Setup/uninstall fixture matrix: 100% pass, byte-identical restores, 0 corrupted files (`tests/test_clients_setup.py`).
- `arbiter doctor` shows verified T1 T2 T3 for both clients once they're observed (`tests/test_doctor_e2e.py`).

## M3 — Task state + verification ✅ Done 2026-09-24
Stage 2 · Build steps 10–16 · Tier T1 (contracts), T2/T3 (observation) · Evidence: [spikes/m3](spikes/m3/README.md) · Decision [0023](decisions/0023-m3-task-state-implementation.md)

- **M3.1 Concurrency primitives**: `concurrency/queues`, `deadlines`, `cancellation`, `priorities`.
- **M3.2 Intent + epochs**: `state/intent`, `goal_epochs`. The intent log is immutable. Epochs are candidate/confirmed (§6.3.1), `arbiter_scope_change` is available, and epochs are tracked per session.
- **M3.3 Typed IR**: `state/schema`, `facts` (with evidence origin, §6.4.1), `hypotheses`, `evidence`, `builder`, `reconcile`, `repo_identity` (§11.9).
- **M3.4 Contracts**: `state/contract_compiler`, `contract_coverage`, `contracts`, `completion/evidence_ledger`, `evidence_grades`.
  - `arbiter_contract_propose` requires quote provenance and typed recipes.
  - Coverage and weak-contract checks run on every proposal, and some constraints are extracted by rules alone.
  - The agent can never set PASS (§6.8.1).
  - `arbiter contracts` gives command-line access.
- **M3.5 Verification observation**: `telemetry/runner_parsers/`, `baseline`, `completion/verify_runner`. Parsers for common runners, report files, `arbiter verify` with `.arbiter/verify.yaml`, and session baseline capture (§12.4, §12.8).
  - Exit codes are joined from transcripts, because hooks lack them: Codex `CommandExecution.exit_code`, Claude `is_error` (decision 0020).
- **M3.6 Test integrity**: `telemetry/test_integrity`, compared against the baseline. Integrity is UNKNOWN when there is no baseline.
- **M3.7 Telemetry + loop alerts**: `telemetry/errors`, `tests`, `diff_stats`, `cache_metrics`, `progress`, and `reasoning/loop_detector` (advisory).
- **M3.8 Eval harness v0**: `eval/trace_replay`, `eval/corpus/` (including adversarial gate, contract-gaming, and test-weakening cases), and `eval/gates` (§20.17 checks in CI).

**Exit:** §20.17 gates pass on the corpus:
- contract recall ≥ 0.95;
- test-weakening detection ≥ 0.95, with 0 missed file deletions;
- 0 parser misreads;
- loop-alert precision ≥ 0.80;
- "yes, continue" never supersedes contracts;
- concurrent sessions from different clients stay isolated.

**Result:** every gate passes on the corpus ([m3/exit_gates.json](spikes/m3/exit_gates.json)): recall 1.0 over 42 requirements; integrity detection 1.0 over 23 weakening cases, 0 missed deletions, 0 false alarms on 5 benign cases; 0 misreads over 51 parser fixtures; loop precision 1.0; 0 continuation replies superseding contracts; isolation 1.0. **Caveat:** the corpus is synthetic and was written alongside the code, so it's a regression floor, not a held-out benchmark (§20.2 cohorts come later).

Implemented as (module map vs. the plan above):
- `state/facts` + `completion/evidence_grades` hold facts and evidence; the session engine (`daemon/session_engine`) is the IR builder.
- `hypotheses` is deferred to M7: there's no inference source before SemIf.
- Two-phase `reconcile` is deferred to M13: v0.1 does no destructive context operations. Resume boundaries feed the epoch rules, and compaction is recorded.
- `telemetry/tests` is covered by `runner_parsers` + `progress`.

## M4 — Rules-only completion gate → **v0.1** ✅ Built 2026-09-24 (PyPI publish pending)
Stage 2 · Build steps 17–18 · Tier T1 (finish-check), T2 (stop hook) · Evidence: [spikes/m4](spikes/m4/README.md) · Decisions [0024](decisions/0024-m4-gate-verdict-rules.md), [0025](decisions/0025-v0-1-packaging-and-launch.md)

- **M4.1 Claim detection**: `completion/claim_detection`. Uses the finish-check call, final-message rules, and "ends with a question → not gated" (§12.7).
- **M4.2 Gate**: `completion/requirements`, `verification_policy`, `gate`. Produces the finish ledger (§12.5) and runs `annotate` by default. `block` mode is opt-in and bounded by `max_stop_blocks_per_epoch`. `arbiter_finish_check` is exposed over MCP. Block reasons follow the wording rule from decision 0017. A regression test checks, against real Claude Code, that no gate message is persisted as memory.
- **M4.3 Minimal breakers**: breakers for hook latency, gate errors, and parser failures (the v0.1 subset of §15.3).
- **M4.4 Status**: `ui/status`, shown through `arbiter status` and the MCP status tool. Hook-injected summaries stay within `max_injected_tokens` and are sent only when they change.
- **M4.5 Release v0.1**: publish `arbiter-agent` to PyPI. Ship the Claude Code plugin package (§4.5.5), a README with install steps, and a changelog.

**Exit:**
- False PASS = 0.
- The gate fires on ≤ 2% of stops that aren't completion claims.
- Severity-weighted false-complete rate ≤ 50% of stock.
- Gating-hook p95 ≤ 300 ms.
- **v0.1 is published and usable daily on Codex and Claude Code without a GPU** (§23.3).

**Result:**
- False PASS 0; the gate fired on 0 of 40 non-claim stops; severity-weighted false-complete is 0.0 of stock (0 vs 38 severity weight over 30 traces).
- Gating-hook p95 with gate logic through a real daemon: 13.8 ms (Codex `mcp_tool`) and 33.5 ms (Claude `http`).
- The real Claude Code regression passed: a block happened, and no gate text was persisted to memory or instruction files (Haiku, $0.09).
- Version 0.1.0 is built (wheel + sdist), along with the Claude Code plugin and local marketplace, the README and the CHANGELOG.
- **Not done by Arbiter's build:**
  - Publishing to PyPI needs the owner's account (`uv publish`), so "published" is pending.
  - "Usable daily" still needs a stretch of real use on the owner's Codex desktop after trusting the hooks once.
- Module map: `completion/requirements` and `verification_policy` live in `evidence_ledger` (verdict rules) and `gate` (mode, bound, wording).

---

## M5 — Client coverage expansion ✅ Done 2026-09-24
Build step 19 · Decision [0027](decisions/0027-m5-client-profiles.md)

- **M5.1** Recorded-fixture contract tests for each profile version (`clients/fixtures/`).
- **M5.2** Profiles for Cursor, Windsurf, VS Code/Copilot, Gemini CLI, Cline, Zed, OpenCode, Goose, and Claude Desktop. Each is verified with the tier set it actually supports, including completion-claim rules and the `native_tool_deferral` flag.

**Exit:** every profiled MCP-capable client reaches at least T1, and profile drift is detected in fixture tests.

**Result:** met in the sandbox (`tests/test_client_profiles.py`, `tests/test_text_config.py`).
- Ten new profiles: `cursor`, `vscode`, `gemini_cli`, `cline`, `zed`, `opencode`, `goose`, `claude_desktop`, `devin_desktop` (Windsurf was renamed 2026-06-02), and `windsurf` (the legacy Cascade path).
- Setup writes each client's own format, including JSONC and YAML, without dropping the user's comments.
- Every profile reaches configured T1. T1 becomes verified once the client launches Arbiter's MCP server under a name its profile lists.
- Hook dialects (T2, command transport) exist for Cursor (annotate only: its stop can't be held back), VS Code/Copilot and Gemini CLI.
- Fixtures are derived from documented formats and should be replaced with real recordings as the clients are installed. T3 transcripts remain Codex and Claude Code only.

## M6 — Repository indexes ✅ Done 2026-09-24
Build step 20 · Tier T1 · Decision [0028](decisions/0028-m6-repository-indexes.md)

- **M6.1** Lexical and path index (`retrieval/lexical`).
- **M6.2** Symbol, AST, import, and call graph (`symbols`, `ast_graph`, `dependencies`, `state/dependency_graph`).
- **M6.3** Content-hash invalidation plus branch/worktree overlays (`overlays`), keyed by normalized repository identity (§11.9).
- **M6.4** Access policy and secret exclusion before indexing (`access_policy`).
- **M6.5** Optional embeddings, off by default.
- **M6.6** Retrieval exposed as MCP tools.

**Exit:** no stale results after edits or branch switches, secrets never indexed, and the index version recorded on every retrieval.

**Result:** met (`tests/test_retrieval.py`).
- **No stale results:** every query refreshes its view first. This holds after same-second edits, deletions and branch switches both ways (switching back re-analyzes nothing), across git worktrees, and when a time-budget cut leaves files pending.
- **Secrets never indexed:** `.env` files, keys and credential stores are never read, and detected secrets are redacted before storage. The index database files contain no secret bytes.
- **Index version recorded:** every result carries `{version, generation, head}`, and session retrievals are audited as `internal.retrieval` events.
- **Retrieval surface:** MCP tools `arbiter_search`, `arbiter_symbol` and `arbiter_related`, plus CLI `arbiter search` and `arbiter index`. Embeddings stay off (interface only).

## M7 — SemIf service (semantic sensor)
Stage 3 · Build steps 21–22 · Decision [0026](decisions/0026-sensor-backends-model-tiers-and-fine-tuning.md) · Research: [semantic-sensor-models](research/semantic-sensor-models.md)

- **M7.1 Backend interface + null backend**: `semif/service`, `backends/null`, `health`. Every backend returns the same scored-option result. No GPU needed.
- **M7.1b Tier 0 encoder backend**: `backends/encoder` with GLiNER2.5-Decide (340M, CPU or GPU).
  - **Families:** completion claim, scope change / continuation, requirement detection, contract coverage.
  - **Claim detection stays rules-first:** the encoder may only add claims the rules missed.
  - **Confidence** is calibrated before the cascade uses it.
  - **No GPU needed,** so it can ship as v0.2 before M7.2.
- **M7.2 llama.cpp GGUF backend**: `backends/llama_cpp`, with direct option-logit scoring, prefix caching, multi-LoRA, a bounded queue, deadlines, OOM recovery, and restart backoff. The SemIf BF16 path (`backends/semif_bf16`) is kept as the reference and parity backend. **Needs the 3080 Ti.**
- **M7.2b Decoder tiers by VRAM**: JevK5 (Qwen3.5-4B, distilled LoRA merged; `alibiserikbay/JevK5-GGUF` Q4_K_M, 2.71 GB) at 6 GB+, K2 Horizon 7B Q4_K_M at 12 GB+, Q6_K at 16 GB+ (K2 at 8 GB is opt-in, short context only). Reasoning is disabled for scoring. Each decision family is routed to the encoder or a decoder tier by benchmark.
- **M7.3 Request budgeting**: `semif/request_budget`, using the exact tokenizer with criterion/envelope reserve (§7.2).
- **M7.4 Validation**: `semif/validation`. Rejects NaN, malformed output, or a wrong revision, and abstains on failure (§7.3).
- **M7.5 Mirroring + batching**: `semif/mirroring`, `batching`, with identical prefixes only.
- **M7.6 `arbiter semif enable`**: checks CUDA and VRAM, proposes the largest tier that fits, and downloads weights only after confirmation.
- **M7.7 Shadow harness**: async scoring, never awaited in hooks (§4.4.5), with calibration data collection.
- **M7.8 Sensor benchmark**: `eval/corpus/sensor/`, labeled judgments per decision family. It reports balanced accuracy, calibration (Brier/ECE), abstention rate and latency per model × quant × backend (encoder included), measured on the exact quantized files. It decides which stage owns each family.

**Exit:**
- 0 malformed results consumed.
- Queue saturation never blocks a hook.
- Latency is stable per §20.17.
- Rules-only mode is fully functional with the null backend.
- The shipped tier table is backed by the benchmark: each tier beats the one below it, and each beats the rules, on its decision families.

**Progress (2026-09-24, decision [0029](decisions/0029-m7-sensor-service.md)):**
- **Done without a GPU:** M7.1, M7.1b (the encoder backend against a fake model), M7.2's llama.cpp client (tested against a mock `llama-server`), M7.3, M7.4, M7.5, M7.6 (`arbiter semif status|enable|disable|bench`; it plans tiers and never downloads anything itself), M7.7 (engine `decision_listeners` feeding `sensor_log`) and M7.8 (the benchmark harness over the eval corpus).
- **Met in tests** (`tests/test_semif.py`): 0 malformed results consumed, saturation never blocks a hook, rules-only mode with the null backend.
- **First real runs (CPU, 2026-09-24):** GLiNER2.5-Decide and JevK5 4B Q4_K_M were benchmarked on the eval corpus ([results](research/semantic-sensor-models.md#measured-results-2026-09-24-cpu-eval-corpus)). Neither beats the rules on any family, so nothing is routed and both stay shadow-only. Two backend fixes came from real runs: reasoning is disabled through the chat template, and scores are pre-sampling.
- **Tier 0 runtimes and held-out corpus (2026-09-25, decision [0031](decisions/0031-tier0-runtimes-and-heldout-corpus.md)):** ONNX `w8e4` (1.08 GB RAM, ~0.26 s on CPU, same decisions as PyTorch), a Core ML backend for macOS, and one-pass mirroring. On the frozen held-out corpus the rules drop to about 0.55 on claims and scope changes. Rules plus sensor reach 0.95 (encoder, claims) and 0.94 (JevK5, scope changes); requirements stay with the rules (0.90). Routing isn't applied until an independent held-out set confirms it.
- **Still pending:**
  - a second, independent held-out set (real sessions with consent, or another author) before any family is routed;
  - a real Mac run of the Core ML backend;
  - GPU runs (latency exit, K2 Horizon 7B), the BF16 reference backend and OOM recovery on a real server;
  - the tier-table benchmark on the 3080 Ti.

## M8 — Policy core + full circuit breakers ✅ Done 2026-09-24
Stage 4 · Build step 23 · **Required before any automatic stage** (decision 0009)

- **M8.1** `policy/authority`, `constraints`, `budgets`, `conflict_resolution` (§15.5).
- **M8.2** `policy/cascade`: rules, then SemIf, then abstain (§7.7).
- **M8.3** `policy/circuit_breaker` and `fail_modes` (the full §18.6 matrix), plus overload shedding.
- **M8.4** `ui/overrides` (the §24 controls, available from the CLI and MCP).

**Exit:** 100% fault-injection trip rate. Optimization fails open, integrity fails conservative, and shims never block the host.

**Result:** met (`tests/test_policy.py`, `arbiter eval --faults`; decision [0030](decisions/0030-m8-policy-core-and-breakers.md)).
- **13 breaker kinds on one board,** each with a fault scenario: gate errors, hook latency, parsers, false completion (latched per session), client adapters, schema misses, controller, retrieval, sensor families, plus four modules not built yet (sensor parity, stale speculation, context restore, learned policy), which are driven synthetically.
- **Recovery:** a half-open trial window, reopen on failure, N clean outcomes to close. Latched incidents need a manual reset. State survives restarts and every transition is audited.
- **Cascade, §15.5 priority** with evidence floors (UNKNOWN never becomes PASS by preference), **authority** (agents can only request one-turn bypasses; weakening integrity needs an interactive user), **budgets and overload shedding** (integrity is never shed).
- **Controls:** `arbiter control`, `arbiter breakers`, and the MCP tool `arbiter_controls`.

## M9 — Advisory modules ✅ Done 2026-09-25 (exit partly met)
Stage 5 · Build steps 24–26

- **M9.1 Reasoning Scheduler (advisory)**, T1/T3/T4: `reasoning/capability_lattice`, `scheduler`, `leases`, `hysteresis`, `risk_floors`, `update_confirmation`. Chooses **model × effort** (§8.8). Surfaced within the injection budget, and through Host Advisory API v0 (`recommend_call`, `session_signals`, `report_outcome`) in **shadow** mode for Hivemind, which logs recommendations but doesn't apply them.
- **M9.2 Retrieval reranker (advisory)**, T1: `retrieval/candidates`, `reranker`, `miss_detector`, with deterministic pins and adaptive top-k.
- **M9.3 Diff Risk (shadow)**, T1/T2: `review/diff_risk`, `blast_radius`, `review_policy`, `test_mapper`, `test_scheduler`.

**Exit:** dangerous misses ≤ 1% of tasks, retrieval recall ≥ 0.95, and an understandable audit trail.

**Result** (decision [0032](decisions/0032-m9-advisory-modules.md); `tests/test_advisory.py`, `arbiter eval --advisory`):
- **Retrieval recall: met.** 0.992 on development queries, 1.0 on held-out queries in two fixture repos (Python and TypeScript). The fixtures are small, so a real-repository check is still wanted.
- **Audit trail: met.** Every recommendation, rerank and advice read is in `advisory_decision` with inputs, reasons and outcome; `arbiter advice --history` shows it.
- **Dangerous misses: met on development data only.**
  - 0 of 161 development cases.
  - Held-out sets on their single runs: 20%, 7.5%, 22.5%. No high-risk change was rated low; every miss was rated medium, which still gets a normal review.
  - Closing this needs real, independently labeled diffs and a diff-risk sensor family (§13.2 semantic features). Diff risk stays shadow-only meanwhile.
- **Scheduler and Host Advisory API v0** in shadow: never outside the allowed set, deadlines and errors abstain, outcomes confined per project.

## M10 — Tool gateway + bounded retrieval automation ✅ Done 2026-09-25
Stage 6 · Build steps 27–28 · Tier T1

- **M10.1** `gateway/catalog`, `search`, `describe`, `call`, `schema_validation`.
- **M10.2** `gateway/authorization_bridge`, `approval_mirror`, `adopt` (read-only proxying unless approval is mirrored; explicit adopt/release; §10.7).
- **M10.3** Per-client benchmark against normal schemas **and** native deferral. Auto-disable where the gateway doesn't win (§10.6).
- **M10.4** Bounded retrieval reranking turned on.

**Exit:** material benefit per §20.17 on the clients where it's enabled, 0 approval-granularity regressions, and no scope expansion.

**Result** (decision [0033](decisions/0033-m10-tool-gateway.md); `tests/test_gateway.py`, `arbiter gateway bench`):
- **Material benefit where enabled: met in the session model.** With hybrid search the gateway turns on for normal-schema clients at 47 and 73 reference tools (24% and 44% lower session cost), and for native-deferral clients only at 73 tools (13%). With lexical search alone, held-out recall is 0.85, so it stays off.
  - These are modeled costs, not measured agent runs; measuring real Codex sessions with a large adopted catalog is the next step.
- **Approval granularity: 0 regressions** (tests): read-only-only proxying without elicitation, per-call approval with exact arguments, no inheritance, sticky denials, adoption refused when approvals can't be mirrored.
- **Scope: no expansion** (tests): exact launch spec (env, cwd); server-to-client requests refused; remote servers not adoptable yet.
- **M10.4:** reranked context is live in the tools and host API; prompt auto-injection is implemented but opt-in (`retrieval.auto_context`).

## M11 — Hivemind host integration + model/effort bounded auto
Stage 7 · Build steps 29–30 · Tier T4 (via orchestrator host; decision 0013)

- **M11.1 Host Advisory API v1**: `hosts/api`, `allowed_set`, `partitions`, `conformance` (§4.6.2–4.6.3). Covers per-project confinement, outcome reporting with cached/uncached usage, and a host-runnable conformance suite.
- **M11.2 Hivemind as first host**: `hosts/hivemind` profile. The Hivemind-side adapter is built **in the Hivemind repo**. It calls `recommend_call`/`session_signals`/`report_outcome` in its proposal lane only, and degrades to current behavior on timeout, error, or absence.
- **M11.3 Model × effort bounded auto**: Hivemind applies Arbiter's recommendation within its tier-allowed set (§8.8). Cache warmth is priced in, and dynamic choice is disabled automatically on cache regression (§17.5).
- **M11.4 Paired measurement**: evaluate against Hivemind's existing tier routing using its M10.7 capability corpus. Report billed cost and input tokens separately (§30.4).

**Exit:**
- Non-inferiority (lower 95% CI bound ≥ −2 pp) with material benefit in cost per successful task, versus Hivemind's routing.
- Zero responses outside the allowed set.
- Zero cross-project data use.
- Hivemind is unchanged when Arbiter is disabled or unreachable.

## M12 — Verification/review auto
Stage 8 · Build step 31 · Tier T2/T4

- **M12.1** The contract gate, test integrity, and review floors become enforcing on T2/T4 clients where evaluation supports it.
- **M12.2** Stratified low-risk audits (§13.6).

**Exit:** false PASS = 0, and the severity-weighted false-complete gate passes.

## M13 — Context Scheduler
Stage 9 · Build step 32

- **M13.1 External**, T3: `context/scheduler`, `projection`, `retention`, `archive`, `rehydrate`, `memory_admission`. Pre-compaction hooks are used only for checkpoints.
- **M13.2 Host context scoring**, T4 via host: `score_context` for Hivemind under the conservative rules of §9.11. That means no blunt caps, pressure-triggered only, referenced content pinned, and eviction recoverable and visible. Off by default until it passes Hivemind's CR7 conversation-integrity probes.
- **M13.3 Physical control (experimental)**, driver session or `NATIVE`: `context/epochs`, `history_cas`, with a two-phase restore sentinel.

**Exit:** 0 restore or state-loss incidents, all host integrity probes passing, and a material saving in **billed** tokens (not only raw input tokens).

## M14 — Optional component inside the Hivemind app (later)
Build step 33

- **M14.1** Packaging decision: a bundled Python runtime or a frozen sidecar binary, recorded as a decision.
- **M14.2** Hivemind manages install, update, and daemon lifecycle only when the user enables the component. It connects to an existing standalone daemon via the handshake instead of starting a second one.
- **M14.3** Hivemind's UI shows recommendations, scores, and breaker state under its own presentation rules, with no "calibrated probability" wording.
- **M14.4** Disabling leaves no trace, and Hivemind returns to its exact pre-Arbiter behavior.

**Exit:** enable, disable, and upgrade cycles pass on Windows; Hivemind's full test suite passes with the component both enabled and disabled; and no second daemon is started when a standalone install exists.

---

## Research track
Stages R1–R5 (§23.2). These start only after the core track is stable, and never block a core release.

- **R1 — Pure-read speculation** (step R1.1): `speculation/*`, allowlist only. **Exit:** positive wall-time delta, and 0 stale-result or side-effect incidents.
- **R2 — Learned utility shadow** (steps R2.1–R2.2): `eval/paired_trials`, `propensity`, `off_policy`, `statistics`, and `policy/utility_model`, `ood`. **Exit:** stable held-out predictions, with no hidden regression in any task class.
- **R3 — Learned utility bounded auto** (step R3.1): `policy/conservative_policy`. **Exit:** a better constrained Pareto frontier than rules, on repo, time, model, and client holdouts.
- **R4 — Selective branching** (steps R4.1–R4.2): `branching/*`; a worktree alone is rejected as a sandbox. **Exit:** net hard-task gain after cost, and 0 isolation failures.
- **R5 — Backend optimization** (step R5.1, **needs the GPU**): BF16 shared-state against fresh, plus an EXL3 backend once it supports the tier models (Qwen3.5 hybrid layers, `k2_horizon`). **Exit:** per-module and end-to-end parity gates pass (§22), and EXL3 is adopted only if faster at equal accuracy.
- **R6 — Fine-tuned sensors** (step R6.1, **needs the GPU and real v0.1 traces**): full fine-tunes of the tier 0 encoder (minutes on the 3080 Ti), plus QLoRA adapters per decision family on the decoder base models.
  - **Labels** come from outcomes, user decisions, and an offline Opus 5.5 pass over a sample; its cost is confirmed before each run.
  - **Exit:** each adapter beats the untuned base and the rules on held-out repos and time windows, with no regression in other families. Calibration is re-fit on the quantized artifact.

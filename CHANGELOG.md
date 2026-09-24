# Changelog

## 0.1.0 — 2026-09-24

First usable release: M0–M4 of [docs/milestones.md](docs/milestones.md). Built and verified locally; not yet published to PyPI.

### Clients and setup (M2)
- Client profiles for Codex (desktop + CLI), Claude Code and generic MCP clients.
- `arbiter setup`, `doctor`, `uninstall`, `probe`:
  - setup shows a diff, backs up, writes atomically and validates;
  - uninstall restores byte-identical files when you haven't edited them since;
  - setup never touches permissions, sandbox or model settings, other MCP servers, or Codex hook trust.
- Codex `mcp_tool` hooks (hash-stable templates), Claude Code loopback `http` hooks, and a command-CLI fallback.
- Transcript discovery and parsers for Codex rollouts and Claude Code sessions (exit codes, usage).
- Per-client verified tiers (T1 MCP, T2 hooks, T3 transcripts). Drift drops only the failing tier.

### Task state and verification (M3)
- Immutable intent log. Goal epochs are *candidate* until confirmed by a new-task marker, `arbiter_scope_change`, a proposal that declares what it supersedes, or a resume boundary. "yes, continue" never supersedes contracts.
- Agent-proposed contracts with verbatim-quote provenance and typed recipes: `test_command`, `command_exit`, `file_exists`, `file_contains`, `paths_unchanged`, `public_surface_unchanged`, `manual`.
- Rule-extracted contracts ("don't change X", requested test commands, "create file X"), and coverage flags for requests no contract covers.
- Evidence with origins (`arbiter_observed` > `host_reported` > `agent_asserted`) and grades (direct, indirect, unavailable, conflicted). Freshness rules. Exit statuses joined from transcripts.
- Runner parsers for pytest, unittest, Jest, Vitest, Mocha, go test, cargo, dotnet, tsc, eslint, ruff, mypy and JUnit XML. A PASS contradicted by the exit status is UNKNOWN.
- Session baseline and test-integrity checks: deleted tests, skip/focus markers, removed tests, weakened assertions, fixture/snapshot rewrites, harness filters, fewer tests run.
- `arbiter verify`, driven by a user-trusted `.arbiter/verify.yaml` (approval needs an interactive terminal).
- Advisory loop alerts: same error repeated, re-runs with no change, edit thrash.
- Evaluation harness and corpus with the §20.17 gates (`arbiter eval`, also run in CI).

### Completion gate (M4)
- Claim detection: `arbiter_finish_check` or completion wording. Messages ending with a question are never gated.
- Finish ledger per claim. `annotate` mode by default; `block` mode is opt-in, bounded per goal epoch, and uses `[Arbiter]` current-turn wording.
- Breakers for hook latency, gate errors and parser failures. They fail open: never a block, never a PASS.
- Optional status summaries through hook `additionalContext` (off by default; sent only on change; capped).
- MCP tools: `arbiter_contract_propose`, `arbiter_contracts`, `arbiter_scope_change`, `arbiter_finish_check`, `arbiter_verify`.
- Claude Code plugin and a local marketplace (`packaging/claude-code`).

### Fixes found while releasing
- Daemons launched through WMI now receive `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `ARBITER_CLIENT_HOME` (previously ignored).
- Identical Claude Code prompts or stops sent more than 2 s apart are no longer dropped as redeliveries.

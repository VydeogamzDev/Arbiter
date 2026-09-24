# 0001 — One per-user daemon with thin per-client shims

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.4, §4.5, principles 29–33

## Context
Arbiter must work with Codex desktop (the primary target), Codex CLI, Claude Code, and essentially every other agent CLI or desktop app, and it must be seamless to set up. Clients start their own copy of every stdio MCP server, often several per session. A standalone MCP server would therefore mean many processes writing one SQLite database, which breaks the single-writer rule, and many processes loading SemIf onto one GPU. MCP alone is also passive: the model decides whether to call it. So it can't implement goal epochs, telemetry, or completion enforcement.

## Decision
- A single per-user daemon owns state, storage, indexes, SemIf, and policy.
- Clients connect through four surfaces: a stateless MCP shim (T1), a stdlib-only hook CLI (T2), transcript watchers inside the daemon (T3), and optional Arbiter-hosted driver sessions (T4).
- Each client is described by a declarative, versioned profile.
- `arbiter setup` detects clients and configures them reversibly.

## Consequences
- Adding a client means adding a profile plus fixtures, not controller code.
- Daemon lifecycle becomes a real engineering concern: detached lazy start, login-autostart fallback, version handshake.
- Capability differs per client, and this must be surfaced honestly (see 0007).

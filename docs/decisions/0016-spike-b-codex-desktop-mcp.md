# 0016 — Spike M0.b: Codex desktop honors MCP servers in `~/.codex/config.toml`

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4.1, §4.4.2, §4.5.2
- Evidence: `docs/spikes/m0/README.md` §b

## Context
T1 for Codex desktop, the primary target, assumed that the desktop app loads MCP servers from the shared `config.toml`.

## Finding
- **The desktop uses the same engine.** It bundles `codex.exe` (`0.155.0-alpha.16`) and runs it as its app-server. Desktop rollouts report `originator: codex_work_desktop` and `cli_version: 0.155.0-alpha.16`, and read the same `CODEX_HOME` (`~/.codex`).
- **The logs show desktop threads loading those servers.** In the last three days of `~/.codex/logs_2.sqlite`, tool-catalog entries from desktop threads name MCP servers defined under `[mcp_servers.*]` in the user's `~/.codex/config.toml`: `eveos` (248 entries), `bonsai-mcp` (20) and `21st` (9). All 40 recent sessions were desktop sessions.
- **Custom model providers work.** A custom `[model_providers.*]` with a `base_url` is honored by the same engine; the spikes ran against a local mock this way. This is relevant to any future request-proxy idea but isn't used now.

## Decision
- Codex desktop gets **T1** by registering `[mcp_servers.arbiter]` in `~/.codex/config.toml`. One entry serves both CLI and desktop.
- The Codex profile treats CLI and desktop as one config target, and tells them apart at runtime by `originator` in the rollout.

## Consequences
- No desktop-specific install step is needed for T1.
- If a future desktop release stops sharing `~/.codex`, the per-client probe (§4.2) will detect it through the missing MCP round trip.

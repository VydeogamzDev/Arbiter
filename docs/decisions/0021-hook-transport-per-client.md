# 0021 — Hook transport per client: in-process handlers first, command CLI as fallback

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.4.1, §4.4.2, §4.4.5, §18.8
- Supersedes: the "stdlib-only hook CLI is the T2 surface" assumption in spec v3.2 §4.4.1 (the hook CLI stays, as a fallback)

## Context
Spikes 0015, 0017 and 0019 showed that both target clients support hook handlers that don't spawn a process. Codex supports `mcp_tool`; Claude Code supports `http` and `mcp_tool`. These are about 100× faster than command hooks on Windows, where Codex command hooks pay for Windows PowerShell 5.1.

## Decision

| Client | Primary T2 transport | Notes |
| --- | --- | --- |
| Codex CLI + desktop | `mcp_tool` → server `arbiter`, tool `arbiter_hook` | per-event `input` templates with guaranteed fields only; separate subagent templates; hash-stable definitions; one-time user trust |
| Claude Code (CLI + desktop) | `http` → `http://127.0.0.1:<port>/hook/<event>` on the daemon | token passed in a header; no trust step at user level; also usable on SessionStart, where `mcp_tool` isn't |
| Other clients | the client's in-process handler if one exists, else the command CLI | command CLI `async` for telemetry; excluded from gating when p95 > 300 ms |

- The MCP shim forwards `arbiter_hook` calls to the daemon over the authenticated pipe. It answers within the gating deadline and fails open, returning an empty result.
- The daemon's HTTP hook endpoint binds to loopback only and requires the per-install token (§18.8). The port and token are written into the client config at setup, in a user-only file.
- Hook responses use each client's decision format (`decision: block` + `reason`, `hookSpecificOutput.additionalContext`), and block reasons follow the wording rule in 0017.

## Consequences
- The daemon owns a small HTTP listener. Its port must be stable, or rewritten into client config on change, which would force the user to re-trust Codex hooks. So **the port is fixed at setup**, and Codex uses the MCP path, whose definition doesn't include the port.
- The command CLI remains for coverage of other clients and for `doctor` round-trip tests.

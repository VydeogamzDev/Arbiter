# 0019 — Spike M0.e: process-spawning hooks miss the budget on Windows; in-process handlers pass

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4.1, §4.4.5, §4.5.1, §20.17
- Evidence: `docs/spikes/m0/results/bench_hooks.json`; `docs/spikes/m0/scripts/bench_*.py`

## Context
§20.17 budgets: telemetry hooks add ≤ 50 ms at p95 (async allowed); gating hooks stay ≤ 300 ms at p95. The question was whether a stdlib-only Python hook installed via `uv` can meet them on Windows.

## Finding
Measured on this machine (Windows 11, Python 3.14), N = 30–40 runs with a ~1 KB stdin payload:

| Launch path | p50 | p95 |
| --- | --- | --- |
| native exe baseline | 28 ms | 85 ms |
| `python -I -S -c pass` | 47 ms | 158 ms |
| uv console-script trampoline (stdlib hook) | 163–209 ms | 266–279 ms |
| Windows PowerShell 5.1 start (`-NoProfile`) | 352 ms | 405 ms |
| **Codex command hook** (PowerShell → trampoline) | **537 ms** | **667 ms** |
| Codex app-server reported command-hook duration | 540–600 ms | — |
| Claude shell form (Git Bash → trampoline) | 294 ms | 418 ms |
| Claude exec form (trampoline, no shell) | 209 ms | 279 ms |
| **Codex `mcp_tool` hook** (app-server `durationMs`) | **2–4 ms** | — |
| **HTTP hook round trip** (localhost POST) | **1.1 ms** | **3.3 ms** |

- Every process-spawning path misses the 50 ms telemetry budget.
- Codex command hooks miss the gating budget too, and a compiled binary can't fix that, because Codex adds PowerShell 5.1 start-up (~350 ms) to every command hook.
- In-process handlers are about 100× faster.

## Decision
- **Primary hook transports** (see 0021):
  - **Codex:** `mcp_tool` hooks served by the already-running Arbiter MCP shim.
  - **Claude Code:** `http` hooks to the daemon's local endpoint.
  - **Other clients:** whichever in-process handler they offer.
- **The command hook CLI is a fallback only,** for clients without an in-process handler type. There it's `async` for telemetry wherever the client supports it, and it's excluded from gating paths whose measured p95 exceeds 300 ms.
- A compiled hook binary is no longer on the critical path. Revisit only if a fallback-only client needs synchronous gating.

## Consequences
- The daemon exposes an authenticated local HTTP endpoint in addition to the named pipe (§18.8).
- The MCP shim serves hook calls as well as agent-facing tools.

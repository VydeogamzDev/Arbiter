# 0006 — Explicit gateway adoption; never collapse per-tool approvals

- Status: Accepted
- Date: 2026-09-24
- Spec: §10.6, §10.7, §4.5.2

## Context
The tool gateway saves schema tokens by fronting many tools behind `tool_search`, `tool_describe`, and `tool_call`. Proxying a user's existing MCP servers means rewriting their client config, and it collapses the client's per-tool approval into a single approval for `tool_call`. That would be a silent permission regression. Some clients, including Claude Code, already defer MCP tool schemas natively, which removes most of the gateway's benefit there.

## Decision
- Setup never adopts existing servers; adoption is explicit, via `arbiter gateway adopt` / `release`.
- Only confirmed read-only tools are proxied by default.
- Mutating tools either stay direct or are proxied only if the client's approval policy can be mirrored, via MCP elicitation or by refusing.
- Approvals are never inherited across tools, and denials are sticky.
- The gateway is off by default and is enabled per client only if it beats that client's native deferral.

## Consequences
- In practice the gateway may stay off for clients with native deferral.
- Read-only classification must come from trusted catalog data plus server annotations, never from description text.

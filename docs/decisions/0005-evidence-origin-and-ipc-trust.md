# 0005 — Evidence origin classes and authenticated local IPC

- Status: Accepted
- Date: 2026-09-24
- Spec: §6.4.1, §18.8

## Context
The daemon accepts events that can move contracts toward PASS. Without controls, any local process could send a forged "tests passed" event. Self-reports from the agent are also weaker than results Arbiter observed itself.

## Decision
- Every fact carries an origin: `arbiter_observed`, `host_reported`, or `agent_asserted`. Recipes declare the minimum origin they accept.
- `agent_asserted` evidence alone never satisfies a contract.
- On Windows, IPC endpoints are restricted to the current user's SID. On macOS and Linux they live in a `0700` runtime directory.
- Every connection presents a per-install token, rotated on restart, and the TCP fallback binds to loopback only.
- No IPC message can set PASS directly.

## Consequences
- A same-user process with full file access is explicitly outside the threat model. The goal is to stop cross-user access, remote access, and casual forgery.
- `arbiter verify` becomes the way to get the strongest evidence.

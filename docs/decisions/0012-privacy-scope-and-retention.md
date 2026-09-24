# 0012 — Project scope, redaction at ingest, retention and caps

- Status: Accepted
- Date: 2026-09-24
- Spec: §16.3.1–16.3.3, §4.4.1

## Context
Arbiter is installed at user scope, and its transcript watchers can see every agent session on the machine, including projects the user never meant it to observe. Redacting secrets only in log output would still leave them in the event log. The append-only log would grow without bound.

## Decision
- Ingest is limited by a project scope: all-except-excluded by default, confirmed during setup; allow-list mode is optional.
- Exclusion uses `.arbiterignore`, a global exclude list, and `arbiter exclude`. Excluded sessions are skipped before parsing.
- Redaction runs at ingest, using typed placeholders plus a keyed HMAC.
- Raw payloads are kept for 30 days; ledgers and metrics for 180 days.
- A 2 GB storage cap is enforced by deleting the oldest expired data first, never active-session evidence.
- Large outputs are content-addressed and size-capped.

## Consequences
- Some evidence from long-past sessions will be gone by design.
- Redaction patterns need fixtures and a false-negative benchmark.

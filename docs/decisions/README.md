# Decisions log

Short records of design decisions, in the style of architecture decision records (ADRs). [arbiter-spec.md](../arbiter-spec.md) is the reference; these files record *why* it says what it says.

Rules:
- One decision per file: `NNNN-short-title.md`, numbered in order and never renumbered.
- A decision is never edited to reverse it. Write a new record that supersedes it, and set the old one's status to `Superseded by NNNN`.
- Loosening a §20.17 numeric gate requires a record here **before** the affected evaluation run starts.
- Assumption-spike results (spec §27, step 0) are recorded here as `Accepted` or `Rejected` findings.

Template:

```markdown
# NNNN — Title

- Status: Proposed | Accepted | Superseded by NNNN | Rejected
- Date: YYYY-MM-DD
- Spec: §x.y

## Context
What forced a decision.

## Decision
What we chose.

## Consequences
What this makes easier, harder, or rules out.
```

## Index

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-multi-client-daemon-and-shims.md) | One per-user daemon with thin per-client shims | Accepted |
| [0002](0002-package-name.md) | Distribution `arbiter-agent`, import `arbiter_agent`, command `arbiter` | Accepted |
| [0003](0003-completion-gate-claims-and-annotate.md) | Gate only completion claims; `annotate` by default | Accepted |
| [0004](0004-host-agent-proposes-contracts.md) | Host agent proposes contracts; Arbiter enforces provenance | Accepted |
| [0005](0005-evidence-origin-and-ipc-trust.md) | Evidence origin classes and authenticated local IPC | Accepted |
| [0006](0006-gateway-adoption-and-approvals.md) | Explicit gateway adoption; never collapse per-tool approvals | Accepted |
| [0007](0007-hosted-session-scope.md) | Honest scope and targets for hosted sessions | Accepted (amended by 0013) |
| [0008](0008-single-availability-taxonomy.md) | One availability taxonomy: tier sets + NATIVE/EXPERIMENTAL | Accepted |
| [0009](0009-circuit-breakers-before-automation.md) | Full circuit breakers before any automatic stage | Accepted |
| [0010](0010-v0-1-scope-and-research-track.md) | v0.1 scope and a separate research track | Accepted |
| [0011](0011-numeric-default-gates.md) | Numeric default gates replace qualitative exit criteria | Accepted |
| [0012](0012-privacy-scope-and-retention.md) | Project scope, redaction at ingest, retention and caps | Accepted |
| [0013](0013-hivemind-host-and-optional-embedding.md) | Arbiter stays separate; Hivemind calls it; later an optional in-app component | Accepted |
| [0014](0014-model-tier-routing-and-billed-token-economics.md) | Model-tier selection in the core scheduler; price billed tokens | Accepted |
| [0015](0015-spike-a-codex-hooks.md) | Spike a: Codex hooks exist, fire in CLI and app-server, need one-time trust | Accepted (spike) |
| [0016](0016-spike-b-codex-desktop-mcp.md) | Spike b: Codex desktop honors `config.toml` MCP servers | Accepted (spike) |
| [0017](0017-spike-c-claude-stop-semantics.md) | Spike c: Claude Stop fires at every turn end; block works; reasons can leak into memory | Accepted (spike) |
| [0018](0018-spike-d-windows-daemon-launch.md) | Spike d: desktop clients run in job objects; launch daemon outside the client tree | Accepted (spike) |
| [0019](0019-spike-e-hook-latency.md) | Spike e: spawning hooks miss the budget on Windows; in-process handlers pass | Accepted (spike) |
| [0020](0020-spike-f-transcripts.md) | Spike f: transcript formats, locations and stable IDs | Accepted (spike) |
| [0021](0021-hook-transport-per-client.md) | Hook transport per client: in-process handlers first, command CLI fallback | Accepted |
| [0022](0022-m1-implementation-choices.md) | M1 implementation choices (IPC auth, blob storage, launcher stub, sockets) | Accepted |
| [0023](0023-m3-task-state-implementation.md) | M3 task-state choices: cursor engine, epoch rules, exit-status join, freshness, audit events | Accepted |
| [0024](0024-m4-gate-verdict-rules.md) | M4 gate verdict rules: claims, low-strength and integrity conditions, breakers | Accepted |
| [0025](0025-v0-1-packaging-and-launch.md) | v0.1 packaging, Claude Code plugin, client-home env for the daemon launcher | Accepted |

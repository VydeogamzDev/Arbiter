# 0007 — Honest scope and targets for hosted sessions

- Status: Accepted (amended by 0013: orchestrator hosts, not Arbiter's own driver mode, are the primary path to T4 savings)
- Date: 2026-09-24
- Spec: §4.4.3, §11.8, §14.3, §30.0

## Context
In sessions started inside a client app, which includes the primary target, Codex desktop, the agent uses its own built-in read, search, shell, and edit tools. Arbiter can't narrow retrieval for those tools, serve prefetched results into them, remove context, or change effort. The v3.1 savings ranges assumed that control.

## Decision
- The spec states explicitly what Arbiter can't control in hosted sessions.
- Retrieval, gateway, and speculation benefits are counted only on Arbiter-served paths.
- A separate hosted-client planning range (§30.0) is added, centered on the completion gate, test integrity, loop detection, and advisory effort.
- The larger ranges are reserved for T4 driver sessions or clients that route tool use through Arbiter.
- Any pre-tool nudge toward Arbiter's retrieval tool is opt-in and never blocks.

## Consequences
- Expectations for Codex desktop are modest on token savings and strong on verification and visibility.
- Driver mode (M11) becomes the path to the headline savings.

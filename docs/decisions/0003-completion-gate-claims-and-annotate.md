# 0003 — Gate only completion claims; `annotate` by default

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.4.5, §12.7

## Context
A stop-type hook fires every time the agent ends a turn. That includes asking the user a question, reporting partial progress, or pausing in conversation. A gate that blocks on every stop would push the agent back to work while it's waiting for the user, waste remote usage, and make Arbiter feel hostile.

## Decision
- The gate evaluates only recognized **completion claims**. Recognition comes from an `arbiter_finish_check` call; from deterministic rules on the final message (a completion statement, no trailing question, no pending calls); and later from an asynchronous SemIf judgment.
- Anything unclassifiable is not gated.
- The default mode is `annotate`, which records the ledger and marks verified/unverified without blocking.
- `block` mode is opt-in per user, repo, or session, and is bounded by `max_stop_blocks_per_epoch`.

## Consequences
- By default, Arbiter's value is visibility ("this finish is unverified: 2 contracts UNKNOWN") rather than enforcement.
- Claim detection needs a per-client profile field and benchmark cases. The false-gating target is ≤ 2% of stops (§20.17).
- Spike 0c must confirm Claude Code stop-hook semantics before this is built.

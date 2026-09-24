# 0004 — Host agent proposes contracts; Arbiter enforces provenance

- Status: Accepted
- Date: 2026-09-24
- Spec: §6.8.1

## Context
Turning free-text user requests into contracts is a semantic task. Contracts are needed from v0.1, long before SemIf exists, and SemIf is only a bounded sensor even when it does. The host agent already understands the request. The risk is that it writes weak contracts that are easy to satisfy.

## Decision
- The agent proposes contracts through `arbiter_contract_propose`, with verbatim user quotes and typed verification recipes.
- Arbiter deterministically verifies that the quotes exist in the immutable intent log, that the recipes are of an evaluable type, and that the active epoch's intent is covered.
- Arbiter also extracts some constraints by rules alone.
- The agent can never set PASS. Weak contracts are flagged.
- Free-text recipes stay UNKNOWN until the user marks them.

## Consequences
- Contract quality depends partly on the agent. Quote provenance, coverage checks, and weak-contract flags limit the damage.
- T3-only clients get contracts only from deterministic extraction or `arbiter contracts` on the command line.
- The contract benchmark must include agents that under-specify on purpose.

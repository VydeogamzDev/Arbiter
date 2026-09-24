# 0009 — Full circuit breakers before any automatic stage

- Status: Accepted
- Date: 2026-09-24
- Spec: §23.1, §27, principle 28

## Context
The v3.1 rollout listed "decision cascade/circuit breakers" as stage 8, after the automatic stages 4–6. Principle 28 and the v3.1 build order both required breakers first.

## Decision
- The rollout now puts the decision cascade and full circuit breakers at stage 4, before every automatic stage.
- v0.1 includes minimal breakers for what it enforces: hook latency, gate errors, and parser failures.

## Consequences
- Stages 5 onward can't start until the fault-injection trip rate is 100%.

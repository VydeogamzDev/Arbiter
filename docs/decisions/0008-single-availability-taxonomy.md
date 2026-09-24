# 0008 — One availability taxonomy: tier sets + NATIVE/EXPERIMENTAL

- Status: Accepted
- Date: 2026-09-24
- Spec: §2 (deployment-scope rule), §4.1, §4.4.3

## Context
After the multi-client revision, the spec had four overlapping ways to say whether a feature is available: v3.1 scope tags (SIDE-CAR SAFE / GATEWAY SAFE / NATIVE/PATCHED / EXPERIMENTAL), Modes A/B/C, client tiers T1–T4, and driver mode. Some text also said "highest tier," which implied a ladder even though tiers are additive.

## Decision
- A feature's availability is (the tier set it requires) × optional `NATIVE` × optional `EXPERIMENTAL`.
- Each client has a *verified tier set*.
- Modes A–C are kept only as historical design background, with a mapping table.

## Consequences
- The code has one `integration/tiers.py` in place of `modes.py`.
- Drift handling drops only the failing tiers.

# 0010 — v0.1 scope and a separate research track

- Status: Accepted
- Date: 2026-09-24
- Spec: §23.1–23.3, §27

## Context
Under v3.1 ordering, nothing useful to a user shipped until roughly the advisory stage. M0 was also very large, because it included broad client-coverage expansion. Speculation, learned policy, branching, and backend optimization are research-grade and were competing with product work. The GPU is also unavailable for now.

## Decision
- **v0.1 works without a GPU,** on Codex and Claude Code. It includes:
  - install, setup, doctor, and uninstall;
  - the daemon, shims, and event log;
  - privacy scope;
  - the session baseline and verification observation;
  - test integrity;
  - loop alerts;
  - agent-proposed contracts;
  - the rules-only completion gate in `annotate` mode (opt-in `block`);
  - status.
- Broad client coverage moves to after v0.1.
- Speculation, learned policy, branching, and backend optimization become a research track (R1–R5) that never blocks core releases.
- Stage 0 is time-boxed assumption spikes.

## Consequences
- There's a usable daily tool early, which generates real traces for later SemIf calibration.
- Research work waits until the core track is stable.

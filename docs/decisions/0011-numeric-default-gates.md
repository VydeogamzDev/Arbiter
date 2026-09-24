# 0011 — Numeric default gates replace qualitative exit criteria

- Status: Accepted
- Date: 2026-09-24
- Spec: §20.17, §28

## Context
Exit criteria such as "very low false-complete rate," "stable latency," and "measurable benefit" couldn't be tested. The non-inferiority margin was never set. Evaluation infrastructure also arrived too late to measure the early gates.

## Decision
- Provisional numeric defaults are adopted (§20.17). Examples: non-inferiority at the lower 95% CI bound ≥ −2 pp; zero false PASS; gating-hook p95 ≤ 300 ms; test-weakening detection ≥ 0.95.
- Tightening a number is free. Loosening one requires a record here before the evaluation run starts.
- Eval harness v0 (trace replay, a benchmark corpus with adversarial cases, and gate checks) moves into stage 2.

## Consequences
- Every milestone's exit gate can be automated.
- Some defaults may prove unrealistic. Loosening them is allowed, but only in the open and recorded in advance.

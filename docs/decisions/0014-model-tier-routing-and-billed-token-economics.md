# 0014 — Model-tier selection in the core scheduler; price billed tokens

- Status: Accepted
- Date: 2026-09-24
- Spec: §8.8, §9.11, §17.5, §30.4

## Context
Hivemind's M10.7 corpus of six Codex calls measured 87.6% of input tokens as cache reads, served at a 90% discount. On its fixed task shapes, per-successful-task cost was about 54.7× lower with Luna than with Sol, and about 2.4× lower with Terra. Separately, Hivemind's CR7 repairs showed that blunt history caps truncated content the next step needed, and the user rejected fixed history limits. The v3.1 spec had deferred model routing and priced savings in raw tokens.

## Decision
- The Reasoning Scheduler chooses **model × effort** within the host- or policy-allowed set. Its objective is expected cost per *successful* task, including retry cost.
- All economic estimates use **billed** usage (uncached input, discounted cached input, output, reasoning). Input-token reduction is reported separately from cost reduction.
- Context control through hosts is **conservative**:
  - no blunt caps;
  - triggered only by real model-limit pressure or a billed-cost gain;
  - referenced content pinned;
  - eviction recoverable and visible;
  - enabled only after passing the host's conversation-integrity probes.

## Consequences
- Priority order for expected savings: model-tier choice, then rework and loop avoidance, then context trimming.
- The earlier driver-column estimates for input-token savings overstate cost impact wherever cache hit rates are high.
- These corpus numbers are tied to one set of models and one date. They motivate the design but are not reused as forecasts.

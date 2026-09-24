# 0013 — Arbiter stays separate; Hivemind calls it; later an optional in-app component

- Status: Accepted
- Date: 2026-09-24
- Spec: §1 (orchestrator hosts), §4.6, §23.1 stage 7, §27 steps 29–33
- Amends: 0007 (the "driver mode is the path to headline savings" consequence)

## Context
Driver-level control (per-call model, effort, and prompt contents) isn't possible inside Codex desktop or Claude Code sessions. The user's other project, Hivemind AI, already launches every agent call itself (`codex exec`, `claude -p`), pins model and effort per profile, and builds every prompt from durable state. That makes it T4 by construction. Hivemind also shares Arbiter's core rule: the LLM proposes and deterministic code disposes.

## Decision
- **Separate project.** Arbiter remains a separate project with its own release cycle.
- **Hivemind calls Arbiter.** It uses a versioned Host Advisory API over authenticated local IPC. Arbiter proposes (model × effort within the host's allowed set, context selection, loop and retrieval signals); Hivemind disposes. Arbiter output never enters Hivemind's guarantee-enforcing code.
- **Embedding later.** Arbiter becomes an **optional, user-enabled component inside the Hivemind app**. When disabled, Hivemind's behavior is unchanged.
- **Host data confinement.** Host-originated data is confined per project, to respect Hivemind's M8 project-local memory rule.
- **Learned policies.** Any learned policy that would shift Hivemind's routing is only a proposal. It gains authority only through Hivemind's human Tier-2 canon promotion.
- **Standalone mode continues.** Arbiter's standalone hosted-client mode continues for everyday Codex desktop and Claude Code use.
- **Driver mode demoted.** Arbiter's own driver mode drops to a later extension.

## Consequences
- The Host Advisory API becomes a stable, versioned contract, and needs a conformance suite that hosts can run.
- Hivemind needs an Arbiter adapter in its own repository. It must degrade silently to current behavior on timeout, error, or absence.
- Embedding needs a packaging decision (a bundled Python runtime or a frozen sidecar) and daemon sharing with any standalone install. That work is deferred until embedding.
- Arbiter's gains inside Hivemind are measured against Hivemind's existing tier routing, using its M10.7 paired corpus.

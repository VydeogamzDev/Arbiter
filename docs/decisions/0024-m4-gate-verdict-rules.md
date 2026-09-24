# 0024 — M4 completion-gate verdict rules

- Status: Accepted
- Date: 2026-09-24
- Spec: §6.8.1, §12.3–12.7, §15.3; decisions 0003, 0017

## Context
The gate reports whether a completion claim is *verified to the configured evidence standard*. Building it required exact rules for claims, verdicts, blocking and failure handling.

## Decisions
1. **Claims.**
   - A stop is gated only when it is a completion claim:
     - `arbiter_finish_check` was called this turn; or
     - the final message states completion (forms such as "Done", "I've fixed…", "all tests pass", "summary of changes"), with no trailing question and no partial-progress wording.
   - Mixed wording is *uncertain*, and uncertain stops are not gated.
   - A message ending in a question is never gated.
2. **Verdict = verified only if all hold:**
   - at least one contract exists in the active epoch (nothing to check means unverified);
   - every active contract is PASS or WAIVED;
   - no requirement-like intent in the epoch is uncovered;
   - no PASS contract is **low-strength** (weak-contract defense, §6.8.1 step 7). A trivially satisfiable recipe can't carry a verified verdict; the user can waive it;
   - when any PASS relies on test evidence (a `test_command` recipe, or a `command_exit` recipe for a runner command), test integrity is OK against a baseline. UNKNOWN or ALERT integrity blocks a verified verdict.
3. **Modes.**
   - `annotate` (default) records the finish ledger and never blocks.
   - `block` returns the agent to work with the missing items, at most `max_stop_blocks_per_epoch` times per epoch. After that the stop is allowed and marked unverified.
   - Mode is set per user (config), or per session (`arbiter gate block|annotate|default`). Per-repo opt-in is left for later.
4. **Wording (0017).** Block reasons:
   - start with `[Arbiter]`;
   - say "for the current turn only";
   - list missing evidence factually;
   - end by stating the note is not a standing preference.

   `check_wording` enforces this in tests and in the eval harness. The opt-in real Claude Code regression passed: Haiku, $0.09 per run; a block happened and nothing was persisted as memory or instructions.
5. **Failure handling.** The gate never blocks and never reports PASS when it can't run. Three breakers make it fail open:
   - `hook_latency`: p95 over the last 50 gated stops above `hooks.gating_p95_budget_ms`, with at least 20 samples;
   - `gate_errors`: 3 exceptions in 10 minutes;
   - `parser:<runner>`: 3 recognized commands with unrecognized output, or PASS/exit-status contradictions, in 10 minutes.

   An open breaker forces that runner's PASS results to UNKNOWN, including results joined later from transcripts. Breakers half-open after a 10-minute cooldown.
6. **Status injection** is off by default (`ui.inject_status`). When enabled, it adds a one-line `additionalContext` summary on SessionStart and UserPromptSubmit. It's sent only when the content hash changes, capped at `hooks.max_injected_tokens`, and uses the same `[Arbiter]` wording.

## Consequences
- On the corpus:
  - false PASS 0;
  - the gate fired on 0 of 40 non-claim stops;
  - severity-weighted false-complete 0.0 of stock;
  - gating p95 through a real daemon of 13.8 ms (Codex `mcp_tool`) and 33.5 ms (Claude `http`).
- Uncovered-intent flags use deterministic sentence rules. They are conservative, so annotate-mode ledgers will often list requests the agent never turned into contracts. That is the intended nudge; block mode stays opt-in.

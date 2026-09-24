# 0023 — M3 task-state implementation choices

- Status: Accepted
- Date: 2026-09-24
- Spec: §6.1, §6.3.1, §6.4.1, §6.8.1, §12.4, §12.8

## Context
M3 turns the event log into operational task state: intent, goal epochs, facts, contracts, evidence, integrity and loop alerts. The spec leaves several mechanics open, and building it exposed a few interactions with M1's design.

## Decisions
1. **Cursor-driven session engine.**
   - The engine consumes `event_log` in seq order from a persisted cursor, rather than reacting only to in-memory ingest callbacks.
   - As a result, events whose hook caller stopped waiting (`pending`) are still processed, and restarts resume where they left off.
   - Each event's state changes happen in one writer job, inside a savepoint: a bad event is skipped, not fatal.
   - Heavy work (baseline capture, integrity scans, git) runs on a bounded background queue. It never runs inside a writer job or on a hook's critical path.
2. **The gate path catches up first.** A Stop hook calls `catch_up()` with part of its deadline before evaluating, so the gate sees every event logged before the stop.
3. **Epoch rules (§6.3.1):**
   - Every prompt becomes an immutable intent row (append-only, enforced by triggers).
   - These confirm a new epoch:
     - the first prompt of a session;
     - an explicit new-task marker;
     - a user phrase rule (`state.new_task_phrases`);
     - the first *non-continuation* prompt after a **resume** boundary.
   - A short continuation reply ("yes, continue") only joins the current epoch.
   - Anything else opens a *candidate* epoch that only `arbiter_scope_change`, or a proposal declaring `supersedes` / `new_task`, can confirm.
   - Compaction is recorded but does not confirm an epoch: auto-compaction is not a user boundary.
   - When an epoch is confirmed:
     - older contracts are superseded;
     - contracts quoting the new epoch's own intents are carried forward, as are ids listed in `carry`.
4. **Intent source.** Hook `UserPromptSubmit` is the primary source. Transcript user messages are used only for sessions with no hook prompts (T3-only clients). Injected messages that start with `<` are ignored.
5. **Facts and origins.**
   - Shell results recognized by a runner parser become `test_run` facts; other shell results become `command_run` facts. Edit tools, plus shell commands that look like writes, become `file_change` facts.
   - Hooks and transcripts yield `host_reported`. Only Arbiter's own reads and `arbiter verify` yield `arbiter_observed`. `arbiter_finish_check` claims are stored as `agent_asserted` and never satisfy a contract.
6. **Exit-status join processes cross-surface duplicates.**
   - A transcript `tool_result` shares its dedupe key with the hook `PostToolUse` for the same tool call (0022 item 3), so it's stored as a duplicate. The engine still processes these duplicates, because they carry the exit status that hooks lack (0020).
   - The join goes by tool-use id first, then by the same command within 5 minutes.
   - Claude Code results carry no command; they join to the hook fact by tool-use id.
   - A parsed PASS contradicted by the exit status becomes UNKNOWN.
7. **Test evidence must be fresh.** A test run observed before the latest code change is *stale*, and its contract is UNKNOWN. Changes to documentation files (`*.md`, `docs/`, …) don't make test evidence stale.
8. **Command matching for test recipes.**
   - A run satisfies a recipe exactly when it has the same tokens, plus only safe flags (`-q`, `-v`, `--tb=…`, …).
   - A broader run (the whole suite, or a parent path) is INDIRECT evidence, which is accepted by default.
   - Narrowing flags (`-k`, `--deselect`, `--lf`, `-t`, `--grep`, …) never match.
   - Two matching runs since the last change that disagree are CONFLICTED, so the contract is UNKNOWN.
9. **Baseline stores metrics, not content.**
   - Test, fixture, snapshot and harness files are hashed and measured: test definitions, assertions, skip/focus markers and harness filters.
   - Integrity compares those metrics, plus same-command test totals, against the current tree. With no baseline, integrity is UNKNOWN.
   - Scans are bounded by file count and time. A scan that runs out of time reports UNKNOWN; it never reports OK.
10. **Internal audit events.**
    - Epoch changes, contract proposals, scope changes, user decisions and verify results are appended to `event_log` as `internal.*` rows. `status` reports them separately from client events.
    - v0.1 does not rebuild task-state tables from the log: file snapshots and verify runs can't be replayed.
11. **Windowed redelivery for repeatable hook events.** Claude Code prompts carry no turn id, so a second identical "continue" used to be dropped forever as a redelivery. For `user_prompt` and `stop` events without a turn id, same-surface idempotency now applies only within 2 seconds. Real retries fall inside that window; a legitimate repeat falls outside it.
12. **Session binding for MCP tools.** A tool call binds to a session in this order:
    - an explicit `session_id`;
    - the last hook session seen by that shim (Codex `mcp_tool` hooks flow through the same shim);
    - the most recent open session of the same client whose `cwd` HMAC matches the shim's working directory (Claude, whose hooks are http);
    - the most recent session of that client in the last 2 hours.

## Consequences
- The M3 gates in §20.17 are checked in CI by `tests/test_eval_gates.py`, over a synthetic, hand-labeled corpus. That corpus was co-developed with the rules. The real-trace cohorts of §20.2 are still needed before any number here is treated as a population estimate.
- `hypotheses` (M7) and two-phase `reconcile` (M13) are deferred; nothing in v0.1 produces inferences or destructive context operations.

# 0017 — Spike M0.c: Claude Code Stop fires at every turn end; block works; block reasons can leak into memory

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4, §12.7, decision 0003
- Evidence: `docs/spikes/m0/README.md` §c

## Context
Decision 0003 assumed that a stop hook can't tell "done" from "waiting on the user". This needed confirming, along with exactly how a block behaves.

## Finding
Tested with Claude Code 2.1.251 (`claude -p`, Haiku, two short runs costing about $0.04 in total), using hooks passed through `--settings`, so the user's settings were untouched.

- **Stop fires on turns that end with a question.** A turn ending "Which function would you like to rename?" fired Stop with `stop_hook_active: false`. Turn end ≠ completion. Decision 0003 is confirmed for both Claude Code and Codex (0015).
- **Stop payload:** `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode`, `stop_hook_active`, `last_assistant_message`, `background_tasks`, `session_crons`.
- **Blocking:** `{"decision":"block","reason":…}` from an `http` hook sent Claude back to work. The follow-up Stop carried `stop_hook_active: true`.
- **Hazard: the model saved the block reason as a persistent memory file.** It wrote a memory under `~/.claude/projects/<key>/memory/` treating the reason as standing user feedback. (The test artifact was deleted.)
- **Handler types:** `command` (a shell form via Git Bash on Windows, or an exec form with `args` and no shell), `http` (POST to a URL, no process), `mcp_tool` (not available on SessionStart), `prompt` and `agent`. User-level and `--settings` hooks need **no** trust step.
- **Tools on Windows:** the shell tool is named `PowerShell`. PostToolUse `tool_response` is structured as `{stdout, stderr, interrupted, isImage}` plus `duration_ms`, with **no exit code**.
- **Hook runs in the transcript:** they're recorded as `hook_success` / `hook_blocking_error` attachments that include `exitCode`.

## Decision
- The completion gate acts only on recognized completion claims (0003 stands). Both clients provide `last_assistant_message` for rule-based claim detection.
- **Block-reason wording rule.** Reasons are factual, current-turn notes that list the missing evidence, prefixed `[Arbiter]`, and explicitly scoped to this turn. They never contain imperative instructions that could be read as standing preferences. This applies to every client. For Claude Code, `additionalContext` is preferred where it's enough.
- Arbiter's Claude Code hooks use **`http` handlers** to the daemon (0021). Command handlers are a fallback only.
- The Claude normalizer maps `PowerShell` and `Bash` to one shell-tool type.

## Consequences
- The gate's `block` mode needs regression tests to confirm that no memory or instruction is persisted from its reasons.
- `annotate` stays the default (0003).

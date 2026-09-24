# 0020 — Spike M0.f: transcript formats, locations and stable IDs for Codex and Claude Code

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4.1 (watchers), §4.4.4, §12.8, §17.4, §17.5
- Evidence: `docs/spikes/m0/README.md` §f. Real transcripts were inspected **structurally only** (record types and keys), not their content.

## Finding

### Codex (CLI and desktop)
- **Location:** `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<local-ts>-<session-uuid>.jsonl`, still written today by desktop sessions. There's also `~/.codex/session_index.jsonl` (`id`, `thread_name`, `updated_at`).
- **Records:** `{timestamp, ordinal, type, payload}`.
  - **`ordinal` is monotonic per file.** Together with the session id it makes a stable dedupe key.
  - Types: `session_meta`, `turn_context`, `response_item` (`message`, `function_call`, `function_call_output`, `custom_tool_call`, `custom_tool_call_output`, `reasoning`), `event_msg` (`task_started`, `task_complete`, `item_completed`, `token_count`, `thread_settings_applied`), `token_usage_record`, `world_state`.
- **Client identity:** `session_meta.originator` separates desktop (`codex_work_desktop`) from CLI. It also carries `cli_version`, `model_provider`, `history_mode` (`paginated` on desktop) and `cwd`.
- **Verification data:** `item_completed` with `CommandExecution` carries `command`, `cwd`, `exit_code`, separate `stdout` and `stderr`, and `duration`. This is the reliable source of exit codes (hooks lack them; 0015).
- **Usage data:**
  - `token_usage_record` / `token_count` give per-response `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens` and `model_context_window`.
  - **`rate_limits.primary.used_percent` with `window_minutes` and `resets_at` gives the plan usage-limit percentage directly.**
  - `thread_settings_applied` records model and `reasoning_effort` changes.
- **Desktop shell work** appears as `custom_tool_call` named `exec` (code mode) with list-typed outputs.
- **Scale:** one active rollout on this machine is **1.09 GB**. There's also a 1.2 GB `thread_history_1.sqlite` and a `codex migrate-rollouts` command, which signals a possible future move away from JSONL.

### Claude Code (CLI, `-p` and desktop)
- **Location:** `~/.claude/projects/<cwd-key>/<sessionId>.jsonl`, where `<cwd-key>` is the path with separators replaced by `-`.
- **Records** carry a stable `uuid` and `parentUuid` (a tree), `sessionId`, `promptId`, `timestamp`, `cwd`, `gitBranch`, `version` and **`entrypoint`** (`claude-desktop` for desktop sessions).
- **Types:** `user`, `assistant` (content: `text`, `thinking`, `tool_use`), `attachment` (including `hook_success` / `hook_blocking_error` with `exitCode`, and `deferred_tools_delta`), `file-history-*`, `queue-operation`, `last-prompt`.
- **Usage:** `message.usage` has `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (with a 1h/5m split), `output_tokens` and thinking tokens, plus `model`.
- **Tool results:** `toolUseResult` has `{stdout, stderr, interrupted}`, and the `tool_result` content has `is_error`. There's **no numeric exit code**, so `arbiter verify` or parsers must supply one.

## Decision
- Both watchers **tail from a saved byte offset** and never re-read files whole.
- **Dedupe keys:** Codex `(session_id, ordinal)`; Claude `(sessionId, uuid)`.
- Hook events carry `transcript_path`, which binds a hook session to its transcript file without guessing.
- The Codex watcher reads `rate_limits` and `thread_settings_applied` for usage-limit and effort telemetry. The Claude watcher reads cache usage.
- **Exit codes:**
  - Codex: taken from `CommandExecution.exit_code`.
  - Claude: `is_error` plus parser inference; `arbiter_observed` evidence still requires `arbiter verify` (§6.4.1, §12.8).
- **Format drift is expected.** The profile declares a parser version, and the probe detects a mismatch. A JSONL→SQLite migration in Codex would ship as a new parser version.

## Consequences
- The T3 tier is richer than T2 for verification and usage, so T2 and T3 are complementary, not redundant.
- Usage-limit percentage, which you asked about for measuring gains, is directly observable for Codex.

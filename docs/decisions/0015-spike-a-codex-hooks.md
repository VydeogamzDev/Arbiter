# 0015 — Spike M0.a: Codex hooks exist, fire in CLI and app-server, and need one-time trust

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4, §4.5.2, §12.7, §12.8
- Evidence: `docs/spikes/m0/README.md` §a; scripts in `docs/spikes/m0/scripts/`

## Context
The whole T2 tier for Codex depended on whether Codex has usable hooks, which events fire, what the payloads contain, and whether hooks also run on the desktop path.

## Finding
Tested on the Codex build bundled with the installed desktop app (`codex-cli 0.155.0-alpha.16`, desktop package 26.917.6896.0). Every run used a throwaway `CODEX_HOME` and a local mock model server, so no user quota or config was touched.

- **Hooks are a stable, enabled feature** (`features: hooks stable true`).
  - Sources: `~/.codex/hooks.json`, `[hooks]` in `config.toml`, repo `.codex/`, and plugins.
  - Events: SessionStart, SessionEnd, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SubagentStart, SubagentStop, PreCompact, PostCompact, Interrupt.
- **Observed firing** in `codex exec` and through `codex app-server` (the process the desktop drives): SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop and SessionEnd. The app-server emits `hook/started` and `hook/completed` notifications with durations.
- **Payloads** follow Claude Code's shape: `session_id`, `turn_id`, `transcript_path`, `cwd`, `model` and `permission_mode` on every event, plus:
  - `Stop`: `last_assistant_message`, `stop_hook_active`.
  - `UserPromptSubmit`: `prompt`.
  - `PreToolUse` / `PostToolUse`: `tool_name`, `tool_input`, `tool_use_id`. Shell calls are normalized to `tool_name: "Bash"`.
  - `PostToolUse` also has `tool_response`, which is **stdout text only, with no exit code**.
- **Blocking Stop:** `{"decision":"block","reason":…}` makes Codex continue the turn. The next Stop arrives with `stop_hook_active: true`.
- **Trust is mandatory.** Non-managed hooks are skipped **silently** until trusted. Trust is stored per hook as `hooks.state.'<source path>:<event>:<group>:<index>'.trusted_hash = "sha256:…"`. Editing a hook's definition re-arms review. `--dangerously-bypass-hook-trust` exists for one invocation. The app-server exposes `hooks/list`, which returns `trustStatus` and `currentHash`.
- **Handler types:** `command` and `mcp_tool` run; `prompt` and `agent` are parsed but skipped. On Windows, `command` hooks run as `powershell.exe -NoProfile -Command "<command>"` (Windows PowerShell 5.1). A quoted executable path therefore fails, and every command hook pays PowerShell start-up time (see 0019).
- **`mcp_tool` hooks** call a tool on an already-connected MCP server. Their `input` templates use `${field}` placeholders. **A placeholder for a field the event doesn't carry makes the hook fail** (for example `agent_id` outside subagents). The tool's text result is read like command output: a JSON `decision: block` blocked Stop.
- **Legacy `notify` is broken on Windows.** The desktop logs show it failing every turn with `os error 206` (the filename or extension is too long).
- **Desktop Codex runs shell work through a code-mode `exec` custom tool.** Whether PreToolUse/PostToolUse fire for those calls wasn't observable without a live desktop turn.

## Decision
- Codex gets **T2** in both CLI and desktop, subject to the user's one-time trust.
- Arbiter's Codex hooks use **`mcp_tool` handlers** pointing at the Arbiter MCP shim (0021). Each event gets its own input template containing only fields that event always carries. Subagent events get separate templates.
- **Hook definitions must be hash-stable across Arbiter upgrades,** so trust isn't re-armed. That means a fixed server, tool and template, with no version strings.
- **Setup never writes `trusted_hash`.** The user trusts the hooks once, via `/hooks` in the CLI or the desktop hooks UI. `arbiter doctor` reads trust status and says clearly when hooks are untrusted.
- **Exit codes come from the rollout transcript (T3), not from hooks.**
- `notify` is not used.
- The M2.3 probe adds a check for whether tool hooks fire for desktop code-mode `exec` calls.

## Consequences
- Setup can't be fully zero-touch for Codex: one trust click remains, by design, because it's a Codex safety control.
- Until the user trusts the hooks, Codex runs at T1+T3, and doctor must make that visible.

# 0027 — M5 client coverage: ten new profiles, minimal-edit writers, hook dialects

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.2, §4.4.2, §4.5.2; decisions 0021, 0025

## Context
M5 extends Arbiter from Codex and Claude Code to every other common MCP client. A research pass (2026-09) against official docs and source found several recent changes:
- **Windsurf** was renamed **Devin Desktop** on 2026-06-02, and its MCP path moved.
- **Cline 4.x** keeps its config under `~/.cline/data/`.
- **Claude Desktop's Windows Store build** reads a virtualized config path.
- **Config formats vary:** JSON, JSONC (Zed, VS Code, OpenCode, the Devin CLI) and YAML (Goose).
- **Hook systems vary:** some clients have none, and those that do differ in format and semantics.

## Decisions
1. **Profiles are data.** There are ten new built-in profiles:
   - `cursor`, `vscode` (GitHub Copilot) and `gemini_cli`, which have hooks;
   - `cline`, `zed`, `opencode`, `goose`, `claude_desktop`, `devin_desktop` and `windsurf` (legacy Cascade path), which are MCP only.

   Each profile file cites its sources and marks what is unconfirmed.
2. **Per-OS paths.** A profile path can be a template, a per-platform mapping, or a list of candidates. A list resolves to the first existing file, else the first existing parent directory, else the first candidate; this covers Claude Desktop's MSIX path and `opencode.json`/`.jsonc`.
   - `ClientEnv` gains `{appdata}`, `{xdg_config}` and `{localappdata}`.
   - When sandboxed (`ARBITER_CLIENT_HOME`), every root derives from the sandbox home, never from the real environment.
3. **Entry templates.** Each profile declares the shape of its entry, using `{command}`, `{args}` and `{argv}` placeholders:
   - OpenCode takes one `command` array;
   - Goose takes `cmd`, `args` and `envs`;
   - Zed takes `context_servers`.

   A single format-independent `is_ours` recognizes Arbiter's entry in any of these shapes.
4. **Minimal-edit writers for JSONC and block YAML.** Re-serializing a user's settings would drop their comments, so Arbiter:
   - inserts or removes only its own entry as text;
   - re-parses the result, and requires the only semantic change to be that entry (plus pruning containers left empty);
   - refuses anything it can't edit safely, such as flow-style YAML or a foreign `arbiter` entry. In that case setup prints a snippet to paste.

   Round trips restore the original bytes.
5. **Manifest records per (client, file, kind).** Gemini keeps MCP servers and hooks in one `settings.json`:
   - records that share a file share the original backup;
   - a change is recomputed at apply time if an earlier change already rewrote the file;
   - uninstall handles all of a file's records together, so an unedited file is still restored byte for byte.
6. **Hook dialects.** These are implemented only where the format is documented, all using the `command` transport (`arbiter hook <client> <event>`):

   | Client | Hook file | Holds back a stop? | Translation |
   | --- | --- | --- | --- |
   | Cursor | `~/.cursor/hooks.json` (v1, handler lists) | **no**: only a follow-up turn, so the gate annotates | `conversation_id`→session, `generation_id`→turn, `workspace_roots[0]`→cwd, `afterAgentResponse.text`→final message |
   | VS Code + Copilot | `~/.copilot/hooks/arbiter.json` (Arbiter owns the file) | yes: `hookSpecificOutput.decision: block` | Claude-compatible payload; `additionalContext` on SessionStart, SubagentStart, PostToolUse |
   | Gemini CLI | `~/.gemini/settings.json` `hooks` (matcher groups, timeouts in ms) | yes: AfterAgent `decision: deny` makes Gemini retry with the reason | BeforeAgent→prompt, AfterAgent→stop (`prompt_response`→final message), Before/AfterTool→tools |

   - The daemon translates the payload into canonical form before ingest, and translates the decision back afterwards.
   - A client that can't hold back a stop gets `annotate`, whatever the gate mode, so it never consumes the block budget.
   - Goose plugin hooks are left for later: `plugin.json` isn't documented well enough.
   - Devin Local already loads `~/.claude/settings.json` hooks, so Claude Code's http hooks fire there, attributed to `claude_code`.
7. **Client attribution from `clientInfo.name`.** Profiles list the names their client reports (`mcp_client_names`, with exact or `prefix*` patterns). This replaces the old substring rule, which counted Claude Desktop (`claude-ai`) as Claude Code.
8. **Recorded-fixture contract tests** in `tests/fixtures/clients/<id>.v<N>.yaml` cover typical configs, entry shape, client names, hook payload translation and response translation. A profile that changes without matching fixtures fails; that's the drift check.
   - The fixtures are **derived from documented formats**. As each client is installed, they should be replaced with real recordings.

## Consequences
- The M5 exit is met in the sandbox:
  - every MCP-capable profile reaches configured T1 through setup;
  - T1 becomes *verified* when the client launches the shim (`client_seen`) and reports a name its profile lists;
  - drift is caught by the fixture tests.
- T2 hooks for Cursor, VS Code and Gemini are *configured* by setup, but verified only once hook events are observed. They haven't been exercised against the real clients yet.
- T3 transcripts remain Codex and Claude Code only. Gemini has JSONL transcripts that are worth a parser later; the others use SQLite or undocumented formats.
- Command hooks on Windows need an Arbiter path without spaces to work under PowerShell (uv's default tool path has none).

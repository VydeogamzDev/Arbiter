# 0025 — v0.1 packaging, Claude Code plugin, and daemon launch environment

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.5.1, §4.5.5, §23.3; decisions 0018, 0021

## Context
Releasing v0.1 needs an installable package, the Claude Code plugin from §4.5.5, and an install path that works for `uv tool install`. Verifying the install in a sandbox exposed a launch bug.

## Decisions
1. **Distribution.**
   - `arbiter-agent` 0.1.0 is built with hatchling (`uv build`). The wheel carries migrations, config defaults, client profiles and the eval corpus.
   - The install check (`docs/spikes/m4/scripts/tool_install_check.py`) installs the local wheel with `uv tool install` into a throwaway `UV_TOOL_DIR`, then runs setup, status, doctor, eval, uninstall and stop against sandboxed client homes.
   - **Publishing to PyPI is left to the owner** (`uv publish` with their token); the build never publishes.
2. **Claude Code plugin** (`packaging/claude-code`, a local marketplace named `arbiter-local`):
   - It bundles the `arbiter` MCP server (`arbiter mcp`), plus `command` hooks in exec form (`arbiter` with `args: [hook, claude_code, <Event>]`, no shell) for the same events setup registers.
   - It does not use `mcp_tool` hooks: Claude Code's docs don't say how a hook names a plugin-bundled MCP server, so Arbiter doesn't guess.
   - It does not use `http` hooks either: the per-install port can't be embedded in a static plugin.
   - `arbiter setup` remains the recommended path, with `http` hooks at about 1 ms each. It detects an enabled `arbiter@…` plugin in Claude settings and leaves Claude Code alone, so nothing is registered twice.
3. **Client-home overrides travel to the daemon on its command line.**
   - WMI-launched processes don't inherit the caller's environment (0018), so a daemon started for a sandbox, or for a user with a custom `CODEX_HOME`, watched the default Codex and Claude folders instead.
   - The launcher now passes `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `ARBITER_CLIENT_HOME` as hidden `--client-env NAME=VALUE` arguments; only those three names are accepted.
   - `status` reports the effective values (`client_homes`), and the WMI launch test asserts them.
   - Found by the sandboxed install check: before the fix, the throwaway store ingested the developer's own transcripts. That store lived in a temp directory and was deleted, and no client file was modified.

## Consequences
- Plugin users pay about 200–300 ms per hook on Windows (a process per hook), versus about 1 ms for setup's `http` hooks. The plugin README says so.
- Revisit `mcp_tool` in the plugin once Claude Code documents plugin MCP server naming for hooks.

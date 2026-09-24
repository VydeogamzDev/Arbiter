# Arbiter as a Claude Code plugin

`arbiter setup` is the recommended way to connect Claude Code: it registers loopback `http`
hooks (about 1 ms each) and the MCP server. This plugin is an alternative for people who
prefer Claude Code's plugin manager. It bundles the same MCP server and uses `command` hooks
that run `arbiter hook claude_code <event>` (about 200-300 ms each on Windows, because each hook
starts a process).

Both need the `arbiter` command on PATH:

```bash
uv tool install arbiter-agent
```

Install from this local marketplace:

```bash
claude plugin marketplace add ./packaging/claude-code
claude plugin install arbiter@arbiter-local
```

(Inside Claude Code: `/plugin marketplace add ./packaging/claude-code`, then
`/plugin install arbiter@arbiter-local`.)

`arbiter setup` detects the enabled plugin and skips Claude Code, so hooks are never
registered twice. To switch to the faster http hooks, disable the plugin and run
`arbiter setup --clients claude_code`.

Notes:
- The plugin uses `command` hooks, not `mcp_tool` hooks: how a hook names a plugin-bundled MCP
  server isn't documented yet, so Arbiter doesn't guess.
- The completion gate runs in `annotate` mode by default and never blocks. Block mode is opt-in
  (`completion.gate_mode: block` in Arbiter's config, or `arbiter gate block` for one session).

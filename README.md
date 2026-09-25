# Arbiter

A local control plane that watches, verifies and (later) advises AI coding agents: Codex (desktop and CLI), Claude Code, and other MCP-capable clients. The design is in [docs/arbiter-spec.md](docs/arbiter-spec.md), the plan is in [docs/milestones.md](docs/milestones.md), and the rationale is in [docs/decisions/](docs/decisions/README.md).

**Status: v0.1.0 (M0–M4).** Arbiter records each agent session locally and turns it into task state:
- what you asked for (an immutable intent log with goal epochs);
- the contracts the agent proposed (each quoting your words);
- what was observed (test runs, exit codes, file changes);
- whether your tests were weakened, compared against a session baseline.

When the agent claims it's done, Arbiter writes a **finish ledger**: verified or not, and what evidence is missing. By default it only annotates. Blocking is opt-in and bounded. Everything stays on your machine, and secrets are redacted before anything is stored.

## Install

```bash
uv tool install arbiter-agent      # or: pipx install arbiter-agent  (Python 3.11+)
arbiter setup                      # detects Codex / Claude Code, shows a diff, backs up, writes
arbiter doctor                     # shows what's verified for each client
```

`arbiter setup` never changes permissions, approvals, sandbox or model settings. It never touches your other MCP servers, and it can't approve Codex hooks for you:
- **Codex (desktop + CLI):** trust the Arbiter hooks once, in `/hooks` in the CLI or under Settings > Hooks in the desktop app. Until then Codex runs at tiers T1+T3, without the completion gate.
- **Claude Code:** works right away (loopback `http` hooks, about 1 ms each). A Claude Code plugin is also available in [packaging/claude-code](packaging/claude-code/README.md).
- **Also supported:** Cursor, VS Code (GitHub Copilot), Gemini CLI, Cline, Zed, OpenCode, Goose, Claude Desktop, Devin Desktop and legacy Windsurf.
  - Setup edits each client's own config file, including JSONC and YAML, without dropping your comments.
  - Cursor, VS Code and Gemini CLI also get hooks.
  - `arbiter setup --clients zed,goose` limits setup to specific clients.
- **Anything else with MCP:** `arbiter setup --print generic_mcp`.

`arbiter uninstall` removes exactly what setup added. Files you haven't edited since are restored byte for byte.

> Until `arbiter-agent` is on PyPI, install from a local build: `uv build`, then `uv tool install dist/arbiter_agent-0.1.0-py3-none-any.whl`.

## Using it

| Command | What it does |
| --- | --- |
| `arbiter status` | daemon health, counters, breakers |
| `arbiter sessions` | recorded sessions |
| `arbiter contracts` | the current session's contracts, uncovered requests, integrity, tests (run it in the project directory, or pass `--session`) |
| `arbiter contracts waive C2` / `confirm C3` / `add "..." --test "pytest -q"` | your decisions: agents can propose contracts but can never mark them PASS |
| `arbiter ledger` | evaluate the finish ledger now |
| `arbiter gate block` / `annotate` / `default` | per-session gate mode (the default is `completion.gate_mode: annotate`) |
| `arbiter verify --init`, then `--trust`, then `arbiter verify` | run your repo's verification commands as Arbiter-observed evidence (`.arbiter/verify.yaml`; trusting it needs an interactive terminal) |
| `arbiter eval` | run the built-in evaluation corpus against the §20.17 gates |
| `arbiter exclude <path>` | stop recording sessions under a path |
| `arbiter control` / `arbiter breakers` | manual controls and circuit breakers: switch a module off, turn the controller off (clients get their normal behavior back; events are still recorded), reset a breaker after inspecting it. Anything that weakens verification asks for confirmation in an interactive terminal |
| `arbiter eval --faults` | inject a fault into every circuit breaker and check that each trips and fails safe |
| `arbiter context "task"` | which files to read for a task: files the error points at, files you named or changed, then the most relevant files, with reasons |
| `arbiter advice [--history]` | advisory read of the session: suggested reasoning effort and why, whether to broaden retrieval or replan, and the risk level of the current diff with a suggested test order. `--history` lists every recorded recommendation (the audit trail) |
| `arbiter eval --advisory` | run the diff-risk and retrieval gates |
| `arbiter gateway servers --client X` / `adopt X` / `release X` / `bench` | tool gateway: move an existing MCP server behind Arbiter's three-tool gateway (with a diff and backup; read-only tools run, tools that change things ask you on every call, and servers whose approvals can't be mirrored stay direct), put it back, or measure per client whether the gateway saves enough to turn on |
| `arbiter semif status` / `enable` / `bench [--heldout]` / `export-onnx` | optional semantic sensor: plans a model tier for your GPU (writes config only with `--yes`, never downloads by itself), benchmarks it against the rules, and exports the tier 0 encoder to a small ONNX model (about 1 GB of RAM, CPU-friendly). It only logs judgments beside the rules and never changes a decision |

Agents get these MCP tools:
- **task state:** `arbiter_contract_propose`, `arbiter_contracts`, `arbiter_scope_change`, `arbiter_finish_check`, `arbiter_verify`;
- **repository retrieval:** `arbiter_search`, `arbiter_symbol`, `arbiter_related`, `arbiter_context` (files to read for a task);
- **advice:** `arbiter_advice` (effort, retrieval and diff-risk advice; advisory only);
- **health and controls:** `arbiter_status`, `arbiter_controls` (agents can list controls and ask for a one-turn bypass; they can't weaken verification).

Retrieval results are always fresh, skip secrets and generated files, and cite `path:line`. Use `arbiter search "..."` in a terminal for the same results.

What the gate checks:
- every active contract is PASS (or waived by you);
- nothing you asked for is left without a contract;
- test results are fresh (observed after the last code change) and come from the exact test command, not a narrowed `-k` run;
- test integrity is OK against the session baseline: no deleted tests, skip markers, weakened assertions, rewritten fixtures or new harness filters.

A message that ends with a question is never gated. Block reasons are prefixed `[Arbiter]` and scoped to the current turn, so agents don't save them as standing preferences.

## Development

```bash
uv sync                      # Python 3.11+, creates .venv
uv run pytest -m "not slow"  # fast suite
uv run pytest -m slow        # exit gates: latency, cold start, WMI launch (Windows)
uv run arbiter eval          # M3/M4 gates on the corpus
uv run ruff check src tests && uv run mypy
ARBITER_REAL_CLAUDE=1 uv run pytest tests/test_real_claude_memory.py -s   # opt-in, real Claude Code (~$0.09)
```

`--home DIR` keeps everything in one folder. Without it, Arbiter uses the platform locations shown by `arbiter paths`.

## Layout

| Piece | Where |
| --- | --- |
| Config (spec §26 defaults, validated overrides), feature flags | `config/`, `flags.py` |
| Daemon: single instance, launched outside client job objects (WMI on Windows), drain on stop | `daemon/server.py`, `lifecycle.py`, `launcher_windows.py` |
| IPC: user-only named pipe or Unix socket, token HMAC challenge, versioned handshake | `daemon/ipc.py`, `auth.py`, `protocol.py` |
| Hook transports: MCP `arbiter_hook` (Codex `mcp_tool`), loopback HTTP (Claude Code), command CLI fallback | `shims/`, `daemon/http_hooks.py` |
| Client profiles, setup / doctor / uninstall, capability probe, transcript discovery | `clients/`, `setup/`, `integration/` |
| SQLite (WAL, single writer, migrations with backup and read-only fallback), append-only event log | `state/store.py`, `writer.py`, `audit/` |
| Task state: intent log, goal epochs, facts, contracts, coverage, repo identity | `state/`, `daemon/session_engine.py` |
| Evidence, finish ledger, claim detection, gate, breakers, verify runner | `completion/` |
| Runner parsers, baseline, test integrity, errors, diff stats, usage | `telemetry/` |
| Loop detection (advisory) | `reasoning/` |
| Repository indexes and retrieval tools | `retrieval/` |
| Semantic sensor: backends, budgeting, validation, mirroring, shadow harness, benchmark | `semif/` |
| Policy core: fail modes, breaker board, cascade, priority and authority, budgets, shedding; controls | `policy/`, `ui/overrides.py` |
| Advisory: reasoning scheduler (model x effort), diff risk and review scheduling, Host Advisory API v0 | `reasoning/`, `review/`, `host/` |
| Tool gateway: catalog, search (lexical + tier-0 encoder), schema validation, approval mirroring, adoption, benchmark | `gateway/` |
| Privacy: redaction at ingest, project scope and `.arbiterignore`, retention and storage cap | `privacy/` |
| Eval harness, corpus and §20.17 gates | `eval/` |

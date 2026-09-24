# M0 assumption spikes — evidence

Run on 2026-09-24 on the user's Windows 11 machine. Findings and decisions are in [decisions 0015–0021](../../decisions/README.md). This file records how each result was obtained, so it can be reproduced.

**Safety of the method**
- Codex spikes used a throwaway `CODEX_HOME` and a local mock of the Responses API (`scripts/mock_responses.py`), so they used **no quota and didn't change the user's `~/.codex`**.
- Claude Code spikes used `claude -p --model haiku --settings <file>`: two runs, about $0.04 in total, with the user's settings unchanged. A test memory file that Claude wrote during the block test was deleted.
- Real transcripts and logs were read **structurally** (record types, keys, counts), not their content.

**Versions**
- Codex desktop package `OpenAI.Codex 26.917.6896.0`.
- Bundled engine `codex-cli 0.155.0-alpha.16`, copied from the package's `app\resources\codex.exe`, because WindowsApps binaries can't be executed in place. `~/.codex/.sandbox-bin/codex.exe` (0.153.3) is also runnable.
- Claude Code `2.1.251` (CLI), `2.1.280` (desktop). Python 3.14.0.

## a. Codex hooks
1. Checked the feature flags (`codex features list` → `hooks stable true`) and extracted the embedded hook input and output JSON schemas from the binary.
2. Read the official hooks reference: sources, events, matchers, trust, and `decision: block`.
3. Ran `codex exec` against the mock with `hooks.json` logging every event (`scripts/log_hook.py`).
   - Without trust, no hooks ran and nothing was reported.
   - With `--dangerously-bypass-hook-trust`, SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop (twice, the second after a block) and SessionEnd all fired.
4. **Windows shell:** the hook parent was `powershell.exe -NoProfile -Command "<command>"`. Quoted executable paths failed.
5. **Tool events:** the mock issued an `exec_command` call. PreToolUse and PostToolUse had `tool_name: "Bash"`. `tool_response` was stdout only; the model-visible output said "Process exited with code 3", but the hook payload didn't.
6. **Desktop path:** drove `codex app-server` over stdio JSON-RPC (`scripts/drive_appserver.py`):
   - `hooks/list` → 12 hooks `untrusted`, and the turn ran with no hooks.
   - `config/batchWrite` of `trusted_hash` (in the throwaway home) → hooks `trusted`, then `hook/started` and `hook/completed` with command-hook durations of 540–600 ms.
7. **`mcp_tool` handlers** (`scripts/mini_mcp.py`): all events arrived with `${field}`-templated arguments. A missing-field placeholder caused hook failure. A JSON `decision: block` returned by the tool blocked Stop. App-server durations were 2–4 ms.

## b. Codex desktop and `config.toml` MCP servers
- In `~/.codex/logs_2.sqlite` over the last three days, `codex_mcp::connection_manager::tool_catalog` entries tied to desktop threads name servers from `[mcp_servers.*]` in `~/.codex/config.toml`: eveos 248, bonsai-mcp 20, 21st 9.
- All recent rollouts have `originator: codex_work_desktop` and `cli_version: 0.155.0-alpha.16`.
- The desktop also runs the legacy `notify` hook, which fails every turn on Windows with `os error 206`.

## c. Claude Code stop semantics
- Hooks were delivered through `--settings`: exec-form `command` hooks (`args`, no shell) plus `http` hooks to `scripts/http_hook_server.py`.
- **Run 1** (a tool call, then "Done…"): the Stop http hook returned `decision: block`. Claude continued; the next Stop had `stop_hook_active: true`. Claude also wrote the block reason into a persistent memory file (hazard; deleted).
- **Run 2** (the model ends by asking a question): Stop fired with `last_assistant_message: "Which function would you like to rename?"`.
- The Windows shell tool is named `PowerShell`. `tool_response` is `{stdout, stderr, interrupted, isImage}`, with no exit code.

## d. Windows daemon survival
- A detached child with `CREATE_BREAKAWAY_FROM_JOB` spawned from a SessionStart hook outlived both `codex exec` and `claude -p`. Those CLI trees aren't in kill-on-close jobs.
- `scripts/job_probe.py`:
  - Codex desktop's engine, Claude desktop and bundled Claude Code are **in job objects**.
  - In a simulated `KILL_ON_JOB_CLOSE` job without breakaway permission, the child is killed, and asking for breakaway is denied. With `BREAKAWAY_OK` or `SILENT_BREAKAWAY_OK`, it survives.
- `scripts/wmi_probe.py`: `Win32_Process.Create` from inside a job produced a process **not in any job**, parented to `WmiPrvSE.exe`, in about 0.8 s.

## e. Hook latency
`scripts/bench_hooks.py` and `scripts/bench_more.py` → `results/bench_hooks.json`. Summary table in decision 0019. Headlines:
- Codex command hook: p50 537 ms, p95 667 ms.
- Claude exec form: p50 209 ms, p95 279 ms.
- Codex `mcp_tool` hook: 2–4 ms.
- HTTP round trip: p50 1.1 ms, p95 3.3 ms.

## f. Transcripts
- **Codex:** rollout paths, record types, the monotonic `ordinal`, `originator`, `CommandExecution.exit_code`, token usage including `cached_input_tokens`, `rate_limits.primary.used_percent` and `thread_settings_applied` were all confirmed on the mock sessions and structurally on real desktop rollouts. One live rollout is 1.09 GB.
- **Claude Code:** `~/.claude/projects/<cwd-key>/<sessionId>.jsonl` with `uuid`/`parentUuid`, `entrypoint: claude-desktop`, usage with cache read and creation tokens, `toolUseResult` with no exit code, and hook-run attachments with `exitCode`.

## Reproducing
1. Copy the desktop engine out of the package: `Copy-Item "C:\Program Files\WindowsApps\OpenAI.Codex_*\app\resources\codex.exe" .`
2. Start the mock: `python scripts/mock_responses.py 18765`.
3. Create a throwaway `CODEX_HOME` whose `config.toml` sets `model_provider = "mock"` with `base_url = "http://127.0.0.1:18765/v1"`, `wire_api = "responses"`, `env_key = "MOCK_API_KEY"`, plus a `hooks.json` pointing at `scripts/log_hook.py` with **unquoted** paths.
4. Run `CODEX_HOME=<tmp> MOCK_API_KEY=x codex exec --skip-git-repo-check "hello" </dev/null`, or `scripts/drive_appserver.py` for the app-server path.

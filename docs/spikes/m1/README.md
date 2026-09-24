# M1 exit evidence

Measured on 2026-09-24 on the development machine: Windows 11, Python 3.14, run from inside the Claude desktop app, so tests run inside a job object like real hook processes. POSIX paths were checked on Ubuntu 22.04 (WSL2, Python 3.10). Raw numbers are in [exit_gates.json](exit_gates.json).

## Exit criteria

| M1 exit criterion | Result | Evidence |
| --- | --- | --- |
| §20.17 daemon cold start ≤ 2 s (p95) | **1.04 s** p95 over 5 cold launches via the production WMI path | `test_daemon_cold_start_p95` |
| §20.17 gating hook p95 ≤ 300 ms | MCP **1.11 ms**, HTTP **25.4 ms** (240 hooks each) | `test_primary_hook_transports_meet_budgets` |
| §20.17 telemetry hook added latency p95 ≤ 50 ms | MCP 1.11 ms, HTTP 25.4 ms | same |
| Command fallback (not gated, decision 0021) | 238 ms p95, under the 1500 ms hard deadline | `test_command_fallback_measured_not_gated` |
| Unauthenticated and cross-user IPC rejected | wrong token rejected; pipe DACL is `D:P(A;;FA;;;<current user SID>)` only, with remote clients rejected; POSIX socket `0600` in a `0700` directory | `test_wrong_token_rejected`, `test_pipe_dacl_is_current_user_only`, POSIX smoke |
| HTTP hooks require the token | 401 without it; `/health` reveals nothing | `test_http_hook_requires_token_and_ingests` |
| Replaying the log reproduces reducer state | equal after mixed clients, out-of-order results, duplicates and a restart | `test_replay_reproduces_live_reducer`, `test_restart_keeps_port_state_and_reducer`, fake-host test |
| Crash recovery | hard kill mid-transaction: integrity OK, every acknowledged commit present, no torn transaction | `test_crash_mid_write_recovers_committed_prefix` |
| Migration rollback | failed migration restores the backup and opens read-only; hooks still fail open | `test_failed_migration_restores_backup_and_degrades`, `test_degraded_store_still_fails_open` |
| Excluded projects never parsed | CLI exclude and `.arbiterignore`: 0 events, 0 blobs, only a counter; the transcript file isn't read | `test_excluded_project_never_stored`, `test_arbiterignore_excludes_subtree` |
| Secrets redacted before first write | scanned every byte of the data and log directories after mcp, http and command runs: no secret present, placeholders present | `test_secrets_never_reach_disk` |
| A daemon crash never blocks the fake host | after a hard kill, MCP and HTTP hooks return `{}` within 3 s each | `test_daemon_crash_never_blocks_fake_host` |

## Also verified
- **Daemon launch:** the daemon is launched outside the calling client's job (the process WMI created is not in a job; its parent is `WmiPrvSE.exe`).
- **Daemon lifecycle:**
  - single-instance lock (a second daemon exits cleanly);
  - launch debounce;
  - drain on stop (40-event burst fully persisted);
  - token rotation per start, with the hook token stable.
- **Version handshake:** an older shim is told to pass through; a newer shim triggers drain-and-restart.
- **Shims:** the hook and MCP import path is stdlib-only (no yaml or sqlite loaded). The MCP shim implements initialize with version negotiation, tools/list, tools/call, ping and error codes.
- **Linux (WSL2):** setsid launch in 0.6 s; Unix socket; `fcntl` lock; all three transports; replay equality; drained stop.

## Totals
- 88 tests passed and 1 skipped (a POSIX-only test on Windows). That includes 5 slow exit-gate tests.
- ruff clean; mypy clean for `--platform win32`, `linux` and `darwin`.
- The CI workflow is at `.github/workflows/ci.yml` and needs a GitHub remote to run.

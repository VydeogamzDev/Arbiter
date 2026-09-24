# 0018 — Spike M0.d: desktop clients run in job objects; launch the daemon outside the client tree

- Status: Accepted (spike finding)
- Date: 2026-09-24
- Spec: §4.4.1 (daemon lifecycle)
- Evidence: `docs/spikes/m0/README.md` §d

## Context
The spec planned a daemon started lazily by the first shim or hook call, detached from the client. On Windows, a process inside a job object with `KILL_ON_JOB_CLOSE` is killed together with its whole job, children included, unless breakaway is allowed.

## Finding
- **Desktop client processes are inside job objects.** That covers the Codex desktop's engine (`%LOCALAPPDATA%\OpenAI\Codex\bin\…\codex.exe`), Claude desktop (`Claude.exe`, MSIX) and its bundled Claude Code, plus their node runtimes. Their job limits can't be read from outside.
- **Simulated worst case:**
  - In a `KILL_ON_JOB_CLOSE` job without breakaway permission, a detached child **is killed** when the job closes.
  - With the same job, requesting `CREATE_BREAKAWAY_FROM_JOB` **fails with access denied**.
  - With `BREAKAWAY_OK` or `SILENT_BREAKAWAY_OK`, the child survives.
- **CLI runs are different.** Children detached from `codex exec` and `claude -p` hooks outlived their clients, because those trees aren't in kill-on-close jobs.
- **A WMI launch escapes the job.** From inside a job, `Win32_Process.Create` produced a process **outside any job**, parented to `WmiPrvSE.exe`, in about 0.8 s (via PowerShell CIM; a direct COM call would be faster). Nothing persistent is installed.

## Decision
- The Windows daemon launcher starts the daemon **outside the client's process tree**, using WMI `Win32_Process.Create`. It never relies on a detached child of a hook or shim.
- If WMI is unavailable, fall back to a detached spawn with breakaway attempted, and report the reduced survivability in `doctor`. Optional per-user login autostart remains available.
- **macOS/Linux:** `setsid`/double-fork. launchd or a systemd user unit is optional.
- The single-instance lock and the version handshake (§16.5) make repeated launch attempts harmless.

## Consequences
- The first call after boot pays about one second for the daemon start. Shims fail open in the meantime (§4.4.5).
- The daemon survives the closing of the desktop app that started it, which it must, because it serves several clients.

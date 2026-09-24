# 0022 — M1 implementation choices

- Status: Accepted
- Date: 2026-09-24
- Spec: §4.4.1, §16.1–16.3, §18.8

## Context
Building M1 required several concrete choices that the spec leaves open.

## Decisions
1. **IPC authentication uses the stdlib HMAC challenge.** This is `multiprocessing.connection` `deliver_challenge` / `answer_challenge` with the per-install token. Frames are **JSON only**; `Connection.recv()` (pickle) is never used on this channel. On Windows a `PipeListener` subclass creates every pipe instance with:
   - a protected current-user-only DACL;
   - `PIPE_REJECT_REMOTE_CLIENTS`;
   - `FILE_FLAG_FIRST_PIPE_INSTANCE`, so a squatter makes startup fail instead of intercepting clients.
2. **Payloads live in SQLite.** They're stored in a content-addressed `blob` table keyed by an HMAC of the redacted content, not as separate files. They commit atomically with their event rows, which keeps retention and crash recovery simple. Space is reclaimed with incremental vacuum.
3. **Cross-surface duplicates are kept as evidence** rows with `duplicate_of`, rather than dropped. The reducer counts each occurrence once, while richer transcript data such as exit codes is retained. Exact re-deliveries on the same surface *are* dropped, by idempotency key.
4. **Account for the Windows venv launcher stub.** A venv `python.exe` is a launcher stub that runs the real interpreter as its child inside the stub's own job. What decision 0018 requires still holds: the process WMI creates (the stub) is outside the client's job, which `test_autostart_launches_outside_client_job` verifies. Consequence: the daemon's PID differs from the launched PID, so tooling reads the PID from `daemon.json`.
5. **HTTP hook port persisted at first start.** Before `arbiter setup` exists, the daemon picks a free loopback port on first start and persists it in `state/http_port`. It reuses that port on later starts, and M2 setup writes it into client config.
6. **The daemon accepts connections only after `daemon.json` is written.** A client that can reach the daemon can then always discover the HTTP port. Early connections wait on the listening endpoint.
7. **`ARBITER_NO_AUTOSTART`** disables lazy daemon launch from shims. Tests use it, and embedding hosts (M14) can use it to manage lifecycle themselves.
8. **POSIX socket placement.** The socket goes in `$XDG_RUNTIME_DIR/arbiter/`, or the state directory. If that path would exceed the AF_UNIX limit, it falls back to `/tmp/arbiter-<uid>/`. Before binding, the directory must be owned by the current user with no group or other access; otherwise the daemon falls back to token-authenticated loopback TCP.
9. **Python version.** The declared minimum is 3.11.
   - M1 code also ran on 3.10: the POSIX smoke test passed on Ubuntu 22.04's Python 3.10.
   - Since M2, setup and conformance parse Codex `config.toml` with `tomllib` (3.11+), so 3.10 is no longer supported.
   - The M3/M4 eval gates still pass on 3.10 under WSL, but the daemon doesn't start there. POSIX coverage comes from CI (Ubuntu and macOS, 3.11–3.14).

## Consequences
- The shim import path stays stdlib-only, enforced by `test_shim_import_path_is_stdlib_only`.
- Storage growth is bounded by the §16.3.3 cap on the single database file.

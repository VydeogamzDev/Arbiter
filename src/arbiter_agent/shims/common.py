"""Shared shim helpers: forward a hook to the daemon with a deadline, fail open otherwise."""

from __future__ import annotations

import os
import threading
from typing import Any

from arbiter_agent.daemon.client import DaemonClient, DaemonUnavailable
from arbiter_agent.daemon.diagnostics import record_failopen
from arbiter_agent.paths import ArbiterPaths

DEFAULT_DEADLINE_S = float(os.environ.get("ARBITER_HOOK_DEADLINE_MS", "1500")) / 1000.0


def trigger_launch(paths: ArbiterPaths, *, background: bool = True) -> None:
    """Start the daemon without waiting for it; never raises. Short-lived processes (the hook
    CLI) must pass ``background=False`` so the launch isn't lost when the process exits."""
    if os.environ.get("ARBITER_NO_AUTOSTART"):
        return

    def run() -> None:
        try:
            from arbiter_agent.daemon.lifecycle import ensure_daemon

            ensure_daemon(paths, wait=0.0)
        except Exception:
            pass

    if background:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


def forward_hook(client_obj: DaemonClient | None, paths: ArbiterPaths, component: str, client: str,
                 payload: Any, *, surface: str, event: str | None = None,
                 deadline: float = DEFAULT_DEADLINE_S,
                 background_launch: bool = True) -> tuple[dict[str, Any], DaemonClient | None]:
    """Return (client decision object, reusable client). ``{}`` means "no decision" (pass through)."""
    c = client_obj or DaemonClient(paths, component=component)
    try:
        result = c.request("ingest_hook", {"client": client, "payload": payload, "surface": surface,
                                           "event": event, "channel": component}, timeout=deadline)
        response = (result or {}).get("response") or {}
        return (response if isinstance(response, dict) else {}), c
    except DaemonUnavailable as exc:
        c.close()
        record_failopen(paths.logs, component, exc.reason)
        if exc.action != "passthrough":
            trigger_launch(paths, background=background_launch)
        return {}, None
    except Exception as exc:
        c.close()
        record_failopen(paths.logs, component, f"{type(exc).__name__}: {exc}")
        return {}, None

"""Fallback command-hook transport: ``arbiter hook <client> <event>`` (decision 0021).

Reads the hook JSON on stdin, forwards it with a deadline, and prints the client decision JSON
(nothing if there is none). Always exits 0: a hook must never break the host agent. Stdlib only.
"""

from __future__ import annotations

import json
import sys

from arbiter_agent.daemon.diagnostics import record_failopen
from arbiter_agent.paths import ArbiterPaths, get_paths
from arbiter_agent.shims.common import DEFAULT_DEADLINE_S, forward_hook

MAX_STDIN = 8 * 1024 * 1024


def main(client: str, event: str | None, paths: ArbiterPaths | None = None) -> int:
    paths = paths or get_paths()
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            record_failopen(paths.logs, "hook_cli", "payload too large")
            return 0
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, OSError) as exc:
        record_failopen(paths.logs, "hook_cli", f"bad input: {exc}")
        return 0
    try:
        response, client_obj = forward_hook(None, paths, "hook_cli", client, payload, surface="hook", event=event,
                                            deadline=DEFAULT_DEADLINE_S, background_launch=False)
        if client_obj is not None:
            client_obj.close()
        if response:
            sys.stdout.write(json.dumps(response))
            sys.stdout.flush()
    except Exception as exc:
        record_failopen(paths.logs, "hook_cli", f"{type(exc).__name__}: {exc}")
    return 0

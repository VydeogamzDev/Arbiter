"""Feature-flag registry (spec §23: every module ships behind its own flag and breaker).

Flags are declared here with their milestone and default. Users may override them in the
``features`` section of config.yaml; the circuit breaker (M8) can additionally force a flag
off at runtime. Stdlib only.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    name: str
    default: bool
    milestone: str
    description: str


_FLAGS = [
    Flag("event_log", True, "M1", "Append-only normalized event log"),
    Flag("http_hooks", True, "M1", "Loopback HTTP hook endpoint (Claude Code http hooks)"),
    Flag("mcp_hook_tool", True, "M1", "arbiter_hook MCP tool (Codex mcp_tool hooks)"),
    Flag("transcript_watchers", True, "M1", "Offset-tailing transcript watchers"),
    Flag("redaction", True, "M1", "Secret redaction at ingest"),
    Flag("retention", True, "M1", "Retention and storage-cap enforcement"),
    Flag("client_setup", True, "M2", "arbiter setup/doctor/uninstall"),
    Flag("task_state", True, "M3", "Session engine: intent log, goal epochs, facts"),
    Flag("contracts", True, "M3", "Agent-proposed contracts and rule extraction"),
    Flag("test_integrity", True, "M3", "Test-integrity detection against the session baseline"),
    Flag("loop_alerts", True, "M3", "Advisory loop alerts"),
    Flag("completion_gate", True, "M4", "Completion gate (annotate by default; block is opt-in)"),
    Flag("circuit_breakers", True, "M4", "Minimal breakers: hook latency, gate errors, parsers"),
    Flag("status_injection", True, "M4", "Hook status summaries (also needs ui.inject_status)"),
    Flag("repo_index", True, "M6", "Repository indexes and Arbiter retrieval tools"),
    Flag("semif", False, "M7", "SemIf inference service"),
    Flag("reasoning_advisor", False, "M9", "Reasoning scheduler recommendations"),
    Flag("tool_gateway", False, "M10", "Stable tool gateway"),
    Flag("host_api", False, "M9", "Host Advisory API"),
    Flag("context_scheduler", False, "M13", "Context scheduler"),
    Flag("speculation", False, "R1", "Pure-read speculation"),
    Flag("branching", False, "R4", "Selective branching"),
]
REGISTRY: dict[str, Flag] = {f.name: f for f in _FLAGS}


class FeatureFlags:
    """Resolved flag state: registry default <- config override <- runtime force-off."""

    def __init__(self, overrides: Mapping[str, bool] | None = None) -> None:
        unknown = set(overrides or {}) - set(REGISTRY)
        if unknown:
            raise KeyError(f"unknown feature flags: {sorted(unknown)}")
        self._overrides = dict(overrides or {})
        self._forced_off: dict[str, str] = {}
        self._lock = threading.Lock()

    def enabled(self, name: str) -> bool:
        flag = REGISTRY[name]
        with self._lock:
            if name in self._forced_off:
                return False
        return self._overrides.get(name, flag.default)

    def force_off(self, name: str, reason: str) -> None:
        """Runtime kill switch used by circuit breakers; does not persist."""
        REGISTRY[name]  # validate
        with self._lock:
            self._forced_off[name] = reason

    def clear_force(self, name: str) -> None:
        with self._lock:
            self._forced_off.pop(name, None)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {n: {"enabled": self.enabled(n), "default": f.default, "milestone": f.milestone,
                    "forced_off": self._forced_off.get(n)} for n, f in REGISTRY.items()}

"""The fail-behavior matrix (spec §18.6) as data, so breakers, status and tests share one source.

- ``fail_open``: stop optimizing and hand the host its normal behavior back.
- ``fail_conservative``: keep recording, never claim more than the evidence shows (UNKNOWN, unverified).
- ``fail_closed``: stop the action entirely (cancel, don't apply, don't export).
- ``abstain``: give no judgment; the caller's conservative default applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Mode(StrEnum):
    OPEN = "fail_open"
    CONSERVATIVE = "fail_conservative"
    CLOSED = "fail_closed"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class FailMode:
    component: str
    mode: Mode
    behavior: str


_ROWS = [
    FailMode("reasoning_optimizer", Mode.OPEN, "baseline/default effort"),
    FailMode("retrieval", Mode.OPEN, "broader/default retrieval: the agent's own search tools"),
    FailMode("tool_gateway", Mode.OPEN, "normal allowed tool surface"),
    FailMode("context_compaction", Mode.OPEN,
             "existing client context and built-in compaction; never a partial rewrite"),
    FailMode("semif", Mode.ABSTAIN,
             "fail open for optimization; abstain for destructive or high-consequence decisions"),
    FailMode("completion_evidence", Mode.CONSERVATIVE, "remain UNKNOWN; never automatically complete"),
    FailMode("speculation", Mode.CLOSED, "cancel and disable"),
    FailMode("branch_isolation", Mode.CLOSED, "don't branch or apply patches"),
    FailMode("privacy", Mode.CLOSED, "no export or persistence beyond policy"),
    FailMode("client_adapters", Mode.OPEN, "pass through to the host agent unchanged; never block it"),
    FailMode("completion_gate", Mode.CONSERVATIVE,
             "never PASS on failure; never block because of its own failure; stop allowed, marked unverified"),
    FailMode("verification_parsers", Mode.CONSERVATIVE, "unrecognized or contradicted output is UNKNOWN, never PASS"),
    FailMode("ipc_auth", Mode.CLOSED, "reject unauthenticated connections; shims pass through"),
    FailMode("migration", Mode.CONSERVATIVE, "restore the backup; daemon read-only degraded; shims pass through"),
    FailMode("setup", Mode.CLOSED, "abort without writing a config that can't be parsed, merged and validated"),
    FailMode("client_profile", Mode.OPEN, "drop only the tiers whose conformance checks fail"),
    FailMode("controller", Mode.OPEN, "shed optimization; hooks return the host's baseline behavior"),
    FailMode("learned_policy", Mode.OPEN, "conservative rules-only policy"),
]
MATRIX: dict[str, FailMode] = {r.component: r for r in _ROWS}


def mode_for(component: str) -> FailMode:
    return MATRIX[component]
